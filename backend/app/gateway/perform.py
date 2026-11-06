"""perform(action): the ONLY place rules live (design E1).

validate -> rules (QA gate, trust, masking, caps, idempotency) -> act through a PORT ->
append EVENTS (fail closed) -> fan out to listeners (SSE).

Every MCP tool (contract §6) and every control action (approve, voice, inject email)
is a handler here. Handlers raise `Refused` or `NeedsApproval`; `perform()` turns them
into the contract's tool result `{ok, status, data, approval_id, error}`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.engine import (
    EngineError,
    FeeQuote,
    compute_franchise_tax,
    form5472_flag,
    format_usd,
    rnd_flag,
)
from app.gateway import intake
from app.gateway.adapters.local import DELAWARE_FEE_URL
from app.gateway.events import PROVIDERS, Event, NewEvent, iso_ts, parse_ts
from app.gateway.masking import company_label, mask_email, mask_text, redact_ids
from app.gateway.ports import (
    CrmContact,
    InMail,
    OutMail,
    PortError,
    Ports,
    Post,
    Slot,
)
from app.gateway.ports.store import EventStorePort, EventWriteError
from app.gateway.pricing import estimate_usd, parse_usd, usd_string
from app.gateway.projections import TRANSITIONS, TRUST_TYPES, Case, State, rebuild
from app.gateway.settings import Settings
from app.gateway.templates import (
    FACT_LABELS,
    MEMO_SUBJECT,
    TemplateError,
    ask_body,
    load_memo_template,
    not_eligible_body,
    render_memo,
)

log = logging.getLogger("utax.perform")

FEE_CACHE_SECONDS = 6 * 3600
SEND_ATTEMPTS = 3  # first try + 2 retries (Error & Rescue Map)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Refused(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data or {}


class ToolError(Refused):
    """A side effect failed (port down after retries). Reported as status "error"."""


class NeedsApproval(Exception):
    def __init__(self, approval_id: str, summary: str) -> None:
        super().__init__(approval_id)
        self.approval_id, self.summary = approval_id, summary


@dataclass
class ToolResult:
    status: str  # done | needs_approval | refused | error
    data: dict[str, Any] = field(default_factory=dict)
    approval_id: str | None = None
    error: dict[str, str] | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("done", "needs_approval")

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "status": self.status, "data": self.data,
                "approval_id": self.approval_id, "error": self.error}


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _cap(text: str) -> str:
    """Uppercase the first letter only ("first memo..." -> "First memo...")."""
    return text[:1].upper() + text[1:]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------- arg helpers


def _str(args: dict, name: str, *, required: bool = True, max_len: int = 5000) -> str | None:
    value = args.get(name)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise Refused("bad_input", f"{name} is required")
        return None
    if not isinstance(value, str):
        raise Refused("bad_input", f"{name} must be a string")
    if len(value) > max_len:
        raise Refused("bad_input", f"{name} is longer than {max_len} characters")
    return value.strip()


def _bool(args: dict, name: str) -> bool | None:
    value = args.get(name)
    if value is None or isinstance(value, bool):
        return value
    raise Refused("bad_input", f"{name} must be true, false or null")


def _int(args: dict, name: str) -> int | None:
    value = args.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Refused("bad_input", f"{name} must be a whole number >= 0")
    return value


def _one_of(args: dict, name: str, allowed: tuple[str, ...], default: str | None = None) -> str:
    value = args.get(name, default)
    if value not in allowed:
        raise Refused("bad_input", f"{name} must be one of {', '.join(allowed)}")
    return value


# ---------------------------------------------------------------- gateway


class Gateway:
    def __init__(
        self,
        store: EventStorePort,
        ports: Ports,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.ports = ports
        self.settings = settings
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Event], None]] = []
        self._fee_cache: tuple[float, FeeQuote] | None = None
        self.state: State = rebuild(store.since(0))
        self._handlers: dict[str, Callable[[dict], dict]] = {
            # MCP tools (contract §6)
            "list_open_cases": self._list_open_cases,
            "get_case": self._get_case,
            "extract_facts": self._extract_facts,
            "ask_client": self._ask_client,
            "mark_not_eligible": self._mark_not_eligible,
            "law_search": self._law_search,
            "compute_tax": self._compute_tax,
            "qa_check": self._qa_check,
            "send_memo": self._send_memo,
            "book_call": self._book_call,
            "phone_check": self._phone_check,
            "post_social": self._post_social,
            "draft_dm": self._draft_dm,
            "hr_record": self._hr_record,
            "book_cost": self._book_cost,
            "firm_brief": self._firm_brief,
            # control actions (contract §5)
            "inject_email": self._inject_email,
            "receive_email": self._receive_email,
            "voice": self._voice,
            "approve": self._approve,
            "ceo_stop": self._ceo_stop,
            "send_dm_by_human": self._send_dm_by_human,
            "hr_hire": self._hr_hire,
        }

    READ_ONLY = frozenset({"list_open_cases", "get_case"})
    TOOLS = ("list_open_cases", "get_case", "extract_facts", "ask_client", "mark_not_eligible",
             "law_search", "compute_tax", "qa_check", "send_memo", "book_call", "phone_check",
             "post_social", "draft_dm", "hr_record", "book_cost", "firm_brief")

    # ------------------------------------------------------------ plumbing

    def subscribe(self, listener: Callable[[Event], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def perform(self, name: str, args: dict | None = None) -> ToolResult:
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult("refused", error={"code": "unknown_action",
                                                "message": f"no action named {name!r}"})
        args = dict(args or {})
        with self._lock:
            try:
                if name not in self.READ_ONLY:
                    self.store.ensure_writable()  # fail closed before any side effect
                data = handler(args)
                result = ToolResult("done", data=data)
            except NeedsApproval as pending:
                result = ToolResult("needs_approval", approval_id=pending.approval_id,
                                    data={"summary": pending.summary,
                                          "next": "tell the Chief of Staff; retry after approval"})
            except ToolError as err:
                result = ToolResult("error", data=err.data,
                                    error={"code": err.code, "message": err.message})
            except Refused as err:
                result = ToolResult("refused", data=err.data,
                                    error={"code": err.code, "message": err.message})
            except EventWriteError as err:
                log.error("event store unavailable: %s", err)
                return ToolResult("error", error={"code": "store_unavailable",
                                                  "message": "event log unavailable; nothing was done"})
            try:
                if name not in self.READ_ONLY:
                    self._drain_outbox()
            except EventWriteError as err:  # the action itself already landed
                log.error("outbox drain failed: %s", err)
            return result

    def _now(self) -> datetime:
        return self.clock()

    def _emit(self, events: list[NewEvent]) -> list[Event]:
        """Append atomically, then project and fan out the new ones. Raises EventWriteError."""
        # Timestamps follow ids: never earlier than the last event, 1 ms apart in a batch.
        base = self._now()
        last = self.store.since(self.state.last_event_id - 1) if self.state.last_event_id else ()
        last_ts = max((parse_ts(e.ts) for e in last), default=None)
        if last_ts is not None and base <= last_ts:
            base = last_ts + timedelta(milliseconds=1)
        stamped = [e if e.ts else replace(e, ts=iso_ts(base + timedelta(milliseconds=i)))
                   for i, e in enumerate(events)]
        appended = self.store.append_batch(stamped, ts=iso_ts(base))
        out: list[Event] = []
        for item in appended:
            out.append(item.event)
            if not item.duplicate:
                self.state.apply(item.event)
                for listener in list(self._listeners):
                    try:
                        listener(item.event)
                    except Exception:  # noqa: BLE001 - a bad listener never blocks the firm
                        log.exception("event listener failed")
        return out

    def _case(self, args: dict) -> Case:
        case_id = _str(args, "case_id", max_len=64)
        case = self.state.cases.get(case_id)
        if case is None:
            raise Refused("not_found", f"no case {case_id}")
        return case

    def _status(self, case: Case, to: str) -> NewEvent | None:
        if case.status == to:
            return None
        if to not in TRANSITIONS.get(case.status, set()):
            raise RuntimeError(f"illegal transition {case.status} -> {to} for {case.case_id}")
        return NewEvent(type="case.status", actor="system", case_id=case.case_id,
                        payload={"from": case.status, "to": to})

    def _require_status(self, case: Case, *allowed: str, code: str = "wrong_status") -> None:
        if case.status not in allowed:
            raise Refused(code, f"{case.case_id} is {case.status}; this needs "
                                f"{' or '.join(allowed)}", {"status": case.status})

    def _done_from(self, key: str) -> Event | None:
        return self.store.find_by_key(key)

    def _block(self, case: Case, reason: str, *, actor: str = "system") -> list[Event]:
        events = [NewEvent(type="case.blocked", actor=actor, case_id=case.case_id,
                           payload={"reason": reason, "needs": "human"})]
        status = self._status(case, "BLOCKED") if "BLOCKED" in TRANSITIONS.get(case.status, ()) \
            or case.status == "BLOCKED" else None
        if status:
            events.append(status)
        return self._emit(events)

    # ------------------------------------------------------------ trust

    def _cap_hit(self, action_type: str) -> str | None:
        now = self._now()
        window_start = now - timedelta(hours=1)
        since = self.state.trust_auto_since.get(action_type)
        if since:
            window_start = max(window_start, parse_ts(since))
        recent = [ts for ts in self.state.cap_log[action_type] if parse_ts(ts) > window_start]
        if len(recent) >= self.settings.cap_per_hour:
            return f"cap: {self.settings.cap_per_hour}/hr"
        cap = self.settings.spend_cap_usd
        if cap is not None and self.state.spent_usd() >= cap:
            return f"budget: ${cap} spent"
        return None

    def _gate(self, action_type: str, action_key: str, summary: str, case_id: str | None) -> None:
        """Trust earned: ASK needs an approval, AUTO runs (until a cap), PAUSED needs a human."""
        if self.state.trust[action_type] == "AUTO":
            reason = self._cap_hit(action_type)
            if reason is None:
                return
            self._emit([NewEvent(type="trust.changed", actor="system",
                                 payload={"action_type": action_type, "from": "AUTO",
                                          "to": "PAUSED", "reason": reason})])
        approval_id = self.state.approval_by_key.get(action_key)
        if approval_id:
            approval = self.state.approvals[approval_id]
            if approval.decision == "allow":
                return
            if approval.decision == "deny":
                raise Refused("denied", "the CEO said no to this action", {"approval_id": approval_id})
            raise NeedsApproval(approval_id, approval.summary)
        approval_id = f"apr_{len(self.state.approvals) + 1}"
        self._emit([NewEvent(type="approval.requested", actor="system", case_id=case_id,
                             payload={"approval_id": approval_id, "action_type": action_type,
                                      "summary": summary},
                             idempotency_key=f"approval:{action_key}",
                             private={"action_key": action_key})])
        raise NeedsApproval(approval_id, summary)

    def _first(self, action_type: str) -> str:
        return "First " if action_type not in self.state.trusted_once else ""

    # ------------------------------------------------------------ port calls with retry

    def _with_retry(self, what: str, call: Callable[[], Any]) -> Any:
        last: PortError | None = None
        for attempt in range(SEND_ATTEMPTS):
            try:
                return call()
            except PortError as err:
                last = err
                if not err.retryable or attempt == SEND_ATTEMPTS - 1:
                    break
                self.sleep(min(err.retry_after or 0.5 * 2**attempt, 30))
        raise ToolError(last.code if last else "port_error", f"{what} failed: {last}")

    def _fee(self) -> FeeQuote:
        cached = self._fee_cache
        if cached and time.monotonic() - cached[0] < FEE_CACHE_SECONDS:
            return cached[1]
        quote = None
        try:
            page = self.ports.scrape.fetch_page(DELAWARE_FEE_URL)
            cents = parse_fee_cents(page.html)
            if cents is not None:
                quote = FeeQuote(cents, page.source)
        except PortError as err:
            log.warning("fee page fetch failed (%s); using the saved snapshot", err)
        if quote is None:
            path = self.settings.fixtures_dir / "snapshots" / "delaware-fees.html"
            cents = parse_fee_cents(path.read_text(encoding="utf-8")) if path.exists() else None
            if cents is None:
                raise Refused("fee_unknown", "annual report fee unknown: no page and no snapshot")
            quote = FeeQuote(cents, "snapshot")
        self._fee_cache = (time.monotonic(), quote)
        return quote

    # ------------------------------------------------------------ inbound

    def _inject_email(self, args: dict) -> dict:
        if self.settings.adapters.get("mail") != "local":
            raise Refused("not_local", "inject-email only works with ADAPTER_MAIL=local")
        sender = _str(args, "from", max_len=320)
        if not _EMAIL_RE.match(sender):
            raise Refused("bad_input", "from must be an email address")
        mail = self.ports.mail.parse_inbound({
            "from": sender, "subject": _str(args, "subject", required=False, max_len=300) or "",
            "body": _str(args, "body", max_len=20000), "message_id": args.get("message_id"),
        })
        return self._receive(mail, provider=self.ports.mail.provider)

    def _receive_email(self, args: dict) -> dict:
        """A parsed inbound mail from a MailPort (webhook Saturday, .eml files tonight)."""
        mail = args.get("mail")
        if not isinstance(mail, InMail):
            raise Refused("bad_input", "mail must be an InMail")
        return self._receive(mail, provider=self.ports.mail.provider)

    def _new_case_number(self) -> int:
        return max((c.number for c in self.state.cases.values()), default=0) + 1

    def _receive(self, mail: InMail, *, provider: str) -> dict:
        key = f"email:{mail.message_id}"
        seen = self._done_from(key)
        if seen is not None:
            return {"case_id": seen.case_id, "duplicate": True}
        sender_key = mail.sender.strip().lower()
        case_id = self.state.case_by_sender.get(sender_key)
        case = self.state.cases.get(case_id) if case_id else None
        number = case.number if case else self._new_case_number()
        case_id = case_id or f"case_{number}"
        masked_name = case.masked_name if case else f"Team #{number}"
        company = company_label(sender_key)
        body, redacted = redact_ids(mail.body)
        subject, sub_redacted = redact_ids(mail.subject)
        events = [NewEvent(
            type="email.received", actor="intake", case_id=case_id, provider=provider,
            payload={"masked_name": masked_name, "from_masked": mask_email(sender_key),
                     "subject": mask_text(subject, masked_name=masked_name, company=company),
                     "body": mask_text(body, masked_name=masked_name, company=company)},
            idempotency_key=key,
            private={"reply_to": mail.sender.strip(), "sender_key": sender_key,
                     "masked_name": masked_name, "company": company,
                     "message_id": mail.message_id, "redactions": redacted + sub_redacted},
        )]
        events += self._reopen(case, case_id)
        self._emit(events)
        return {"case_id": case_id, "masked_name": masked_name, "redacted": redacted + sub_redacted}

    def _reopen(self, case: Case | None, case_id: str) -> list[NewEvent]:
        current = case.status if case else "NEW"
        if current in ("INTAKE_PENDING", "BLOCKED"):
            return []
        return [NewEvent(type="case.status", actor="system", case_id=case_id,
                         payload={"from": current, "to": "INTAKE_PENDING"})]

    def _voice(self, args: dict) -> dict:
        text = _str(args, "text", max_len=4000)
        channel = _one_of(args, "channel", ("mic", "typed"), default="typed")
        speaker = _one_of(args, "speaker", ("ceo", "judge"), default="ceo")
        text, _ = redact_ids(text)
        if speaker == "ceo":
            text = mask_text(text, masked_name="", company=None)
            self._emit([
                NewEvent(type="voice.command", actor="ceo", provider="local",
                         payload={"text": text, "channel": channel, "speaker": "ceo",
                                  "case_id": None}),
                NewEvent(type="touch.operating", actor="ceo",
                         payload={"note": f'voice: "{text[:120]}"', "approval_id": None}),
            ])
            # Stand-in: the Chief of Staff forward (Naïve REST) is wired once a key exists.
            return {"forwarded": False, "note": "logged; Chief of Staff forward not wired yet"}
        reply = _str(args, "reply_email", max_len=320)
        if not _EMAIL_RE.match(reply or ""):
            raise Refused("bad_input", "a judge case needs reply_email")
        sender_key = reply.lower()
        case_id = self.state.case_by_sender.get(sender_key)
        case = self.state.cases.get(case_id) if case_id else None
        number = case.number if case else self._new_case_number()
        case_id = case_id or f"case_{number}"
        masked_name = case.masked_name if case else f"Team #{number}"
        events = [NewEvent(
            type="voice.command", actor="ceo", case_id=case_id, provider="local",
            payload={"text": mask_text(text, masked_name=masked_name, company=None),
                     "channel": channel, "speaker": "judge", "case_id": case_id},
            private={"reply_to": reply, "sender_key": sender_key, "masked_name": masked_name,
                     "company": None},
        )]
        events += self._reopen(case, case_id)
        self._emit(events)
        return {"case_id": case_id, "masked_name": masked_name, "priority": True}

    # ------------------------------------------------------------ approvals

    def _approve(self, args: dict) -> dict:
        approval_id = _str(args, "approval_id", max_len=64)
        decision = _one_of(args, "decision", ("allow", "deny"))
        note = _str(args, "note", required=False, max_len=500)
        approval = self.state.approvals.get(approval_id)
        if approval is None:
            raise Refused("not_found", f"no approval {approval_id}")
        if approval.decision is not None:
            return {"approval_id": approval_id, "decision": approval.decision, "duplicate": True}
        action_type = approval.action_type
        current = self.state.trust[action_type]
        key = f"approve:{approval_id}"
        private = {"decision": decision, "ceo_note": note}
        if decision == "deny":
            self._emit([NewEvent(type="touch.operating", actor="ceo", case_id=approval.case_id,
                                 payload={"note": f"denied: {approval.summary}"[:160],
                                          "approval_id": approval_id},
                                 idempotency_key=key, private=private)])
            return {"approval_id": approval_id, "decision": "deny"}
        events: list[NewEvent] = []
        if current != "AUTO":
            first = action_type not in self.state.trusted_once and current == "ASK"
            kind = "touch.training" if first else "touch.operating"
            events.append(NewEvent(type=kind, actor="ceo", case_id=approval.case_id,
                                   payload={"note": f"{action_type} → AUTO",
                                            "approval_id": approval_id},
                                   idempotency_key=key, private=private))
            reason = ("first approval" if first else
                      "resumed by a human" if current == "PAUSED" else "approved again")
            events.append(NewEvent(type="trust.changed", actor="system",
                                   payload={"action_type": action_type, "from": current,
                                            "to": "AUTO", "reason": reason}))
        else:
            events.append(NewEvent(type="touch.operating", actor="ceo", case_id=approval.case_id,
                                   payload={"note": f"approved: {approval.summary}"[:160],
                                            "approval_id": approval_id},
                                   idempotency_key=key, private=private))
        self._emit(events)
        return {"approval_id": approval_id, "decision": "allow",
                "trust": self.state.trust[action_type]}

    def _ceo_stop(self, args: dict) -> dict:
        action_type = _one_of(args, "action_type", TRUST_TYPES)
        current = self.state.trust[action_type]
        events = [NewEvent(type="touch.operating", actor="ceo",
                           payload={"note": f"stop {action_type}", "approval_id": None})]
        if current != "ASK":
            events.append(NewEvent(type="trust.changed", actor="system",
                                   payload={"action_type": action_type, "from": current,
                                            "to": "ASK", "reason": "CEO stop"}))
        self._emit(events)
        return {"trust": self.state.trust[action_type]}

    # ------------------------------------------------------------ cos tools

    _NEXT = {"NEW": "get_case", "INTAKE_PENDING": "extract_facts", "READY": "compute_tax",
             "COMPUTED": "qa_check", "QA_PASSED": "send_memo", "SENT": "book_call",
             "STALLED": "ask_client", "QA_FAILED": None, "BLOCKED": None}

    def _waiting_reason(self, case: Case) -> str:
        if case.status == "BLOCKED":
            return case.blocked_reason or "blocked: needs a human"
        if case.status == "INTAKE_PENDING" and case.missing:
            if case.asks and case.asked_after_text == len(case.texts):
                return "waiting on the client's reply"
            return "missing: " + ", ".join(case.missing)
        return {"INTAKE_PENDING": "new message to read", "READY": "ready for tax",
                "COMPUTED": "needs QA", "QA_PASSED": "memo ready to send",
                "SENT": "offer the review call", "STALLED": "no reply yet",
                "QA_FAILED": "QA failed"}.get(case.status, case.status.lower())

    def _list_open_cases(self, args: dict) -> dict:
        open_cases = [c for c in self.state.cases.values()
                      if c.status not in ("CALL_BOOKED", "NOT_ELIGIBLE")]
        open_cases.sort(key=lambda c: (not c.priority, c.number))
        return {"cases": [{"case_id": c.case_id, "masked_name": c.masked_name,
                           "status": c.status, "priority": c.priority,
                           "waiting_reason": self._waiting_reason(c),
                           "next": self._NEXT.get(c.status)} for c in open_cases]}

    def _get_case(self, args: dict) -> dict:
        return self._case(args).detail()

    def _firm_brief(self, args: dict) -> dict:
        cases = sorted(self.state.cases.values(), key=lambda c: c.number)
        phrases = {"CALL_BOOKED": "call booked", "SENT": "memo sent", "BLOCKED": "blocked",
                   "NOT_ELIGIBLE": "not eligible", "STALLED": "no reply",
                   "INTAKE_PENDING": "waiting on numbers", "READY": "ready for tax",
                   "COMPUTED": "in QA", "QA_PASSED": "memo ready", "QA_FAILED": "QA failed",
                   "NEW": "new"}
        parts = [f"{c.masked_name} {phrases.get(c.status, c.status.lower())}" for c in cases]
        text = f"{len(cases)} case{'s' if len(cases) != 1 else ''}"
        text += (": " + ", ".join(parts) + ".") if parts else "."
        shortlisted = sum(1 for c in self.state.candidates.values() if c.get("stage") == "shortlisted")
        if shortlisted:
            text += (f" {shortlisted} candidate{'s' if shortlisted != 1 else ''} shortlisted,"
                     " waiting on you.")
        pending = [a for a in self.state.approvals.values() if a.decision is None]
        if pending:
            text += f" {len(pending)} approval{'s' if len(pending) != 1 else ''} waiting."
        counts = {"clients": len(cases),
                  "sent": sum(1 for c in cases if c.memo_message_id),
                  "blocked": sum(1 for c in cases if c.status == "BLOCKED")}
        self._emit([NewEvent(type="brief.generated", actor="cos", provider="local",
                             payload={"text": text, "counts": counts})])
        return {"text": text, "counts": counts}

    # ------------------------------------------------------------ intake tools

    def _extract_facts(self, args: dict) -> dict:
        case = self._case(args)
        self._require_status(case, "INTAKE_PENDING", "READY", "STALLED")
        quotes = args.get("quotes") or {}
        if not isinstance(quotes, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                                    for k, v in quotes.items()):
            raise Refused("bad_input", "quotes must map fact names to verbatim spans")
        unknown = set(quotes) - set(intake.NUMERIC) - set(intake.BOOLEAN)
        if unknown:
            raise Refused("bad_input", f"unknown facts: {sorted(unknown)}")
        inc = _bool(args, "incorporated_delaware")
        foreign = _bool(args, "foreign_owner_25")
        if inc is False:
            raise Refused("not_eligible", "not a Delaware corporation: call mark_not_eligible")
        digest = _digest({"q": quotes, "inc": inc, "f": foreign, "t": len(case.texts)})
        texts = [*case.texts, case.subject]
        check = intake.check_facts(quotes=quotes, incorporated_delaware=inc,
                                   foreign_owner_25=foreign, texts=texts)
        if not check.ok:
            self._emit([NewEvent(type="facts.missing", actor="intake", case_id=case.case_id,
                                 provider="local",
                                 payload={"missing": check.missing, "asks_so_far": case.asks},
                                 idempotency_key=f"missing:{case.case_id}:{digest}",
                                 private={"problems": check.problems})])
            return {"missing": check.missing, "problems": check.problems,
                    "next": "ask_client" if case.asks < self.settings.max_asks else "firm_brief"}
        events = [NewEvent(type="facts.extracted", actor="intake", case_id=case.case_id,
                           provider="local",
                           payload={"facts": check.facts, "quotes": check.quotes,
                                    "assumed": check.assumed},
                           idempotency_key=f"facts:{case.case_id}:{digest}",
                           private={"unrealistic": check.unrealistic})]
        status = self._status(case, "READY") if case.status != "READY" else None
        if case.status == "STALLED":
            events.append(NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                   payload={"from": "STALLED", "to": "INTAKE_PENDING"}))
            status = NewEvent(type="case.status", actor="system", case_id=case.case_id,
                              payload={"from": "INTAKE_PENDING", "to": "READY"})
        if status:
            events.append(status)
        self._emit(events)
        return {"facts": check.facts, "assumed": check.assumed, "next": "compute_tax"}

    def _ask_client(self, args: dict) -> dict:
        case = self._case(args)
        self._require_status(case, "INTAKE_PENDING", "STALLED")
        missing = args.get("missing") or list(case.missing)
        if not isinstance(missing, list) or not all(m in FACT_LABELS for m in missing):
            raise Refused("bad_input", f"missing must list fact names: {sorted(FACT_LABELS)}")
        if not missing:
            raise Refused("nothing_missing", "no facts are missing; call extract_facts")
        if case.asks and case.asked_after_text == len(case.texts):
            return {"already_asked": True, "asks_so_far": case.asks}
        if case.asks >= self.settings.max_asks:
            self._block(case, f"still missing facts after {case.asks} asks")
            raise Refused("max_asks", f"asked {case.asks} times already; case is BLOCKED")
        if not case.reply_to:
            raise Refused("no_reply_address", f"{case.case_id} has no reply address")
        key = f"ask:{case.case_id}:t{len(case.texts)}"
        labels = ", ".join(FACT_LABELS.get(m, m) for m in missing)
        self._gate("email", key, _cap(f"{self._first('email')}re-ask email: {case.masked_name}, "
                                      f"needs {labels}"[:160]), case.case_id)
        body = ask_body(missing, case.problems)
        sent = self._with_retry("ask email", lambda: self.ports.mail.send(
            OutMail(to=case.reply_to, subject=f"Re: {case.subject}".strip(), body=body), key))
        self._emit([NewEvent(type="client.asked", actor="intake", case_id=case.case_id,
                             provider=self.ports.mail.provider,
                             payload={"missing": missing, "body": body}, idempotency_key=key,
                             private={"message_id": sent.message_id})])
        return {"asked": missing, "asks_so_far": case.asks, "message_id": sent.message_id}

    def _mark_not_eligible(self, args: dict) -> dict:
        case = self._case(args)
        reason = _str(args, "reason", max_len=300)
        self._require_status(case, "NEW", "INTAKE_PENDING", "READY", "STALLED")
        if not case.reply_to:
            raise Refused("no_reply_address", f"{case.case_id} has no reply address")
        key = f"not_eligible:{case.case_id}:t{len(case.texts)}"
        self._gate("email", key,
                   _cap(f"{self._first('email')}not-eligible reply: {case.masked_name}"),
                   case.case_id)
        sent = self._with_retry("not-eligible email", lambda: self.ports.mail.send(
            OutMail(to=case.reply_to, subject=f"Re: {case.subject}".strip(),
                    body=not_eligible_body()), key))
        events = [NewEvent(type="case.not_eligible", actor="intake", case_id=case.case_id,
                           provider=self.ports.mail.provider,
                           payload={"reason": mask_text(reason, masked_name=case.masked_name,
                                                        company=case.company)},
                           idempotency_key=key, private={"message_id": sent.message_id})]
        events.append(self._status(case, "NOT_ELIGIBLE"))
        self._emit(events)
        return {"status": "NOT_ELIGIBLE", "message_id": sent.message_id}

    # ------------------------------------------------------------ tax tools

    def _law_search(self, args: dict) -> dict:
        case = self._case(args)
        query = _str(args, "query", max_len=300)
        try:
            hits = self.ports.scrape.search(query)
            results = [{"cite": h.cite, "snippet": h.snippet} for h in hits]
            status = "ok"
        except PortError as err:
            log.warning("law search failed: %s", err)
            results, status = [], "failed"
        self._emit([NewEvent(type="law.searched", actor="tax", case_id=case.case_id,
                             provider=self.ports.scrape.provider, status=status,
                             payload={"query": query, "results": results})])
        if status != "ok":
            raise ToolError("search_failed", "law search is unavailable right now")
        return {"results": results}

    def _engine_result(self, facts: dict, fee: FeeQuote) -> dict:
        result = compute_franchise_tax(
            authorized_shares=facts["authorized_shares"], issued_shares=facts["issued_shares"],
            gross_assets_cents=facts["gross_assets_cents"],
            stated_par=Decimal(facts["stated_par"]), annual_report_fee=fee,
        )
        return {
            "authorized_method_cents": result.authorized_method_cents,
            "assumed_par_method_cents": result.assumed_par_method_cents,
            "chosen": result.chosen,
            "annual_report_fee_cents": result.annual_report_fee_cents,
            "fee_source": result.fee_source,
            "total_cents": result.total_cents,
            "due_date": result.due_date,
            "form5472_flag": form5472_flag(facts["foreign_owner_25"]),
            "rnd_flag": rnd_flag(facts.get("gross_receipts_cents"),
                                 facts.get("years_with_receipts")),
            "engine_version": result.engine_version,
        }

    def _compute_tax(self, args: dict) -> dict:
        case = self._case(args)
        key = f"tax:{case.case_id}:v{case.facts_version}"
        done = self._done_from(key)
        if done is not None:
            return {"result": done.payload, "duplicate": True}
        self._require_status(case, "READY")
        if not case.facts:
            raise Refused("no_facts", "extract_facts first")
        fee = self._fee()
        try:
            payload = self._engine_result(case.facts, fee)
        except EngineError as err:
            self._block(case, f"engine refused the facts: {err}")
            raise ToolError("engine_error", str(err)) from err
        self._emit([NewEvent(type="tax.computed", actor="tax", case_id=case.case_id,
                             provider="local", payload=payload, idempotency_key=key),
                    self._status(case, "COMPUTED")])
        return {"result": payload, "next": "qa_check"}

    def _qa_check(self, args: dict) -> dict:
        case = self._case(args)
        key = f"qa:{case.case_id}:v{case.facts_version}"
        done = self._done_from(key)
        if done is not None:
            return {"passed": done.type == "qa.passed", "checks": done.payload["checks"],
                    "duplicate": True}
        self._require_status(case, "COMPUTED")
        texts = [*case.texts, case.subject]
        checks: list[dict] = []
        quote_problem = False
        for fact in ("authorized_shares", "issued_shares", "gross_assets", "stated_par"):
            if fact == "stated_par" and fact not in case.quotes:
                continue
            quote = case.quotes.get(fact, "")
            fact_key = "gross_assets_cents" if fact == "gross_assets" else fact
            stored = case.facts.get(fact_key)
            ok, note = True, "quote found verbatim"
            if not intake.quote_found(quote, texts):
                ok, note = False, "quote not found in the client's message"
            else:
                try:
                    parsed = intake._parse_fact(fact, quote)
                    if fact == "stated_par":
                        ok = Decimal(str(parsed)) == Decimal(str(stored))
                    else:
                        ok = parsed == stored
                    if not ok:
                        note = "quote parses to a different number"
                except EngineError:
                    ok, note = False, "quote no longer parses"
            quote_problem |= not ok
            checks.append({"field": fact, "ok": ok, "note": note})
        try:
            fee = FeeQuote(case.result["annual_report_fee_cents"], case.result["fee_source"])
            rerun = self._engine_result(case.facts, fee)
            same = rerun == case.result
            checks.append({"field": "total", "ok": same,
                           "note": "engine re-run matches" if same else "engine re-run differs"})
        except EngineError as err:
            same = False
            checks.append({"field": "total", "ok": False, "note": f"engine refused: {err}"})
        if case.unrealistic:
            checks.append({"field": "inputs", "ok": False,
                           "note": "unrealistic: " + ", ".join(case.unrealistic) + " (human review)"})
        passed = all(c["ok"] for c in checks)
        if passed:
            self._emit([NewEvent(type="qa.passed", actor="qa", case_id=case.case_id,
                                 provider="local", payload={"checks": checks},
                                 idempotency_key=key),
                        self._status(case, "QA_PASSED")])
            return {"passed": True, "checks": checks, "next": "send_memo"}
        events = [NewEvent(type="qa.failed", actor="qa", case_id=case.case_id, provider="local",
                           payload={"checks": checks}, idempotency_key=key),
                  NewEvent(type="case.status", actor="system", case_id=case.case_id,
                           payload={"from": "COMPUTED", "to": "QA_FAILED"})]
        reask = quote_problem and same and not case.unrealistic
        if reask:
            events.append(NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                   payload={"from": "QA_FAILED", "to": "INTAKE_PENDING"}))
        else:
            bad = ", ".join(c["field"] for c in checks if not c["ok"])
            events += [NewEvent(type="case.blocked", actor="qa", case_id=case.case_id,
                                payload={"reason": f"QA failed: {bad}", "needs": "human"}),
                       NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                payload={"from": "QA_FAILED", "to": "BLOCKED"})]
        self._emit(events)
        return {"passed": False, "checks": checks,
                "next": "ask_client" if reask else "firm_brief"}

    # ------------------------------------------------------------ accounting tools

    def _memo_body(self, case: Case) -> str:
        template, _source = load_memo_template(self.settings.memo_template_path)
        facts = case.facts or {}
        rnd_known = facts.get("gross_receipts_cents") is not None \
            and facts.get("years_with_receipts") is not None
        try:
            return render_memo(template, masked_name=case.masked_name, result=case.result,
                               form5472=bool(case.result.get("form5472_flag")),
                               rnd=bool(case.result.get("rnd_flag")), rnd_known=rnd_known)
        except TemplateError as err:
            raise Refused("template_invalid", str(err)) from err

    def _send_memo(self, args: dict) -> dict:
        case = self._case(args)
        key = f"memo:{case.case_id}:v{case.facts_version}"
        done = self._done_from(key)
        if done is not None:
            return {"message_id": done.payload["message_id"], "duplicate": True}
        # QA gate: a code check on case status, not a prompt.
        self._require_status(case, "QA_PASSED", code="qa_gate")
        if case.qa_version != case.facts_version or case.result_version != case.facts_version:
            raise Refused("qa_gate", "QA ran on older facts; run compute_tax and qa_check again")
        if not case.reply_to:
            raise Refused("no_reply_address", f"{case.case_id} has no reply address")
        body = self._memo_body(case)
        summary = (f"{self._first('email')}memo email: {case.masked_name}, "
                   f"{format_usd(case.result['authorized_method_cents'])} → "
                   f"{format_usd(case.result['total_cents'])}")
        self._gate("email", key, _cap(summary), case.case_id)
        try:
            sent = self._with_retry("memo email", lambda: self.ports.mail.send(
                OutMail(to=case.reply_to, subject=MEMO_SUBJECT, body=body), key))
        except ToolError as err:
            self._block(case, f"memo email failed: {err.code}", actor="acct")
            raise
        seconds = 0
        if case.facts_ts:
            seconds = max(0, int((self._now() - parse_ts(case.facts_ts)).total_seconds()))
        self._emit([
            NewEvent(type="memo.sent", actor="acct", case_id=case.case_id,
                     provider=self.ports.mail.provider,
                     payload={"to_masked": mask_email(case.reply_to), "subject": MEMO_SUBJECT,
                              "body": body, "message_id": sent.message_id,
                              "seconds_since_last_fact": seconds},
                     idempotency_key=key),
            self._status(case, "SENT"),
            NewEvent(type="crm.sync_requested", actor="system", case_id=case.case_id,
                     payload={"object": "deal", "stage": "memo_sent"},
                     idempotency_key=f"crm:{case.case_id}:memo_sent:v{case.facts_version}",
                     private={"sync_key": f"{case.case_id}:memo_sent:v{case.facts_version}"}),
        ])
        return {"message_id": sent.message_id, "seconds_since_last_fact": seconds,
                "next": "book_call"}

    def _phone_check(self, args: dict) -> dict:
        case = self._case(args)
        if case.memo_message_id is None:
            raise Refused("no_memo", "send_memo first")
        key = f"phone:{case.case_id}:v{case.memo_version}"
        done = self._done_from(key)
        if done is not None:
            return {**done.payload, "duplicate": True}
        memo = next((e for e in self.store.since(0)
                     if e.type == "memo.sent" and e.case_id == case.case_id
                     and e.payload["message_id"] == case.memo_message_id), None)
        body = memo.payload["body"] if memo else ""
        try:
            check = self.ports.device.open_and_capture(case.memo_message_id, body)
            payload = {"screenshot_url": check.screenshot_url, "rendered_ok": check.rendered_ok}
            status = "ok"
        except PortError as err:  # never blocks the case
            log.warning("phone check skipped: %s", err)
            payload, status = {"screenshot_url": None, "rendered_ok": False}, "skipped"
        self._emit([NewEvent(type="phone.checked", actor="qa", case_id=case.case_id,
                             provider=self.ports.device.provider, status=status,
                             payload=payload, idempotency_key=key)])
        return {**payload, "status": status}

    def _book_call(self, args: dict) -> dict:
        case = self._case(args)
        start = _str(args, "start", max_len=40)
        try:
            when = parse_ts(start)
            if when.tzinfo is None:
                raise ValueError
        except ValueError as err:
            raise Refused("bad_input", "start must be ISO 8601 with a timezone") from err
        key = f"call:{case.case_id}:v{case.memo_version}"
        done = self._done_from(key)
        if done is not None:
            return {**done.payload, "duplicate": True}
        self._require_status(case, "SENT")
        start_iso = iso_ts(when)
        self._gate("calendar_invite", key,
                   _cap(f"{self._first('calendar_invite')}calendar invite: "
                        f"{case.masked_name}, {start_iso}"), case.case_id)
        try:
            self._with_retry("calendar booking", lambda: self.ports.calendar.book(
                Slot(start=start_iso, minutes=15), case.reply_to,
                f"U-Tax review: {case.masked_name}", key))
        except ToolError as err:
            self._block(case, f"calendar booking failed: {err.code} (memo was sent)",
                        actor="intake")
            raise
        self._emit([
            NewEvent(type="call.booked", actor="intake", case_id=case.case_id,
                     provider=self.ports.calendar.provider,
                     payload={"start": start_iso, "minutes": 15,
                              "invitee_masked": mask_email(case.reply_to or "")},
                     idempotency_key=key),
            self._status(case, "CALL_BOOKED"),
            NewEvent(type="crm.sync_requested", actor="system", case_id=case.case_id,
                     payload={"object": "deal", "stage": "call_booked"},
                     idempotency_key=f"crm:{case.case_id}:call_booked:v{case.memo_version}",
                     private={"sync_key": f"{case.case_id}:call_booked:v{case.memo_version}"}),
        ])
        return {"start": start_iso, "minutes": 15}

    # ------------------------------------------------------------ marketing / hr / cost

    def _post_social(self, args: dict) -> dict:
        channel = _str(args, "channel", max_len=40).lower()
        text, _ = redact_ids(_str(args, "text", max_len=3000))
        image_url = _str(args, "image_url", required=False, max_len=2000)
        key = f"post:{_digest([channel, text, image_url])}"
        done = self._done_from(key)
        if done is not None:
            return {"url": done.payload["url"], "duplicate": True}
        self._gate("social_post", key,
                   _cap(f"{self._first('social_post')}post on {channel}: {text[:80]}"),
                   None)
        try:
            posted = self._with_retry("social post", lambda: self.ports.social.post(
                Post(channel=channel, text=text, image_url=image_url), key))
        except ToolError:
            self._emit([NewEvent(type="post.published", actor="mkt",
                                 provider=self.ports.social.provider, status="failed",
                                 payload={"channel": channel, "text": text, "url": None,
                                          "image_url": image_url})])
            raise
        self._emit([NewEvent(type="post.published", actor="mkt",
                             provider=self.ports.social.provider,
                             payload={"channel": channel, "text": text, "url": posted.url,
                                      "image_url": image_url}, idempotency_key=key)])
        return {"url": posted.url}

    def _draft_dm(self, args: dict) -> dict:
        label = _str(args, "recipient_label", max_len=120)
        text, _ = redact_ids(_str(args, "text", max_len=3000))
        key = f"dm:{_digest([label, text])}"
        self._emit([NewEvent(type="dm.drafted", actor="mkt",
                             payload={"recipient_label": label, "text": text},
                             idempotency_key=key)])
        return {"drafted": True, "note": "a human sends LinkedIn DMs; never automated"}

    def _send_dm_by_human(self, args: dict) -> dict:
        label = _str(args, "recipient_label", max_len=120)
        text, _ = redact_ids(_str(args, "text", max_len=3000))
        self._emit([NewEvent(type="dm.sent_by_human", actor="ceo",
                             payload={"recipient_label": label, "text": text},
                             idempotency_key=f"dmsent:{_digest([label, text])}"),
                    NewEvent(type="touch.operating", actor="ceo",
                             payload={"note": f"sent DM to {label}"[:120], "approval_id": None})])
        return {"sent_by_human": True}

    def _hr_record(self, args: dict) -> dict:
        stage = _one_of(args, "stage", ("applicant", "screened", "shortlisted", "declined"))
        candidate_id = _str(args, "candidate_id", max_len=64)
        name = _str(args, "name_masked", max_len=80)
        note, _ = redact_ids(_str(args, "note", required=False, max_len=500) or "")
        self._emit([NewEvent(type=f"hr.{stage}", actor="hr", provider="local",
                             payload={"candidate_id": candidate_id, "name_masked": name,
                                      "note": note},
                             idempotency_key=f"hr:{stage}:{candidate_id}")])
        return {"candidate_id": candidate_id, "stage": stage}

    def _hr_hire(self, args: dict) -> dict:
        """The human makes the final call (legal limit): only the CEO's console hires."""
        candidate_id = _str(args, "candidate_id", max_len=64)
        cand = self.state.candidates.get(candidate_id)
        if cand is None:
            raise Refused("not_found", f"no candidate {candidate_id}")
        note = _str(args, "note", required=False, max_len=300) or "hired by the CEO"
        self._emit([NewEvent(type="hr.hired", actor="ceo", provider="local",
                             payload={"candidate_id": candidate_id,
                                      "name_masked": cand["name_masked"], "note": note},
                             idempotency_key=f"hr:hired:{candidate_id}"),
                    NewEvent(type="touch.operating", actor="ceo",
                             payload={"note": f"hire {cand['name_masked']}", "approval_id": None})])
        return {"candidate_id": candidate_id, "stage": "hired"}

    _AGENTS = ("cos", "intake", "tax", "qa", "acct", "mkt", "hr")

    def _book_cost(self, args: dict) -> dict:
        provider = _str(args, "provider", max_len=40).lower()
        if provider not in PROVIDERS:
            raise Refused("bad_input", f"unknown provider {provider!r}")
        model = _str(args, "model", required=False, max_len=120) or "unknown"
        tokens_in, tokens_out = _int(args, "tokens_in"), _int(args, "tokens_out")
        agent = args.get("agent") or "system"
        if agent not in (*self._AGENTS, "system"):
            raise Refused("bad_input", f"agent must be one of {', '.join(self._AGENTS)}")
        raw_cost = args.get("cost_usd")
        try:
            cost = parse_usd(raw_cost) if raw_cost not in (None, "") else None
        except (TypeError, ValueError, InvalidOperation) as err:
            raise Refused("bad_input", "cost_usd must be a decimal string like \"0.0034\"") from err
        estimated = cost is None
        if cost is None:
            cost = estimate_usd(model, tokens_in, tokens_out)
        case_id = _str(args, "case_id", required=False, max_len=64)
        if case_id and case_id not in self.state.cases:
            case_id = None
        key = args.get("idempotency_key")
        self._emit([NewEvent(type="cost.booked", actor=agent, case_id=case_id, provider=provider,
                             payload={"provider": provider, "model": model,
                                      "estimated": estimated},
                             tokens_in=tokens_in, tokens_out=tokens_out,
                             cost_usd=usd_string(cost),
                             idempotency_key=f"cost:{key}" if isinstance(key, str) and key else None)])
        return {"cost_usd": usd_string(cost), "estimated": estimated}

    # ------------------------------------------------------------ outbox (CRM mirror)

    def _drain_outbox(self) -> None:
        """Mirror jobs (HubSpot): `crm.sync_requested` without `crm.synced`. Never blocks a client."""
        for sync_key, job in list(self.state.crm_pending.items()):
            case = self.state.cases.get(job["case_id"])
            if case is None:
                continue
            try:
                self.ports.crm.upsert_contact(CrmContact(key=case.case_id, label=case.masked_name))
                self.ports.crm.set_stage(f"deal:{case.case_id}", job["stage"])
            except PortError as err:
                log.warning("crm sync %s failed: %s", sync_key, err)
                if sync_key not in self.state.crm_failed:
                    self._emit([NewEvent(type="crm.synced", actor="system", case_id=case.case_id,
                                         provider=self.ports.crm.provider, status="failed",
                                         payload={"object": job["object"], "stage": job["stage"]},
                                         idempotency_key=f"crmfail:{sync_key}",
                                         private={"sync_key": sync_key})])
                continue
            self._emit([NewEvent(type="crm.synced", actor="system", case_id=case.case_id,
                                 provider=self.ports.crm.provider,
                                 payload={"object": job["object"], "stage": job["stage"]},
                                 idempotency_key=f"crmok:{sync_key}",
                                 private={"sync_key": sync_key})])


_FEE_RE = re.compile(r"(?is)annual\s+report[^$]{0,120}?\$\s?(\d{1,4}(?:\.\d{2})?)")


def parse_fee_cents(html: str) -> int | None:
    """The annual report filing fee from the Delaware fee page (or its snapshot)."""
    text = re.sub(r"<[^>]+>", " ", html)
    match = _FEE_RE.search(text)
    if not match:
        return None
    return int(Decimal(match.group(1)) * 100)
