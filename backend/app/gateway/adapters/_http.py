"""Shared plumbing for the sponsor HTTP adapters: one error mapping, one key encoding.

Every adapter maps provider answers onto the port errors the same way, so perform()'s
retry rule (retryable = backoff can help) holds for all of them:
  429            -> RateLimited (retry_after from Retry-After, seconds or HTTP date)
  401            -> NotConfigured (the key is wrong or revoked: retrying can't help)
  400/403/404/409/422 -> Rejected (this input is refused)
  5xx, timeouts, connection errors -> Unavailable (retryable)
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.gateway.ports import NotConfigured, PortError, RateLimited, Rejected, Unavailable

MAX_RETRY_AFTER = 60.0
# Short on purpose: ports run inside perform() under the gateway lock, and webhooks must
# answer within the provider's window (AgentMail: 15 s). perform() retries up to 3 times.
TIMEOUT = httpx.Timeout(8.0, connect=3.0)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def retry_after(response: httpx.Response) -> float | None:
    value = (response.headers.get("retry-after") or "").strip()
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, min(seconds, MAX_RETRY_AFTER))


def _detail(response: httpx.Response) -> str:
    """A short reason from the provider's error body, with any email address removed
    (a 400 may echo the recipient; this text reaches agents and logs)."""
    try:
        body = response.json()
    except ValueError:
        text = response.text
    else:
        if isinstance(body, dict):
            parts = [str(body[k]) for k in ("code", "message", "error") if body.get(k)]
            text = ": ".join(parts) or str(body)
        else:
            text = str(body)
    return _EMAIL.sub("[email]", text)[:200]


def provider_code(response: httpx.Response) -> str:
    """The machine-readable error code in a provider's body (`code`, else `name`)."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    inner = body.get("error") if isinstance(body.get("error"), dict) else body
    return str(inner.get("code") or inner.get("type") or inner.get("name") or "")


def json_dict(response: httpx.Response) -> dict:
    """A 2xx body as a dict ({} when it is empty, not JSON, or not an object)."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def check(response: httpx.Response, provider: str) -> httpx.Response:
    """Raise the port error for a non-2xx answer; return the response otherwise. The
    raised error carries `.status` and `.provider_code` so an adapter can refine it."""
    status = response.status_code
    if 200 <= status < 300:
        return response
    try:
        _raise(response, provider, status)
    except PortError as err:
        err.status = status  # type: ignore[attr-defined]
        err.provider_code = provider_code(response)  # type: ignore[attr-defined]
        raise
    return response


def _raise(response: httpx.Response, provider: str, status: int) -> None:
    detail = f"{provider} {status}: {_detail(response)}"
    if status == 429:
        raise RateLimited(detail, retry_after=retry_after(response))
    if status == 401:
        raise NotConfigured(detail)
    if status in (400, 403, 404, 409, 422):
        raise Rejected(detail)
    if status >= 500:
        raise Unavailable(detail, retry_after=retry_after(response))
    raise PortError(detail)


def call(provider: str, fn: Callable[[], httpx.Response]) -> httpx.Response:
    """Run one HTTP call; transport failures become Unavailable (the caller may retry)."""
    try:
        response = fn()
    except httpx.TimeoutException as err:
        raise Unavailable(f"{provider} timed out") from err
    except httpx.TransportError as err:
        raise Unavailable(f"{provider} unreachable: {type(err).__name__}") from err
    return check(response, provider)


def fingerprint(*parts: object) -> str:
    """A short hash of what a call does (recipient, subject, body...), so one gateway key
    reused for a different call is a different provider key."""
    raw = "\x1f".join(str(p) for p in parts).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def safe_key(key: str, prefix: str = "utax") -> str:
    """A provider idempotency key from a gateway key: deterministic, 1..256 chars of
    [A-Za-z0-9-._~] (the common charset rule), and free of case ids or addresses."""
    return f"{prefix}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:48]}"
