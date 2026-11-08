"""Task 40c: ADAPTER_SCRAPE=apify boots, and preflight has an Apify line."""

from __future__ import annotations

import httpx

from app.gateway.adapters.apify import ApifyScrape
from app.gateway.registry import build_ports
from app.gateway.settings import Settings
from app.preflight import check_apify

TOKEN = "apify_api_" + "t" * 20


def world(usage=(2.5, 5.0), actor_status=200):
    seen = []

    def respond(request):
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        path = request.url.path
        if path == "/v2/users/me":
            return httpx.Response(200, json={"data": {"username": "utax"}})
        if path.startswith("/v2/acts/"):
            return httpx.Response(actor_status, json={"data": {"id": "x"}})
        if path == "/v2/users/me/limits":
            return httpx.Response(200, json={"data": {
                "current": {"monthlyUsageUsd": usage[0]},
                "limits": {"maxMonthlyUsageUsd": usage[1]}}})
        return httpx.Response(404, json={})

    return respond, seen


def settings(tmp_path, **kw):
    return Settings(data_dir=tmp_path, **{"adapters": {"scrape": "apify"},
                                          "apify_token": TOKEN, **kw})


def test_apify_boots(tmp_path):
    assert isinstance(build_ports(settings(tmp_path)).scrape, ApifyScrape)


def test_no_line_unless_apify_is_switched_on(tmp_path):
    respond, seen = world()
    assert check_apify(Settings(data_dir=tmp_path), httpx.MockTransport(respond)) == []
    assert seen == []


def test_green_reads_only_and_shows_the_credit_left(tmp_path):
    respond, seen = world()
    [check] = check_apify(settings(tmp_path), httpx.MockTransport(respond))
    assert check.ok and "$2.50 of $5.00 left" in check.reason
    assert TOKEN not in check.line()
    assert {m for m, _, _ in seen} == {"GET"}
    assert ("GET", "/v2/acts/apify~cheerio-scraper", f"Bearer {TOKEN}") in seen
    assert ("GET", "/v2/acts/apify~google-search-scraper", f"Bearer {TOKEN}") in seen


def test_red_on_little_credit_a_missing_actor_or_no_token(tmp_path):
    respond, _ = world(usage=(4.5, 5.0))
    [check] = check_apify(settings(tmp_path), httpx.MockTransport(respond))
    assert not check.ok and "under $1.00" in check.reason
    respond, _ = world(actor_status=404)
    [check] = check_apify(settings(tmp_path), httpx.MockTransport(respond))
    assert not check.ok and "answered 404" in check.reason
    [check] = check_apify(settings(tmp_path, apify_token=""), httpx.MockTransport(respond))
    assert not check.ok and "APIFY_TOKEN" in check.reason
