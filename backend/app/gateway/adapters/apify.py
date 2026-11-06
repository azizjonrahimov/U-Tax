"""ScrapePort on Apify: the Delaware fee page fetched live (settles the $50 vs $100 annual
report fee), and a law search for Tax's `law_search`.

Both use Apify's "Run Actor synchronously and get dataset items" endpoint
(`POST /v2/actors/{actorId}/run-sync-get-dataset-items`, Bearer token; returns the dataset
items, 408 after 300 s; https://docs.apify.com/api/v2):
- fetch_page: `apify/cheerio-scraper`, one start URL, depth 0, a page function that returns the
  raw HTML. Only allowlisted URLs are fetched: the URL comes from the gateway, but an agent can
  name a page and every run costs money, so anything else is Rejected before a call is made.
- search: `apify/google-search-scraper`, one results page, `site:` limited to Delaware's own
  domains, mapped to SearchHit(cite=title · url, snippet=description).

Every run carries a `timeout` and a `maxTotalChargeUsd` cap. Input fields are from each actor's
published input schema (default build) and the organicResults shape from its readme, read on
2026-11-07.

UNVERIFIED (no token tonight): the run-sync response with a real token; whether the fee page
renders its fee table without JavaScript (if not, switch the actor to apify/web-scraper).
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.gateway.adapters.local import DELAWARE_FEE_URL
from app.gateway.events import iso_ts
from app.gateway.ports import (
    AuthExpired,
    NotConfigured,
    Page,
    PortError,
    RateLimited,
    Rejected,
    SearchHit,
    Unavailable,
)

API = "https://api.apify.com/v2"
PAGE_ACTOR = "apify~cheerio-scraper"
SEARCH_ACTOR = "apify~google-search-scraper"
ALLOWED_PAGES = frozenset({DELAWARE_FEE_URL})
LAW_SITES = ("delaware.gov", "delcode.delaware.gov")
PAGE_FUNCTION = (
    "async function pageFunction(context) {\n"
    "  const { request, body } = context;\n"
    "  return { url: request.loadedUrl || request.url, html: String(body) };\n"
    "}"
)


def _map_error(response: httpx.Response) -> PortError:
    retry = response.headers.get("retry-after")
    try:
        retry_after = float(retry) if retry else None
    except ValueError:
        retry_after = None
    status = response.status_code
    try:
        err = (response.json() or {}).get("error") or {}
    except (ValueError, AttributeError):
        err = {}
    what = f"{err.get('type', '')}: {err.get('message', '')}".strip(": ")[:200] or response.text[:200]
    if status == 429:
        return RateLimited(f"apify rate limited ({what})", retry_after=retry_after)
    if status in (401, 403):
        return AuthExpired(f"apify token rejected ({what})")
    if status == 408 or status >= 500:
        return Unavailable(f"apify {status} ({what})", retry_after=retry_after)
    return Rejected(f"apify {status} ({what})")  # 400 bad input, 402 out of credits, 404 no actor


class ApifyScrape:
    provider = "apify"

    def __init__(self, token: str, *, allowed_pages: frozenset[str] = ALLOWED_PAGES, run_timeout: int = 60,
                 max_charge_usd: str = "0.10", transport: httpx.BaseTransport | None = None) -> None:
        if not token:
            raise NotConfigured("apify: no APIFY_TOKEN")
        self.allowed_pages = allowed_pages
        self.run_timeout = run_timeout
        self.max_charge_usd = max_charge_usd
        # run-sync holds the connection for the whole run: allow it plus a margin
        self._http = httpx.Client(base_url=API, transport=transport, timeout=run_timeout + 30,
                                  headers={"Authorization": f"Bearer {token}"})

    def __repr__(self) -> str:  # never show the token
        return "ApifyScrape()"

    def _run(self, actor: str, run_input: dict) -> list[dict]:
        params = {"timeout": self.run_timeout, "maxTotalChargeUsd": self.max_charge_usd, "format": "json", "clean": "true"}
        try:
            response = self._http.post(f"/actors/{actor}/run-sync-get-dataset-items", params=params, json=run_input)
        except httpx.TimeoutException as exc:
            raise Unavailable(f"apify run timed out: {exc}") from exc
        except httpx.TransportError as exc:
            raise Unavailable(f"apify unreachable: {exc}") from exc
        if not response.is_success:
            raise _map_error(response)
        try:
            items = response.json()
        except ValueError as exc:
            raise Rejected("apify returned no JSON") from exc
        if not isinstance(items, list):
            raise Rejected("apify returned an unexpected shape")
        return [item for item in items if isinstance(item, dict)]

    def fetch_page(self, url: str) -> Page:
        if url not in self.allowed_pages:
            raise Rejected(f"apify: {url} is not an allowlisted page")
        items = self._run(PAGE_ACTOR, {
            "startUrls": [{"url": url}],
            "pageFunction": PAGE_FUNCTION,
            "proxyConfiguration": {"useApifyProxy": True},
            "maxPagesPerCrawl": 1,
            "maxCrawlingDepth": 0,
            "linkSelector": "",
            "maxRequestRetries": 2,
        })
        html = next((str(i.get("html")) for i in items if i.get("html")), "")
        if not html.strip():
            raise Unavailable(f"apify fetched no HTML for {url}")
        return Page(url=url, html=html, fetched_at=iso_ts(datetime.now(timezone.utc)), source="apify")

    def search(self, query: str, limit: int = 3) -> list[SearchHit]:
        query = " ".join(query.split())[:200]
        if not query:
            raise Rejected("apify: empty search query")
        limit = max(1, min(int(limit), 10))
        sites = " OR ".join(f"site:{s}" for s in LAW_SITES)
        items = self._run(SEARCH_ACTOR, {
            "queries": f"{query} ({sites})",
            "maxPagesPerQuery": 1,
            "countryCode": "us",
            "languageCode": "en",
            "mobileResults": False,
            "saveHtml": False,
            "saveHtmlToKeyValueStore": False,
        })
        hits: list[SearchHit] = []
        for item in items:
            for result in item.get("organicResults") or []:
                title, link, text = result.get("title"), result.get("url"), result.get("description")
                if title and link and text:
                    hits.append(SearchHit(cite=f"{title} · {link}"[:200], snippet=str(text)[:300]))
        return hits[:limit]
