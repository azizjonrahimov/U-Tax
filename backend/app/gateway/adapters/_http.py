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
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from app.gateway.ports import NotConfigured, PortError, RateLimited, Rejected, Unavailable

MAX_RETRY_AFTER = 60.0


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
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        return str(body.get("message") or body.get("error") or body.get("code") or body)[:200]
    return str(body)[:200]


def check(response: httpx.Response, provider: str) -> httpx.Response:
    """Raise the port error for a non-2xx answer; return the response otherwise."""
    status = response.status_code
    if 200 <= status < 300:
        return response
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


def safe_key(key: str, prefix: str = "utax") -> str:
    """A provider idempotency key from a gateway key: deterministic, 1..256 chars of
    [A-Za-z0-9-._~] (the common charset rule), and free of case ids or addresses."""
    return f"{prefix}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:48]}"
