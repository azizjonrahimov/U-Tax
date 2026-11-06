"""In-process fakes of the sponsor APIs, as httpx MockTransports, built from their docs.

A fake also turns a FaultPlan into real HTTP answers, so the port contract suite drives the
adapter's own error mapping: RateLimited -> 429 + Retry-After, Unavailable -> 503,
NotConfigured -> 401, Rejected -> 403.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.gateway.ports import FaultPlan, NotConfigured, PortError, RateLimited, Rejected, Unavailable


def fault_response(faults: FaultPlan | None) -> httpx.Response | None:
    if faults is None:
        return None
    try:
        faults.check()
    except RateLimited as err:
        return httpx.Response(429, headers={"Retry-After": str(int(err.retry_after or 1))},
                              json={"name": "RateLimitError", "message": "slow down"})
    except Unavailable:
        return httpx.Response(503, json={"name": "ServiceUnavailable", "message": "down"})
    except NotConfigured:
        return httpx.Response(401, json={"name": "Unauthorized", "message": "bad key"})
    except Rejected:
        return httpx.Response(403, json={"name": "MessageRejectedError", "message": "no"})
    except PortError:
        return httpx.Response(500, json={"name": "InternalError", "message": "boom"})
    return None


class FakeAgentMail:
    """POST /v0/inboxes/{inbox}/messages/send and GET .../messages/{id}, with
    Idempotency-Key replay (same key + same request = same message, no second mail;
    same key + different request = 409), per docs.agentmail.to/idempotency."""

    def __init__(self, faults: FaultPlan | None = None, api_key: str = "am_test") -> None:
        self.faults = faults
        self.api_key = api_key
        self.sent: list[dict[str, Any]] = []
        self.by_key: dict[str, tuple[str, dict[str, Any]]] = {}
        self.stored: dict[str, dict[str, Any]] = {}  # message_id -> full message (GET)
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {self.api_key}":
            return httpx.Response(401, json={"name": "Unauthorized", "message": "bad key"})
        failed = fault_response(self.faults)
        if failed is not None:
            return failed
        parts = request.url.path.strip("/").split("/")
        if request.method == "POST" and parts[:2] == ["v0", "inboxes"] and parts[3:] == [
                "messages", "send"]:
            body = json.loads(request.content)
            key = request.headers.get("idempotency-key")
            if key is not None and not key:
                return httpx.Response(400, json={"name": "ValidationError", "errors": []})
            signature = {"inbox": parts[2], **body}
            if key and key in self.by_key:
                message_id, first = self.by_key[key]
                if first != signature:
                    return httpx.Response(409, json={"name": "ConflictError",
                                                     "message": "key reused"})
                return httpx.Response(200, json={"message_id": message_id, "thread_id": "t"})
            message_id = f"<am-{len(self.sent) + 1}@agentmail.to>"
            self.sent.append({"message_id": message_id, "inbox": parts[2], **body})
            if key:
                self.by_key[key] = (message_id, signature)
            return httpx.Response(200, json={"message_id": message_id, "thread_id": "t"})
        if request.method == "GET" and parts[:2] == ["v0", "inboxes"] and len(parts) == 5:
            message = self.stored.get(httpx.URL(request.url).path.split("/")[-1]) \
                or self.stored.get(parts[4])
            if message is None:
                return httpx.Response(404, json={"name": "NotFoundError", "message": "no"})
            return httpx.Response(200, json=message)
        return httpx.Response(404, json={"name": "NotFoundError", "message": request.url.path})
