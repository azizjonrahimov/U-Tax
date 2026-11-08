"""Naïve social adapter (task 8), built from the Naïve OpenAPI spec (2026-09-30)."""

from __future__ import annotations

import json
import os
from dataclasses import replace

import httpx
import pytest

from app.gateway.adapters import _http
from app.gateway.adapters.naive_social import NaiveSocial
from app.gateway.ports import (ChannelFailed, NotConfigured, Post, RateLimited, Rejected,
                               Unavailable)

from .fakes import FakeNaive


def social(tmp_path, fake=None, **kw):
    fake = fake or FakeNaive()
    return NaiveSocial("sk_test", identity=fake.identity, data_dir=tmp_path,
                       transport=fake.transport(), **kw), fake


def test_post_uses_the_documented_route_and_body(tmp_path):
    port, fake = social(tmp_path)
    posted = port.post(Post(channel="linkedin", text="We check Delaware franchise tax.",
                            image_url="https://cdn.u-tax.test/card.png"), key="post:abc")
    request = fake.requests[-1]
    assert request.url.path == f"/v1/identities/{fake.identity}/social/posts"
    assert request.headers["authorization"] == "Bearer sk_test"
    assert request.headers["idempotency-key"].startswith("utax-")
    assert request.headers["vetta-version"] == "2026-09-30"
    assert json.loads(request.content) == {"content": "We check Delaware franchise tax.",
                                           "platforms": ["linkedin"], "ai_generated": True,
                                           "media_urls": ["https://cdn.u-tax.test/card.png"]}
    assert posted.url == f"naive://identities/{fake.identity}/social/posts/pst_1"


def test_a_retry_with_the_same_key_posts_once(tmp_path):
    fake = FakeNaive()
    first, _ = social(tmp_path, fake)
    a = first.post(Post(channel="x", text="hi"), key="k")
    again, _ = social(tmp_path / "fresh", fake)  # lost answer, no local index
    assert again.post(Post(channel="x", text="hi"), key="k") == a and len(fake.posts) == 1


def test_the_answer_url_wins_when_naive_gives_one(tmp_path):
    transport = httpx.MockTransport(lambda r: httpx.Response(
        200, json={"id": "pst_9", "url": "https://x.com/utax/status/9"}))
    port = NaiveSocial("sk_test", identity="idn_x", data_dir=tmp_path, transport=transport)
    assert port.post(Post(channel="x", text="hi"), key="k").url == "https://x.com/utax/status/9"


@pytest.mark.parametrize("status,error", [(429, RateLimited), (503, Unavailable),
                                          (401, NotConfigured), (400, Rejected),
                                          (422, Rejected)])
def test_answers_map_onto_port_errors(tmp_path, status, error):
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json={"error": {}}))
    port = NaiveSocial("sk_test", identity="idn_x", data_dir=tmp_path, transport=transport)
    with pytest.raises(error):
        port.post(Post(channel="x", text="hi"), key="k")


def test_unknown_channel_and_missing_config(tmp_path):
    port, fake = social(tmp_path)
    with pytest.raises(Rejected):
        port.post(Post(channel="tiktok", text="hi"), key="k")
    assert fake.requests == []
    with pytest.raises(NotConfigured):
        NaiveSocial("", identity="idn_x", data_dir=tmp_path)
    with pytest.raises(NotConfigured):
        NaiveSocial("sk_test", identity="", data_dir=tmp_path)


@pytest.mark.parametrize("answer", [{"status": "ok"}, {"data": {"id": "p1"}},
                                    {"posts": [{"id": "p2"}]}, {"post": "p3"}, None, [1]])
def test_any_2xx_is_a_live_post_and_a_retry_never_reposts(tmp_path, answer):
    # review: a 2xx without a readable id used to be recorded as failed, then re-posted
    calls = []

    def handle(request):
        calls.append(request)
        if answer is None:
            return httpx.Response(202)
        return httpx.Response(201, json=answer)

    port = NaiveSocial("sk_test", identity="idn_x", data_dir=tmp_path,
                       transport=httpx.MockTransport(handle))
    first = port.post(Post(channel="x", text="hi"), key="k")
    assert first.url.startswith("naive://identities/idn_x/social/posts/")
    assert port.post(Post(channel="x", text="hi"), key="k") == first and len(calls) == 1


def test_409_in_flight_is_final_and_404_is_configuration(tmp_path):
    # task 50: a 409 "still in flight" never clears on a retry with the same key
    for status, error in ((409, ChannelFailed), (404, NotConfigured), (403, NotConfigured)):
        transport = httpx.MockTransport(lambda r, s=status: httpx.Response(s, json={}))
        port = NaiveSocial("sk_test", identity="utax-mkt", data_dir=tmp_path / str(status),
                           transport=transport)
        with pytest.raises(error):
            port.post(Post(channel="x", text="hi"), key="k")


def test_registry_builds_it_only_with_key_and_identity(tmp_path):
    from app.gateway.registry import AdapterNotReady, build_port
    from app.gateway.settings import Settings

    base = replace(Settings(), data_dir=tmp_path, adapters={"social": "naive"})
    with pytest.raises(AdapterNotReady, match="NAIVE_API_KEY"):
        build_port("social", base)
    with pytest.raises(AdapterNotReady, match="NAIVE_SOCIAL_IDENTITY"):
        build_port("social", replace(base, naive_api_key="sk_x"))
    port = build_port("social", replace(base, naive_api_key="sk_x",
                                        naive_social_identity="idn_x"))
    assert port.provider == "naive"


@pytest.mark.skipif(not (os.environ.get("NAIVE_API_KEY") and os.environ.get("NAIVE_SOCIAL_IDENTITY")
                         and os.environ.get("NAIVE_SOCIAL_TEST_POST") == "yes"),
                    reason="posts publicly: needs NAIVE_API_KEY, NAIVE_SOCIAL_IDENTITY and "
                           "NAIVE_SOCIAL_TEST_POST=yes")
def test_live_post_once(tmp_path):
    port = NaiveSocial(os.environ["NAIVE_API_KEY"], identity=os.environ["NAIVE_SOCIAL_IDENTITY"],
                       data_dir=tmp_path)
    assert port.post(Post(channel="x", text="U-Tax adapter check."), key="live-1").url
