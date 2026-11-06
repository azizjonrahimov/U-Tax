"""ToolBuyPort on Monid: agents buy the data calls they need (enrichment, search) from one
prepaid balance, and each purchase is booked on the money panel ("agents pay their own vendors").

API (https://monid.ai/docs/api-reference, read on 2026-11-07): `POST /v1/run`
`{provider, endpoint, input: {body, queryParams, pathParams}}`, Bearer key.
- 200: COMPLETED (output + `billing.reportedCost`) or BLOCKED (a workspace control: budget, run
  cap or balance). 202: async accepted, poll `GET /v1/runs/{runId}`.
- A COMPLETED run whose provider failed comes back with the provider's status (an upstream 402
  as 502), unbilled. 402 from Monid itself is our wallet. 408 TIMED_OUT is terminal and unbilled.
  429 carries Retry-After. Errors are `{code, message}`.

Spend safety, because every run costs money and an agent picks the tool:
- only tools in the allowlist (MONID_TOOLS, JSON: {"enrichment": {"provider": "...",
  "endpoint": "..."}}) can be bought; anything else is Rejected before a call. Empty by default:
  nothing is bought until the owner names the tools;
- a daily cap across all purchases (MONID_DAILY_CAP_USD, default 1.00), checked before each run
  against what this process has booked today; also set a run cap control in the Monid dashboard;
- `/v1/run` takes no idempotency key, so a purchase is remembered by our key (a repeat returns
  the first Purchase, no second charge), and a key whose run timed out on our side is refused
  until someone checks `GET /v1/runs` (it may have been charged).

`cost_usd` is the run's `billing.reportedCost` converted in code (MICRO_DOLLAR / CENT / DOLLAR)
to a decimal string of USD; "0.0000" for unbilled runs.

UNVERIFIED (no key tonight): shapes are from the published OpenAPI reference; whether a COMPLETED
200 always carries `billing`; the provider/endpoint names for our tools (choose with
`POST /v1/discover` on Saturday).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.gateway.ports import (
    AuthExpired,
    NotConfigured,
    PortError,
    Purchase,
    RateLimited,
    Rejected,
    Unavailable,
)

API = "https://api.monid.ai"
UNITS = {"MICRO_DOLLAR": Decimal("0.000001"), "CENT": Decimal("0.01"), "DOLLAR": Decimal("1")}
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _map_error(response: httpx.Response) -> PortError:
    status, body = response.status_code, _body(response)
    message = str(body.get("message") or response.text)[:200]
    if status == 429:
        return RateLimited(f"monid rate limited: {message}", retry_after=_retry_after(response))
    if status in (401, 403):
        return AuthExpired(f"monid key rejected: {message}")
    if status == 402:  # always our wallet (a provider's own 402 arrives as 502)
        return Rejected(f"monid balance too low: {message}")
    if status in (408, 504) or status >= 500:  # timed out (unbilled) / upstream failure
        return Unavailable(f"monid {status}: {message}", retry_after=_retry_after(response))
    return Rejected(f"monid {status}: {message}")  # 400 input, 404 endpoint, provider 4xx (unbilled)


def cost_usd(run: dict) -> str:
    """billing.reportedCost → decimal string of USD (4+ places). Unbilled runs are "0.0000"."""
    reported = (run.get("billing") or {}).get("reportedCost") if isinstance(run.get("billing"), dict) else None
    if not isinstance(reported, dict):
        return "0.0000"
    try:
        value = Decimal(int(reported["value"])) * UNITS[str(reported["unit"])]
    except (KeyError, ValueError, TypeError, InvalidOperation) as exc:
        raise Unavailable(f"monid: unreadable cost {reported!r}") from exc
    if value < 0:
        raise Unavailable("monid: negative cost")
    four = value.quantize(Decimal("0.0001"))
    return format(four if four == value else value.normalize(), "f")  # never "1E-6"


class MonidToolBuy:
    provider = "monid"

    def __init__(self, api_key: str, *, tools: dict[str, dict[str, str]] | None = None,
                 daily_cap_usd: str | None = None, max_wait: float = 20.0, poll_every: float = 2.0,
                 transport: httpx.BaseTransport | None = None, timeout: float = 30.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], str] = lambda: datetime.now(timezone.utc).date().isoformat()) -> None:
        if not api_key:
            raise NotConfigured("monid: no MONID_API_KEY")
        if tools is None:
            raw = os.environ.get("MONID_TOOLS", "").strip()
            try:
                tools = json.loads(raw) if raw else {}
            except ValueError as exc:
                raise NotConfigured("monid: MONID_TOOLS is not JSON") from exc
        self.tools = {name: spec for name, spec in tools.items()
                      if isinstance(spec, dict) and spec.get("provider") and spec.get("endpoint")}
        self.daily_cap = Decimal(daily_cap_usd or os.environ.get("MONID_DAILY_CAP_USD", "1.00"))
        self.max_wait, self.poll_every = max_wait, poll_every
        self._sleep, self._clock, self._today = sleep, clock, today
        self._bought: dict[str, Purchase] = {}  # our key -> the first purchase
        self._unsure: set[str] = set()  # keys whose run may have been charged
        self._spent: dict[str, Decimal] = {}  # day -> booked USD
        self._http = httpx.Client(base_url=API, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def __repr__(self) -> str:  # never show the key
        return "MonidToolBuy()"

    def close(self) -> None:
        self._http.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return self._http.request(method, path, **kwargs)
        except httpx.TimeoutException:
            raise
        except (httpx.HTTPError, httpx.InvalidURL) as exc:
            raise Unavailable(f"monid unreachable: {exc}") from exc

    def buy(self, tool: str, input: dict[str, Any], key: str) -> Purchase:
        if key in self._bought:
            return self._bought[key]
        if key in self._unsure:
            raise Rejected(f"monid: purchase {key} may already be charged; check GET /v1/runs first")
        spec = self.tools.get(tool)
        if spec is None:
            raise Rejected(f"monid: '{tool}' is not an allowlisted tool (MONID_TOOLS)")
        day = self._today()
        if self._spent.get(day, Decimal(0)) >= self.daily_cap:
            raise Rejected(f"monid: today's cap of ${self.daily_cap} is spent")
        payload = {"provider": spec["provider"], "endpoint": spec["endpoint"],
                   "input": input if set(input) <= {"body", "queryParams", "pathParams"} and input else {"body": input}}
        try:
            response = self._request("POST", "/v1/run", json=payload)
        except httpx.TimeoutException as exc:
            self._unsure.add(key)
            raise Unavailable(f"monid run timed out (it may still be charged): {exc}") from exc
        run = self._settle(response, key)
        purchase = Purchase(result=run.get("output"), cost_usd=cost_usd(run), bought=True)
        self._spent[day] = self._spent.get(day, Decimal(0)) + Decimal(purchase.cost_usd)
        self._bought[key] = purchase
        return purchase

    def _settle(self, response: httpx.Response, key: str) -> dict:
        if response.status_code == 202:
            return self._poll(_body(response).get("runId"), key)
        if not response.is_success:
            raise _map_error(response)
        run = _body(response)
        return self._terminal(run)

    def _terminal(self, run: dict) -> dict:
        status = run.get("status")
        if status == "COMPLETED":
            return run
        if status == "BLOCKED":
            raise Rejected("monid: blocked by a workspace control (budget, run cap or balance)")
        if status in ("TIMED_OUT", "FAILED", "STOPPED"):
            raise Unavailable(f"monid run ended {status}")
        raise Unavailable(f"monid: unexpected run status {status!r}")

    def _poll(self, run_id: object, key: str) -> dict:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            self._unsure.add(key)
            raise Unavailable("monid: async run without a usable runId")
        deadline = self._clock() + self.max_wait
        while True:
            try:
                response = self._request("GET", f"/v1/runs/{run_id}")
            except httpx.TimeoutException as exc:
                self._unsure.add(key)
                raise Unavailable(f"monid poll timed out: {exc}") from exc
            if not response.is_success:
                self._unsure.add(key)
                raise _map_error(response)
            run = _body(response)
            if run.get("status") not in ("READY", "RUNNING", "STOPPING"):
                return self._terminal(run)
            if self._clock() >= deadline:
                self._unsure.add(key)  # still running: it may finish and be charged
                raise Unavailable(f"monid run {run_id} still running after {self.max_wait:.0f}s")
            self._sleep(self.poll_every)
