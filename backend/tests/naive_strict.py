"""Task 49: the real Naive answers 400 validation_failed for a list `limit` outside 1..100.
`strict(handler)` wraps a fake Naive's MockTransport handler (sync or async) to do the same,
so a limit above 100 fails in tests the way it fails live."""

from __future__ import annotations

import inspect

import httpx

MAX_LIMIT = 100


def refusal(request: httpx.Request) -> httpx.Response | None:
    raw = request.url.params.get("limit")
    if raw is None:
        return None
    try:
        ok = 1 <= int(raw) <= MAX_LIMIT
    except ValueError:
        ok = False
    if ok:
        return None
    return httpx.Response(400, json={"error": {
        "type": "invalid_request_error", "code": "validation_failed",
        "message": "limit must be 1..100", "param": "limit"}})


def strict(handler):
    if inspect.iscoroutinefunction(handler):
        async def checked_async(request: httpx.Request) -> httpx.Response:
            return refusal(request) or await handler(request)
        return checked_async

    def checked(request: httpx.Request) -> httpx.Response:
        return refusal(request) or handler(request)
    return checked
