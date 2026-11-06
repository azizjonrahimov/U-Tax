"""ApifyScrape (ScrapePort) against a mocked Apify API: no live calls."""

from __future__ import annotations

import json

import httpx
import pytest

from app.gateway.adapters.apify import PAGE_ACTOR, SEARCH_ACTOR, ApifyScrape
from app.gateway.adapters.local import DELAWARE_FEE_URL
from app.gateway.ports import AuthExpired, FaultPlan, NotConfigured, PortError, RateLimited, Rejected, Unavailable

TOKEN = "apify_api_test_0123456789"
FEE_HTML = "<html><body><h2>Annual Report Filing Fee</h2><p>Annual Report filing fee $50.00</p></body></html>"
SEARCH_ITEMS = [{
    "searchQuery": {"term": "x"},
    "organicResults": [
        {"title": "Title 8 § 503", "url": "https://delcode.delaware.gov/title8/c005/", "description": "assumed par value capital method · min $400"},
        {"title": "Franchise Taxes", "url": "https://corp.delaware.gov/frtax/", "description": "Two methods to calculate franchise tax"},
        {"title": "No description", "url": "https://corp.delaware.gov/x/"},
    ],
}]


def handler_for(seen: list[httpx.Request], page_items=None, search_items=None):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == f"/v2/actors/{PAGE_ACTOR}/run-sync-get-dataset-items":
            items = page_items if page_items is not None else [{"url": DELAWARE_FEE_URL, "html": FEE_HTML}]
            return httpx.Response(201, json=items)
        if request.url.path == f"/v2/actors/{SEARCH_ACTOR}/run-sync-get-dataset-items":
            return httpx.Response(201, json=search_items if search_items is not None else SEARCH_ITEMS)
        return httpx.Response(404, json={"error": {"type": "record-not-found", "message": "no"}})

    return handler


def make(handler) -> ApifyScrape:
    return ApifyScrape(TOKEN, transport=httpx.MockTransport(handler))


def test_fetch_fee_page_returns_live_html():
    seen: list[httpx.Request] = []
    page = make(handler_for(seen)).fetch_page(DELAWARE_FEE_URL)
    assert page.source == "apify" and page.url == DELAWARE_FEE_URL and "$50.00" in page.html
    assert page.fetched_at.endswith("Z")
    request = seen[0]
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.url.params["maxTotalChargeUsd"] == "0.10" and request.url.params["timeout"] == "60"
    body = json.loads(request.content)
    assert body["startUrls"] == [{"url": DELAWARE_FEE_URL}]
    assert body["maxPagesPerCrawl"] == 1 and body["maxCrawlingDepth"] == 0
    assert "pageFunction" in body and body["proxyConfiguration"] == {"useApifyProxy": True}


def test_fee_page_parses_like_the_snapshot():
    from app.gateway.perform import parse_fee_cents

    page = make(handler_for([])).fetch_page(DELAWARE_FEE_URL)
    assert parse_fee_cents(page.html) == 5000


def test_only_allowlisted_pages_are_fetched_and_nothing_is_called():
    seen: list[httpx.Request] = []
    with pytest.raises(Rejected, match="not an allowlisted page"):
        make(handler_for(seen)).fetch_page("https://example.invalid/nothing")
    assert seen == []


def test_empty_html_is_unavailable():
    with pytest.raises(Unavailable, match="no HTML"):
        make(handler_for([], page_items=[{"url": DELAWARE_FEE_URL, "html": "   "}])).fetch_page(DELAWARE_FEE_URL)


def test_search_maps_organic_results_and_limits_sites():
    seen: list[httpx.Request] = []
    hits = make(handler_for(seen)).search("delaware franchise tax   assumed par value method", limit=3)
    assert [h.cite for h in hits] == ["Title 8 § 503 · https://delcode.delaware.gov/title8/c005/",
                                      "Franchise Taxes · https://corp.delaware.gov/frtax/"]
    assert all(h.cite and h.snippet for h in hits)
    body = json.loads(seen[0].content)
    assert body["queries"].startswith("delaware franchise tax assumed par value method (")
    assert "site:delaware.gov" in body["queries"] and body["maxPagesPerQuery"] == 1


def test_search_respects_limit_and_refuses_empty_query():
    assert len(make(handler_for([])).search("franchise tax", limit=1)) == 1
    with pytest.raises(Rejected):
        make(handler_for([])).search("   ")


def test_429_maps_to_rate_limited():
    scrape = make(lambda r: httpx.Response(429, headers={"retry-after": "3"},
                                           json={"error": {"type": "rate-limit-exceeded", "message": "slow"}}))
    with pytest.raises(RateLimited) as err:
        scrape.search("x")
    assert err.value.retry_after == 3.0


@pytest.mark.parametrize("status,kind,error", [
    (401, "invalid-token", AuthExpired), (403, "insufficient-permissions", AuthExpired),
    (402, "not-enough-usage-to-run-paid-actor", Rejected), (400, "invalid-input", Rejected),
    (404, "record-not-found", Rejected), (408, "run-timeout-exceeded", Unavailable), (502, "x", Unavailable),
])
def test_errors_map(status, kind, error):
    scrape = make(lambda r: httpx.Response(status, json={"error": {"type": kind, "message": "m"}}))
    with pytest.raises(error):
        scrape.fetch_page(DELAWARE_FEE_URL)


def test_client_timeout_and_transport_errors_are_unavailable():
    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    def down(request):
        raise httpx.ConnectError("down", request=request)

    for handler in (slow, down):
        with pytest.raises(Unavailable):
            make(handler).search("x")


def test_unexpected_body_is_rejected():
    with pytest.raises(Rejected):
        make(lambda r: httpx.Response(201, json={"not": "a list"})).search("x")


def test_no_token_is_not_configured_and_repr_hides_token():
    with pytest.raises(NotConfigured):
        ApifyScrape("")
    assert TOKEN not in repr(ApifyScrape(TOKEN))


def contract_factory(tmp_path, faults: FaultPlan) -> ApifyScrape:
    """How tests/contract/test_ports.py SCRAPE can list this adapter: FaultPlan errors become
    the matching HTTP answers."""
    inner = handler_for([])

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            faults.check()
        except RateLimited:
            return httpx.Response(429, json={"error": {"type": "rate-limit-exceeded", "message": "x"}})
        except AuthExpired:
            return httpx.Response(401, json={"error": {"type": "invalid-token", "message": "x"}})
        except PortError:
            return httpx.Response(503, json={"error": {"type": "x", "message": "x"}})
        return inner(request)

    return make(handler)


def test_passes_the_port_contract_scrape_check(tmp_path):
    from app.gateway.perform import parse_fee_cents

    faults = FaultPlan()
    port = contract_factory(tmp_path, faults)
    page = port.fetch_page(DELAWARE_FEE_URL)
    assert page.source in ("snapshot", "apify")
    assert parse_fee_cents(page.html) == 5000 or page.source == "apify"
    hits = port.search("delaware franchise tax assumed par value method")
    assert hits and all(h.cite and h.snippet for h in hits)
    with pytest.raises(PortError):
        port.fetch_page("https://example.invalid/nothing")
    faults.add(Unavailable())
    with pytest.raises(Unavailable):
        port.search("anything")
