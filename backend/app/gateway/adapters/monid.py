"""ToolBuyPort on Monid: agents buy the data calls they need (enrichment, search) from one
prepaid balance, and each purchase is booked on the money panel ("agents pay their own vendors").

API (https://monid.ai/docs/api-reference, read on 2026-11-07): `POST /v1/run`
`{provider, endpoint, input: {body}}`, Bearer key. No idempotency key, no max-cost field.
- 200: COMPLETED (output + `billing.reportedCost`) or BLOCKED (a workspace control gate; not
  charged). 202: async accepted, poll `GET /v1/runs/{runId}`.
- A COMPLETED run whose provider failed comes back with the provider's status (an upstream 402
  as 502). 402 from Monid itself is our wallet. 408 TIMED_OUT is terminal and zero-billed.
  429 carries Retry-After. Errors are `{code, message}`.

Spend safety, because every run costs money and an agent picks the tool:
- only allowlisted tools can be bought (MONID_TOOLS, JSON: {"enrichment": {"provider": "exa",
  "endpoint": "search", "max_usd": "0.05", "fields": ["query"]}}); empty by default, so nothing
  is bought until the owner names the tools. `max_usd` (required) is that tool's price ceiling;
  `fields` (optional) limits which input keys the agent may send;
- the agent's input goes only in `body`, and is refused if it carries an email address;
- a daily cap (MONID_DAILY_CAP_USD, default 1.00): before each run, today's booked spend plus
  every in-flight or unsure ceiling plus this tool's ceiling must fit. Also set a run cap control in the
  Monid dashboard;
- a purchase is remembered by our key (a repeat returns the first Purchase; the same key with a
  different tool or input is Rejected). If a failure leaves it unclear whether Monid charged (a
  timeout or a dropped connection after sending, a 5xx, an unreadable reply, a run that never
  finished, anything unexpected after sending), the key is marked unsure: it is refused and its
  ceiling stays reserved against the cap until `resolve(key, cost_usd, result)` records what
  `GET /v1/runs` shows (a charged run is then remembered, so the key never pays twice);
- a COMPLETED run with no readable `billing.reportedCost` books the tool's ceiling (fail
  closed); `max_usd` must be above 0. Purchases are remembered for the last 1000 keys.
All of this is per-process memory: a restart forgets purchases, unsure keys and today's spend.
The gateway should rebuild them from its cost events at boot (reported to the backend).

`cost_usd` is `billing.reportedCost` (MICRO_DOLLAR / CENT / DOLLAR) converted exactly, in code,
to a decimal string of USD with at least 4 places.

UNVERIFIED (no key tonight): shapes are from the published OpenAPI reference; whether a
COMPLETED 200 always carries `billing`; whether FAILED/STOPPED runs are unbilled (treated as
unsure); the provider/endpoint names and prices for our tools (choose with `POST /v1/discover`
on Saturday).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.gateway.adapters import _http
from app.gateway.ports import NotConfigured, PortError, Purchase, Rejected, Unavailable

log = logging.getLogger(__name__)

API = "https://api.monid.ai"
UNITS = {"MICRO_DOLLAR": Decimal("0.000001"), "CENT": Decimal("0.01"), "DOLLAR": Decimal("1")}
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
NOT_CHARGED = (400, 401, 402, 403, 404, 408, 409, 422, 429)  # answers that prove no run was billed
PENDING = ("READY", "RUNNING", "STOPPING")
MAX_REMEMBERED = 1000


class Unreadable(Exception):
    """A billing field we can't turn into money."""


def _money(value: object, what: str) -> Decimal:
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise NotConfigured(f"monid: {what} is not a number") from exc
    if not amount.is_finite() or amount < 0:
        raise NotConfigured(f"monid: {what} must be a finite amount >= 0")
    return amount


def _usd(value: Decimal) -> str:
    four = value.quantize(Decimal("0.0001"))
    return format(four if four == value else value.normalize(), "f")  # never "1E-6"


def cost_usd(run: dict) -> str | None:
    """billing.reportedCost → exact decimal string of USD; None when the run carries none.
    Raises Unreadable for a cost that is present but not money."""
    billing = run.get("billing")
    reported = billing.get("reportedCost") if isinstance(billing, dict) else None
    if reported is None:
        return None
    try:
        unit = UNITS[str(reported["unit"])]
        value = Decimal(str(reported["value"]))
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise Unreadable(repr(reported)[:120]) from exc
    if isinstance(reported.get("value"), bool) or not value.is_finite() or value < 0:
        raise Unreadable(repr(reported)[:120])
    try:
        return _usd(value * unit)
    except ArithmeticError as exc:
        raise Unreadable(repr(reported)[:120]) from exc


def _has_email(value: object) -> bool:
    if isinstance(value, str):
        return bool(EMAIL.search(value))
    if isinstance(value, dict):
        return any(_has_email(k) or _has_email(v) for k, v in value.items())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_has_email(v) for v in value)
    return False


def _tools(raw: object) -> dict[str, dict]:
    if not isinstance(raw, dict):
        raise NotConfigured("monid: MONID_TOOLS must be a JSON object of tools")
    tools = {}
    for name, spec in raw.items():
        if not (isinstance(spec, dict) and isinstance(spec.get("provider"), str) and spec["provider"]
                and isinstance(spec.get("endpoint"), str) and spec["endpoint"]):
            raise NotConfigured(f"monid: tool '{name}' needs provider and endpoint")
        if "max_usd" not in spec:
            raise NotConfigured(f"monid: tool '{name}' needs max_usd (its price ceiling)")
        ceiling = _money(spec["max_usd"], f"max_usd of '{name}'")
        if ceiling <= 0:
            raise NotConfigured(f"monid: max_usd of '{name}' must be above 0 (it is what the cap reserves)")
        fields = spec.get("fields")
        if fields is not None and not (isinstance(fields, list) and all(isinstance(f, str) for f in fields)):
            raise NotConfigured(f"monid: fields of '{name}' must be a list of names")
        tools[name] = {"provider": spec["provider"], "endpoint": spec["endpoint"], "max_usd": ceiling,
                       "fields": set(fields) if fields is not None else None}
    return tools


class Unsure(Unavailable):
    """The run may have been charged; the key is now refused until resolved."""


class MonidToolBuy:
    provider = "monid"

    def __init__(self, api_key: str, *, tools: dict[str, dict] | None = None,
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
        self.tools = _tools(tools)
        self.daily_cap = _money(daily_cap_usd or os.environ.get("MONID_DAILY_CAP_USD") or "1.00",
                                "MONID_DAILY_CAP_USD")
        self.max_wait, self.poll_every, self.timeout = max_wait, poll_every, timeout
        self._sleep, self._clock, self._today = sleep, clock, today
        self._lock = threading.Lock()
        self._bought: OrderedDict[str, tuple[str, Purchase]] = OrderedDict()  # key -> (fingerprint, first purchase)
        self._unsure: dict[str, tuple[str, Decimal]] = {}  # key -> (fingerprint, ceiling still reserved)
        self._inflight: dict[str, Decimal] = {}  # key -> reserved ceiling
        self._spent: tuple[str, Decimal] = ("", Decimal(0))  # (day, booked USD)
        self._http = httpx.Client(base_url=API, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def __repr__(self) -> str:  # never show the key
        return "MonidToolBuy()"

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ bookkeeping

    def _spent_today(self) -> Decimal:
        day = self._today()
        if self._spent[0] != day:
            self._spent = (day, Decimal(0))
        return self._spent[1]

    def _book(self, amount: Decimal) -> None:
        self._spent = (self._spent[0], self._spent_today() + amount)

    def _committed(self) -> Decimal:
        """Booked today + every in-flight ceiling + every unsure run's ceiling (it may be charged)."""
        held = sum(self._inflight.values(), Decimal(0)) + sum((c for _, c in self._unsure.values()), Decimal(0))
        return self._spent_today() + held

    def _remember(self, key: str, fp: str, purchase: Purchase) -> None:
        self._bought[key] = (fp, purchase)
        self._bought.move_to_end(key)
        while len(self._bought) > MAX_REMEMBERED:
            self._bought.popitem(last=False)

    def resolve(self, key: str, cost_usd: str | None, result: Any = None) -> None:
        """After checking GET /v1/runs for an unsure key. Never charged: resolve(key, None) frees
        the key and its reserved ceiling. Charged: resolve(key, "0.0400", output) books the cost
        and remembers the purchase, so the same key returns it instead of paying again."""
        amount = _money(cost_usd, "cost_usd") if cost_usd is not None else None  # parse before forgetting
        with self._lock:
            entry = self._unsure.pop(key, None)
            if entry is None:
                return
            if amount is not None and amount > 0:
                self._book(amount)
                self._remember(key, entry[0], Purchase(result=result, cost_usd=_usd(amount), bought=True))

    # ------------------------------------------------------------------ buying

    def buy(self, tool: str, input: dict[str, Any], key: str) -> Purchase:
        spec = self.tools.get(tool)
        if spec is None:
            raise Rejected(f"monid: '{tool}' is not an allowlisted tool (MONID_TOOLS)")
        if not isinstance(input, dict):
            raise Rejected("monid: input must be an object")
        if spec["fields"] is not None and not set(input) <= spec["fields"]:
            raise Rejected(f"monid: '{tool}' takes only {sorted(spec['fields'])}")
        try:
            canonical = json.dumps(input, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise Rejected("monid: input must be plain JSON (objects, lists, strings, finite numbers)") from exc
        input = json.loads(canonical)  # what is fingerprinted, checked and sent is the same JSON
        if _has_email(input):
            raise Rejected("monid: input carries an email address; send no client contact data")
        fp = _http.fingerprint(tool, canonical)
        ceiling = spec["max_usd"]
        with self._lock:
            if key in self._bought:
                first_fp, purchase = self._bought[key]
                if first_fp != fp:
                    raise Rejected("monid: this key was used for a different purchase")
                self._bought.move_to_end(key)
                return purchase
            if key in self._unsure:
                raise Rejected(f"monid: purchase {key} may already be charged; check GET /v1/runs, then resolve()")
            if key in self._inflight:
                raise Rejected(f"monid: purchase {key} is already in flight")
            if self._committed() + ceiling > self.daily_cap:
                raise Rejected(f"monid: today's cap of ${self.daily_cap} would be exceeded")
            self._inflight[key] = ceiling
        try:
            run = self._run({"provider": spec["provider"], "endpoint": spec["endpoint"], "input": {"body": input}})
        except Unsure:
            with self._lock:
                self._unsure[key] = (fp, ceiling)
                self._inflight.pop(key, None)
            raise
        except PortError:  # proved not charged: release the reservation
            with self._lock:
                self._inflight.pop(key, None)
            raise
        except BaseException as exc:  # anything else after sending: assume it may be charged
            with self._lock:
                self._unsure[key] = (fp, ceiling)
                self._inflight.pop(key, None)
            raise Unsure(f"monid: unexpected {type(exc).__name__} after sending; it may be charged") from exc
        try:
            cost = cost_usd(run)
        except (Unreadable, ArithmeticError):
            cost = None
        if cost is None:
            log.warning("monid: run for %s has no readable cost; booking the ceiling", tool)
            cost = _usd(ceiling)
        purchase = Purchase(result=run.get("output"), cost_usd=cost, bought=True)
        with self._lock:
            self._book(Decimal(cost))
            self._remember(key, fp, purchase)
            self._inflight.pop(key, None)
        return purchase

    def _run(self, payload: dict) -> dict:
        try:
            response = self._http.post("/v1/run", json=payload)
        except httpx.ConnectError as exc:  # never left this machine: nothing was charged
            raise Unavailable("monid unreachable") from exc
        except httpx.HTTPError as exc:  # sent, then lost: it may have run
            raise Unsure(f"monid: no answer after sending ({type(exc).__name__}); it may be charged") from exc
        if response.status_code == 202:
            return self._poll(_http.json_dict(response).get("runId"))
        if not response.is_success:
            raise self._error(response)
        return self._terminal(_http.json_dict(response))

    def _error(self, response: httpx.Response) -> PortError:
        status = response.status_code
        if status == 402:  # always our wallet (a provider's own 402 arrives as 502)
            return Rejected("monid: balance too low; top up the workspace")
        if status == 403:
            return NotConfigured("monid: the key has no workspace or no access")
        if status == 408:  # TIMED_OUT: terminal and zero-billed
            return Unavailable("monid run timed out (not billed)")
        try:
            _http.check(response, "monid")
        except PortError as err:
            if status in NOT_CHARGED:
                return err
            return Unsure(f"{err}; it may be charged")
        return Unsure(f"monid {status}; it may be charged")

    def _terminal(self, run: dict) -> dict:
        status = run.get("status")
        if status == "COMPLETED":
            return run
        if status == "BLOCKED":  # a workspace control gate stopped it before it ran
            raise Rejected("monid: blocked by a workspace control (budget, run cap or balance)")
        if status == "TIMED_OUT":
            raise Unavailable("monid run timed out (not billed)")
        raise Unsure(f"monid: run ended {status!r}; it may be charged")

    def _poll(self, run_id: object) -> dict:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise Unsure("monid: async run without a usable runId; it may be charged")
        deadline = self._clock() + self.max_wait
        while True:
            remaining = max(0.5, deadline - self._clock())
            try:
                response = self._http.get(f"/v1/runs/{run_id}", timeout=min(self.timeout, remaining))
            except httpx.HTTPError as exc:
                raise Unsure(f"monid poll failed ({type(exc).__name__}); run {run_id} may be charged") from exc
            if not response.is_success:
                raise Unsure(f"monid poll answered {response.status_code}; run {run_id} may be charged")
            run = _http.json_dict(response)
            if run.get("status") not in PENDING:
                return self._terminal(run)
            if self._clock() >= deadline:
                raise Unsure(f"monid run {run_id} still running after {self.max_wait:.0f}s; it may be charged")
            self._sleep(self.poll_every)
