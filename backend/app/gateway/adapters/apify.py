"""ScrapePort on Apify: the Delaware fee page fetched live (settles the $50 vs $100 annual
report fee), and a law search for Tax's `law_search`.

Both use Apify's "Run Actor synchronously and get dataset items" endpoint
(`POST /v2/actors/{actorId}/run-sync-get-dataset-items`, Bearer token; returns the dataset
items, 408 after 300 s; https://docs.apify.com/api/v2):
- fetch_page: `apify/cheerio-scraper`, one start URL, depth 0, a page function that returns the
  raw HTML and the URL it actually loaded. Only allowlisted URLs are fetched (an agent can name a
  page and every run costs money), and a run that redirected anywhere else is refused, so the
  gateway falls back to its saved snapshot.
- search: `apify/google-search-scraper`, one results page, the `site:` clause first, the
  agent's query stripped of search operators and capped by words, and every hit post-filtered
  to Delaware's own domains (Google doesn't strictly honour `site:`). Results are cached for
  6 h by normalized query, so Tax's near-identical queries don't start a paid run each time.

Spend bounds per run: `timeout`, `memory`, `maxItems` (binds pay-per-result actors) and
`maxTotalChargeUsd` (caps all pricing models, per the run-sync reference).
Input fields are from each actor's published input schema (default build), the organicResults
shape from its readme, read on 2026-11-07.

UNVERIFIED (no token tonight): the authenticated run-sync route and response (only an
unauthenticated 402 was seen); whether the fee page renders its fee table without JavaScript
(if not, switch to apify/web-scraper); the per-run cost (not reported back to the gateway yet).
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlsplit

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
LAW_DOMAIN = "delaware.gov"  # covers corp.delaware.gov, delcode.delaware.gov, ...
SEARCH_CACHE_SECONDS = 6 * 3600
MAX_QUERY_WORDS = 20  # Google ignores words past the 32nd; the site clause goes first anyway
PAGE_FUNCTION = (
    "async function pageFunction(context) {\n"
    "  const { request, body } = context;\n"
    "  return { url: request.loadedUrl || request.url, html: String(body) };\n"
    "}"
)
_OPERATORS = re.compile(
    r"(?i)\b(?:all)?(?:site|inurl|intitle|intext|inanchor|filetype|related|cache|before|after|define|info):\S*"
    r"|\bAROUND\(\d*\)|\bOR\b|\bAND\b|[\"'()|*~]|(?<!\w)-\w*")


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:  # an HTTP-date Retry-After: let the caller's backoff decide
        return None


def _map_error(response: httpx.Response) -> PortError:
    status = response.status_code
    try:
        err = response.json().get("error")
    except (ValueError, AttributeError):
        err = None
    if isinstance(err, dict):
        what = f"{err.get('type', '')}: {err.get('message', '')}".strip(": ")
    else:
        what = str(err or response.text)
    what = what[:200]
    if status == 429:
        return RateLimited(f"apify rate limited ({what})", retry_after=_retry_after(response))
    if status in (401, 403):
        return AuthExpired(f"apify token rejected ({what})")
    if status == 408 or status >= 500:
        return Unavailable(f"apify {status} ({what})", retry_after=_retry_after(response))
    return Rejected(f"apify {status} ({what})")  # 400 bad input, 402 out of credits, 404 no actor


def _keep(word: str) -> bool:
    """Drop anything that could be a client detail: emails, money, long or dashed numbers."""
    return not ("@" in word or "$" in word or re.search(r"\d{4,}|\d[-.,/]\d|\d-", word))


def clean_query(query: str) -> str:
    """The agent's words only: no search operators, no emails, amounts or ID-like numbers
    (section numbers like "503" stay), at most MAX_QUERY_WORDS words."""
    words = [w for w in _OPERATORS.sub(" ", query or "").split() if _keep(w)]
    return " ".join(words[:MAX_QUERY_WORDS])


def _is_law_host(url: str) -> bool:
    if "\\" in url or any(ch.isspace() for ch in url):  # browsers read "\" as "/": host confusion
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.username or parts.password or "@" in parts.netloc or port not in (None, 443):
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (host == LAW_DOMAIN or host.endswith("." + LAW_DOMAIN))


class ApifyScrape:
    provider = "apify"

    PAGE_TIMEOUT = 60  # seconds per run
    SEARCH_TIMEOUT = 30
    MEMORY_MB = 512
    MAX_CHARGE_USD = "0.10"

    def __init__(self, token: str, *, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if not token:
            raise NotConfigured("apify: no APIFY_TOKEN")
        self._clock = clock
        self._cache: dict[str, tuple[float, list[SearchHit]]] = {}
        # run-sync holds the connection for the whole run: allow it plus a margin
        self._http = httpx.Client(base_url=API, transport=transport, timeout=self.PAGE_TIMEOUT + 30,
                                  headers={"Authorization": f"Bearer {token}"})

    def __repr__(self) -> str:  # never show the token
        return "ApifyScrape()"

    def close(self) -> None:
        self._http.close()

    def _run(self, actor: str, run_input: dict, *, timeout: int, max_items: int) -> list[dict]:
        params = {"timeout": timeout, "memory": self.MEMORY_MB, "maxItems": max_items,
                  "maxTotalChargeUsd": self.MAX_CHARGE_USD, "format": "json", "clean": "true"}
        try:
            response = self._http.post(f"/actors/{actor}/run-sync-get-dataset-items", params=params,
                                       json=run_input, timeout=timeout + 30)
        except httpx.TimeoutException as exc:
            raise Unavailable(f"apify run timed out: {exc}") from exc
        except httpx.HTTPError as exc:  # transport, decoding, redirects, bad URL
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
        if url not in ALLOWED_PAGES:
            raise Rejected(f"apify: {url} is not an allowlisted page")
        items = self._run(PAGE_ACTOR, {
            "startUrls": [{"url": url}],
            "pageFunction": PAGE_FUNCTION,
            "proxyConfiguration": {"useApifyProxy": True},
            "maxPagesPerCrawl": 1,
            "maxCrawlingDepth": 0,
            "linkSelector": "",
            "maxRequestRetries": 2,
        }, timeout=self.PAGE_TIMEOUT, max_items=1)
        item = next((i for i in items if isinstance(i.get("html"), str) and i["html"].strip()), None)
        if item is None:
            raise Unavailable(f"apify fetched no HTML for {url}")
        loaded = str(item.get("url") or url)
        if loaded.rstrip("/") not in {u.rstrip("/") for u in ALLOWED_PAGES}:
            raise Unavailable(f"apify: {url} redirected to {loaded[:120]}; not the allowlisted page")
        return Page(url=url, html=item["html"], fetched_at=iso_ts(datetime.now(timezone.utc)), source="apify")

    def search(self, query: str, limit: int = 3) -> list[SearchHit]:
        words = clean_query(query)
        if not words:
            raise Rejected("apify: empty search query")
        limit = max(1, min(int(limit), 10))
        key = words.lower()
        cached = self._cache.get(key)
        if cached and self._clock() - cached[0] < SEARCH_CACHE_SECONDS:
            return cached[1][:limit]
        items = self._run(SEARCH_ACTOR, {
            "queries": f"site:{LAW_DOMAIN} {words}",
            "maxPagesPerQuery": 1,
            "countryCode": "us",
            "languageCode": "en",
            "mobileResults": False,
            "saveHtml": False,
            "saveHtmlToKeyValueStore": False,
        }, timeout=self.SEARCH_TIMEOUT, max_items=1)
        hits: list[SearchHit] = []
        for item in items:
            results = item.get("organicResults")
            for result in results if isinstance(results, list) else []:
                if not isinstance(result, dict):
                    continue
                title, link, text = result.get("title"), result.get("url"), result.get("description")
                if isinstance(title, str) and isinstance(link, str) and isinstance(text, str) and title and text \
                        and _is_law_host(link):
                    hits.append(SearchHit(cite=f"{title[:120]} · {link}", snippet=text[:300]))
        if hits:  # never pin an empty or failed answer
            self._cache[key] = (self._clock(), hits)
        return hits[:limit]
