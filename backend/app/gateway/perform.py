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
from app.gateway.masking import (
    company_label,
    has_email,
    has_phone,
    MAX_BODY_CHARS,
    TAX_WORDS,
    agent_text,
    html_to_text,
    mail_identity,
    mask_email,
    mask_spoken_emails,
    mask_text,
    redact_ids,
    sender_words,
    strip_contacts,
)
from app.gateway.ports import (
    CrmContact,
    InMail,
    OutMail,
    PortError,
    Ports,
    Post,
    RateLimited,
    Rejected,
    Slot,
    Unavailable,
)
from app.gateway.ports.store import EventStorePort, EventWriteError
from app.gateway.pricing import estimate_usd, parse_usd, usd_string
from app.gateway.projections import TRANSITIONS, TRUST_TYPES, Case, State, rebuild
from app.gateway.projections import money_text
from app.gateway.settings import Settings
from app.gateway.templates import (
    MEMO_REF,
    with_reference,
    FACT_LABELS,
    MEMO_SUBJECT,
    NOT_ELIGIBLE_REASONS,
    DECISION_SUBJECT,
    QUESTIONNAIRE_SUBJECT,
    TemplateError,
    Templates,
    ask_values,
    ceo_update_values,
    memo_values,
)

log = logging.getLogger("utax.perform")

FEE_CACHE_SECONDS = 6 * 3600
FEE_REFRESH_SECONDS = 5 * 3600  # v1.7 §6f: refreshed before the 6 h TTL, outside the lock
PHONE_ATTEMPTS = 3  # v1.7: per memo version; only an ok result ends them
PHONE_RUN_TIMEOUT = 10 * 60  # a cloud-phone task still running after this is cancelled
LAW_SEARCHES_PER_HOUR = 20  # v1.7, firm-wide
AGENTS = ("cos", "intake", "tax", "qa", "acct", "mkt", "hr")
# Contract §6 "Agent" column: the token's actor may call only these (get_case: any).
TOOL_ACTORS: dict[str, tuple[str, ...]] = {
    "list_open_cases": ("cos",), "get_case": AGENTS, "extract_facts": ("intake",),
    "ask_client": ("intake",), "mark_not_eligible": ("intake",), "law_search": ("tax",),
    "compute_tax": ("tax",), "qa_check": ("qa",), "send_memo": ("acct",),
    "book_call": ("intake",), "phone_check": ("qa",), "post_social": ("mkt",),
    "draft_dm": ("mkt",), "hr_record": ("hr",), "firm_brief": ("cos",),
    "get_candidate": ("hr",), "flag_case": ("qa",),  # v1.4
    "buy_tool": ("mkt",),  # v1.7.3: Monid data calls, allowlisted and capped
}
POST_MAX_CHARS = 255
X_MAX_CHARS = 280
MEMOS_PER_ADDRESS_PER_DAY = 3
FLAG_MAILS_PER_HOUR = 1
SLOT_COUNT = 3
SLOT_MINUTES = 15
SLOT_LEAD = timedelta(hours=2)
SLOT_DAY_UTC = (17, 24)  # 9:00-16:00 Pacific, in UTC hours [start, end)
# Dollar amounts a post may name (the public headline numbers); anything else is refused.
POST_ALLOWED_AMOUNTS = {"$85,165", "$400", "$450", "$50"}
_POST_AMOUNT = re.compile(r"\$\s?\d[\d, ]*(?:\.\d+)?\s?(?:[kKmMbB]\b|mm\b|bn\b)?")
_CASE_REF = re.compile(r"(?i)\bcase[_ -]?\d+\b|\bteam\s*#?\s*\d+\b")
_WORD_AMOUNT = re.compile(
    r"(?i)(?:\b(?:usd|eur|gbp)\s*\d[\d, ]*|\d[\d,]*(?:\.\d+)?\s*(?:k|m|mm|bn)?\s*"
    r"(?:dollars|bucks|usd|eur|euros?)\b|[\u20ac\u00a3]\s?\d|\d[\d,.]*\s?[$\u20ac\u00a3]"
    r"|\$\s?\d[\d,.]*\s+(?:thousand|million|billion)\b)")
_TEAM_WORD = re.compile(r"(?i)\bteam\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|"
                        r"eleven|twelve|[a-z]+teen|twenty|thirty|forty|fifty)\b")
_BARE_PHONE = re.compile(r"(?<!\d)\d{10,11}(?!\d)")
POST_CHANNELS = ("x", "linkedin")
SEND_ATTEMPTS = 3  # first try + 2 retries (Error & Rescue Map)
MAX_RETRY_SLEEP = 5.0
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_AUTO_SUBJECT = re.compile(r"(?i)^\s*(auto(matic)?[ -]?(reply|response)\b|auto:|out of (the )?office\b"
                           r"|autoreply\b|vacation\b|abwesenheit\b"
                           # calendar RSVPs to our review-call invitation (D27)
                           r"|(accepted|declined|tentative|tentatively accepted|"
                           r"updated invitation|invitation)\s*:)")
CLIENT_REPLY_WAKES_PER_DAY = 3
MAX_REVISIONS = 2


def new_text(body: str) -> str:
    """v1.6.2: only what the client newly wrote: no quoted lines, no signature, no
    "On ... wrote:" attribution (any length, or wrapped onto two lines), no Outlook
    "From:/Sent:" header block or "Original Message" divider."""
    lines = body.splitlines()
    out = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(">"):
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if stripped in ("--", "-- ") \
                or re.match(r"(?i)^on\b.*\bwrote:\s*$", stripped) \
                or (re.match(r"(?i)^on\b", stripped) and re.search(r"(?i)\bwrote:\s*$", nxt)) \
                or re.match(r"(?i)^-+\s*original message\s*-+$", stripped) \
                or (re.match(r"(?i)^from:\s", stripped)
                    and re.match(r"(?i)^(sent|date|to):\s", nxt)):
            break
        out.append(line)
    return "\n".join(out)


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


def clip(text: str, limit: int) -> str:
    """Cut at the last word boundary before `limit`, with an ellipsis: never mid-word."""
    text = text.strip()
    if len(text) <= limit:
        return text
    if limit < 2:
        return text[:max(limit, 0)]
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    if space >= limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:.-") + "…"


def tidy_lines(text: str) -> str:
    """Three or more newlines become one blank line; trailing spaces go."""
    text = "\n".join(line.rstrip(" \t") for line in text.split("\n"))  # linear
    return re.sub(r"\n{3,}", "\n\n", text)


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
        # v1.7 §6f: slow sponsor I/O never runs under self._lock
        self._phone_jobs: dict[str, dict[str, Any]] = {}
        self._jobs_lock = threading.Lock()
        self._fee_refresh_lock = threading.Lock()
        self._epoch: str | None = None
        self._booted_at = self._now()  # the first CEO digest waits a full period from here
        self._law_inflight = 0
        self._outbox_paused_until = 0.0  # a CRM rate limit pauses the whole outbox  # law searches reserved but not yet recorded (cap, §6f)
        self._fee_tried_at = 0.0
        self.state: State = rebuild(store.since(0))
        self.templates = Templates(settings.templates_dir, settings.memo_template_path)
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
            "get_candidate": self._get_candidate,
            "flag_case": self._flag_case,
            "buy_tool": self._buy_tool,
            "firm_brief": self._firm_brief,
            # gateway / control actions (contract §5, §6a-c): never callable by an agent token
            "inject_email": self._inject_email,
            "receive_email": self._receive_email,
            "voice": self._voice,
            "approve": self._approve,
            "ceo_stop": self._ceo_stop,
            "unblock": self._unblock,
            "stall_sweep": self._stall_sweep,
            "ceo_brief": self._ceo_brief,
            "ceo_digest": self._ceo_digest,
            "book_spend": self._book_spend,
            "send_dm_by_human": self._send_dm_by_human,
            "set_trust": self._set_trust,
            "candidate_decision": self._candidate_decision,
            "retry_wakes": self._retry_wakes_action,
        }
        self._wakers: list[Callable[[str], None]] = []
        self._pending_wakes: list[str] = []
        # reads that touch a slow port run before the lock is taken (contract §6f)
        self._slow_reads: dict[str, Callable[[dict], dict]] = {"law_search": self._law_fetch,
                                                               "buy_tool": self._buy_fetch}
        self._restore_toolbuy()
        self._outbox_tries: dict[str, tuple[int, float]] = {}
        self._outbox_inflight: set[str] = set()
        self._digest_backoff_until = 0.0
        # failed wake deliveries: (message, waker, attempt, due monotonic time)
        self._wake_retries: list[tuple[str, Callable[[str], None], int, float]] = []

    READ_ONLY = frozenset({"list_open_cases", "get_case"})

    # ------------------------------------------------------------ plumbing

    def subscribe(self, listener: Callable[[Event], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def on_wake(self, waker: Callable[[str], None]) -> Callable[[], None]:
        """Contract §6b: called with each wake-up message ("New case case_7", ...)."""
        self._wakers.append(waker)
        return lambda: self._wakers.remove(waker) if waker in self._wakers else None

    def _wake(self, message: str) -> None:
        self._pending_wakes.append(message)

    WAKE_BACKOFF = (5, 15, 60, 300)  # seconds; then every 5 minutes until it lands

    def _deliver_wakes(self, wakes: list[str]) -> None:
        for message in wakes:
            log.info("WAKE %s", message)
            for waker in list(self._wakers):
                self._try_wake(message, waker, attempt=0)

    def _try_wake(self, message: str, waker: Callable[[str], None], *, attempt: int) -> None:
        try:
            waker(message)
        except Exception:  # noqa: BLE001 - a dead waker never blocks the firm; retried
            delay = self.WAKE_BACKOFF[min(attempt, len(self.WAKE_BACKOFF) - 1)]
            log.exception("waker failed for %r (attempt %d); retry in %ss", message,
                          attempt + 1, delay)
            with self._lock:
                self._wake_retries.append((message, waker, attempt + 1,
                                           time.monotonic() + delay))

    def retry_wakes(self) -> int:
        """Re-deliver failed wakes whose backoff has passed (the server timer calls this)."""
        now = time.monotonic()
        with self._lock:
            due = [w for w in self._wake_retries if w[3] <= now]
            self._wake_retries = [w for w in self._wake_retries if w[3] > now]
        for message, waker, attempt, _ in due:
            if waker in self._wakers:
                self._try_wake(message, waker, attempt=attempt)
        return len(due)

    def pending_wake_retries(self) -> list[str]:
        with self._lock:
            return [w[0] for w in self._wake_retries]

    def retry_questionnaires(self) -> list[str]:
        """Questionnaires that couldn't go out (paused, failed) are retried by the tick."""
        with self._lock:
            todo = [cid for cid, c in self.state.candidates.items()
                    if c.get("reply_to") and not c.get("questionnaire_sent")
                    and c.get("stage") == "applicant"
                    and self.state.trust["email"] == "AUTO"]
            return [cid for cid in todo if self._send_questionnaire(cid) == "sent"]

    def retry_decision_mails(self) -> list[str]:
        """Decision mails that couldn't go out (paused, failed) are retried by the tick;
        the mail port's key makes a resend of a delivered one a no-op."""
        with self._lock:
            if self.state.trust["email"] != "AUTO":
                return []
            todo = [cid for cid, c in self.state.candidates.items()
                    if c.get("stage") in ("hired", "declined") and c.get("reply_to")
                    and not c.get("decision_mailed")]
            return [cid for cid in todo if self._send_decision_mail(cid) == "sent"]

    def _retry_wakes_action(self, args: dict) -> dict:
        return {"pending": self.pending_wake_retries()}

    def boot_wakes(self) -> list[str]:
        """Wake-ups owed after a restart (wakes live in memory only): every open case and
        every candidate still at "applicant". The server delivers these once at startup."""
        with self._lock:
            owed = [f"{'FAST PATH' if c.priority else 'New case'} {c.case_id}"
                    for c in sorted(self.state.cases.values(), key=lambda c: c.number)
                    if c.status in ("NEW", "INTAKE_PENDING", "READY", "COMPUTED", "QA_PASSED")
            or (c.status == "SENT" and not c.call)]
            owed += [f"New applicant {cid}" for cid, cand in sorted(self.state.candidates.items())
                     if cand.get("stage") == "applicant"]
        self._deliver_wakes(owed)
        return owed

    HIDDEN = "[hidden: flagged by QA]"

    def flagged_injection(self, case_id: str | None) -> bool:
        case = self.state.cases.get(case_id) if case_id else None
        return case is not None and "possible_injection" in case.flags

    def public_event(self, event: Event, *, admin: bool = False) -> dict[str, Any]:
        """An event as theater readers see it (v1.7.1): once QA flags a case as a possible
        injection, every event of that case, earlier ones too, has its subject and body
        replaced. The admin token still sees the originals."""
        out = event.public()
        if admin or not self.flagged_injection(event.case_id):
            return out
        payload = dict(out["payload"])
        for key in ("subject", "body"):
            if key in payload:
                payload[key] = self.HIDDEN
        # the same rule for every other field that carries the client's words
        if event.type == "voice.command" and "text" in payload:
            payload["text"] = self.HIDDEN
        if isinstance(payload.get("quotes"), dict):
            payload["quotes"] = {k: self.HIDDEN for k in payload["quotes"]}
        return {**out, "payload": payload}

    def public_state(self, *, admin: bool = False) -> dict[str, Any]:
        """GET /api/state, read under the lock so a concurrent write can't tear it. The
        per-candidate list is admin-only (v1.6.1); the theater gets counts."""
        with self._lock:
            state = self.state.public()
        if not admin:
            state["hiring"] = {k: v for k, v in state["hiring"].items() if k != "candidates"}
        return state

    def perform(self, name: str, args: dict | None = None, *, actor: str | None = None) -> ToolResult:
        """Run one action. `actor` is set for agent tool calls (from the MCP token) and
        limits them to that agent's tools; control actions are never open to an agent."""
        handler = self._handlers.get(name)
        if handler is None:
            return ToolResult("refused", error={"code": "unknown_action",
                                                "message": f"no action named {name!r}"})
        if actor is not None and actor not in TOOL_ACTORS.get(name, ()):
            return ToolResult("refused", error={"code": "tool_not_allowed",
                                                "message": f"{actor} may not call {name}"})
        args = dict(args or {})
        args.pop("_actor", None)  # never caller-supplied
        args.pop("_prefetch", None)
        if actor is not None:
            args["_actor"] = actor
        slow = self._slow_reads.get(name)
        if slow is not None:
            try:
                args["_prefetch"] = slow(args)  # outside the lock
            except Exception:  # noqa: BLE001 - a broken adapter must not kill the server
                log.exception("%s failed unexpectedly before the lock", name)
                return ToolResult("error", error={"code": "internal_error",
                                                  "message": f"{name} failed unexpectedly"})
        try:
            result, wakes = self._perform_locked(name, handler, args)
        finally:
            if args.get("_law_reserved"):
                with self._lock:
                    self._law_inflight -= 1
        if name == "phone_check" and result.status == "done" and result.data.get("pending"):
            try:
                self.advance_phone_jobs()  # outside the lock: a local device is done now
            except Exception:  # noqa: BLE001 - the tick tries again
                log.exception("phone job failed to advance")
        # Outside the lock: a slow Naïve call or CRM mirror never blocks the next write.
        self._deliver_wakes(wakes)
        return result

    def _perform_locked(self, name: str, handler: Callable[[dict], dict],
                        args: dict) -> tuple[ToolResult, list[str]]:
        with self._lock:
            self._pending_wakes = []
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
                # A port may already have acted (its key makes a retry safe), so don't
                # claim nothing happened.
                result = ToolResult("error", error={
                    "code": "store_unavailable",
                    "message": "event log unavailable; retry the same call when it is back"})
            except Exception as err:  # noqa: BLE001 - a broken adapter must not kill the server
                log.exception("%s failed unexpectedly", name)
                result = ToolResult("error", error={"code": "internal_error",
                                                    "message": f"{type(err).__name__} in {name}"})
            wakes, self._pending_wakes = self._pending_wakes, []
            return result, wakes

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
            # A "duplicate" newer than our projection landed on an earlier attempt whose
            # reply we never saw (a commit that reported failure): project it now.
            if not item.duplicate or item.event.id > self.state.last_event_id:
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

    @staticmethod
    def _fact_texts(case: Case) -> list[str]:
        """What facts are judged on, oldest first: the subject, then what each message newly
        says (quoted originals cut), so a later correction supersedes cleanly."""
        return [case.agent_subject or case.subject,
                *(new_text(t) for t in (case.agent_texts or case.texts))]

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

    def _block(self, case: Case, reason: str, *, notice_key: str | None = None) -> list[Event]:
        events = [NewEvent(type="case.blocked", actor="system", case_id=case.case_id,
                           payload={"reason": reason, "needs": "human"})]
        status = self._status(case, "BLOCKED") if "BLOCKED" in TRANSITIONS.get(case.status, ()) \
            or case.status == "BLOCKED" else None
        if status:
            events.append(status)
        out = self._emit(events)
        self._notify_ceo("blocked", f"Blocked: {case.masked_name}", f"{case.masked_name}: {reason}",
                         key=notice_key or f"ceo:blocked:{out[0].id}")
        return out

    def _notify_ceo(self, reason: str, subject: str, summary: str, *, key: str,
                    channel: str = "email", extra: list[NewEvent] | None = None,
                    body: str | None = None) -> bool:
        """CEO updates (contract §5, D24): email CEO_EMAIL (with retries), then log
        `ceo.notified` (plus any `extra` events in the same batch). Never fails the action
        that triggered it; returns whether the notice went out."""
        if channel == "email":
            if not self.settings.ceo_email:
                return False
            try:
                self._with_retry("CEO email", lambda: self.ports.mail.send(
                    OutMail(to=self.settings.ceo_email, subject=f"U-Tax: {subject}",
                            body=body or self.templates.render(
                                "ceo-update-email", ceo_update_values(
                                    reason, subject, summary, self.settings.ceo_console_url))),
                    key))
            except ToolError as err:
                log.warning("CEO email failed (%s): %s", reason, err)
                return False
        try:
            self._emit([*(extra or []),
                        NewEvent(type="ceo.notified", actor="cos",
                                 provider=None if channel == "login" else self.ports.mail.provider,
                                 payload={"channel": channel, "subject": clip(subject, 120),
                                          "summary": clip(summary, 500), "reason": reason},
                                 idempotency_key=key)])
        except EventWriteError as err:  # the mail went (its key dedupes a resend)
            log.error("ceo.notified not logged: %s", err)
            return False
        return True

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

    PUBLIC_CAP_PER_HOUR = 10

    def _gate(self, action_type: str, action_key: str, summary: str, case_id: str | None,
              *, public: bool = False) -> None:
        """Trust earned: ASK needs an approval, AUTO runs (until a cap), PAUSED needs a human.

        `public`: the action serves a case opened from the anonymous /talk page. Those sends
        have their own hourly bucket: when it is full only public sends are refused, and the
        firm-wide trust never pauses because of anonymous traffic.
        """
        if public and self.state.trust[action_type] == "AUTO":
            hour_ago = self._now() - timedelta(hours=1)
            recent = [ts for ts in self.state.cap_log.get(f"{action_type}_public", [])
                      if parse_ts(ts) > hour_ago]
            if len(recent) >= self.PUBLIC_CAP_PER_HOUR:
                raise Refused("public_cap", "public /talk sends are at their hourly cap; "
                                            "try again later", {"action_type": action_type})
            return
        if self.state.trust[action_type] == "AUTO":
            reason = self._cap_hit(action_type)
            if reason is None:
                return
            written = self._emit([NewEvent(type="trust.changed", actor="system",
                                           payload={"action_type": action_type, "from": "AUTO",
                                                    "to": "PAUSED", "reason": reason})])
            self._notify_ceo("paused", f"{action_type} paused ({reason})",
                             f"{action_type} sends are paused ({reason}). Resume it in the "
                             "CEO console when you're ready.",
                             key=f"ceo:paused:{written[0].id}")
        if self.state.trust[action_type] == "PAUSED":
            # v1.4: a pause (cap, budget or the CEO's standing order) holds until resumed
            raise Refused("paused", f"{action_type} is paused; the CEO resumes it "
                                    "(POST /api/trust)", {"action_type": action_type})
        approval_id = self.state.approval_by_key.get(action_key)
        if approval_id:
            approval = self.state.approvals[approval_id]
            if approval.decision == "allow":
                return
            if approval.decision == "deny":
                raise Refused("denied", "the CEO said no to this action", {"approval_id": approval_id})
            if approval.decision is None:
                raise NeedsApproval(approval_id, approval.summary)
            # "superseded" (the type went AUTO, then back to ASK): ask again
        approval_id = f"apr_{len(self.state.approvals) + 1}"
        self._emit([NewEvent(type="approval.requested", actor="system", case_id=case_id,
                             payload={"approval_id": approval_id, "action_type": action_type,
                                      "summary": summary},
                             idempotency_key=f"approval:{action_key}:{approval_id}",
                             private={"action_key": action_key})])
        if not (public and self._recent_public_notice()):  # anonymous traffic: throttled
            self._notify_ceo("approval", clip(f"Approve? {summary}", 120),
                             f"{approval_id}: {summary}",
                             key=f"ceo:approval:{approval_id}")
        raise NeedsApproval(approval_id, summary)

    def _recent_public_notice(self) -> bool:
        cutoff = self._now() - timedelta(minutes=10)
        return any(parse_ts(ts) > cutoff for ts in self.state.public_notice_log)

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
                # short: this runs under the gateway lock
                self.sleep(min(err.retry_after or 0.5 * 2**attempt, MAX_RETRY_SLEEP))
        raise ToolError(last.code if last else "port_error", f"{what} failed: {last}")

    def _fee(self) -> FeeQuote:
        """The annual report fee, under the lock: the warmed cache or the saved snapshot,
        never a fetch (v1.7 §6f: refresh_fee() fetches, outside the lock)."""
        cached = self._fee_cache
        if cached and time.monotonic() - cached[0] < FEE_CACHE_SECONDS:
            return cached[1]
        cents = self._snapshot_fee_cents()
        if cents is None:
            raise Refused("fee_unknown", "annual report fee unknown: no page and no snapshot")
        return FeeQuote(cents, "snapshot")

    def _snapshot_fee_cents(self) -> int | None:
        path = self.settings.fixtures_dir / "snapshots" / "delaware-fees.html"
        return parse_fee_cents(path.read_text(encoding="utf-8")) if path.exists() else None

    def refresh_fee(self, *, force: bool = False) -> None:
        """Fetch the Delaware fee page outside the lock (at boot, then from the tick before
        the 6 h TTL). A live fee that differs from the snapshot is not used: the snapshot
        stays and the CEO is told once per differing value."""
        if not self._fee_refresh_lock.acquire(blocking=False):
            return  # another refresh is running
        try:
            cached = self._fee_cache
            if cached and not force and time.monotonic() - cached[0] < FEE_REFRESH_SECONDS:
                return
            if not force and time.monotonic() - self._fee_tried_at < 600:
                return  # a failed refresh retries every 10 minutes, not every tick
            self._fee_tried_at = time.monotonic()
            snapshot = self._snapshot_fee_cents()
            live, source, ran = None, "snapshot", False
            try:
                page = self.ports.scrape.fetch_page(DELAWARE_FEE_URL)
                live, source, ran = parse_fee_cents(page.html), page.source, True
            except (Unavailable, Rejected) as err:  # the run may have started: it is paid
                ran = True
                log.warning("fee page fetch failed (%s); the snapshot stays", err.code)
            except PortError as err:  # not configured, rate limited: nothing ran
                log.warning("fee page fetch failed (%s); the snapshot stays", err.code)
            if ran:
                self._book_run(self.ports.scrape.provider, "page", None)
            if live is not None and (snapshot is None or live == snapshot):
                quote = FeeQuote(live, source)
            elif snapshot is not None:
                quote = FeeQuote(snapshot, "snapshot")
                if live is not None:  # v1.7: never quote an unchecked fee change
                    with self._lock:
                        self._notify_ceo(
                            "flagged", "Fee page differs from snapshot",
                            f"fee page differs from snapshot: the Delaware page shows "
                            f"{format_usd(live)}, the saved snapshot {format_usd(snapshot)}. "
                            "Memos use the snapshot until a human checks.",
                            key=f"ceo:fee:{live}:{snapshot}")
            else:
                return  # nothing usable: _fee() refuses until a page or snapshot exists
            self._fee_cache = (time.monotonic(), quote)
        finally:
            self._fee_refresh_lock.release()

    def _book_run(self, provider: str, kind: str, case_id: str | None,
                  key: str | None = None) -> None:
        """v1.7 §6a: one estimate per sponsor run (outside the lock). Local runs cost nothing."""
        from app.gateway.pricing import APIFY_RUN_USD, MOBILERUN_RUN_USD

        price = {"apify": APIFY_RUN_USD, "mobilerun": MOBILERUN_RUN_USD}.get(provider)
        if price is None:
            return
        args = {"provider": provider, "model": f"{provider}-{kind}", "cost_usd": str(price),
                "estimated": True, "case_id": case_id}
        if key:
            args["idempotency_key"] = key
        result = self.perform("book_spend", args)
        if result.status != "done":
            log.warning("spend estimate for a %s run not booked: %s", provider, result.error)

    # ------------------------------------------------------------ inbound

    def _inject_email(self, args: dict) -> dict:
        if self.settings.adapters.get("mail") != "local":
            raise Refused("not_local", "inject-email only works with ADAPTER_MAIL=local")
        sender = _str(args, "from", max_len=320)
        if not _EMAIL_RE.match(sender):
            raise Refused("bad_input", "from must be an email address")
        inbox = _one_of(args, "inbox", ("clients", "hiring"), default="clients")
        mail = self.ports.mail.parse_inbound({
            "from": sender, "subject": _str(args, "subject", required=False, max_len=300) or "",
            "body": _str(args, "body", max_len=20000), "message_id": args.get("message_id"),
        })
        return self._route_inbound(mail, inbox)

    def _receive_email(self, args: dict) -> dict:
        """A parsed inbound mail from a MailPort (webhook Saturday, .eml files tonight)."""
        mail = args.get("mail")
        if not isinstance(mail, InMail):
            raise Refused("bad_input", "mail must be an InMail")
        return self._route_inbound(mail, _one_of(args, "inbox", ("clients", "hiring"),
                                                 default="clients"))

    def _route_inbound(self, mail: InMail, inbox: str) -> dict:
        """Contract §6c: two inboxes. Checkups open cases; applicants go to HR."""
        own = {mail_identity(a) for a in (self.settings.ceo_email, self.settings.inbox_clients,
                                          self.settings.inbox_hiring) if a}
        if mail_identity(mail.sender) in own:  # v1.6 mail-loop guard (+tags, Gmail dots)
            log.warning("dropped mail from the firm's own address %s", mail.sender)
            return {"dropped": True, "reason": "own address"}
        if mail.auto_reply or _AUTO_SUBJECT.search(mail.subject or ""):  # v1.6.2
            log.info("dropped an auto-reply from %s", mail.sender)
            return {"dropped": True, "reason": "auto-reply"}
        mail = InMail(message_id=mail.message_id, sender=mail.sender, subject=mail.subject,
                      body=tidy_lines(html_to_text(mail.body[:MAX_BODY_CHARS])))
        if inbox == "hiring":
            return self._receive_applicant(mail)
        result = self._receive(mail, provider=self.ports.mail.provider)
        if result.get("client_replied"):
            self._wake(f"Client replied {result['case_id']}")
        elif not result.get("duplicate") and not result.get("quiet") \
                and not result.get("blocked"):
            self._wake(f"New case {result['case_id']}")
        return result

    # ------------------------------------------------------------ hiring inbox (§6c)

    def _candidate_for(self, sender_key: str) -> tuple[str, str, bool]:
        for cid, cand in self.state.candidates.items():
            if cand.get("sender_key") == sender_key:
                return cid, cand["name_masked"], False
        numbers = [int(c.rsplit("_", 1)[1]) for c in self.state.candidates
                   if re.fullmatch(r"cand_\d+", c)]
        number = max(numbers, default=0) + 1  # never collide with an id already in use
        n, letters = number, ""
        while n:
            n, rem = divmod(n - 1, 26)
            letters = chr(65 + rem) + letters
        return f"cand_{number}", f"Candidate {letters}", True

    def _receive_applicant(self, mail: InMail) -> dict:
        key = f"applicant:{mail.message_id}"
        seen = self._done_from(key)
        if seen is not None:
            return {"candidate_id": seen.payload["candidate_id"], "duplicate": True}
        sender_key = mail.sender.strip().lower()
        candidate_id, name_masked, new = self._candidate_for(sender_key)
        known = self.state.candidates.get(candidate_id, {})
        first_answer = not new and not known.get("answers")
        # The public note is the stated credentials only (the raw text can carry a name or
        # signature); the redacted, contact-free text stays private for the HR screen.
        raw = re.sub(r"\s+", " ", strip_contacts(mail.body)).strip()[:2000]
        note = credentials_summary(raw) if new else "replied to the questionnaire"
        self._emit([NewEvent(type="hr.applicant", actor="hr", provider=self.ports.mail.provider,
                             payload={"candidate_id": candidate_id, "name_masked": name_masked,
                                      "note": note},
                             idempotency_key=key,
                             private={"reply_to": mail.sender.strip(), "sender_key": sender_key,
                                      "message_id": mail.message_id,
                                      "text": mask_text(raw, masked_name=name_masked,
                                                        company=None,
                                                        hide_words=sender_words(sender_key))})])
        questionnaire = "skipped"
        if new:
            questionnaire = self._send_questionnaire(candidate_id)
            self._wake(f"New applicant {candidate_id}")
        elif first_answer:
            self._wake(f"Applicant answered {candidate_id}")  # v1.5: now HR has answers
        return {"candidate_id": candidate_id, "name_masked": name_masked, "new": new,
                "questionnaire": questionnaire}

    def _send_questionnaire(self, candidate_id: str) -> str:
        """Auto-reply (trust type email). If email is still ASK, the approval sends it later."""
        cand = self.state.candidates[candidate_id]
        key = f"questionnaire:{candidate_id}"
        if cand.get("questionnaire_sent"):
            return "sent"
        try:
            self._gate("email", key, _cap(f"{self._first('email')}hiring questionnaire: "
                                          f"{cand['name_masked']}"), None)
        except NeedsApproval:
            return "needs_approval"
        except Refused as err:  # denied by the CEO, or email is paused
            return err.code
        try:
            sent = self._with_retry("questionnaire email", lambda: self.ports.mail.send(
                OutMail(to=cand["reply_to"], subject=QUESTIONNAIRE_SUBJECT,
                        body=self.templates.render("hr-questionnaire", {}), inbox="hiring"),
                key))
        except ToolError as err:
            log.warning("questionnaire to %s failed: %s", candidate_id, err)
            return "failed"
        # contract v1.4.1: the auto-reply is an event, and it counts against the email cap
        self._emit([NewEvent(type="hr.questionnaire_sent", actor="system",
                             provider=self.ports.mail.provider,
                             payload={"candidate_id": candidate_id,
                                      "name_masked": cand["name_masked"],
                                      "note": "AI-screen questionnaire (disclosed)"},
                             idempotency_key=f"qsent:{candidate_id}",
                             private={"message_id": sent.message_id})])
        return "sent"

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
                     "subject": mask_text(subject, masked_name=masked_name, company=company,
                                          hide_words=sender_words(sender_key)),
                     "body": mask_text(body, masked_name=masked_name, company=company,
                                       hide_words=sender_words(sender_key))},
            idempotency_key=key,
            private={"reply_to": mail.sender.strip(), "sender_key": sender_key,
                     "masked_name": masked_name, "company": company,
                     "message_id": mail.message_id, "redactions": redacted + sub_redacted,
                     "agent_text": agent_text(body), "agent_subject": agent_text(subject)},
        )]
        result = {"case_id": case_id, "masked_name": masked_name,
                  "redacted": redacted + sub_redacted}
        if case is not None and case.status in ("SENT", "CALL_BOOKED"):
            if re.search(r"\d", strip_contacts(new_text(body))):  # a phone isn't a fact
                day_ago = self._now() - timedelta(days=1)
                today = [ts for ts in case.client_reply_log if parse_ts(ts) > day_ago]
                if len(today) >= CLIENT_REPLY_WAKES_PER_DAY or \
                        case.revisions >= MAX_REVISIONS:
                    # v1.6.2: a human looks before another round
                    self._emit(events)
                    self._block(case, "client keeps sending new numbers (revision limit)",
                                notice_key=f"ceo:blocked:revisions:{case_id}")
                    result["blocked"] = True
                    return result
                # v1.6: a reply with numbers re-reads the case (memo v2 only if a fact changed)
                events.append(NewEvent(type="case.status", actor="system", case_id=case_id,
                                       payload={"from": case.status, "to": "INTAKE_PENDING"},
                                       private={"reason": "client_reply"}))
                result["client_replied"] = True
            else:
                result["quiet"] = True  # "thanks!" or a quoted thread wakes nobody
        else:
            events += self._reopen(case, case_id)
        self._emit(events)
        return result

    def _reopen(self, case: Case | None, case_id: str) -> list[NewEvent]:
        current = case.status if case else "NEW"
        # BLOCKED waits for a human; a reply to a SENT/CALL_BOOKED case ("thanks!") must not
        # re-run the pipeline and re-send the memo: the CEO reopens those (`reopen`).
        # A voice case (opened from the anonymous /talk page) is different: mail from its
        # address is the mailbox owner speaking, so it reopens a NOT_ELIGIBLE one.
        if case is not None and case.priority and current == "NOT_ELIGIBLE":
            return [NewEvent(type="case.status", actor="system", case_id=case_id,
                             payload={"from": current, "to": "INTAKE_PENDING"})]
        if current in ("INTAKE_PENDING", "BLOCKED", "SENT", "CALL_BOOKED", "NOT_ELIGIBLE"):
            return []
        return [NewEvent(type="case.status", actor="system", case_id=case_id,
                         payload={"from": current, "to": "INTAKE_PENDING"})]

    def _voice(self, args: dict) -> dict:
        turns = args.get("turns")
        context: list[dict] = []  # /talk turns kept for agents as labelled context
        if turns is not None:
            # v1.5: only what the customer said counts; the AI's read-back never becomes a
            # quote (it would echo numbers back as if the client had written them)
            if not isinstance(turns, list) or not all(
                    isinstance(t, dict) and isinstance(t.get("text"), str) for t in turns):
                raise Refused("bad_input", "turns must be [{speaker, text}]")
            customer = [t["text"].strip() for t in turns
                        if t.get("speaker") == "customer" and t["text"].strip()]
            context = [{"speaker": "customer" if t.get("speaker") == "customer" else "agent",
                        "text": mask_spoken_emails(agent_text(redact_ids(t["text"])[0]))[:1000]}
                       for t in turns if t["text"].strip()][:60]
            joined = "\n".join(customer)
            if len(joined) > 4000:
                raise Refused("too_long", "the customer's transcript is over 4000 characters")
            args = {**args, "text": joined}
        text = _str(args, "text", max_len=4000)
        channel = _one_of(args, "channel", ("voice", "typed"), default="typed")
        speaker = _one_of(args, "speaker", ("customer", "ceo"))
        text, _ = redact_ids(text)
        text = mask_spoken_emails(text)
        if speaker == "ceo":
            text = mask_text(text, masked_name="", company=None)
            self._emit([
                NewEvent(type="voice.command", actor="ceo", provider="local",
                         payload={"text": text, "channel": channel, "speaker": "ceo",
                                  "case_id": None}),
                NewEvent(type="touch.operating", actor="ceo",
                         payload={"note": f'order: "{clip(text, 120)}"', "approval_id": None}),
            ])
            self._wake(f"CEO: {text}")
            return {"forwarded": True}
        reply = _str(args, "reply_email", max_len=320)
        if not _EMAIL_RE.match(reply or ""):
            raise Refused("bad_input", "a customer case needs reply_email")
        request_id = _str(args, "request_id", required=False, max_len=80)
        if request_id:  # the page's own id for this submission: a retry is the same case
            seen = self._done_from(f"voice:{request_id}")
            if seen is not None:
                return {"case_id": seen.case_id, "duplicate": True, "priority": True}
        # Public, unauthenticated page: always a fresh case. Never join (or reveal) a case
        # by the email typed in, or anyone could read or redirect someone else's case.
        number = self._new_case_number()
        case_id = f"case_{number}"
        masked_name = f"Team #{number}"
        # v1.5: replies from the typed address thread onto this case, but only when no
        # case owns that address yet: a typed email never captures someone else's case
        owned = reply.lower() in self.state.case_by_sender
        sender_key = f"voice:{case_id}" if owned else reply.lower()
        events = [NewEvent(
            type="voice.command", actor="intake", case_id=case_id, provider="local",
            payload={"text": mask_text(text, masked_name=masked_name,
                                       company=company_label(reply),
                                       hide_words=sender_words(reply)),
                     "channel": channel, "speaker": "customer", "case_id": case_id,
                     "masked_name": masked_name},  # v1.6.4
            private={"reply_to": reply, "sender_key": sender_key, "masked_name": masked_name,
                     "company": None, "agent_text": agent_text(text), "turns": context},
            idempotency_key=f"voice:{request_id}" if request_id else None,
        )]
        events += self._reopen(None, case_id)
        self._emit(events)
        self._wake(f"FAST PATH {case_id}")
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
        target = approval.case_id or "firm"
        if decision == "deny":
            self._emit([NewEvent(type="touch.operating", actor="ceo", case_id=approval.case_id,
                                 payload={"note": f"denied: {approval.summary}"[:160],
                                          "approval_id": approval_id},
                                 idempotency_key=key, private=private)])
            self._wake(f"Approval {approval_id} denied for {target}")
            return {"approval_id": approval_id, "decision": "deny"}
        events: list[NewEvent] = []
        if current == "PAUSED":
            # the CEO's pause (or a cap) holds until POST /api/trust: record the OK only
            events.append(NewEvent(type="touch.operating", actor="ceo", case_id=approval.case_id,
                                   payload={"note": f"approved while paused: {approval.summary}"[:160],
                                            "approval_id": approval_id},
                                   idempotency_key=key, private=private))
        elif current != "AUTO":
            first = action_type not in self.state.trusted_once and current == "ASK"
            kind = "touch.training" if first else "touch.operating"
            events.append(NewEvent(type=kind, actor="ceo", case_id=approval.case_id,
                                   payload={"note": f"{action_type} → AUTO",
                                            "approval_id": approval_id},
                                   idempotency_key=key, private=private))
            reason = "first approval" if first else "approved again"
            events.append(NewEvent(type="trust.changed", actor="system",
                                   payload={"action_type": action_type, "from": current,
                                            "to": "AUTO", "reason": reason}))
        else:
            events.append(NewEvent(type="touch.operating", actor="ceo", case_id=approval.case_id,
                                   payload={"note": f"approved: {approval.summary}"[:160],
                                            "approval_id": approval_id},
                                   idempotency_key=key, private=private))
        waiting = self._pending_of(action_type, exclude=approval_id)
        self._emit(events)
        result = {"approval_id": approval_id, "decision": "allow",
                  "trust": self.state.trust[action_type]}
        owned = self._run_gateway_owned(approval.action_key)
        if owned is not None:  # gateway-owned action: no agent retries it, so the gateway does
            result[owned[0]] = owned[1]
        self._wake_superseded(waiting)
        self._wake(f"Approval {approval_id} allowed for {target}")
        return result

    def _pending_of(self, action_type: str, *, exclude: str | None = None) -> list:
        return [a for a in self.state.approvals.values()
                if a.action_type == action_type and a.decision is None
                and a.approval_id != exclude]

    def _wake_superseded(self, approvals: list) -> None:
        """After a flip to AUTO, the other actions that were waiting on an approval of that
        type are superseded: wake their owners (or run the gateway-owned questionnaire)."""
        for a in approvals:
            if a.decision != "superseded":
                continue
            if self._run_gateway_owned(a.action_key) is not None:
                continue
            if a.case_id:
                self._wake(f"Approval {a.approval_id} allowed for {a.case_id}")

    def _run_gateway_owned(self, action_key: str) -> tuple[str, str] | None:
        kind, _, target = action_key.partition(":")
        if kind == "questionnaire":
            return "questionnaire", self._send_questionnaire(target)
        if kind == "decision":
            return "mail", self._send_decision_mail(target)
        return None

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

    UNBLOCK_TARGETS = {"BLOCKED": ("INTAKE_PENDING", "READY", "NOT_ELIGIBLE"),
                       "SENT": ("INTAKE_PENDING",), "CALL_BOOKED": ("INTAKE_PENDING",)}

    def _unblock(self, args: dict) -> dict:
        """The CEO's runbook ("retry" / "skip"): move a BLOCKED case on, or reopen a sent
        case when the client sent new numbers. An operating touch; wakes the crew."""
        case = self._case(args)
        targets = self.UNBLOCK_TARGETS.get(case.status)
        if not targets:
            raise Refused("wrong_status", f"{case.case_id} is {case.status}; nothing to unblock")
        to = _one_of(args, "to", targets, default=targets[0])
        if to == "READY" and not case.facts:
            raise Refused("no_facts", "READY needs extracted facts; use INTAKE_PENDING")
        note = _str(args, "note", required=False, max_len=200) or f"{case.status} → {to}"
        self._emit([NewEvent(type="touch.operating", actor="ceo", case_id=case.case_id,
                             payload={"note": f"{case.masked_name}: {note}"[:160],
                                      "approval_id": None}),
                    self._status(case, to)])
        self._wake(f"{'FAST PATH' if case.priority else 'New case'} {case.case_id}")
        return {"case_id": case.case_id, "status": to}

    STALL_AFTER = timedelta(hours=6)

    def _stall_sweep(self, args: dict) -> dict:
        """Design: no reply 6 h after a re-ask -> STALLED (one nudge allowed, then close).
        Called by the server's timer."""
        stalled = []
        now = self._now()
        for case in sorted(self.state.cases.values(), key=lambda c: c.number):
            waiting = case.status == "INTAKE_PENDING" and case.asks \
                and case.asked_after_text == len(case.texts) and case.last_ask_ts
            if waiting and now - parse_ts(case.last_ask_ts) >= self.STALL_AFTER:
                self._emit([self._status(case, "STALLED")])
                stalled.append(case.case_id)
        return {"stalled": stalled}

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
        case = self._case(args)
        detail = {**case.detail(), "offered_slots": self.offered_slots(case)}
        if args.get("_actor") not in (None, "intake", "qa"):
            # v1.6 names are for the seats that read facts; everyone else gets the masked
            # copy, so a post or a DM can't name a client it never saw
            detail["messages"] = list(case.texts)
            detail["subject"] = case.subject
            detail["conversation"] = []
        return detail

    def offered_slots(self, case: Case | None = None) -> list[str]:
        """The next free 15-minute slots (9:00-16:00 Pacific). Anchored to the case's memo
        time (2 h after it), so the list an agent saw, and the CEO approved, stays the same
        until those slots pass; never earlier than 30 min from now."""
        booked = {c.call["start"] for c in self.state.cases.values() if c.call}
        # e2e finding: a time another case is waiting on the CEO for (or was approved for,
        # not yet booked) is held, so two cases are never offered and approved the same slot
        for approval in self.state.approvals.values():
            parts = approval.action_key.split(":", 3)  # call:<case>:v<n>r<m>:<start>
            if len(parts) == 4 and parts[0] == "call" and approval.decision in (None, "allow") \
                    and (case is None or parts[1] != case.case_id):
                booked.add(parts[3])
        anchor = parse_ts(case.memo_ts) if case is not None and case.memo_ts else self._now()
        start = max(anchor + SLOT_LEAD, self._now() + timedelta(minutes=30))
        start = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1) \
            if (start.minute or start.second or start.microsecond) else start
        slots: list[str] = []
        when = start
        for _ in range(7 * 24 * 60 // SLOT_MINUTES):  # look a week ahead at most
            if SLOT_DAY_UTC[0] <= when.hour < SLOT_DAY_UTC[1]:
                iso = iso_ts(when)
                if iso not in booked:
                    slots.append(iso)
                    if len(slots) == SLOT_COUNT:
                        break
            when += timedelta(minutes=SLOT_MINUTES)
        return slots

    def _firm_brief(self, args: dict) -> dict:
        text, counts = self._brief_text()
        self._emit([NewEvent(type="brief.generated", actor="cos", provider="local",
                             payload={"text": text, "counts": counts})])
        return {"text": text, "counts": counts}

    def _pending_approvals(self) -> list[dict]:
        return [{"approval_id": a.approval_id, "action_type": a.action_type, "summary": a.summary}
                for a in self.state.approvals.values() if a.decision is None]

    def _ceo_brief(self, args: dict) -> dict:
        """GET /api/ceo/brief: the login brief (stand-in: a template over the projections)."""
        since_id = _int(args, "since_id") or 0
        # Reuse the last login brief (a reload, a polling console) while nothing but
        # upkeep happened since, or it is under a minute old: no event spam.
        cached = self.state.last_login_brief
        if cached is not None:
            fresh = self._now() - parse_ts(cached["ts"]) < timedelta(minutes=1)
            if fresh or self.state.last_event_id == cached["notice_id"]:
                return {"brief_event_id": cached["brief_id"], "text": cached["text"],
                        "counts": cached["counts"],
                        "pending_approvals": self._pending_approvals(),
                        "last_event_id": self.state.last_event_id}
        counts = self._brief_text()[1]
        text = self.templates.render("login-brief", self._login_values(since_id))
        written = self._emit([NewEvent(type="brief.generated", actor="cos", provider="local",
                                       payload={"text": text, "counts": counts})])
        self._notify_ceo("login", "Login brief", text, channel="login",
                         key=f"ceo:login:{written[0].id}")
        return {"brief_event_id": written[0].id, "text": text, "counts": counts,
                "pending_approvals": self._pending_approvals(),
                "last_event_id": self.state.last_event_id}

    def _ceo_digest(self, args: dict) -> dict:
        """Every CEO_DIGEST_HOURS (the task-6 scheduler calls this): brief + email."""
        if not self.settings.ceo_email:
            return {"sent": False, "reason": "no CEO_EMAIL"}
        last = next((e for e in reversed(list(self.store.since(0)))
                     if e.type == "ceo.notified" and e.payload["reason"] == "digest"), None)
        due = timedelta(hours=self.settings.ceo_digest_hours)
        # the first digest is due CEO_DIGEST_HOURS after boot, never right after it (a demo
        # restart must not mail the CEO)
        anchor = parse_ts(last.ts) if last is not None else self._booted_at
        if self._now() - anchor < due:
            return {"sent": False, "reason": "not due"}
        text, counts = self._brief_text()
        since = last.id if last is not None else 0
        body = self.templates.render("ceo-digest-email", self._digest_values(since))
        # one key per digest window: a retry in the same window never mails twice, and the
        # brief is logged only together with a digest that actually went out
        if time.monotonic() < self._digest_backoff_until:
            return {"sent": False, "reason": "backing off after a failed send"}
        window = int(self._now().timestamp() // due.total_seconds())
        sent = self._notify_ceo(
            "digest", "Firm digest", text, key=f"ceo:digest:w{window}", body=body,
            extra=[NewEvent(type="brief.generated", actor="cos", provider="local",
                            payload={"text": text, "counts": counts},
                            idempotency_key=f"brief:digest:w{window}")])
        if not sent:
            self._digest_backoff_until = time.monotonic() + 300
        return {"sent": sent}

    def _period(self, since_id: int) -> dict[str, Any]:
        events = list(self.store.since(since_id))
        spend = sum((Decimal(e.cost_usd) for e in events if e.cost_usd), Decimal(0))
        return {
            "clients_new": sum(1 for e in events if e.type == "case.status"
                               and e.payload["from"] == "NEW"),
            "memos_sent": sum(1 for e in events if e.type == "memo.sent" and e.status == "ok"),
            "calls_booked": sum(1 for e in events if e.type == "call.booked"
                                and e.status == "ok"),
            "agent_spend": f"${money_text(spend)}",
            "flags": [e for e in events if e.type == "case.flagged"],
        }

    def _lines(self) -> dict[str, str]:
        pending = self._pending_approvals()
        blocked = [c for c in self.state.cases.values() if c.status == "BLOCKED"]
        waiting = [c for c in self.state.candidates.values()
                   if c.get("stage") in ("screened", "shortlisted")]
        return {
            "approvals": "\n".join(f"- {a['approval_id']}: {a['summary']}" for a in pending),
            "blocked": "\n".join(f"- {c.masked_name}: {c.blocked_reason or 'needs you'}"
                                 for c in blocked),
            "hiring": "\n".join(f"- {c['name_masked']} ({c.get('credentials') or 'no '
                                 'credentials stated'}): {c['stage']}, waiting on you"
                                 for c in waiting),
            "n_approvals": str(len(pending)), "n_blocked": str(len(blocked)),
            "n_hiring": str(len(waiting)),
        }

    def _login_values(self, since_id: int) -> dict[str, str]:
        period, lines = self._period(since_id), self._lines()
        n = int(lines["n_approvals"])
        return {
            "greeting": "Welcome back",
            "clients_new": str(period["clients_new"]), "memos_sent": str(period["memos_sent"]),
            "tax_saved": format_usd(self.state.tax_saved_cents),
            "agent_spend": period["agent_spend"],
            "approvals_line": (f"{n} approval{'s' if n != 1 else ''} waiting on you."
                               if n else "Nothing is waiting on your approval."),
            "blocked_line": (f"{lines['n_blocked']} blocked case(s) need you."
                             if lines["blocked"] else ""),
            "hiring_line": (f"{lines['n_hiring']} candidate(s) waiting on your hiring call."
                            if lines["hiring"] else ""),
        }

    def _digest_values(self, since_id: int) -> dict[str, str]:
        period, lines = self._period(since_id), self._lines()
        flags = period["flags"]
        flag_lines = "\n".join(
            f"- {self.state.cases[e.case_id].masked_name if e.case_id in self.state.cases else e.case_id}: "
            f"{e.payload['kind'].replace('_', ' ')}" for e in flags)
        return {
            "period": "since the last digest" if since_id else "so far",
            "clients_new": str(period["clients_new"]), "memos_sent": str(period["memos_sent"]),
            "calls_booked": str(period["calls_booked"]),
            "tax_saved": format_usd(self.state.tax_saved_cents),
            "agent_spend": period["agent_spend"],
            "touches_training": str(self.state.touches["training"]),
            "touches_operating": str(self.state.touches["operating"]),
            "touches_engineering": str(self.state.touches["engineering"]),
            "pending_approvals": lines["approvals"] or "- nothing",
            "blocked_lines": lines["blocked"] or "- nothing",
            "hiring_line": lines["hiring"] or "- nobody waiting",
            "flags_lines": (f"{len(flags)} flag(s):\n{flag_lines}" if flags else "- none"),
            "console_url": self.settings.ceo_console_url,
        }

    def _brief_text(self) -> tuple[str, dict]:
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
        return text, counts

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
        digest = _digest({"q": quotes, "inc": inc, "f": foreign, "t": len(case.texts),
                          "r": case.reopens})
        texts = self._fact_texts(case)
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
        if case.resume_status and case.status == "INTAKE_PENDING" and case.facts \
                and check.ok and check.facts == case.facts \
                and case.facts_version == case.memo_version:
            # v1.6: the reply changed no fact: no memo v2, the case goes back where it was
            back = case.resume_status
            self._emit([self._status(case, back)])
            return {"facts": check.facts, "unchanged": True, "status": back}
        key = f"facts:{case.case_id}:{digest}"
        seen = self._done_from(key)
        if seen is not None and seen.id == case.facts_event_id:
            # a retry of the call that produced the current facts (or the same facts again
            # on a reopened case): no new facts event, but the case moves on to READY
            if case.status == "STALLED":
                self._emit([NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                     payload={"from": "STALLED", "to": "INTAKE_PENDING"}),
                            NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                     payload={"from": "INTAKE_PENDING", "to": "READY"})])
            elif case.status != "READY":
                self._emit([self._status(case, "READY")])
            return {"facts": seen.payload["facts"], "assumed": seen.payload["assumed"],
                    "duplicate": True, "next": "compute_tax"}
        if seen is not None:  # A, then B, then A again: A must apply again
            key = f"{key}:v{case.facts_version}"
        public_quotes = {f: mask_text(q, masked_name=case.masked_name, company=case.company,
                                      hide_words=sender_words(case.sender_key))
                         for f, q in check.quotes.items()}  # v1.6.2: the screen sees masked
        events = [NewEvent(type="facts.extracted", actor="intake", case_id=case.case_id,
                           provider="local",
                           payload={"facts": check.facts, "quotes": public_quotes,
                                    "assumed": check.assumed},
                           idempotency_key=key,
                           private={"unrealistic": check.unrealistic, "quotes": check.quotes})]
        # STALLED has no direct edge to READY: go back through intake first.
        if case.status == "STALLED":
            events += [NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                payload={"from": "STALLED", "to": "INTAKE_PENDING"}),
                       NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                payload={"from": "INTAKE_PENDING", "to": "READY"})]
        elif case.status != "READY":
            events.append(self._status(case, "READY"))
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
        stalled = case.status == "STALLED"
        if not stalled and case.asks and case.asked_after_text == len(case.texts):
            return {"already_asked": True, "asks_so_far": case.asks}
        if case.asks >= self.settings.max_asks:
            self._block(case, f"still missing facts after {case.asks} asks")
            raise Refused("max_asks", f"asked {case.asks} times already; case is BLOCKED")
        if not case.reply_to:
            raise Refused("no_reply_address", f"{case.case_id} has no reply address")
        # a STALLED case gets one nudge (it counts as an ask), then max_asks closes it
        key = f"ask:{case.case_id}:t{len(case.texts)}" + (f":nudge{case.asks}" if stalled else "")
        labels = ", ".join(FACT_LABELS.get(m, m) for m in missing)
        self._gate("email", key, _cap(f"{self._first('email')}re-ask email: {case.masked_name}, "
                                      f"needs {labels}"[:160]), case.case_id, public=case.priority)
        body = self.templates.render("ask-client",
                                     ask_values(case.masked_name, missing, case.problems))
        outgoing = self.templates.render("ask-client", ask_values(
            case.masked_name, missing, case.problems, greeting_name=self._greeting(case)))
        sent = self._with_retry("ask email", lambda: self.ports.mail.send(
            OutMail(to=case.reply_to, subject=f"Re: {case.subject}".strip(), body=outgoing),
            key))
        events = [NewEvent(type="client.asked", actor="intake", case_id=case.case_id,
                           provider=self.ports.mail.provider,
                           payload={"missing": missing, "body": body}, idempotency_key=key,
                           private={"message_id": sent.message_id, "public": case.priority})]
        if stalled:  # the nudge went out: back to waiting (the sweep can stall it once more)
            events.append(NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                   payload={"from": "STALLED", "to": "INTAKE_PENDING"}))
        self._emit(events)
        return {"asked": missing, "asks_so_far": case.asks, "message_id": sent.message_id}

    def _mark_not_eligible(self, args: dict) -> dict:
        case = self._case(args)
        reason = _one_of(args, "reason", NOT_ELIGIBLE_REASONS)
        self._require_status(case, "NEW", "INTAKE_PENDING", "READY", "STALLED")
        if not case.reply_to:
            raise Refused("no_reply_address", f"{case.case_id} has no reply address")
        key = f"not_eligible:{case.case_id}"  # one rejection mail per case, ever
        self._gate("email", key,
                   _cap(f"{self._first('email')}not-eligible reply: {case.masked_name}"),
                   case.case_id, public=case.priority)
        sent = self._with_retry("not-eligible email", lambda: self.ports.mail.send(
            OutMail(to=case.reply_to, subject=f"Re: {case.subject}".strip(),
                    body=self.templates.render(f"not-eligible-{reason}", {
                        "masked_name": case.masked_name,
                        "greeting_name": self._greeting(case)})), key))
        events = [NewEvent(type="case.not_eligible", actor="intake", case_id=case.case_id,
                           provider=self.ports.mail.provider, payload={"reason": reason},
                           idempotency_key=key,
                           private={"message_id": sent.message_id, "public": case.priority})]
        events.append(self._status(case, "NOT_ELIGIBLE"))
        self._emit(events)
        return {"status": "NOT_ELIGIBLE", "message_id": sent.message_id}

    # ------------------------------------------------------------ tax tools

    # ------------------------------------------------------------ buy_tool (v1.7.3)

    _BUY_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,120}")

    @staticmethod
    def _buy_fingerprint(tool: str, data: dict) -> str:
        from app.gateway.adapters import _http

        return _http.fingerprint(tool, json.dumps(data, sort_keys=True, allow_nan=False))

    def _buy_fetch(self, args: dict) -> dict:
        """buy_tool's slow half, before the lock: validate, then ToolBuyPort.buy()."""
        tool, data, key = args.get("tool"), args.get("input"), args.get("key")
        if not isinstance(tool, str) or not tool or len(tool) > 80:
            return {"refused": "tool must be a tool name from MONID_TOOLS"}
        if not isinstance(data, dict):
            return {"refused": "input must be an object"}
        if not isinstance(key, str) or not self._BUY_KEY.fullmatch(key):
            return {"refused": "key must be 1-120 of A-Z a-z 0-9 _ . : -"}
        try:
            text = json.dumps(data, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            return {"refused": "input must be plain JSON"}
        if has_email(text) or has_phone(text) or _CASE_REF.search(text):
            return {"refused": "input must not carry an email, a phone number or a case id "
                               "(public startup data only)"}
        port_key = f"buy:{key}"
        try:
            purchase = self.ports.toolbuy.buy(tool, data, port_key)
        except PortError as err:
            unsure = type(err).__name__ == "Unsure"
            ceiling = None
            spec = getattr(self.ports.toolbuy, "tools", {}).get(tool) if unsure else None
            if spec:
                ceiling = str(spec.get("max_usd"))
            return {"error": err.code, "message": str(err)[:200], "unsure": unsure,
                    "fingerprint": self._buy_fingerprint(tool, data), "port_key": port_key,
                    "ceiling": ceiling, "retryable": err.retryable}
        return {"purchase": purchase, "fingerprint": self._buy_fingerprint(tool, data),
                "port_key": port_key}

    def _buy_tool(self, args: dict) -> dict:
        tool = _str(args, "tool", max_len=80)
        key = _str(args, "key", max_len=120)
        fetched = args.get("_prefetch") or {}
        if fetched.get("refused"):
            raise Refused("bad_input", fetched["refused"])
        provider = self.ports.toolbuy.provider
        private = {"key": fetched.get("port_key"), "fingerprint": fetched.get("fingerprint")}
        if "error" in fetched:
            if fetched.get("unsure"):  # it may be charged: remembered across restarts
                self._emit([NewEvent(type="tool.bought", actor="mkt", provider=provider,
                                     status="failed", payload={"tool": tool, "provider": provider},
                                     idempotency_key=f"tool:unsure:{fetched['port_key']}",
                                     private={**private, "unsure": True,
                                              "ceiling": fetched.get("ceiling")})])
            raise (ToolError if fetched.get("retryable") else Refused)(
                fetched["error"], f"buy_tool: {fetched['message']}")
        purchase = fetched["purchase"]
        done = self._done_from(f"tool:{fetched['port_key']}")
        if done is None:  # the adapter replays a repeat key; the log records it once
            events = [NewEvent(type="tool.bought", actor="mkt", provider=provider,
                               payload={"tool": tool, "provider": provider},
                               cost_usd=purchase.cost_usd,
                               idempotency_key=f"tool:{fetched['port_key']}",
                               private={**private, "bought": purchase.bought})]
            if purchase.bought:
                events.append(NewEvent(type="cost.booked", actor="system", provider=provider,
                                       payload={"provider": provider, "model": f"tool:{tool}",
                                                "estimated": False},
                                       cost_usd=purchase.cost_usd,
                                       idempotency_key=f"cost:tool:{fetched['port_key']}"))
            self._emit(events)
        return {"tool": tool, "key": key, "bought": purchase.bought,
                "cost_usd": purchase.cost_usd, "result": purchase.result}

    def _restore_toolbuy(self) -> None:
        """Monid keeps purchases, unsure keys and today's spend in memory; replay them from
        tool.bought at boot, so a restart never re-buys a key or forgets today's spend."""
        port = self.ports.toolbuy
        if not all(hasattr(port, name) for name in ("_remember", "_book", "_unsure", "_lock")):
            return
        from app.gateway.ports import Purchase

        today = self._now().date().isoformat()
        resolved: set[str] = set()
        bought = [e for e in self.store.since(0) if e.type == "tool.bought"]
        with port._lock:
            for e in bought:
                private = e.private or {}
                key, fp = private.get("key"), private.get("fingerprint")
                if not key or not fp:
                    continue
                if e.status == "ok":
                    resolved.add(key)
                    port._unsure.pop(key, None)
                    if not private.get("bought", True):
                        continue
                    port._remember(key, fp, Purchase(result=None, cost_usd=e.cost_usd or "0",
                                                     bought=True))
                    if e.ts[:10] == today and e.cost_usd:
                        port._spent_today()
                        port._book(Decimal(e.cost_usd))
                elif private.get("unsure") and key not in resolved:
                    ceiling = Decimal(private.get("ceiling") or "0")
                    port._unsure[key] = (fp, ceiling)

    def _law_searches_last_hour(self) -> int:
        hour_ago = self._now() - timedelta(hours=1)
        return sum(1 for ts in self.state.law_log if parse_ts(ts) > hour_ago)

    def _law_fetch(self, args: dict) -> dict:
        """law_search's slow half, before the lock (v1.7 §6f): clean the query, check the
        hourly cap, run the search. The handler re-checks under the lock and records it."""
        from app.gateway.adapters.apify import clean_query

        raw = args.get("query")
        case_id = args.get("case_id")
        # the handler's own checks first: nothing paid runs for a call it would refuse
        if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 300 \
                or not isinstance(case_id, str):
            return {"query": "", "skipped": True}
        query = clean_query(raw)[:300]
        with self._lock:
            known = case_id.strip() in self.state.cases
            capped = self._law_searches_last_hour() + self._law_inflight >= LAW_SEARCHES_PER_HOUR
            if query and known and not capped:
                self._law_inflight += 1  # reserved: released after the handler ran
                args["_law_reserved"] = True
        if not query or not known or capped:
            return {"query": query, "skipped": True, "capped": capped}
        try:
            hits = self.ports.scrape.search(query)
            out = {"query": query, "status": "ok",
                   "results": [{"cite": h.cite, "snippet": h.snippet} for h in hits]}
        except PortError as err:
            log.warning("law search failed: %s", err.code)
            out = {"query": query, "status": "failed", "results": []}
        self._book_run(self.ports.scrape.provider, "search", str(args.get("case_id")))
        return out

    def _law_search(self, args: dict) -> dict:
        case = self._case(args)
        _str(args, "query", max_len=300)
        fetched = args.get("_prefetch") or {}
        query = fetched.get("query") or ""
        if not query:
            raise Refused("bad_input", "the query has no searchable words left after cleaning "
                                       "(no emails, amounts or ID numbers)")
        if fetched.get("skipped"):
            if fetched.get("capped"):
                raise Refused("law_cap",
                              f"law_search is limited to {LAW_SEARCHES_PER_HOUR} an hour")
            raise Refused("bad_input", "law_search needs a known case_id and a query")
        status, results = fetched.get("status", "failed"), fetched.get("results", [])
        self._emit([NewEvent(type="law.searched", actor="tax", case_id=case.case_id,
                             provider=self.ports.scrape.provider, status=status,
                             payload={"query": query, "results": results})])
        if status != "ok":
            raise ToolError("search_failed", "law search is unavailable right now")
        return {"results": results, "query": query}

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
        key = f"tax:{case.case_id}:v{case.facts_version}r{case.reopens}"
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
        key = f"qa:{case.case_id}:v{case.facts_version}r{case.reopens}"
        done = self._done_from(key)
        if done is not None:
            return {"passed": done.type == "qa.passed", "checks": done.payload["checks"],
                    "duplicate": True}
        self._require_status(case, "COMPUTED")
        texts = self._fact_texts(case)
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
                    elif fact != "stated_par" and not intake.matches_mentions(
                            fact, Decimal(parsed) / (100 if fact == "gross_assets" else 1),
                            texts):
                        ok, note = False, "number isn't the one the client wrote for it"
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
            bad_facts = [c["field"] for c in checks if not c["ok"] and c["field"] in FACT_LABELS]
            events += [
                NewEvent(type="case.status", actor="system", case_id=case.case_id,
                         payload={"from": "QA_FAILED", "to": "INTAKE_PENDING"}),
                # record what to re-ask, so ask_client has something to ask for
                NewEvent(type="facts.missing", actor="intake", case_id=case.case_id,
                         provider="local",
                         payload={"missing": bad_facts, "asks_so_far": case.asks},
                         idempotency_key=f"qamissing:{case.case_id}:v{case.facts_version}r{case.reopens}",
                         private={"problems": [f"can you confirm your {FACT_LABELS[f]}"
                                               for f in bad_facts]}),
            ]
        else:
            bad = ", ".join(c["field"] for c in checks if not c["ok"])
            events += [NewEvent(type="case.blocked", actor="system", case_id=case.case_id,
                                payload={"reason": f"QA failed: {bad}", "needs": "human"}),
                       NewEvent(type="case.status", actor="system", case_id=case.case_id,
                                payload={"from": "QA_FAILED", "to": "BLOCKED"})]
        written = self._emit(events)
        if not reask:
            self._notify_ceo("blocked", f"Blocked: {case.masked_name}",
                             f"{case.masked_name}: QA failed ({bad})",
                             key=f"ceo:blocked:{written[0].id}")
        return {"passed": False, "checks": checks,
                "next": "ask_client" if reask else "firm_brief"}

    # ------------------------------------------------------------ accounting tools

    @staticmethod
    def _greeting(case: Case) -> str:
        """v1.6 {{greeting_name}}: the client's own company name if known, else "there".
        Only the outgoing mail uses it; every event copy keeps the masked name."""
        if case.priority or not case.company:  # /talk and webmail: "there"
            return "there"
        label = re.sub(r"[^A-Za-z0-9 ]+", " ", case.company).strip()
        label = re.sub(r"\s+", " ", label)[:40].strip()
        return label.title() if label else "there"

    def _memo_ref(self, key: str) -> str:
        """v1.7: `UTX-` + 12 capitals/digits, unique per memo version and per event log
        (the log's first event is in the hash: a reset never repeats an old reference, so a
        rehearsal memo in the QA mailbox can't be mistaken for a real one)."""
        if self._epoch is None:
            first = next(iter(self.store.since(0, limit=1)), None)
            self._epoch = f"{first.id}:{first.ts}" if first is not None else ""
        return "UTX-" + hashlib.sha256(f"{self._epoch}|{key}".encode()).hexdigest()[:12].upper()

    def _memo_body(self, case: Case, *, outgoing: bool = False, memo_ref: str) -> str:
        """The memo text. `memo_ref` is minted once per memo version (_memo_ref of the memo
        key) and reused for both renders, retries and memo.sent; no render without it."""
        if not MEMO_REF.fullmatch(memo_ref or ""):
            raise Refused("template_invalid", "memo_ref must be UTX- plus 8-16 capitals/digits")
        facts = case.facts or {}
        rnd_known = facts.get("gross_receipts_cents") is not None \
            and facts.get("years_with_receipts") is not None
        try:
            return self.templates.render("memo", memo_values(
                masked_name=case.masked_name, result=case.result,
                form5472=bool(case.result.get("form5472_flag")),
                rnd=bool(case.result.get("rnd_flag")), rnd_known=rnd_known,
                greeting_name=self._greeting(case) if outgoing else case.masked_name,
                memo_ref=memo_ref))
        except TemplateError as err:
            raise Refused("template_invalid", str(err)) from err

    def _send_memo(self, args: dict) -> dict:
        case = self._case(args)
        key = f"memo:{case.case_id}:v{case.facts_version}r{case.reopens}"
        done = self._done_from(key)
        if done is not None:
            return {"message_id": done.payload["message_id"], "duplicate": True}
        # QA gate: a code check on case status, not a prompt.
        self._require_status(case, "QA_PASSED", code="qa_gate")
        if case.qa_version != case.facts_version or case.result_version != case.facts_version:
            raise Refused("qa_gate", "QA ran on older facts; run compute_tax and qa_check again")
        if not case.reply_to:
            raise Refused("no_reply_address", f"{case.case_id} has no reply address")
        day_ago = self._now() - timedelta(days=1)
        recent = [ts for ts in self.state.memo_log.get(mail_identity(case.reply_to), [])
                  if parse_ts(ts) > day_ago]
        if len(recent) >= MEMOS_PER_ADDRESS_PER_DAY:  # v1.5: on top of the per-IP limit
            self._block(case, f"memo cap: {MEMOS_PER_ADDRESS_PER_DAY} memos a day already "
                              "went to this address")
            raise Refused("memo_cap", f"{MEMOS_PER_ADDRESS_PER_DAY} memos a day per address; "
                                      "the case is BLOCKED for the CEO")
        memo_ref = self._memo_ref(key)
        body = with_reference(self._memo_body(case, memo_ref=memo_ref), memo_ref)  # masked
        outgoing = with_reference(self._memo_body(case, outgoing=True, memo_ref=memo_ref),
                                  memo_ref)  # the mail the client receives
        summary = (f"{self._first('email')}memo email: {case.masked_name}, "
                   f"{format_usd(case.result['authorized_method_cents'])} → "
                   f"{format_usd(case.result['total_cents'])}")
        self._gate("email", key, _cap(summary), case.case_id, public=case.priority)
        try:
            sent = self._with_retry("memo email", lambda: self.ports.mail.send(
                OutMail(to=case.reply_to, subject=MEMO_SUBJECT, body=outgoing,
                        bcc=self.settings.mail_qa_bcc or None), key))
        except ToolError as err:
            self._block(case, f"memo email failed: {err.code}")
            raise
        seconds = 0
        if case.facts_ts:
            seconds = max(0, int((self._now() - parse_ts(case.facts_ts)).total_seconds()))
        self._emit([
            NewEvent(type="memo.sent", actor="acct", case_id=case.case_id,
                     provider=self.ports.mail.provider,
                     payload={"to_masked": mask_email(case.reply_to), "subject": MEMO_SUBJECT,
                              "body": body, "message_id": sent.message_id,
                              "seconds_since_last_fact": seconds, "memo_ref": memo_ref},
                     idempotency_key=key, private={"public": case.priority}),
            self._status(case, "SENT"),
            NewEvent(type="crm.sync_requested", actor="system", case_id=case.case_id,
                     payload={"object": "deal", "stage": "memo_sent"},
                     idempotency_key=f"crm:{case.case_id}:memo_sent:v{case.facts_version}r{case.reopens}",
                     private={"sync_key": f"{case.case_id}:memo_sent:v{case.facts_version}r{case.reopens}"}),
        ])
        return {"message_id": sent.message_id, "seconds_since_last_fact": seconds,
                "memo_ref": memo_ref, "next": "book_call"}

    def _phone_check(self, args: dict) -> dict:
        """v1.7: starts a phone run for the latest memo and returns {pending: true}; the
        run itself happens outside the lock (advance_phone_jobs, also from the tick), which
        writes phone.checked. Only an ok result ends the attempts; at most 3 per memo."""
        case = self._case(args)
        if case.memo_message_id is None:
            raise Refused("no_memo", "send_memo first")
        ref = case.memo_ref
        if not ref:
            raise Refused("no_memo_ref", "this memo has no reference line; send a new memo "
                                         "to phone-check it")
        attempts = case.phone_attempts.get(ref, [])
        if "ok" in attempts:
            return {**(case.phone or {}), "pending": False, "duplicate": True}
        job_id = f"{case.case_id}:{ref}"
        with self._jobs_lock:
            running = self._phone_jobs.get(job_id)
            if running is not None:
                return {"pending": True, "attempt": running["attempt"]}
            if len(attempts) >= PHONE_ATTEMPTS:
                return {"pending": False, "status": "skipped",
                        "status_note": f"{PHONE_ATTEMPTS} phone attempts used for this memo"}
            memo = next((e for e in reversed(list(self.store.since(0)))
                         if e.type == "memo.sent" and e.case_id == case.case_id
                         and e.payload["message_id"] == case.memo_message_id), None)
            attempt = len(attempts) + 1
            self._phone_jobs[job_id] = {
                "case_id": case.case_id, "memo_ref": ref, "attempt": attempt,
                "key": f"phone:{case.case_id}:{ref}:a{attempt}",
                "mail_id": case.memo_message_id, "body": memo.payload["body"] if memo else "",
                "task_id": None, "started": None, "busy": False}
        return {"pending": True, "attempt": attempt}

    def advance_phone_jobs(self) -> None:
        """Move every pending phone run one step, outside the gateway lock (§6f). A
        two-phase device (Mobilerun) is started, then polled on later calls; a one-call
        device (local) finishes at once. Only phone.checked is written under the lock."""
        with self._jobs_lock:
            jobs = [job for job in self._phone_jobs.values() if not job["busy"]]
            for job in jobs:
                job["busy"] = True
        for job in jobs:
            try:
                self._advance_phone(job)
                job["crashes"] = 0
            except Exception as err:  # noqa: BLE001 - an adapter bug must not stall the rest
                job["crashes"] = job.get("crashes", 0) + 1
                log.exception("phone job %s failed unexpectedly", job["key"])
                if job["crashes"] >= 3:  # retire it: the attempt counts as skipped
                    try:
                        self._record_phone(job, None, Unavailable(f"crashed: {type(err).__name__}"))
                    except Exception:  # noqa: BLE001
                        log.exception("phone job %s could not be retired", job["key"])
            finally:
                job["busy"] = False

    def _advance_phone(self, job: dict[str, Any]) -> None:
        port = self.ports.device
        two_phase = callable(getattr(port, "start", None)) and \
            callable(getattr(port, "finish", None))
        check, error = None, None
        try:
            if not two_phase:
                check = port.open_and_capture(job["mail_id"], job["body"])
            elif job["task_id"] is None:
                job["task_id"] = port.start(job["mail_id"], job["body"], key=job["key"],
                                            reference=job["memo_ref"])
                job["started"] = time.monotonic()
                self._book_run(port.provider, "task", job["case_id"], key=f"run:{job['key']}")
                return  # polled from the tick
            else:
                check = port.finish(job["task_id"])
                if check is None:
                    if time.monotonic() - job["started"] < PHONE_RUN_TIMEOUT:
                        return
                    if callable(getattr(port, "cancel", None)):
                        port.cancel(job["task_id"])
                    raise Unavailable("the phone run took too long")
        except PortError as err:
            error = err
        self._record_phone(job, check, error)

    def _record_phone(self, job: dict[str, Any], check: Any, error: PortError | None) -> None:
        if check is not None:
            status = "ok"
            note = ("memo opened on a cloud phone" if check.rendered_ok
                    else "memo found on the phone, but unreadable or without its disclaimer")
        else:
            status = "skipped"
            note = f"phone check skipped ({error.code if error else 'unknown'})"
        if error is not None:
            log.warning("phone check %s skipped: %s", job["key"], error.code)
        event = NewEvent(type="phone.checked", actor="qa", case_id=job["case_id"],
                         provider=self.ports.device.provider, status=status,
                         payload={"rendered_ok": bool(check and check.rendered_ok),
                                  "status_note": note},
                         idempotency_key=job["key"],
                         private={"screenshot_url": check.screenshot_url if check else None,
                                  "memo_ref": job["memo_ref"], "attempt": job["attempt"],
                                  "task_id": job["task_id"]})
        try:
            with self._lock:
                self.store.ensure_writable()
                self._emit([event])
        except EventWriteError as err:  # keep the job: the next tick records it
            log.error("phone.checked not logged yet: %s", err)
            return
        with self._jobs_lock:
            self._phone_jobs.pop(f"{job['case_id']}:{job['memo_ref']}", None)

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
        if case.call:
            # A reopened case (memo v2) already has its review call: keep that one
            # booking instead of sending the client a second invite.
            self._emit([self._status(case, "CALL_BOOKED")])
            return {"start": case.call["start"], "minutes": case.call["minutes"],
                    "reused": True}
        start_iso = iso_ts(when)
        offered = self.offered_slots(case)
        if start_iso not in offered:
            raise Refused("slot_not_offered", "start must be one of the case's offered_slots",
                          {"offered_slots": offered})
        # the approval names the time: an OK for one slot never books another
        slot_key = f"{key}:{start_iso}"
        approved = [k[len(key) + 1:] for k, apr in self.state.approval_by_key.items()
                    if k.startswith(f"{key}:") and k != slot_key
                    and self.state.approvals[apr].decision == "allow"]
        still_open = sorted(s for s in approved if s in offered)
        own = self.state.approval_by_key.get(slot_key)
        if still_open and not (own and self.state.approvals[own].decision == "allow"):
            # the CEO approved that time; the AUTO it earned never moves the call elsewhere
            raise Refused("slot_not_approved", f"the CEO approved {still_open[0]}; book that slot",
                          {"approved_start": still_open[0], "offered_slots": offered})
        self._gate("calendar_invite", slot_key,
                   _cap(f"{self._first('calendar_invite')}calendar invite: "
                        f"{case.masked_name}, {start_iso}"), case.case_id)
        try:
            self._with_retry("calendar booking", lambda: self.ports.calendar.book(
                Slot(start=start_iso, minutes=SLOT_MINUTES), case.reply_to,
                "U-Tax: your franchise tax review call", slot_key))
        except ToolError as err:
            self._block(case, f"calendar booking failed: {err.code} (memo was sent)")
            raise
        self._emit([
            NewEvent(type="call.booked", actor="intake", case_id=case.case_id,
                     provider=self.ports.calendar.provider,
                     payload={"start": start_iso, "minutes": SLOT_MINUTES,
                              "invitee_masked": mask_email(case.reply_to or "")},
                     idempotency_key=key),
            self._status(case, "CALL_BOOKED"),
            NewEvent(type="crm.sync_requested", actor="system", case_id=case.case_id,
                     payload={"object": "deal", "stage": "call_booked"},
                     idempotency_key=f"crm:{case.case_id}:call_booked:v{case.memo_version}",
                     private={"sync_key": f"{case.case_id}:call_booked:v{case.memo_version}"}),
        ])
        return {"start": start_iso, "minutes": SLOT_MINUTES}

    # ------------------------------------------------------------ marketing / hr / cost

    def _post_social(self, args: dict) -> dict:
        channel = _str(args, "channel", max_len=40).lower()
        kind = _one_of(args, "kind", ("client", "hiring"), default="client")
        raw = _str(args, "text", max_len=3000)
        if len(raw) > POST_MAX_CHARS:  # the appended link must still fit an X post
            raise Refused("too_long", f"post text is {len(raw)} characters; the limit is "
                                      f"{POST_MAX_CHARS}")
        if not self.settings.public_talk_url:
            raise Refused("no_talk_url", "posts need PUBLIC_TALK_URL (where they send people)")
        link = self.settings.public_talk_url if kind == "client" else self.settings.inbox_hiring
        if not link:
            raise Refused("no_hiring_inbox", "hiring posts need MAIL_INBOX_HIRING")
        text, _ = redact_ids(raw)
        image_url = _str(args, "image_url", required=False, max_len=2000) or None
        if image_url is not None and not self._asset_url_ok(image_url):
            # v1.6.4: the theater renders it, so only our own asset hosts, over https
            raise Refused("bad_input", "image_url must be https on a host in PUBLIC_ASSET_HOSTS")
        key = f"post:{_digest([channel, kind, text, image_url])}"
        done = self._done_from(key)
        if done is not None:
            return {"url": done.payload["url"], "duplicate": True}
        if channel not in POST_CHANNELS:
            raise Refused("bad_input", f"channel must be one of {', '.join(POST_CHANNELS)}")
        if image_url and (not image_url.startswith(("https://", "local://"))
                          or has_email(image_url) or _CASE_REF.search(image_url)):
            raise Refused("content_guard", "post refused: image_url must be a plain https link")
        problem = post_problem(text, allowed_url=self.settings.public_talk_url) \
            or self._names_a_client(text)
        if problem:
            raise Refused("content_guard", f"post refused: {problem}")
        text = f"{text.rstrip()} {link}"  # v1.5: the gateway appends the link after the guard
        if channel == "x" and len(text) > X_MAX_CHARS:
            raise Refused("too_long", f"with the link the post is {len(text)} characters; "
                                      f"X allows {X_MAX_CHARS}")
        self._gate("social_post", key,
                   _cap(f"{self._first('social_post')}post on {channel}: {clip(text, 80)}"),
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

    def _asset_url_ok(self, url: str) -> bool:
        from urllib.parse import urlsplit

        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
        except ValueError:
            return False
        return parts.scheme == "https" and not parts.username and not parts.password \
            and host in self.settings.public_asset_hosts

    def _names_a_client(self, text: str) -> str | None:
        lowered = text.lower()
        for case in self.state.cases.values():
            words = [w for w in [case.company, *sender_words(case.sender_key)]
                     if w and len(w) >= 3 and w.lower() not in TAX_WORDS]
            if any(re.search(rf"\b{re.escape(w.lower())}\b", lowered) for w in words):
                return "it names a client"
        return None

    def _draft_dm(self, args: dict) -> dict:
        label = _str(args, "recipient_label", max_len=120)
        text, _ = redact_ids(_str(args, "text", max_len=3000))
        problem = self._names_a_client(f"{label} {text}")
        if problem:
            raise Refused("content_guard", f"DM refused: {problem}")
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
                             payload={"note": clip(f"sent DM to {label}", 120),
                                      "approval_id": None},
                             idempotency_key=f"touch:dmsent:{_digest([label, text])}")])
        return {"sent_by_human": True}

    def _hr_record(self, args: dict) -> dict:
        """v1.4: HR screens and shortlists. Applicants come from the gateway (§6c); hiring
        and declining are the CEO's (POST /api/candidates/{id}/decision)."""
        stage = _one_of(args, "stage", ("screened", "shortlisted"))
        candidate_id = _str(args, "candidate_id", max_len=64)
        cand = self.state.candidates.get(candidate_id)
        if cand is None:
            raise Refused("not_found", f"no candidate {candidate_id}")
        if cand.get("stage") in ("hired", "declined"):
            raise Refused("wrong_status", f"{candidate_id} is already {cand['stage']}")
        note = strip_contacts(_str(args, "note", required=False, max_len=500) or "")
        written = self._emit([NewEvent(type=f"hr.{stage}", actor="hr", provider="local",
                                       payload={"candidate_id": candidate_id,
                                                "name_masked": cand["name_masked"],
                                                "note": note},
                                       idempotency_key=f"hr:{stage}:{candidate_id}")])
        if stage == "shortlisted":
            self._notify_ceo("shortlisted", f"Shortlisted: {cand['name_masked']}",
                             f"{cand['name_masked']} is shortlisted ({cand.get('credentials')}). "
                             "Hire or decline in the CEO console.",
                             key=f"ceo:shortlisted:{candidate_id}:{written[0].id}")
        return {"candidate_id": candidate_id, "stage": stage}

    def _get_candidate(self, args: dict) -> dict:
        """v1.4 (hr): the masked candidate. Stated credentials, questionnaire answers with
        contact details removed, stage; never the address."""
        candidate_id = _str(args, "candidate_id", max_len=64)
        cand = self.state.candidates.get(candidate_id)
        if cand is None:
            raise Refused("not_found", f"no candidate {candidate_id}")
        return {"candidate_id": candidate_id, "name_masked": cand["name_masked"],
                "stage": cand.get("stage"), "credentials": cand.get("credentials"),
                "answers": list(cand.get("texts", [])),
                "questionnaire_sent": bool(cand.get("questionnaire_sent"))}

    FLAG_KINDS = ("possible_injection", "unrealistic_inputs")

    def _flag_case(self, args: dict) -> dict:
        """v1.4 (qa): `case.flagged`; it goes into the next CEO update email."""
        case = self._case(args)
        kind = _one_of(args, "kind", self.FLAG_KINDS)
        written = self._emit([NewEvent(type="case.flagged", actor="qa", case_id=case.case_id,
                                       payload={"kind": kind},
                                       idempotency_key=f"flag:{case.case_id}:{kind}")])
        hour_ago = self._now() - timedelta(hours=1)
        mailed = [ts for ts in self.state.flag_notice_log if parse_ts(ts) > hour_ago]
        if len(mailed) < FLAG_MAILS_PER_HOUR:  # v1.5: the rest go into the digest
            self._notify_ceo("flagged", f"Flagged: {case.masked_name}",
                             f"{case.masked_name}: QA flagged {kind.replace('_', ' ')}",
                             key=f"ceo:flagged:{case.case_id}:{kind}:{written[0].id}")
        return {"case_id": case.case_id, "kind": kind}

    def _candidate_decision(self, args: dict) -> dict:
        """POST /api/candidates/{id}/decision: the human makes the final call (legal
        limit), as `hr.hired` / `hr.declined` with actor ceo, plus an operating touch."""
        candidate_id = _str(args, "candidate_id", max_len=64)
        decision = _one_of(args, "decision", ("hire", "decline"))
        cand = self.state.candidates.get(candidate_id)
        if cand is None:
            raise Refused("not_found", f"no candidate {candidate_id}")
        stage = "hired" if decision == "hire" else "declined"
        if cand.get("stage") in ("hired", "declined"):
            # a repeat click retries a decision mail that didn't go out (key-deduped)
            return {"candidate_id": candidate_id, "stage": cand["stage"], "duplicate": True,
                    "mail": self._send_decision_mail(candidate_id)}
        note = _str(args, "note", required=False, max_len=300) or f"{stage} by the CEO"
        written = self._emit([NewEvent(type=f"hr.{stage}", actor="ceo", provider="local",
                             payload={"candidate_id": candidate_id,
                                      "name_masked": cand["name_masked"], "note": note},
                             idempotency_key=f"hr:{stage}:{candidate_id}"),
                    NewEvent(type="touch.operating", actor="ceo",
                             payload={"note": f"{decision} {cand['name_masked']}",
                                      "approval_id": None},
                             idempotency_key=f"touch:hr:{stage}:{candidate_id}")])
        mail = self._send_decision_mail(candidate_id) if written else "skipped"
        return {"candidate_id": candidate_id, "stage": stage, "mail": mail}

    def _send_decision_mail(self, candidate_id: str) -> str:
        """v1.5: hr-hired / hr-declined to the candidate, under email trust. Gateway-owned:
        if it needs an approval, the approval sends it."""
        cand = self.state.candidates[candidate_id]
        stage = cand.get("stage")
        if stage not in ("hired", "declined") or not cand.get("reply_to"):
            return "skipped"
        if cand.get("decision_mailed"):
            return "sent"
        key = f"decision:{candidate_id}"
        try:
            self._gate("email", key, _cap(f"{self._first('email')}{stage} mail: "
                                          f"{cand['name_masked']}"), None)
        except NeedsApproval:
            return "needs_approval"
        except Refused as err:
            return err.code
        try:
            self._with_retry("decision email", lambda: self.ports.mail.send(
                OutMail(to=cand["reply_to"], subject=DECISION_SUBJECT,
                        body=self.templates.render(f"hr-{stage}", {}), inbox="hiring"),
                key))
        except ToolError as err:
            log.warning("decision mail to %s failed: %s", candidate_id, err)
            return "failed"
        self._emit([NewEvent(type="hr.decision_mailed", actor="system",
                             provider=self.ports.mail.provider,
                             payload={"candidate_id": candidate_id,
                                      "name_masked": cand["name_masked"],
                                      "note": f"{stage} mail sent"},
                             idempotency_key=f"decision_mailed:{candidate_id}")])
        return "sent"

    def _set_trust(self, args: dict) -> dict:
        """POST /api/trust: the CEO's standing order ("stop posting" = social_post PAUSED;
        resume = AUTO). trust.changed + touch.operating."""
        action_type = _one_of(args, "action_type", TRUST_TYPES)
        to = _one_of(args, "state", ("PAUSED", "AUTO"))
        current = self.state.trust[action_type]
        if current == to:
            return {"action_type": action_type, "trust": to, "duplicate": True}
        waiting = self._pending_of(action_type) if to == "AUTO" else []
        self._emit([NewEvent(type="touch.operating", actor="ceo",
                             payload={"note": f"{action_type} \u2192 {to}",
                                      "approval_id": None}),
                    NewEvent(type="trust.changed", actor="system",
                             payload={"action_type": action_type, "from": current, "to": to,
                                      "reason": "CEO order"})])
        self._wake_superseded(waiting)
        if to == "AUTO":  # resumed: wake whatever the pause held up
            for case in self.state.cases.values():
                if case.status in ("QA_PASSED", "SENT", "INTAKE_PENDING") and not case.priority:
                    self._wake(f"New case {case.case_id}")
        return {"action_type": action_type, "trust": to}

    def _book_spend(self, args: dict) -> dict:
        """Contract §6a: the gateway books agent spend; no agent reports money.

        Sources: Naïve `session.usage` (`consumed_micro_usd`, integer micro-USD), a sponsor
        response's cost (decimal string), or the code price table (`estimated: true`).
        """
        provider = _str(args, "provider", max_len=40).lower()
        if provider not in PROVIDERS:
            raise Refused("bad_input", f"unknown provider {provider!r}")
        model = _str(args, "model", required=False, max_len=120) or "unknown"
        tokens_in, tokens_out = _int(args, "tokens_in"), _int(args, "tokens_out")
        micro = args.get("consumed_micro_usd")
        raw_cost = args.get("cost_usd")
        try:
            if micro is not None:
                if isinstance(micro, bool) or not isinstance(micro, int) or micro < 0:
                    raise ValueError("consumed_micro_usd must be integer micro-USD")
                cost = Decimal(micro) / 1_000_000
            else:
                cost = parse_usd(raw_cost) if raw_cost not in (None, "") else None
        except (TypeError, ValueError, InvalidOperation) as err:
            raise Refused("bad_input", f"bad cost: {err}") from err
        estimated = cost is None or args.get("estimated") is True  # v1.7 per-run estimates
        if cost is None:
            cost = estimate_usd(model, tokens_in, tokens_out)
        case_id = _str(args, "case_id", required=False, max_len=64)
        if case_id and case_id not in self.state.cases:
            case_id = None
        key = args.get("idempotency_key")
        if micro is not None and not (isinstance(key, str) and key):
            raise Refused("bad_input", "usage bookings need idempotency_key (the usage event id)")
        self._emit([NewEvent(type="cost.booked", actor="system", case_id=case_id, provider=provider,
                             payload={"provider": provider, "model": model,
                                      "estimated": estimated},
                             tokens_in=tokens_in, tokens_out=tokens_out,
                             cost_usd=usd_string(cost),
                             idempotency_key=f"cost:{key}" if isinstance(key, str) and key else None)])
        return {"cost_usd": usd_string(cost), "estimated": estimated}

    # ------------------------------------------------------------ outbox (CRM mirror)

    OUTBOX_BACKOFF = (0, 30, 120, 600)  # seconds before attempt n (then every 10 min)

    def drain_outbox(self) -> None:
        """Mirror jobs (HubSpot): `crm.sync_requested` without `crm.synced`.

        Runs from the tick only, outside the lock (CRM latency never touches a request or an
        agent), with per-job backoff, and never blocks a client (task 10):
        - a case's stage jobs go in order: a later stage waits while an earlier one of the
          same case is still pending (in flight, backing off, or failed);
        - a rate limit stops the whole batch and pauses the outbox for its Retry-After.
        """
        now = time.monotonic()
        if now < self._outbox_paused_until:
            return
        with self._lock:
            jobs = []
            held: set[str] = set()  # cases with an earlier stage still pending
            for sync_key, job in self.state.crm_pending.items():
                case = self.state.cases.get(job["case_id"])
                if case is None:
                    continue
                if case.case_id in held:
                    continue
                tries, last = self._outbox_tries.get(sync_key, (0, 0.0))
                wait = self.OUTBOX_BACKOFF[min(tries, len(self.OUTBOX_BACKOFF) - 1)]
                held.add(case.case_id)  # later stages of this case wait for this one
                if now - last >= wait and sync_key not in self._outbox_inflight:
                    self._outbox_inflight.add(sync_key)  # claimed: no double mirror
                    jobs.append((sync_key, dict(job), case.case_id, case.masked_name))
        for index, (sync_key, job, case_id, masked_name) in enumerate(jobs):
            limited: RateLimited | None = None
            try:
                self.ports.crm.upsert_contact(CrmContact(key=case_id, label=masked_name))
                self.ports.crm.set_stage(f"deal:{case_id}", job["stage"])
                ok = True
            except RateLimited as err:
                limited, ok = err, False
                log.warning("crm sync %s rate limited; outbox paused", sync_key)
            except Exception as err:  # noqa: BLE001 - a mirror never fails the firm
                log.warning("crm sync %s failed: %s", sync_key, err)
                ok = False
            with self._lock:
                self._outbox_inflight.discard(sync_key)
                if limited is not None:
                    # not this job's fault: no backoff step, no failure event; the whole
                    # batch waits for the provider's Retry-After
                    self._outbox_paused_until = time.monotonic() + \
                        min(max(limited.retry_after or 30.0, 1.0), 600.0)
                    for rest_key, *_ in jobs[index + 1:]:
                        self._outbox_inflight.discard(rest_key)
                    return
                tries, _ = self._outbox_tries.get(sync_key, (0, 0.0))
                self._outbox_tries[sync_key] = (tries + 1, time.monotonic())
                if not ok and sync_key in self.state.crm_failed:
                    continue  # the failure is already on the log; just retry later
                try:
                    self._emit([NewEvent(
                        type="crm.synced", actor="system", case_id=case_id,
                        provider=self.ports.crm.provider, status="ok" if ok else "failed",
                        payload={"object": job["object"], "stage": job["stage"]},
                        idempotency_key=f"{'crmok' if ok else 'crmfail'}:{sync_key}",
                        private={"sync_key": sync_key})])
                except EventWriteError as err:
                    log.error("outbox result not logged: %s", err)
                    for rest_key, *_ in jobs[index + 1:]:
                        self._outbox_inflight.discard(rest_key)
                    return
                if ok:
                    self._outbox_tries.pop(sync_key, None)

_CREDENTIALS = re.compile(
    r"(?i)\b(EA|CPA|CMA|CFP|JD|enrolled agent|tax attorney|attorney|bookkeeper)\b")
_YEARS = re.compile(r"(?i)\b(\d{1,2})\+?\s*(?:years?|yrs?)\b")


def credentials_summary(text: str) -> str:
    """"EA · 6 yrs" from an applicant's mail: stated credentials only, never free text."""
    found: list[str] = []
    for match in _CREDENTIALS.finditer(text):
        word = match.group(1)
        label = "EA" if word.lower() == "enrolled agent" else (
            word.upper() if len(word) <= 3 else word.lower())
        if label not in found:
            found.append(label)
    years = _YEARS.search(text)
    if years:
        found.append(f"{years.group(1)} yrs")
    return " · ".join(found) or "no credentials stated"


_HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z0-9_]{2,}")
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[a-z0-9-]+\.(?:com|io|ai|dev|co|net|org|app|xyz)\b\S*")


def post_problem(text: str, *, allowed_url: str = "") -> str | None:
    """Contract §6 post_social content guard: no client may be identifiable in a post,
    and the only link is the firm's own PUBLIC_TALK_URL (v1.4)."""
    if _HANDLE.search(text):
        return "it mentions an @handle"
    allowed = allowed_url.rstrip("/").lower()
    for match in _URL.finditer(text):
        url = match.group(0).rstrip(".,;!?)").rstrip("/").lower()
        if not allowed or url != allowed:
            return "it links somewhere other than the firm's talk page"
    if _CASE_REF.search(text) or _TEAM_WORD.search(text):
        return "it names a case or a Team #N"
    if has_email(text):
        return "it contains an email address"
    if has_phone(text) or _BARE_PHONE.search(text):
        return "it contains a phone number"
    if _WORD_AMOUNT.search(text):
        return "it names a dollar amount in words (only the headline $ numbers may appear)"
    allowed = ", ".join(sorted(POST_ALLOWED_AMOUNTS, key=lambda a: -len(a)))
    for match in _POST_AMOUNT.finditer(text):
        amount = match.group(0).replace(" ", "").rstrip(",.")
        if amount not in POST_ALLOWED_AMOUNTS:
            return f"it names the amount {amount} (only {allowed} may appear)"
    return None


_FEE_RE = re.compile(
    r"(?is)annual\s+report\s+(?:filing\s+)?fee[^$\n]{0,60}?\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)(?![\d,])"
)
FEE_BOUNDS_CENTS = (10_00, 500_00)  # outside this, the page changed: use the snapshot


def parse_fee_cents(html: str) -> int | None:
    """The annual report filing fee from the Delaware fee page (or its snapshot)."""
    text = re.sub(r"<[^>]+>", " ", html)
    match = _FEE_RE.search(text)
    if not match:
        return None
    cents = int(Decimal(match.group(1).replace(",", "")) * 100)
    low, high = FEE_BOUNDS_CENTS
    return cents if low <= cents <= high else None
