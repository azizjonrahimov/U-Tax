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
from app.gateway.ports import NotConfigured, Post, Posted, Rejected
from app.naive.client import DEFAULT_API_BASE, _api_root

PLATFORMS = {"x": "x", "linkedin": "linkedin"}  # our post_social channels -> Naïve platforms


def _post_url(body: Any, identity: str) -> str | None:
    if not isinstance(body, dict):
        return None
    for key in ("url", "post_url", "permalink"):
        if isinstance(body.get(key), str) and body[key]:
            return body[key]
    post_id = body.get("id") or body.get("post_id") or (body.get("post") or {}).get("id")
    return f"naive://identities/{identity}/social/posts/{post_id}" if post_id else None


class NaiveSocial:
    provider = "naive"

    def __init__(self, api_key: str, *, identity: str, data_dir: Path,
                 api_base: str = DEFAULT_API_BASE, transport: httpx.BaseTransport | None = None,
                 timeout: float = 15.0) -> None:
        if not api_key:
            raise NotConfigured("NAIVE_API_KEY is not set")
        if not identity:
            raise NotConfigured("NAIVE_SOCIAL_IDENTITY is not set (idn_...)")
        self.identity = identity
        self._client = httpx.Client(base_url=_api_root(api_base), transport=transport,
                                    timeout=timeout,
                                    headers={"Authorization": f"Bearer {api_key}",
                                             "User-Agent": "u-tax-gateway"})
        self._index = _KeyedJson(data_dir / "naive" / "social.json")

    def post(self, post: Post, key: str) -> Posted:
        known = self._index.get(key)
        if known:
            return Posted(url=known)
        platform = PLATFORMS.get(post.channel)
        if platform is None:
            raise Rejected(f"no Naïve platform for channel {post.channel!r}")
        body: dict[str, Any] = {"content": post.text, "platforms": [platform]}
        if post.image_url:
            body["media_urls"] = [post.image_url]
        response = _http.call(self.provider, lambda: self._client.post(
            f"/identities/{quote(self.identity, safe='')}/social/posts", json=body,
            headers={"Idempotency-Key": _http.safe_key(key)}))
        try:
            data = response.json()
        except ValueError:
            data = None
        url = _post_url(data, self.identity)
        if not url:
            raise Rejected("Naïve answered without a post id")
        self._index.put(key, url)
        return Posted(url=url)
