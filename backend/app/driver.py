"""Local driver: the stand-in crew until Naïve runs the real agents (design D26).

It receives the same §6b wake messages the Chief of Staff will ("New case case_7",
"Approval apr_1 allowed for case_7", ...) and plays the agents' tool calls through
perform() under each agent's own seat (`actor=`), so every rule, refusal and event is the
real one. Its "reading" of the email is deliberately simple (regex over the mentions
intake already finds); the point is a real live stream for the theater, not judgement.

Each agent step books a fixed price-table estimate (contract §6a: `estimated: true`).
Runs on its own thread; a wake only enqueues, so delivery never blocks the gateway.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from typing import Any

from app.gateway import intake
from app.gateway.perform import Gateway, ToolResult

log = logging.getLogger("utax.driver")

_CASE = re.compile(r"\b(case_\d+)\b")
_CAND = re.compile(r"\b(cand_\d+)\b")
# rough token counts per agent step, priced by the code table (estimated)
STEP_TOKENS = {"intake": (1430, 210), "tax": (900, 120), "qa": (700, 90), "acct": (600, 80),
               "cos": (2100, 140), "hr": (800, 100), "mkt": (812, 96)}
STAND_IN_MODEL = "local/stand-in"
_INJECTION = re.compile(r"(?i)\bignore (?:that|this|the above|your rules|previous)\b"
                        r"|\bdisregard\b|\bsend me every\b")


def read_facts(texts: list[str]) -> dict[str, Any]:
    """The stand-in Intake agent: point at spans; the gateway parses and checks them."""
    text = "\n".join(texts)
    quotes: dict[str, str] = {}
    for m in (m for t in texts for m in intake.mentions(t)):
        if m.label in ("authorized_shares", "issued_shares", "gross_assets") \
                and m.label not in quotes:
            quotes[m.label] = m.span
    unlabelled = [m for t in texts for m in intake.mentions(t) if m.is_share and m.label is None]
    if "authorized_shares" not in quotes and unlabelled:
        quotes["authorized_shares"] = unlabelled[0].span  # the gateway re-asks a conflict
    inc = None
    match = re.search(r"(?i)[^.\n]*\bdelaware\b[^.\n]*", text)
    if re.search(r"(?i)\b(llc|partnership|not incorporated|haven't incorporated|"
                 r"not yet incorporated)\b", text):
        inc = False
    elif match and re.search(r"(?i)c-?corp|corporation|\binc\b|incorporated", match.group(0)):
        inc = True
        quotes["incorporated_delaware"] = match.group(0).strip()
    foreign = None
    no_foreign = re.search(r"(?i)\bno foreign (?:owners?|shareholders?|investors?)\b", text)
    yes_foreign = re.search(r"(?i)\bforeign (?:owner|shareholder|investor)s?\b[^.\n]{0,40}"
                            r"\b(?:2[5-9]|[3-9]\d|100)\s?%", text)
    if no_foreign:
        foreign, quotes["foreign_owner_25"] = False, no_foreign.group(0)
    elif yes_foreign:
        foreign, quotes["foreign_owner_25"] = True, yes_foreign.group(0)
    reason = None
    if inc is False:
        reason = "not_a_corporation" if re.search(r"(?i)\b(llc|partnership)\b", text) \
            else "not_incorporated"
    elif match is None and re.search(r"(?i)incorporated in (?!delaware)\w+", text):
        inc, reason = False, "outside_delaware"
    return {"quotes": quotes, "incorporated_delaware": inc, "foreign_owner_25": foreign,
            "not_eligible_reason": reason, "suspicious": bool(_INJECTION.search(text))}


class LocalCrew:
    def __init__(self, gateway: Gateway, *, pace: float = 1.2) -> None:
        self.gw = gateway
        self.pace = pace
        # Wakes are coalesced: one pending entry per case/candidate, FAST PATH first, so a
        # judge's /talk case never waits behind a backlog of repeats.
        self._queue: queue.PriorityQueue[tuple[int, int, str | None]] = queue.PriorityQueue()
        self._pending: set[str] = set()
        self._order = 0
        self._plock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="utax-local-crew", daemon=True)
        self._unsubscribe = gateway.on_wake(self.enqueue)
        self._step_no = 0

    def start(self) -> "LocalCrew":
        self._thread.start()
        log.info("local crew started (pace %.1fs)", self.pace)
        return self

    MAX_PENDING = 200

    def enqueue(self, message: str) -> None:
        target = _CASE.search(message) or _CAND.search(message)
        key = target.group(1) if target else message
        with self._plock:
            if key in self._pending or len(self._pending) >= self.MAX_PENDING:
                return  # already queued (one advance handles it) or overloaded
            self._pending.add(key)
            self._order += 1
            priority = 0 if message.startswith("FAST PATH") else 1
            self._queue.put((priority, self._order, message))

    def stop(self) -> None:
        self._unsubscribe()
        self._queue.put((-1, 0, None))
        self._thread.join(timeout=5)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Tests and the CLI: block until every queued wake is handled."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    # ------------------------------------------------------------ loop

    def _run(self) -> None:
        while True:
            _prio, _n, message = self._queue.get()
            try:
                if message is None:
                    return
                target = _CASE.search(message) or _CAND.search(message)
                with self._plock:
                    self._pending.discard(target.group(1) if target else message)
                self.handle(message)
            except Exception:  # noqa: BLE001 - one bad wake never stops the crew
                log.exception("local crew failed on %r", message)
            finally:
                self._queue.task_done()

    def handle(self, message: str) -> None:
        log.info("crew woke: %s", message)
        if message.startswith("CEO: "):
            self._call("cos", "firm_brief", {})
            return
        if message.startswith("New applicant "):
            return  # v1.5: nothing to screen until the questionnaire answers arrive
        if message.startswith("Applicant answered "):
            cand = _CAND.search(message)
            if cand:
                self._screen(cand.group(1))
            return
        if " denied for " in message:
            return  # the CEO said no; nothing to retry
        case = _CASE.search(message)
        if case:
            self.advance(case.group(1))

    # ------------------------------------------------------------ steps

    def _call(self, actor: str, tool: str, args: dict[str, Any]) -> ToolResult:
        if self.pace:
            time.sleep(self.pace)  # so the theater can show each step
        result = self.gw.perform(tool, args, actor=actor)
        self._step_no += 1
        if result.status != "done" or result.data.get("duplicate"):
            return result  # only work actually done is booked (§6a estimate)
        tokens_in, tokens_out = STEP_TOKENS.get(actor, (500, 60))
        self.gw.perform("book_spend", {
            "provider": "local", "model": STAND_IN_MODEL, "tokens_in": tokens_in,
            "tokens_out": tokens_out, "case_id": args.get("case_id"),
            "idempotency_key": f"driver:{actor}:{tool}:{args.get('case_id', '')}:{time.time_ns()}",
        })
        log.info("%s %s -> %s %s", actor, tool, result.status,
                 (result.error or {}).get("code", ""))
        return result

    def advance(self, case_id: str, max_steps: int = 12) -> None:
        """Walk the case forward until it waits on someone (approval, client, human)."""
        for _ in range(max_steps):
            case = self.gw.state.cases.get(case_id)
            if case is None:
                return
            status = case.status
            if status == "INTAKE_PENDING":
                done = self._intake(case_id)
            elif status == "READY":
                self._call("tax", "law_search", {
                    "case_id": case_id, "query": "delaware franchise tax assumed par value method"})
                done = self._call("tax", "compute_tax", {"case_id": case_id}).status == "done"
            elif status == "COMPUTED":
                result = self._call("qa", "qa_check", {"case_id": case_id})
                if result.status == "done" and not result.data.get("passed") \
                        and any(c["field"] == "inputs" and not c["ok"]
                                for c in result.data.get("checks", [])):
                    self._call("qa", "flag_case", {"case_id": case_id,
                                                   "kind": "unrealistic_inputs"})
                done = result.status == "done"
            elif status == "QA_PASSED":
                done = self._call("acct", "send_memo", {"case_id": case_id}).status == "done"
            elif status == "SENT":
                if case.phone is None:
                    self._call("qa", "phone_check", {"case_id": case_id})
                slots = self._call("intake", "get_case", {"case_id": case_id}).data.get(
                    "offered_slots") or []
                if not slots:
                    return
                done = self._call("intake", "book_call",
                                  {"case_id": case_id, "start": slots[0]}).status == "done"
            else:  # CALL_BOOKED, BLOCKED, NOT_ELIGIBLE, STALLED, NEW, QA_FAILED: wait
                return
            if not done or self.gw.state.cases[case_id].status == status:
                return  # waiting on an approval, the client or a human

    def _intake(self, case_id: str) -> bool:
        detail = self._call("intake", "get_case", {"case_id": case_id}).data
        read = read_facts(detail.get("messages", []))
        if read["not_eligible_reason"]:
            return self._call("intake", "mark_not_eligible", {
                "case_id": case_id, "reason": read["not_eligible_reason"]}).status == "done"
        result = self._call("intake", "extract_facts", {
            "case_id": case_id, "quotes": read["quotes"],
            "incorporated_delaware": read["incorporated_delaware"],
            "foreign_owner_25": read["foreign_owner_25"]})
        if read["suspicious"]:
            self._call("qa", "flag_case", {"case_id": case_id, "kind": "possible_injection"})
        if result.status != "done":
            return False
        if result.data.get("missing"):
            self._call("intake", "ask_client", {"case_id": case_id,
                                                "missing": result.data["missing"]})
            return False  # now it waits for the client (or the approval)
        return True

    def _screen(self, candidate_id: str) -> None:
        cand = self._call("hr", "get_candidate", {"candidate_id": candidate_id}).data
        if not cand or cand.get("stage") != "applicant":
            return
        self._call("hr", "hr_record", {"stage": "screened", "candidate_id": candidate_id,
                                       "note": "questionnaire reviewed by the AI screen"})
        credentials = cand.get("credentials") or ""
        if any(c in credentials for c in ("EA", "CPA")):
            self._call("hr", "hr_record", {"stage": "shortlisted", "candidate_id": candidate_id,
                                           "note": f"{credentials}; the CEO decides"})
