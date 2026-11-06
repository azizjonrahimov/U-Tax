"""Naïve social adapter (ADAPTER_SOCIAL=naive).

Built against the Naïve OpenAPI spec (`GET https://api.vetta.sh/v1/openapi.json`, version
2026-09-30, read 2026-11-07): `POST /v1/identities/{identity}/social/posts` with
`Authorization: Bearer sk_...`, JSON `{content, platforms: ["x"|"linkedin"|...],
media_urls?}`, scope `sessions:write`. The identity needs an activated social workspace
with the X / LinkedIn accounts connected (owner steps: `POST .../social/activate`, then
the portal link from `POST .../social/portal`). The spec does not describe response bodies,
so the post id/url is read defensively. Like the async Naïve client, every POST carries an
`Idempotency-Key` reused across retries; a local index keeps the first url per key.

Sync on purpose: ports run inside perform() on a worker thread, never on the event loop.
Real calls are UNVERIFIED until a key exists; the MockTransport tests pin the shapes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.gateway.adapters import _http
from app.gateway.adapters.local import _KeyedJson
from app.gateway.ports import NotConfigured, Post, Posted, Rejected, Unavailable
from app.naive.client import API_VERSION, DEFAULT_API_BASE, _api_root

PLATFORMS = {"x": "x", "linkedin": "linkedin"}  # our post_social channels -> Naïve platforms


def _post_url(body: Any, identity: str) -> str | None:
    """The post's url or id from an answer the spec doesn't describe: flat, under
    `data`/`post`, or the first of `posts`. None when nothing usable is there."""
    candidates: list[Any] = [body]
    if isinstance(body, dict):
        candidates += [body.get("data"), body.get("post"),
                       (body.get("posts") or [None])[0] if isinstance(body.get("posts"), list)
                       else None]
    for item in candidates:
        if isinstance(item, str) and item:
            return f"naive://identities/{identity}/social/posts/{item}"
        if not isinstance(item, dict):
            continue
        for key in ("url", "post_url", "permalink"):
            if isinstance(item.get(key), str) and item[key]:
                return item[key]
        post_id = item.get("id") or item.get("post_id")
        if isinstance(post_id, (str, int)) and str(post_id):
            return f"naive://identities/{identity}/social/posts/{post_id}"
    return None


class NaiveSocial:
    provider = "naive"

    def __init__(self, api_key: str, *, identity: str, data_dir: Path,
                 api_base: str = DEFAULT_API_BASE, transport: httpx.BaseTransport | None = None,
                 timeout: httpx.Timeout | float = _http.TIMEOUT) -> None:
        if not api_key:
            raise NotConfigured("NAIVE_API_KEY is not set")
        if not identity:
            raise NotConfigured("NAIVE_SOCIAL_IDENTITY is not set (its id or name)")
        self.identity = identity
        self._client = httpx.Client(base_url=_api_root(api_base), transport=transport,
                                    timeout=timeout,
                                    headers={"Authorization": f"Bearer {api_key}",
                                             "Vetta-Version": API_VERSION,
                                             "Accept": "application/json",
                                             "User-Agent": "u-tax-gateway"})
        self._index = _KeyedJson(data_dir / "naive" / "social.json")

    def post(self, post: Post, key: str) -> Posted:
        platform = PLATFORMS.get(post.channel)
        if platform is None:
            raise Rejected(f"no Naïve platform for channel {post.channel!r}")
        # the gateway key plus what is posted: a changed text or link is a new post
        post_key = f"{key}|{_http.fingerprint(platform, post.text, post.image_url)}"
        known = self._index.get(post_key)
        if known:
            return Posted(url=known)
        body: dict[str, Any] = {"content": post.text, "platforms": [platform],
                                "ai_generated": True}
        if post.image_url:
            body["media_urls"] = [post.image_url]
        idem = _http.safe_key(post_key)
        try:
            response = _http.call(self.provider, lambda: self._client.post(
                f"/identities/{quote(self.identity, safe='')}/social/posts", json=body,
                headers={"Idempotency-Key": idem}))
        except Rejected as err:
            status = getattr(err, "status", 0)
            if status == 409:  # the same key still in flight (a timed-out first try)
                raise Unavailable(str(err)) from err
            if status in (403, 404):  # wrong identity, no social workspace, no scope
                raise NotConfigured(f"{err} (check NAIVE_SOCIAL_IDENTITY, its activated social "
                                    "workspace, and the key's sessions:write scope)") from err
            raise
        # any 2xx is a live post: record it, so a retry never posts it twice
        url = _post_url(_http.json_dict(response) or None, self.identity) \
            or f"naive://identities/{self.identity}/social/posts/{idem}"
        self._index.put(post_key, url)
        return Posted(url=url)
