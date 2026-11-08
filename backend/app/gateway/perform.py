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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    # v1.11 (D31): the live CEO orders
    "make_ad": ("mkt",), "source_candidates": ("hr",), "hr_outreach": ("hr",),
    "follow_up": ("intake",), "list_candidates": ("hr",),  # v1.11.2
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


def _episode(n: int) -> str:
    """The idempotency suffix of a buy key's nth unsure episode ("" for the first). "#" is
    outside the key charset, so no agent key can collide with it."""
    return f"#{n}" if n else ""


def _holds_nul(value: Any) -> bool:
    """Any string (key or value) in a JSON value that holds a NUL character."""
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            if "\x00" in node:
                return True
        elif isinstance(node, dict):
            stack.extend(node.keys())
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return False


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


def _fit_labels(labels: list[str], room: int) -> str:
    """Task 15: as many whole labels as fit in `room`, then "+N more" (the CEO sees what is
    missing instead of a cut list). The first label always shows; `clip` trims it if needed."""
    for shown in range(len(labels), 0, -1):
        more = len(labels) - shown
        text = ", ".join(labels[:shown]) + (f" +{more} more" if more else "")
        if len(text) <= room or shown == 1:
            return text
    return ""


def _when(iso: str) -> str:
    """A slot for a person: "Saturday, November 8 at 10:00 AM Pacific". The slots live in
    9:00-16:00 PST (SLOT_DAY_UTC), so a fixed -8 offset (no tz database needed on Windows)."""
    local = parse_ts(iso).astimezone(timezone(timedelta(hours=-8)))
    hour = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%A, %B')} {local.day} at {hour} Pacific"


def _form_greeting(name: str) -> str:
    """v1.12: a sign-up's greeting from the form's name: letters, spaces, ' and - only,
    whitespace collapsed, at most 40 characters; anything else is "there"."""
    text = " ".join((name or "").split())
    if not text or len(text) > 40 or not re.fullmatch(r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*", text):
        return "there"
    return text


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
        self._touch_channel: str | None = None  # v1.8: set by the console; touches: click
        self._law_inflight = 0
        self._buy_inflight: set[str] = set()  # buy keys between their intent and their events
        self._outbox_paused_until = 0.0  # a CRM rate limit pauses the whole outbox
        self._outbox_limited: dict[str, int] = {}  # rate limits per job (then it backs off)
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
            "make_ad": self._make_ad,
            "source_candidates": self._source_candidates,
            "hr_outreach": self._hr_outreach,
            "follow_up": self._follow_up,
            "list_candidates": self._list_candidates,
            "resolve_purchase": self._resolve_purchase,  # control: the CEO, not an agent
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
            # v1.8 §5b: the CEO voice console (admin routes only)
            "ceo_tool": self._ceo_tool,
            "ceo_confirm": self._ceo_confirm,
            "ceo_command": self._ceo_command,
            # v1.9 §6b-bis: Naïve's data-leak confirms (app/naive_confirm.py), never an agent's
            "naive_confirm": self._naive_confirm,
            "naive_question": self._naive_question,
            "naive_notice": self._naive_notice,
            # v1.12 §5a (D32): the product shell's public sign-up
            "signup": self._signup,
            "naive_recovered": self._naive_recovered,
        }
        self._naive_noticed: dict[tuple[str, str], datetime] = {}
        self._welcome_failures: dict[str, int] = {}  # v1.12: case -> failed welcome sends
        self._naive_questions: set[str] = set()  # notices tried this process (mailed or not)
        self._wakers: list[Callable[[str], None]] = []
        self._pending_wakes: list[str] = []
        # reads that touch a slow port run before the lock is taken (contract §6f)
        self._slow_reads: dict[str, Callable[[dict], dict]] = {
            "law_search": self._law_fetch, "buy_tool": self._buy_fetch,
            "source_candidates": self._source_fetch}  # v1.11: Monid search before the lock
        self._order_ctx = threading.local()  # v1.11.1: the CEO order a tool call serves
        from app.gateway.adapters.local import _KeyedJson

        self._buy_intents = _KeyedJson(settings.data_dir / "toolbuy-intents.json")
        # v1.11: an outreach's slot, written before its email: a retry can't pick another
        self._outreach_intents = _KeyedJson(settings.data_dir / "outreach-intents.json")
        self._restore_toolbuy()
        self._record_leftovers()
        self._expire_proposals()
        self._seed_hr_candidates()
        self._outbox_tries: dict[str, tuple[int, float]] = {}
        self._outbox_inflight: set[str] = set()
        self._digest_backoff_until = 0.0
        self._public_cap_retry_at = 0.0  # task 13: next try of a failed at-capacity notice
        self._voice_pending: dict[str, int] = {}  # task 13: paid voice calls in flight
        self._voice_pending_usd = Decimal(0)
        self._tts_talk_pending = 0  # v1.10 §5c: /talk TTS characters in flight
        # failed wake deliveries: (message, waker, attempt, due monotonic time)
        self._wake_retries: list[tuple[str, Callable[[str], None], int, float]] = []

    READ_ONLY = frozenset({"list_open_cases", "get_case", "list_candidates"})

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
                    and c.get("stage") == "applicant" and not c.get("listed")
                    and self._mail_may_go(f"questionnaire:{cid}")]
            return [cid for cid in todo if self._send_questionnaire(cid) == "sent"]

    def _mail_may_go(self, key: str) -> bool:
        """Email AUTO, or (task 14) an allowed approval for this gateway-owned mail and email
        not PAUSED: an OK carried out on a resume to ASK whose send failed is retried."""
        trust = self.state.trust["email"]
        if trust == "AUTO":
            return True
        approval = self.state.approvals.get(self.state.approval_by_key.get(key, ""))
        return trust != "PAUSED" and approval is not None and approval.decision == "allow"

    def retry_decision_mails(self) -> list[str]:
        """Decision mails that couldn't go out (paused, failed) are retried by the tick;
        the mail port's key makes a resend of a delivered one a no-op."""
        with self._lock:
            todo = [cid for cid, c in self.state.candidates.items()
                    if c.get("stage") in ("hired", "declined") and c.get("reply_to")
                    and not c.get("decision_mailed")
                    and self._mail_may_go(f"decision:{cid}")]
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

    def wake_trigger(self, message: str) -> int | None:
        """22b: the event a once-per-entity wake answers. "New case X" / "FAST PATH X": the
        case's latest inbound mail or voice; "New applicant X": the latest applicant mail.
        A second email on an open case is a new trigger (a new Naive session); a restart's
        boot wake finds the same one (Naive dedupes it)."""
        kind, _, entity = message.rpartition(" ")
        with self._lock:
            if kind in ("New case", "FAST PATH"):
                case = self.state.cases.get(entity)
                return case.last_inbound_id if case is not None else None
            if kind == "New applicant":
                return (self.state.candidates.get(entity) or {}).get("last_event_id")
        return None

    def flagged_injection(self, case_id: str | None) -> bool:
        case = self.state.cases.get(case_id) if case_id else None
        return case is not None and "possible_injection" in case.flags

    def public_event(self, event: Event, *, admin: bool = False) -> dict[str, Any]:
        """An event as theater readers see it (v1.7.1): once QA flags a case as a possible
        injection, every event of that case, earlier ones too, has its subject and body
        replaced. The admin token still sees the originals."""
        out = event.public()
        if not admin:
            out = self._without_ceo_words(event, out)
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

    ORDER_READBACK_PUBLIC = "An order for the Chief of Staff. Say confirm."

    def _without_ceo_words(self, event: Event, out: dict[str, Any]) -> dict[str, Any]:
        """v1.8.5: the CEO's spoken words and order text are private. Rows written before
        that kept them in the payload; theater readers get the same shape as new rows."""
        payload = out["payload"]
        if event.type in ("ceo.confirmed", "ceo.cancelled") and "heard" in payload:
            payload = {k: v for k, v in payload.items() if k != "heard"}
            payload["heard_len"] = len(out["payload"]["heard"] or "")
        elif (event.type == "ceo.proposed" and payload.get("tool") == "order"
              and "text" in (payload.get("args") or {})):
            payload = {**payload, "args": {"text_len": len(payload["args"]["text"] or "")},
                       "readback": self.ORDER_READBACK_PUBLIC}
        else:
            return out
        return {**out, "payload": payload}

    def public_case_cap(self) -> int:
        """PUBLIC_CASES_PER_DAY, else 50 (200 with PUBLIC_MODE=event: the finale crowd)."""
        cap = self.settings.public_cases_per_day
        if cap is None:
            return 200 if self.settings.public_mode == "event" else 50
        return max(0, cap)

    def voice_refusal(self, kind: str, *, public: bool = True) -> str | None:
        """Task 13: why paid voice (`realtime` mint, `tts` brief) can't run now, else None.
        Read under the lock. Typed chat and the browser's own speech keep working.

        - SPEND_CAP_USD reached.
        - Mints only (the public, unauthenticated one): /talk is at its daily case cap, or
          today's mints reached 2 x PUBLIC_CASES_PER_DAY. That bounds what anonymous mints
          can book a day (2 x 50 x $0.30 = $30 by default), so they can't run the firm-wide
          budget out overnight."""
        cap = self.settings.spend_cap_usd
        with self._lock:
            if cap is not None and self.state.spent_usd() + self._voice_pending_usd >= cap:
                return "budget"
            if kind != "realtime" or not public:  # the CEO's mints have their own cap
                return None
            day = iso_ts(self._now())[:10]
            cases = self.public_case_cap()
            if self.state.public_cases_by_day.get(day, 0) >= cases:
                return "at_capacity"
            if (self.state.voice_mints_by_day.get(day, 0)
                    + self._voice_pending.get("realtime", 0)) >= 2 * cases:
                return "mints"
        return None

    def reserve_voice(self, kind: str, *, paid: bool, public: bool = True) -> str | None:
        """Task 13: check `voice_refusal` and, for a paid provider, hold the estimate as in
        flight (counted against the budget and the mint cap) in one step under the lock,
        BEFORE the paid call, so concurrent calls can't all pass the same check. A store
        that can't be written refuses the call ("unbooked"): the spend couldn't be recorded.
        Every reservation that returns None must be settled with `settle_voice`."""
        from app.gateway.pricing import VOICE_USD

        with self._lock:
            refusal = self.voice_refusal(kind, public=public)
            if refusal or not paid:
                return refusal
            try:
                self.store.ensure_writable()
            except EventWriteError as err:
                log.warning("voice %s refused, spend can't be booked: %s", kind, err)
                return "unbooked"
            self._voice_pending[kind] = self._voice_pending.get(kind, 0) + 1
            self._voice_pending_usd += VOICE_USD[kind]
        return None

    def settle_voice(self, kind: str, model: str, *, ok: bool) -> None:
        """Release a `reserve_voice` hold; book the estimated cost.booked only when the
        call produced a session or audio (OpenAI reports no cost on either), so a provider
        outage books no phantom spend and never uses up the day's mints."""
        from app.gateway.pricing import VOICE_USD

        with self._lock:
            self._voice_pending[kind] = max(0, self._voice_pending.get(kind, 0) - 1)
            self._voice_pending_usd = max(Decimal(0), self._voice_pending_usd - VOICE_USD[kind])
            if not ok:
                return
            result = self.perform("book_spend", {
                "provider": "openai", "model": f"{kind}:{model}"[:120],
                "cost_usd": str(VOICE_USD[kind]), "estimated": True})
        if result.status != "done":
            log.error("voice %s spend not booked after the call: %s", kind, result.error)

    @staticmethod
    def _tts_usd(provider: str | None, chars: int) -> Decimal:
        """The estimate a TTS call holds while in flight and books once it answered."""
        from app.gateway.pricing import ELEVENLABS_USD_PER_1K_CHARS, VOICE_USD

        cost = (ELEVENLABS_USD_PER_1K_CHARS * chars / 1000 if provider == "elevenlabs"
                else VOICE_USD["tts"])
        return max(cost, Decimal("0.0001")).quantize(Decimal("0.0001"))

    def reserve_tts(self, purpose: str, chars: int, provider: str | None = "elevenlabs"
                    ) -> str | None:
        """v1.10 §5c: hold a /api/tts call before the paid request, under the lock, so
        concurrent calls can't all pass the same check. The hold is the call's own estimate
        (per character on ElevenLabs). "budget" at SPEND_CAP_USD, "unbooked" when the log
        can't be written, "talk_budget" when /talk's TTS_PUBLIC_CHARS_PER_DAY would be passed
        (in-flight characters count). A None must be settled (settle_tts)."""
        hold = self._tts_usd(provider, chars)
        cap = self.settings.spend_cap_usd
        with self._lock:
            if cap is not None and self.state.spent_usd() + self._voice_pending_usd + hold > cap:
                return "budget"
            try:
                self.store.ensure_writable()
            except EventWriteError as err:
                log.warning("tts refused, spend can't be booked: %s", err)
                return "unbooked"
            if purpose == "talk":
                day = iso_ts(self._now())[:10]
                used = self.state.tts_talk_chars_by_day.get(day, 0) + self._tts_talk_pending
                if used + chars > self.settings.tts_public_chars_per_day:
                    return "talk_budget"
                self._tts_talk_pending += chars
            self._voice_pending["tts"] = self._voice_pending.get("tts", 0) + 1
            self._voice_pending_usd += hold
        return None

    def settle_tts(self, purpose: str, chars: int, provider: str, model: str, *,
                   ok: bool) -> None:
        """Release a reserve_tts hold (the same estimate); book it as cost.booked (chars in
        tokens_in) only when the provider answered with audio."""
        cost = self._tts_usd(provider, chars)
        with self._lock:
            if purpose == "talk":
                self._tts_talk_pending = max(0, self._tts_talk_pending - chars)
            self._voice_pending["tts"] = max(0, self._voice_pending.get("tts", 0) - 1)
            self._voice_pending_usd = max(Decimal(0), self._voice_pending_usd - cost)
            if not ok:
                return
            result = self.perform("book_spend", {
                "provider": provider, "model": f"tts:{purpose}:{model}"[:120],
                "tokens_in": chars, "cost_usd": str(cost), "estimated": True})
        if result.status != "done":
            log.error("tts spend not booked after the call: %s", result.error)

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
        for private in ("_actor", "_prefetch", "_law_reserved", "_buy_reserved",
                        "_order_id"):
            args.pop(private, None)  # never caller-supplied
        if actor is not None:
            args["_actor"] = actor
            order = self._valid_order(args.pop("order_id", None))  # v1.11.1, else ignored
            if order:
                args["_order_id"] = order
        slow = self._slow_reads.get(name)
        if slow is not None:
            try:
                args["_prefetch"] = slow(args)  # outside the lock
            except EventWriteError as err:  # the log can't take a write: nothing was paid
                log.error("event store unavailable: %s", err)
                self._release(args)
                return ToolResult("error", error={
                    "code": "store_unavailable",
                    "message": "event log unavailable; retry the same call when it is back"})
            except Exception:  # noqa: BLE001 - a broken adapter must not kill the server
                log.exception("%s failed unexpectedly before the lock", name)
                self._release(args)
                return ToolResult("error", error={"code": "internal_error",
                                                  "message": f"{name} failed unexpectedly"})
        prev_order = getattr(self._order_ctx, "value", None)
        self._order_ctx.value = args.get("_order_id") or prev_order
        try:
            result, wakes = self._perform_locked(name, handler, args)
        finally:
            self._order_ctx.value = prev_order
            self._release(args)
        if name == "phone_check" and result.status == "done" and result.data.get("pending"):
            try:
                self.advance_phone_jobs()  # outside the lock: a local device is done now
            except Exception:  # noqa: BLE001 - the tick tries again
                log.exception("phone job failed to advance")
        # Outside the lock: a slow Naïve call or CRM mirror never blocks the next write.
        self._deliver_wakes(wakes)
        return result

    def _release(self, args: dict) -> None:
        """Give back what a slow read reserved: after its handler ran, or after it failed."""
        with self._lock:
            if args.get("_law_reserved"):
                self._law_inflight -= 1
            if args.get("_buy_reserved"):
                self._buy_inflight.discard(args["_buy_reserved"])

    def _perform_locked(self, name: str, handler: Callable[[dict], dict],
                        args: dict) -> tuple[ToolResult, list[str]]:
        with self._lock:
            self._pending_wakes = []
            try:
                if name not in self.READ_ONLY and not self._is_read(name, args):
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
        channel = getattr(self, "_touch_channel", None) or "click"
        events = [replace(e, payload={**e.payload, "channel": channel})
                  if e.type.startswith("touch.") and "channel" not in e.payload else e
                  for e in events]  # v1.8: every touch says how the CEO touched
        order = getattr(self._order_ctx, "value", None)
        if order:  # v1.11.1: the theater groups a CEO order's steps by it
            events = [e if "order_id" in e.payload
                      else replace(e, payload={**e.payload, "order_id": order}) for e in events]
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
    SUMMARY_MAX = 160  # approval summaries (the /ceo list, the CEO's mail)
    NOTE_MAX = 160  # touch notes ("approved: <summary>", the CEO's unblock note)

    def _gate(self, action_type: str, action_key: str, summary: str, case_id: str | None,
              *, public: bool = False, detail: str | None = None) -> None:
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
            # "superseded": the type went AUTO and back to ASK, or (task 14) a newer request
            # or a reopen replaced it: ask again
        old = self.state.approvals.get(approval_id or "")
        # the request that replaced it is still waiting on the CEO: no second mail
        replaced = old is not None and (old.superseded_by or "").startswith("apr_") \
            and self._newest(old) is not None
        approval_id = f"apr_{len(self.state.approvals) + 1}"
        summary = clip(summary, self.SUMMARY_MAX)  # task 15: whole words, never "a foreign o"
        self._emit([NewEvent(type="approval.requested", actor="system", case_id=case_id,
                             payload={"approval_id": approval_id, "action_type": action_type,
                                      "summary": summary},
                             idempotency_key=f"approval:{action_key}:{approval_id}",
                             private={"action_key": action_key,
                                      **({"detail": detail} if detail else {})})])
        # anonymous traffic is throttled; so is an agent going back and forth between two
        # requests (slot A, B, A...): the console lists the newest, one mail was enough
        if not (public and self._recent_public_notice()) and not replaced:
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
        if not new and self.state.candidates.get(candidate_id, {}).get("contacted"):
            # task 32: the reply to the outreach note books the call and sends the invite
            self._send_candidate_invite(candidate_id, reply_id=mail.message_id)
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

    # ------------------------------------------------------------ v1.12 §5a (D32)

    SIGNUPS_PER_EMAIL_PER_DAY = 3
    WELCOME_TRIES = 3  # a welcome the provider keeps refusing stops after this many
    SIGNUP_SUBJECT = "Your free Delaware franchise tax check"

    def _signup(self, args: dict) -> dict:
        """POST /api/signup: a case opened like an inbound email (source "signup"), the crew
        woken, and one welcome email asking for the numbers (trust email)."""
        name = " ".join(_str(args, "name", max_len=120).split())
        company = " ".join(_str(args, "company", max_len=120).split())  # required, not stored
        email = _str(args, "email", max_len=320).strip()
        if not name or not company:
            raise Refused("bad_input", "name and company are required")
        if not _EMAIL_RE.match(email):
            raise Refused("bad_input", "email must be an email address")
        identity = mail_identity(email)
        own = {mail_identity(a) for a in (self.settings.ceo_email, self.settings.inbox_clients,
                                          self.settings.inbox_hiring) if a}
        if identity in own:  # never a case (or a welcome mail) to the firm itself
            raise Refused("bad_input", "that address can't sign up")
        request_id = _str(args, "request_id", required=False, max_len=80)
        # the address in the key: a replayed or guessed id never returns another's case
        key = f"signup:{mail_identity(email)}:{request_id}" if request_id else None
        if key:
            seen = self._done_from(key)
            if seen is not None:
                return {"case_id": seen.case_id, "masked_name": seen.payload["masked_name"],
                        "duplicate": True}
        day = iso_ts(self._now())[:10]
        sender = email.lower()
        if self.state.signups_by_day.get((day, identity), 0) >= self.SIGNUPS_PER_EMAIL_PER_DAY:
            raise Refused("rate_limited", "this email has signed up 3 times today; reply to "
                                          "our welcome email instead")
        # an address that already belongs to a client: the same generic answer, nothing
        # created, nothing mailed (never a way to test or re-mail an existing client)
        if any(mail_identity(k) == identity for k in self.state.case_by_sender):
            return {"ok": True}
        self._public_cap()  # the public daily case cap (raises at_capacity)
        number = self._new_case_number()
        case_id = f"case_{number}"
        masked_name = f"Team #{number}"
        sender_key = sender
        body = "Signed up on the U-Tax site for a free Delaware franchise tax check."
        events = [NewEvent(
            type="email.received", actor="intake", case_id=case_id, provider="local",
            payload={"masked_name": masked_name, "from_masked": mask_email(sender),
                     "subject": "Sign-up", "body": body, "source": "signup"},
            idempotency_key=key,
            private={"reply_to": email, "sender_key": sender_key, "masked_name": masked_name,
                     # the company from the address, like any inbound mail: the typed
                     # one would feed the client-name guard ("startup" blocking posts)
                     "company": company_label(sender), "greeting_name": _form_greeting(name),
                     "identity": identity, "message_id": f"signup-{case_id}",
                     "redactions": 0,
                     "agent_text": agent_text(f"{body} Waiting for their numbers by email."),
                     "agent_subject": "Sign-up"},
        )]
        events += self._reopen(None, case_id)
        self._emit(events)
        self._wake(f"New case {case_id}")
        self._send_signup_welcome(case_id)
        return {"case_id": case_id, "masked_name": masked_name}

    def _send_signup_welcome(self, case_id: str) -> str:
        """The sign-up's one welcome email (trust email). On ASK the CEO's approval sends it
        later (retry_signup_welcomes, on the tick)."""
        key = f"signup-welcome:{case_id}"
        if self._done_from(key) is not None:
            return "sent"
        if self._welcome_failures.get(case_id, 0) >= self.WELCOME_TRIES:
            return "gave_up"  # a provider that keeps refusing: never retried forever
        case = self.state.cases[case_id]
        try:
            self._gate("email", key, _cap(f"{self._first('email')}welcome email: "
                                          f"{case.masked_name}"), case_id, public=True)
        except NeedsApproval:
            return "needs_approval"
        except Refused as err:  # denied, or email paused
            return err.code
        try:
            sent = self._with_retry("welcome email", lambda: self.ports.mail.send(
                OutMail(to=case.reply_to, subject=self.SIGNUP_SUBJECT,
                        body=self.templates.render("signup-welcome", {
                            "greeting_name": self._greeting(case)})), key))
        except ToolError as err:
            failures = self._welcome_failures.get(case_id, 0) + 1
            self._welcome_failures[case_id] = failures
            log.warning("welcome email for %s failed (%d/%d): %s", case_id, failures,
                        self.WELCOME_TRIES, err)
            return "failed"
        # public: it counts in /talk's own send bucket, never the firm's
        self._emit([NewEvent(type="client.followed_up", actor="intake", case_id=case_id,
                             provider=self.ports.mail.provider,
                             payload={"kind": "signup_welcome"}, idempotency_key=key,
                             private={"message_id": sent.message_id, "public": True})])
        return "sent"

    def retry_signup_welcomes(self) -> list[str]:
        """Welcome emails that couldn't go out (ASK, paused, failed): the tick retries."""
        with self._lock:
            todo = [cid for cid in sorted(self.state.signup_cases)
                    if self._done_from(f"signup-welcome:{cid}") is None
                    and self._mail_may_go(f"signup-welcome:{cid}")]
            return [cid for cid in todo if self._send_signup_welcome(cid) == "sent"]

    def record_tool_call(self, actor: str, tool: str, status: str, order_id: Any) -> None:
        """v1.12 §5a: tool.called for every MCP tool call (reads too): the seat, the tool,
        the outcome, the CEO order it served. No args or data. Never raises."""
        try:
            order = self._valid_order(order_id)
            with self._lock:
                self._emit([NewEvent(type="tool.called", actor=actor, payload={
                    "tool": tool, "status": status, **({"order_id": order} if order else {})})])
        except Exception:  # noqa: BLE001 - the animation event never fails a tool call
            log.exception("tool.called for %s %s not written", actor, tool)

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

    def _public_cap(self) -> None:
        """Task 13: at most PUBLIC_CASES_PER_DAY new /talk cases a UTC day, so an overnight
        flood can't drain the agents' budgets. At the cap: one CEO notice a day, then 429."""
        day = iso_ts(self._now())[:10]
        cap = self.public_case_cap()
        if self.state.public_cases_by_day.get(day, 0) < cap:
            return
        key = f"ceo:public-cap:{day}"
        # the logged ceo.notified keeps it to one a day (across restarts too); a failed mail
        # is tried again 10 minutes later, never on every flood request under the lock
        if time.monotonic() >= self._public_cap_retry_at and self._done_from(key) is None:
            self._public_cap_retry_at = time.monotonic() + 600
            summary = (f"public /talk reached its {cap} new cases for {day} (UTC); new /talk "
                       "cases are refused until tomorrow. Email still opens cases.")
            # own body: the "paused" template's lines ("resume it in the console") don't
            # apply, this lifts itself at midnight UTC (or raise PUBLIC_CASES_PER_DAY)
            self._notify_ceo("paused", "public /talk at capacity", summary, key=key,
                             body=f"{summary}\n\nIt lifts itself at midnight UTC; to take more "
                                  "today, raise PUBLIC_CASES_PER_DAY and restart.\n\n"
                                  f"{self.settings.ceo_console_url}")
        inbox = self.settings.inbox_clients or "our clients inbox"
        raise Refused("at_capacity", f"We're at capacity for today. Please email {inbox} and "
                                     "we'll pick your case up from there.")

    # task 13: /talk turns. The page keeps ~200 agent turns plus the customer's, so a hard
    # ceiling only stops abuse; agents get the first 60 non-empty turns, 1000 chars each
    MAX_TURNS, CONTEXT_TURNS, CONTEXT_CHARS = 1000, 60, 1000

    def _voice(self, args: dict) -> dict:
        # task 13: every refusal (and a retry) is decided before any masking, which is the
        # costly part (the email scan is quadratic), so a request that opens no case stays
        # cheap under the lock
        speaker = _one_of(args, "speaker", ("customer", "ceo"))
        reply = request_id = None
        if speaker == "customer":
            reply = _str(args, "reply_email", max_len=320)
            if not _EMAIL_RE.match(reply or ""):
                raise Refused("bad_input", "a customer case needs reply_email")
            request_id = _str(args, "request_id", required=False, max_len=80)
            if request_id:  # the page's own id for this submission: a retry is the same case
                seen = self._done_from(f"voice:{request_id}")
                if seen is not None:
                    return {"case_id": seen.case_id, "duplicate": True, "priority": True}
        channel = _one_of(args, "channel", ("voice", "typed"), default="typed")
        turns = args.get("turns")
        if turns is not None:
            if isinstance(turns, list) and len(turns) > self.MAX_TURNS:
                raise Refused("too_long", f"at most {self.MAX_TURNS} turns")
            # v1.5: only what the customer said counts; the AI's read-back never becomes a
            # quote (it would echo numbers back as if the client had written them)
            if not isinstance(turns, list) or not all(
                    isinstance(t, dict) and isinstance(t.get("text"), str) for t in turns):
                raise Refused("bad_input", "turns must be [{speaker, text}]")
            customer = [t["text"].strip() for t in turns
                        if t.get("speaker") == "customer" and t["text"].strip()]
            joined = "\n".join(customer)
            if len(joined) > 4000:
                raise Refused("too_long", "the customer's transcript is over 4000 characters")
            args = {**args, "text": joined}
        text = _str(args, "text", max_len=4000)
        if speaker == "customer":
            self._public_cap()
        context: list[dict] = []  # /talk turns kept for agents as labelled context
        for t in (turns or []) if speaker == "customer" else []:
            if len(context) >= self.CONTEXT_TURNS:
                break
            raw = t["text"].strip()
            if raw:  # cut before masking (cheap); when cut, the masked tail is dropped too,
                # since a contact detail split at the cut edge may not have been masked
                margin = 200
                masked = mask_spoken_emails(agent_text(redact_ids(
                    raw[:self.CONTEXT_CHARS + margin])[0]))
                if len(raw) > self.CONTEXT_CHARS + margin:
                    masked = masked[:max(0, len(masked) - margin)]
                context.append({"speaker": "customer" if t.get("speaker") == "customer"
                                else "agent", "text": masked[:self.CONTEXT_CHARS]})
        text, _ = redact_ids(text)
        text = mask_spoken_emails(text)
        if speaker == "ceo":
            text = mask_text(text, masked_name="", company=None)
            written = self._emit([
                NewEvent(type="voice.command", actor="ceo", provider="local",
                         payload={"text": text, "channel": channel, "speaker": "ceo",
                                  "case_id": None}),
                NewEvent(type="touch.operating", actor="ceo",
                         payload={"note": f'order: "{clip(text, 120)}"', "approval_id": None,
                                  "channel": self._touch_channel or channel}),
            ])
            # v1.11.1: the order's own id rides the wake, so each card can carry it
            self._wake(f"CEO order_{written[0].id}: {text}")
            return {"forwarded": True}
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
        if approval.decision == "superseded" and approval.superseded_by:  # task 14
            by = self._newest(approval)  # never a figure the CEO didn't see
            why = (f"decide {by} instead" if by else
                   "the case changed; the crew asks again if it still applies")
            raise Refused("superseded", f"{approval_id} is out of date: {why}",
                          {"approval_id": approval_id, "superseded_by": by})
        if approval.decision is not None:
            return {"approval_id": approval_id, "decision": approval.decision, "duplicate": True}
        action_type = approval.action_type
        current = self.state.trust[action_type]
        key = f"approve:{approval_id}"
        private = {"decision": decision, "ceo_note": note}
        target = approval.case_id or "firm"
        if decision == "deny":
            self._emit([NewEvent(type="touch.operating", actor="ceo", case_id=approval.case_id,
                                 payload={"note": clip(f"denied: {approval.summary}", self.NOTE_MAX),
                                          "approval_id": approval_id},
                                 idempotency_key=key, private=private)])
            self._wake(f"Approval {approval_id} denied for {target}")
            return {"approval_id": approval_id, "decision": "deny"}
        events: list[NewEvent] = []
        if current == "PAUSED":
            # the CEO's pause (or a cap) holds until POST /api/trust: record the OK now,
            # carry it out on resume (task 14)
            events.append(NewEvent(type="touch.operating", actor="ceo", case_id=approval.case_id,
                                   payload={"note": clip(f"approved while paused: {approval.summary}", self.NOTE_MAX),
                                            "approval_id": approval_id},
                                   idempotency_key=key, private={**private, "while_paused": True}))
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
                                   payload={"note": clip(f"approved: {approval.summary}", self.NOTE_MAX),
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

    def _newest(self, approval) -> str | None:
        """The pending approval that replaced this one (following the chain), or None."""
        seen = set()
        while approval.superseded_by and approval.superseded_by.startswith("apr_") \
                and approval.approval_id not in seen:
            seen.add(approval.approval_id)
            approval = self.state.approvals[approval.superseded_by]
        return approval.approval_id if approval.decision is None else None

    def _pending_of(self, action_type: str, *, exclude: str | None = None) -> list:
        return [a for a in self.state.approvals.values()
                if a.action_type == action_type and a.decision is None
                and a.approval_id != exclude]

    def _wake_superseded(self, approvals: list) -> None:
        """After a flip to AUTO, the other actions that were waiting on an approval of that
        type are superseded: wake their owners (or run the gateway-owned questionnaire)."""
        self._carry_out([a for a in approvals if a.decision == "superseded"])

    def _carry_out(self, approvals: list) -> None:
        """Run each gateway-owned action (questionnaire, decision mail); wake the owner of
        every other one so its agent retries the call (a post: the firm, as _approve does).
        A failed mail is retried by the tick (`_mail_may_go`)."""
        for a in approvals:
            try:
                if self._run_gateway_owned(a.action_key) is not None:
                    continue
            except EventWriteError:
                raise  # fail closed: the store is down (the tick retries the mail)
            except Exception:  # noqa: BLE001 - one bad send never strands the rest
                log.exception("carrying out %s failed; the tick retries it", a.approval_id)
                continue
            self._wake(f"Approval {a.approval_id} allowed for {a.case_id or 'firm'}")

    def _released(self, action_type: str, current: str) -> list:
        """Task 14: the OKs given while `action_type` was PAUSED, carried out on resume."""
        if current != "PAUSED":
            return []
        return [a for a in self.state.approvals.values()
                if a.action_type == action_type and a.decision == "allow" and a.while_paused]

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
        released = self._released(action_type, current)
        self._emit(events)
        self._carry_out(released)  # task 14: a stop out of PAUSED is a resume to ASK
        if current == "PAUSED":
            self._wake_held()
        return {"trust": self.state.trust[action_type]}

    def _wake_held(self) -> None:
        """Resumed: wake whatever the pause held up."""
        for case in self.state.cases.values():
            if case.status in ("QA_PASSED", "SENT", "INTAKE_PENDING") and not case.priority:
                self._wake(f"New case {case.case_id}")

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
                             payload={"note": clip(f"{case.masked_name}: {note}", self.NOTE_MAX),
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

    def _waiting_reason(self, case: Case) -> str:
        if case.status == "BLOCKED":
            return case.blocked_reason or "blocked: needs a human"
        if case.case_id in self.state.signup_cases and len(case.message_ids) <= 1:
            return "waiting on the client's reply"  # v1.12: the welcome asked for the numbers
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
        # task 26: no `next` hint; the Chief of Staff's routing table decides the step (the
        # hint contradicted it, e.g. ask_client on a STALLED case)
        return {"cases": [{"case_id": c.case_id, "masked_name": c.masked_name,
                           "status": c.status, "priority": c.priority,
                           "waiting_reason": self._waiting_reason(c)} for c in open_cases]}

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
        booked |= self.state.candidate_call_starts  # v1.11: one calendar for both
        try:  # an outreach under way holds its slot before its event is written
            booked |= {v for v in self._outreach_intents.load().values() if isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            log.exception("outreach intents unreadable; their slots are not held")
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
        anchor = max(parse_ts(last.ts), self._booted_at) if last is not None \
            else self._booted_at
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
        spend = sum((Decimal(e.cost_usd) for e in events
                     if e.cost_usd and e.type == "cost.booked"), Decimal(0))  # contract §4
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
        summary = _cap(f"{self._first('email')}re-ask email: {case.masked_name}, needs ")
        self._gate("email", key, summary + _fit_labels(
            [FACT_LABELS.get(m, m) for m in missing], self.SUMMARY_MAX - len(summary)),
            case.case_id, public=case.priority)
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
    BUY_INPUT_MAX = 2000  # characters of JSON: a data call needs a query, not a document
    BUY_RESULT_KEPT = 20_000  # a result up to this size is kept (private) for repeats
    BUY_RESULT_DEPTH = 100  # a paid result deeper than this, or with more keys and values
    BUY_RESULT_VALUES = 1_000_000  # than this, is neither stored nor relayed (json's C limit is the OS's)

    @staticmethod
    def _buy_fingerprint(tool: str, data: dict) -> str:
        from app.gateway.adapters import _http

        return _http.fingerprint(tool, json.dumps(data, sort_keys=True, allow_nan=False))

    def _buy_problem(self, tool: Any, data: Any, key: Any) -> str | None:
        if not isinstance(tool, str) or not tool or len(tool) > 80:
            return "tool must be a tool name from MONID_TOOLS"
        if not isinstance(data, dict):
            return "input must be an object"
        if not isinstance(key, str) or not self._BUY_KEY.fullmatch(key):
            return "key must be 1-120 of A-Z a-z 0-9 _ . : -"
        from app.gateway.adapters import _http

        if _http.too_nested(data):  # before json.dumps and before any intent is written
            return (f"input must nest at most {_http.MAX_DEPTH} levels "
                    f"and hold at most {_http.MAX_VALUES} keys and values")
        try:
            text = json.dumps(data, sort_keys=True, allow_nan=False, ensure_ascii=False)
        except (TypeError, ValueError, RecursionError):
            return "input must be plain JSON"
        if len(text) > self.BUY_INPUT_MAX:
            return f"input is over {self.BUY_INPUT_MAX} characters"
        import unicodedata

        folded = unicodedata.normalize("NFKC", text)  # fullwidth digits and @ fold to ASCII
        if has_email(folded) or has_phone(folded) or _CASE_REF.search(folded) \
                or redact_ids(folded)[1]:
            return ("input must not carry an email, a phone number, an SSN/EIN or a case id "
                    "(public startup data only)")
        return None

    def _buy_episode(self, port_key: str) -> tuple[int, Event | None, Event | None]:
        """(episode, its unsure record, its resolution). A "not charged" resolution closes an
        episode and frees the key, so a later unsure gets records of its own (#1, #2...);
        they used to dedupe into the first, and the second possible charge was forgotten."""
        episode = 0
        while True:
            unsure = self._done_from(f"tool:unsure:{port_key}{_episode(episode)}")
            resolved = self._done_from(f"tool:resolved:{port_key}{_episode(episode)}")
            if resolved is None or resolved.status != "skipped":
                return episode, unsure, resolved
            episode += 1

    def _buy_leftover(self, port_key: str, key: str, fp: str, left: dict) -> dict:
        """An intent an earlier call wrote and never settled (a crash, or a store error after
        Monid answered): it may have paid. A purchase this process still remembers is booked
        now; anything else becomes an unsure record for the CEO, never a second buy."""
        left_fp = left.get("fingerprint")
        recall = getattr(self.ports.toolbuy, "remembered", None)
        known = recall(port_key) if callable(recall) else None
        if known is not None and known[0] == left_fp:
            return {"purchase": known[1], "fingerprint": left_fp, "port_key": port_key,
                    "other_input": left_fp != fp, "tool": left.get("tool")}
        return {"error": "unavailable", "unsure": True, "retryable": False,
                "tool": left.get("tool"),
                "message": f"purchase {key} may already be charged (an earlier call did not "
                           "finish); the CEO resolves it (resolve_purchase) before it can run again",
                "fingerprint": left_fp, "port_key": port_key, "ceiling": left.get("ceiling"),
                "intent_ts": left.get("ts"), "provider": left.get("provider")}

    def _buy_fetch(self, args: dict) -> dict:
        """buy_tool's slow half, before the lock. Fail closed before paying: the log must be
        writable, the key's history is checked on the log, and an intent is written down
        first, so a crash between Monid's charge and our event replays as 'unsure'."""
        tool, data, key = args.get("tool"), args.get("input"), args.get("key")
        problem = self._buy_problem(tool, data, key)
        if problem:
            return {"refused": problem}
        port_key, fp = f"buy:{key}", self._buy_fingerprint(tool, data)
        with self._lock:
            self.store.ensure_writable()  # never pay for what can't be recorded
            if port_key in self._buy_inflight:  # never touch the intent of a call still running
                return {"refused": f"purchase {key} is already in flight; retry when it returns"}
            self._buy_inflight.add(port_key)  # released after the handler ran (perform)
            args["_buy_reserved"] = port_key
            known = self._done_from(f"tool:{port_key}")
            if known is not None:  # 17b: a booking whose commit reply was lost counts once
                self._catch_up_cost(self._done_from(f"cost:tool:{port_key}"))
            episode, unsure, resolved = self._buy_episode(port_key)
        if known is None and resolved is not None and resolved.status == "ok":
            known = resolved  # the CEO found it charged: it is a purchase like any other
        if known is not None:  # the log is the memory: a repeat never reaches Monid
            if (known.private or {}).get("fingerprint") != fp:
                return {"refused": "this key was used for a different purchase"}
            return {"known": known, "port_key": port_key, "fingerprint": fp}
        if unsure is not None and resolved is None:
            return {"refused": f"purchase {key} may already be charged; the CEO resolves it "
                               "(resolve_purchase) before it can run again"}
        left = self._buy_intents.get(port_key)
        if left and left.get("provider") == "local":  # written by the log-only toolbuy
            self._buy_intents.put(port_key, None)
            left = None
        if left:  # never overwrite, never clear what an unsettled earlier call wrote
            return self._buy_leftover(port_key, key, fp, left)
        if episode and unsure is None:  # 17b: the last attempt was answered "not charged";
            self._resolve_port(port_key, None)  # a hold kept after that lost reply goes now
        with self._lock:  # the budget gates only a call that would pay (a repeat is free)
            cap = self.settings.spend_cap_usd
            if cap is not None and self.state.spent_usd() >= cap:
                return {"refused": f"the firm's budget (SPEND_CAP_USD {cap}) is spent"}
        spec = getattr(self.ports.toolbuy, "tools", {}).get(tool) or {}
        ceiling = str(spec.get("max_usd")) if spec.get("max_usd") is not None else None
        self._buy_intents.put(port_key, {"tool": tool, "fingerprint": fp, "ceiling": ceiling,
                                         "ts": iso_ts(self._now()),
                                         "provider": self.ports.toolbuy.provider})
        try:
            purchase = self.ports.toolbuy.buy(tool, data, port_key)
        except PortError as err:
            unsure_now = type(err).__name__ == "Unsure"
            if not unsure_now and getattr(err, "proves_uncharged", True):
                self._buy_intents.put(port_key, None)  # proved not charged
            return {"error": err.code, "message": str(err)[:200], "unsure": unsure_now,
                    "fingerprint": fp, "port_key": port_key, "ceiling": ceiling,
                    "retryable": err.retryable}
        return {"purchase": purchase, "fingerprint": fp, "port_key": port_key}

    def _buy_tool(self, args: dict) -> dict:
        tool = _str(args, "tool", max_len=80)
        key = _str(args, "key", max_len=120)
        fetched = args.get("_prefetch") or {}
        if fetched.get("refused"):
            raise Refused("bad_input", fetched["refused"])
        tool = fetched.get("tool") or tool  # a leftover books under the tool it was for
        provider = self.ports.toolbuy.provider
        if "known" in fetched:
            known = fetched["known"]
            return {"tool": tool, "key": key, "bought": (known.private or {}).get("bought", True),
                    "cost_usd": known.cost_usd, "result": (known.private or {}).get("result"),
                    "duplicate": True}
        private = {"key": fetched.get("port_key"), "fingerprint": fetched.get("fingerprint")}
        if "error" in fetched:
            if fetched.get("unsure"):  # it may be charged: remembered across restarts
                # the episode open now (under the lock), never one closed while Monid answered
                episode = self._buy_episode(fetched["port_key"])[0]
                charged_by = fetched.get("provider") or provider  # a leftover's own provider
                self._emit([NewEvent(type="tool.bought", actor="mkt", provider=charged_by,
                                     status="failed", payload={"tool": tool, "provider": charged_by},
                                     idempotency_key=f"tool:unsure:{fetched['port_key']}"
                                                     f"{_episode(episode)}",
                                     private={**private, "unsure": True,
                                              "ceiling": fetched.get("ceiling"),
                                              "intent_ts": fetched.get("intent_ts")})])
                self._buy_intents.put(fetched["port_key"], None)
            raise (ToolError if fetched.get("retryable") and not fetched.get("unsure")
                   else Refused)(fetched["error"], f"buy_tool: {fetched['message']}")
        purchase = fetched["purchase"]
        result, size = self._relayable(purchase.result, key)
        kept = result if size <= self.BUY_RESULT_KEPT else None
        events = [NewEvent(type="tool.bought", actor="mkt", provider=provider,
                           payload={"tool": tool, "provider": provider},
                           cost_usd=purchase.cost_usd,
                           idempotency_key=f"tool:{fetched['port_key']}",
                           private={**private, "bought": purchase.bought, "result": kept})]
        if purchase.bought:
            events.append(NewEvent(type="cost.booked", actor="system", provider=provider,
                                   payload={"provider": provider, "model": f"tool:{tool}",
                                            "estimated": False},
                                   cost_usd=purchase.cost_usd,
                                   idempotency_key=f"cost:tool:{fetched['port_key']}"))
        self._emit(events)
        self._buy_intents.put(fetched["port_key"], None)
        if fetched.get("other_input"):  # an earlier call's charge, booked; not this input's
            raise Refused("bad_input", "this key was used for a different purchase")
        return {"tool": tool, "key": key, "bought": purchase.bought,
                "cost_usd": purchase.cost_usd, "result": result}

    def _relayable(self, result: Any, key: str) -> tuple[Any, int]:
        """(result, its JSON size) when it can go to the log and the agent; (None, 4) when a
        paid result is deeper or wider than the BUY_RESULT_* bounds, or not plain, writable
        JSON (NaN, Infinity, a lone surrogate, NUL): writing the event, or the MCP reply,
        would fail after the charge. The cost is booked either way."""
        from app.gateway.adapters import _http

        if _http.too_nested(result, max_depth=self.BUY_RESULT_DEPTH,
                            max_values=self.BUY_RESULT_VALUES):
            why = (f"nests over {self.BUY_RESULT_DEPTH} levels or holds over "
                   f"{self.BUY_RESULT_VALUES} keys and values")
        else:
            try:
                text = json.dumps(result, ensure_ascii=False, allow_nan=False)
                text.encode("utf-8")  # a lone surrogate can be neither written nor sent
            except (TypeError, ValueError, RecursionError):
                why = "is not plain JSON (NaN, Infinity, a lone surrogate or a non-JSON value)"
            else:
                if not _holds_nul(result):  # Postgres jsonb refuses NUL
                    return result, len(text)
                why = "holds a NUL character"
        log.warning("buy_tool %s: the paid result %s; dropped, the cost is booked", key, why)
        return None, len("null")

    def _resolve_purchase(self, args: dict) -> dict:
        """POST /api/dev/... or the CEO console: an unsure purchase checked in Monid's
        GET /v1/runs. `key` is the agent's key. `cost_usd` null (or `charged: false`) = never
        charged (frees the key and its reservation); a decimal (`charged: true`) = charged,
        booked and remembered. Answers once per episode; checked before anything is booked:
        a cost over the purchase's ceiling needs `over_ceiling: true`, never over
        monid.MAX_PLAUSIBLE_USD."""
        key = _str(args, "key", max_len=120)
        if not self._BUY_KEY.fullmatch(key):  # "#" separates episodes: never in a key
            raise Refused("bad_input", "key must be the agent's buy_tool key "
                                       "(1-120 of A-Z a-z 0-9 _ . : -)")
        port_key = f"buy:{key}"  # the agent's key, exactly as buy_tool's refusal names it
        wanted = _int(args, "episode") or 0  # 17b: the episode the CEO checked in Monid
        if port_key in self._buy_inflight:  # its intent is live, not a leftover
            raise Refused("wrong_status", f"purchase {key} is in flight; resolve it after "
                                          "it returns")
        if self._done_from(f"tool:{port_key}") is not None:  # 17b: the log first
            self._catch_up_cost(self._done_from(f"cost:tool:{port_key}"))
            self._buy_intents.put(port_key, None)  # an intent whose clear failed: settled
            raise Refused("not_found", f"purchase {key} was already bought and booked; "
                                       "nothing to resolve")
        episode, unsure, resolved = self._buy_episode(port_key)
        left = self._buy_intents.get(port_key) if unsure is None and resolved is None else None
        if isinstance(left, dict):  # unsettled, not on the log yet: record it first
            recall = getattr(self.ports.toolbuy, "remembered", None)
            held = recall(port_key) if callable(recall) else None
            if held is not None and held[0] == left.get("fingerprint"):
                raise Refused("wrong_status", f"purchase {key} is held by this process (Monid "
                                              "answered; its record failed): the agent's retry "
                                              "of the same key books it exactly")
            self._record_leftover(port_key, left)
            episode, unsure, resolved = self._buy_episode(port_key)
        if resolved is not None:  # once only: a second answer used to book its cost too
            self._catch_up_cost(self._done_from(f"cost:tool:{port_key}"))
            self._resolve_port(port_key, resolved.cost_usd)  # a lost commit ack: catch up
            raise Refused("wrong_status", f"purchase {key} is already resolved as charged")
        if unsure is None:
            if episode:
                self._resolve_port(port_key, None)  # no leftover is open: only a stale hold
                raise Refused("wrong_status", f"purchase {key} is already resolved as not charged")
            hint = " (use the agent's key, without the buy: prefix)" \
                if key.startswith("buy:") else ""
            raise Refused("not_found", f"no unsure purchase {key}{hint}")
        if wanted != episode:  # a replayed answer for a settled episode, or a new leftover
            raise Refused("wrong_status", f"purchase {key} has a newer unsure attempt, episode "
                                          f"{episode}, nobody has checked yet: check it in "
                                          f"Monid's runs, then resend with episode: {episode}")
        raw = args.get("cost_usd")
        try:
            cost = parse_usd(raw) if raw not in (None, "") else None
        except (TypeError, ValueError, InvalidOperation) as err:
            raise Refused("bad_input", f"bad cost: {err}") from err
        charged = args.get("charged")
        if charged is not None and not isinstance(charged, bool):
            raise Refused("bad_input", "charged must be true or false")
        if charged is True and not cost:
            raise Refused("bad_input", "charged: true needs the cost_usd Monid's run shows")
        if charged is False and cost:
            raise Refused("bad_input", "charged: false takes no cost_usd")
        over = args.get("over_ceiling")
        if over is not None and not isinstance(over, bool):
            raise Refused("bad_input", "over_ceiling must be true or false")
        from app.gateway.adapters.monid import MAX_PLAUSIBLE_USD

        ceiling = self._resolve_ceiling(unsure)  # checked before anything is booked: once only
        if cost and cost > MAX_PLAUSIBLE_USD:
            raise Refused("bad_input", f"cost_usd {usd_string(cost)} is over "
                                       f"${MAX_PLAUSIBLE_USD}; no data call costs that")
        if cost and usd_string(cost) == usd_string(Decimal(0)):  # booked at 4 places
            cost = Decimal("0.0001")  # 17b: a real charge is never booked as $0.0000
        if cost and cost > ceiling and not over:  # max_usd is ours: Monid can charge more
            raise Refused("bad_input", f"cost_usd {usd_string(cost)} is over this purchase's "
                                       f"ceiling ${usd_string(ceiling)}; check the run in Monid, "
                                       "then resend with over_ceiling: true if it is right")
        provider = unsure.provider or self.ports.toolbuy.provider
        tool = unsure.payload.get("tool", "?")
        private = {"key": port_key, "fingerprint": (unsure.private or {}).get("fingerprint"),
                   "resolved": True}
        events = [NewEvent(type="tool.bought", actor="system", provider=provider,
                           status="ok" if cost else "skipped",
                           payload={"tool": tool, "provider": provider},
                           cost_usd=usd_string(cost) if cost else None,
                           idempotency_key=f"tool:resolved:{port_key}{_episode(episode)}",
                           private={**private, "bought": bool(cost)})]
        if cost:
            events.append(NewEvent(type="cost.booked", actor="system", provider=provider,
                                   payload={"provider": provider, "model": f"tool:{tool}",
                                            "estimated": False},
                                   cost_usd=usd_string(cost),
                                   idempotency_key=f"cost:tool:{port_key}"))
        # settled: a leftover intent must not re-block it. Cleared first: if the write
        # below fails, the unresolved unsure record still blocks the key
        self._buy_intents.put(port_key, None)
        self._emit(events)
        self._resolve_port(port_key, usd_string(cost) if cost else None)
        return {"key": key, "charged": bool(cost), "episode": episode}

    def _catch_up_cost(self, booked: Event | None) -> None:
        """A cost.booked on the log the projection never applied (its commit reply was lost,
        and newer events landed since): apply it once, or the spend cap undercounts it."""
        if (booked is not None and booked.idempotency_key
                and booked.idempotency_key not in self.state.cost_keys):
            self.state.apply(booked)

    def _resolve_port(self, port_key: str, cost_usd: str | None) -> None:
        """The adapter's half of a resolution (its unsure hold and daily spend). Idempotent:
        a key it no longer holds is a no-op, so a retry after a lost commit ack catches up."""
        resolve = getattr(self.ports.toolbuy, "resolve", None)
        if callable(resolve):
            resolve(port_key, cost_usd)

    def _record_leftovers(self) -> None:
        """At boot: an intent a crash left (no record on the log) becomes an unsure record
        now, so the CEO sees and resolves it without waiting for an agent to retry the key."""
        try:
            intents = self._buy_intents.load() or {}
        except Exception:  # noqa: BLE001 - a bad file never stops the server
            log.exception("toolbuy intents unreadable at boot")
            return
        if not isinstance(intents, dict):  # 17b: never crash the boot on a damaged file
            log.error("toolbuy intents file is not a JSON object; left as is")
            return
        for port_key, left in intents.items():
            if not left:
                continue
            try:
                self._record_leftover(port_key, left)
            except Exception:  # noqa: BLE001 - the intent stays; the key's first retry records it
                log.exception("leftover purchase %s not recorded at boot", port_key)

    def _record_leftover(self, port_key: str, left: dict) -> None:
        """One unsettled intent onto the log as an unsure record (unless the key is settled
        or already unsure), then cleared. One the local toolbuy wrote never charged: just
        cleared (whatever toolbuy runs now: a local boot keeps what Monid may have charged)."""
        if left.get("provider") == "local":
            self._buy_intents.put(port_key, None)
            return
        episode, unsure, resolved = self._buy_episode(port_key)
        settled = self._done_from(f"tool:{port_key}") is not None or (
            resolved is not None and resolved.status == "ok")
        if not settled and unsure is None:
            provider = left.get("provider") or self.ports.toolbuy.provider  # who may have charged
            with self._lock:
                self._emit([NewEvent(
                    type="tool.bought", actor="system", provider=provider, status="failed",
                    payload={"tool": left.get("tool") or "?", "provider": provider},
                    idempotency_key=f"tool:unsure:{port_key}{_episode(episode)}",
                    private={"key": port_key, "fingerprint": left.get("fingerprint"),
                             "unsure": True, "ceiling": left.get("ceiling"),
                             "intent_ts": left.get("ts")})])
        self._buy_intents.put(port_key, None)

    @staticmethod
    def _resolve_ceiling(unsure: Event) -> Decimal:
        """The ceiling the purchase reserved (the tool's max_usd); monid.MAX_PLAUSIBLE_USD
        when the record has none."""
        from app.gateway.adapters.monid import MAX_PLAUSIBLE_USD

        raw = (unsure.private or {}).get("ceiling")
        try:
            ceiling = parse_usd(raw) if raw not in (None, "") else MAX_PLAUSIBLE_USD
        except (TypeError, ValueError, InvalidOperation):
            ceiling = MAX_PLAUSIBLE_USD
        return min(ceiling, MAX_PLAUSIBLE_USD)

    def _restore_toolbuy(self) -> None:
        """Monid keeps purchases, unsure keys and today's spend in memory; replay them from
        the log (and from intents a crash left behind) at boot, so a restart never re-buys a
        key or forgets today's spend. Unsure ceilings count on their own day only."""
        port = self.ports.toolbuy
        needed = ("_remember", "_book", "_unsure", "_lock", "_spent_today")
        if port.provider == "local":
            return
        if not all(hasattr(port, name) for name in needed):
            log.error("toolbuy %s can't be restored (adapter changed?): today's spend and "
                      "past purchases are not replayed", port.provider)
            return
        from app.gateway.ports import Purchase

        today = self._now().date().isoformat()
        try:
            done: set[str] = set()
            with port._lock:
                for e in self.store.since(0):
                    if e.type != "tool.bought":
                        continue
                    private = e.private or {}
                    key, fp = private.get("key"), private.get("fingerprint")
                    if not key or not fp:
                        continue
                    if e.status == "skipped":  # resolved as not charged: the key is free
                        port._unsure.pop(key, None)
                        continue
                    if e.status == "ok":
                        done.add(key)
                        port._unsure.pop(key, None)
                        if not private.get("bought", True):
                            continue
                        port._remember(key, fp, Purchase(result=private.get("result"),
                                                         cost_usd=e.cost_usd or "0", bought=True))
                        if e.ts[:10] == today and e.cost_usd:
                            port._spent_today()
                            port._book(Decimal(e.cost_usd))
                    elif (private.get("unsure") and key not in done
                          and str(private.get("intent_ts") or e.ts)[:10] == today):  # its day
                        port._unsure[key] = (fp, Decimal(private.get("ceiling") or "0"))
                for key, intent in (self._buy_intents.load() or {}).items():
                    if not isinstance(intent, dict) or key in done or key in port._unsure \
                            or intent.get("provider") == "local":  # log-only: never charged
                        continue
                    if str(intent.get("ts", ""))[:10] == today:  # crashed mid-purchase
                        port._unsure[key] = (intent["fingerprint"],
                                             Decimal(intent.get("ceiling") or "0"))
        except Exception:  # noqa: BLE001 - a bad record never stops the server
            log.exception("toolbuy replay failed; today's Monid spend may be under-counted")

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
        if case.greeting_name:  # v1.12: the sign-up form's name, already cleaned
            return case.greeting_name
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
                   None, detail=f'the {channel} post, word for word: "{text}"')
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
        own = (urlsplit(self.settings.public_url).hostname or "").lower() \
            if self.settings.public_url else ""
        ours = bool(own) and host == own and not parts.query \
            and re.fullmatch(r"/assets/ad_[0-9]{1,6}\.png", parts.path or "") is not None
        return parts.scheme == "https" and not parts.username and not parts.password \
            and (host in self.settings.public_asset_hosts or ours)

    # ------------------------------------------------------------ v1.11 (D31) CEO orders

    def _valid_order(self, raw: Any) -> str | None:
        """`order_<id>` naming an existing CEO voice.command, else None (ignored)."""
        if not isinstance(raw, str) or not re.fullmatch(r"order_[0-9]{1,12}", raw):
            return None
        event_id = int(raw.split("_", 1)[1])
        try:  # an order id is a hint: a store hiccup ignores it, never fails the tool
            found = next(iter(self.store.since(event_id - 1, limit=1)), None)
        except Exception:  # noqa: BLE001 - see above
            log.exception("order id %s not checked", raw)
            return None
        if (found is None or found.id != event_id or found.type != "voice.command"
                or found.payload.get("speaker") != "ceo"):
            return None
        return f"order_{event_id}"  # "order_07" groups with order_7

    def _seed_hr_candidates(self) -> None:
        """HR_CANDIDATES (the CEO's own list) become candidates once, with their address
        private like an applicant's, and never sent the applicant questionnaire."""
        from email.utils import getaddresses

        for _, address in getaddresses([self.settings.hr_candidates or ""]):
            sender_key = address.strip().lower()
            if not has_email(sender_key) or "@" not in sender_key:
                continue
            if any(c.get("sender_key") == sender_key for c in self.state.candidates.values()):
                continue
            candidate_id, name_masked, _ = self._candidate_for(sender_key)
            digest = hashlib.sha256(sender_key.encode()).hexdigest()[:16]
            try:
                with self._lock:
                    self._emit([NewEvent(
                        type="hr.applicant", actor="hr", provider=None,
                        payload={"candidate_id": candidate_id, "name_masked": name_masked,
                                 "note": "on the CEO's list"},
                        idempotency_key=f"hrlist:{digest}",
                        private={"reply_to": address.strip(), "sender_key": sender_key,
                                 "listed": True})])
            except EventWriteError:
                log.exception("HR_CANDIDATES entry not recorded; retried at the next boot")
                return

    def _booking_line(self) -> str:
        """v1.11.3: the owner's booking page, https only (anything else is left out, and
        the line holding only {{booking_url}} disappears)."""
        from urllib.parse import urlsplit

        url = (self.settings.hr_booking_url or "").strip()
        try:
            parts = urlsplit(url)
        except ValueError:
            return ""
        if (parts.scheme != "https" or not parts.hostname or parts.username
                or any(ch.isspace() or ord(ch) < 32 for ch in url)):
            if url:
                log.warning("HR_BOOKING_URL ignored: it must be an https link")
            return ""
        return f"You can also pick a time on my calendar: {url}"

    def _list_candidates(self, args: dict) -> dict:
        """v1.11.2 (hr): every candidate, masked: the applicants and the CEO's
        HR_CANDIDATES entries (stable ids). No names, no addresses."""
        slots = self.offered_slots(None)
        rows = [{"candidate_id": cid, "label": c["name_masked"], "stage": c.get("stage"),
                 "has_address": bool(c.get("reply_to")), "contacted": c.get("contacted"),
                 "offered_slots": slots}
                for cid, c in sorted(self.state.candidates.items(),
                                     key=lambda item: int(item[0].rsplit("_", 1)[1])
                                     if item[0].rsplit("_", 1)[1].isdigit() else 0)]
        return {"candidates": rows}

    AD_HEADLINE_MAX = 80

    def _make_ad(self, args: dict) -> dict:
        """v1.11 (mkt): render the firm's ad PNG; nothing leaves the firm until post_social."""
        from app.gateway import ad_image

        kind = _one_of(args, "kind", ("client", "hiring"))
        headline = " ".join(_str(args, "headline", max_len=400).split())
        if not headline or len(headline) > self.AD_HEADLINE_MAX:
            raise Refused("bad_input", f"headline must be 1-{self.AD_HEADLINE_MAX} characters")
        problem = post_problem(headline, allowed_url=self.settings.public_talk_url) \
            or self._names_a_client(headline)
        if problem:
            raise Refused("content_guard", f"headline refused: {problem}")
        if not self.settings.public_url.startswith("https://"):
            raise Refused("no_public_url", "make_ad needs UTAX_PUBLIC_URL (the gateway's https "
                                           "address) to serve the image")
        key = f"ad:{_digest([kind, headline])}"
        done = self._done_from(key)
        if done is not None:
            # the same ad on today's host (a quick tunnel's host changes on restart)
            name = done.payload["image_url"].rsplit("/", 1)[-1]
            path = self.settings.data_dir / "assets" / name
            if not path.is_file():
                ad_image.render(kind, headline, path)
            return {"image_url": f"{self.settings.public_url}/assets/{name}",
                    "duplicate": True}
        number = self.state.ads_created + 1
        name = f"ad_{number}.png"
        ad_image.render(kind, headline, self.settings.data_dir / "assets" / name)
        image_url = f"{self.settings.public_url}/assets/{name}"
        self._emit([NewEvent(type="ad.created", actor="mkt",
                             payload={"kind": kind, "headline": headline,
                                      "image_url": image_url},
                             idempotency_key=key)])
        return {"image_url": image_url}

    SOURCE_TOOL = "candidate_search"  # the MONID_TOOLS entry source_candidates buys
    # ploid /linkedin/search (monid inspect, 2026-11-07): {filters: {keywords, title, ...},
    # limit 1-100}, nothing else; $0.01 a person, so limit = count keeps it at max_usd
    SOURCE_FILTERS = {"keywords": "Delaware franchise tax",
                      "title": ["CPA", "Enrolled Agent", "Tax Accountant"]}

    def _source_fetch(self, args: dict) -> dict:
        """v1.11 (hr), before the lock: a Monid public search through buy_tool's money-safe
        path (intent, ceilings, caps); off (no Monid, or no candidate_search tool) = none."""
        count = args.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 5:
            return {"refused": "count must be 1-5"}
        tools = getattr(self.ports.toolbuy, "tools", {}) or {}
        if self.ports.toolbuy.provider == "local" or self.SOURCE_TOOL not in tools:
            return {"off": True}
        # its own namespace (never an mkt buy_tool key); a new key each minute, so a
        # refused or unsure one never blocks every later search
        sub = {"tool": self.SOURCE_TOOL,
               "input": {"filters": dict(self.SOURCE_FILTERS), "limit": count},
               "key": f"hr_source_{count}_{iso_ts(self._now())[:16].replace(':', '')}"}
        fetched = self._buy_fetch(sub)
        if sub.get("_buy_reserved"):
            args["_buy_reserved"] = sub["_buy_reserved"]
        return {"buy": sub, "fetched": fetched}

    def _source_candidates(self, args: dict) -> dict:
        fetched = args.get("_prefetch") or {}
        if fetched.get("refused"):
            raise Refused("bad_input", fetched["refused"])
        count = args["count"]
        if fetched.get("off"):
            self._emit([NewEvent(type="hr.sourced", actor="hr",
                                 payload={"count": 0, "source": "none"})])
            return {"leads": [], "source": "none",
                    "note": "Monid search is off (no candidate_search tool): no leads"}
        sub = dict(fetched["buy"], _prefetch=fetched["fetched"])
        bought = self._buy_tool(sub)
        leads = self._public_leads(bought.get("result"), count)
        self._emit([NewEvent(type="hr.sourced", actor="hr",
                             payload={"count": len(leads), "source": "monid"})])
        return {"leads": leads, "source": "monid",
                "note": "public leads only: no address, so hr_outreach can't email them"}

    @staticmethod
    def _rows_of(result: Any, depth: int = 0) -> list:
        """The list of people in a search result, whichever key the provider uses (a
        non-empty list first; at most 3 levels deep)."""
        if isinstance(result, list):
            return result
        if not isinstance(result, dict) or depth > 3:
            return []
        for key in ("results", "people", "profiles", "items", "data"):
            value = result.get(key)
            if isinstance(value, list) and value:
                return value
            if isinstance(value, dict):
                inner = Gateway._rows_of(value, depth + 1)
                if inner:
                    return inner
        return []

    @staticmethod
    def _public_leads(result: Any, count: int) -> list[dict]:
        """Masked public leads from a Monid result: a headline and a public https URL,
        never an email, a phone or a name."""
        rows = Gateway._rows_of(result)
        leads: list[dict] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            # strings only: an object's repr could carry a name or more
            url = next((v for k in ("url", "profileUrl", "profile_url", "linkedinUrl",
                                    "linkedin_url", "public_url")
                        if isinstance(v := row.get(k), str) and v), "")
            # a headline (role and firm), never the person's name field
            headline = strip_contacts(next(
                (v for k in ("headline", "title", "position", "jobTitle")
                 if isinstance(v := row.get(k), str) and v.strip()), ""))
            headline = " ".join(redact_ids(headline)[0].split())[:120]
            if not url.startswith("https://") or has_email(url) or not headline:
                continue
            n, letters = len(leads) + 1, ""
            while n:
                n, rem = divmod(n - 1, 26)
                letters = chr(65 + rem) + letters
            leads.append({"lead_id": f"lead_{len(leads) + 1}", "label": f"Lead {letters}",
                          "headline": headline, "public_url": url[:300]})
            if len(leads) == count:
                break
        return leads

    def _hr_outreach(self, args: dict) -> dict:
        """v1.11 (hr): email one candidate whose address the firm holds with consent (an
        applicant, or the CEO's HR_CANDIDATES), with the gig and a calendar invite."""
        candidate_id = _str(args, "candidate_id", max_len=64)
        cand = self.state.candidates.get(candidate_id)
        if cand is None:
            raise Refused("no_consented_address", f"{candidate_id} is not a candidate with an "
                                                  "address (a sourced lead has none)")
        if not cand.get("reply_to"):
            raise Refused("no_consented_address", f"{candidate_id} has no consented address")
        if cand.get("stage") in ("declined", "hired"):
            raise Refused("wrong_status", f"{candidate_id} is already {cand['stage']}")
        key = f"hr_outreach:{candidate_id}"
        done = self._done_from(key)
        if done is not None:
            return {**done.payload, "duplicate": True}
        slot = _str(args, "slot", max_len=40)
        try:
            start = iso_ts(parse_ts(slot))
        except ValueError as err:
            raise Refused("bad_input", "slot must be ISO 8601 with a timezone") from err
        bound = self._outreach_intents.get(candidate_id)
        if bound and bound != start:  # an earlier try may have mailed that time already
            raise Refused("slot_bound", f"{candidate_id}'s outreach is for {bound}; retry with "
                                        "that slot", {"slot": bound})
        offered = self.offered_slots(None)
        if start not in offered and start != bound:
            raise Refused("slot_not_offered", "slot must be one of get_candidate's "
                                              "offered_slots", {"offered_slots": offered})
        label = cand["name_masked"]
        # the approval names the time (like book_call): an OK for X never sends Y. The
        # invite is a calendar invite too: its own trust applies, before anything leaves
        self._gate("email", f"{key}:{start}", _cap(
            f"{self._first('email')}outreach email + call invite: {label} at {start}"), None)
        self._gate("calendar_invite", f"{key}:{start}:call", _cap(
            f"{self._first('calendar_invite')}calendar invite: {label} at {start}"), None)
        self._outreach_intents.put(candidate_id, start)  # before anything leaves
        # task 32: email 1 is a short personal note proposing the time (no invite, no link:
        # those send it to spam); the candidate's reply books the call (email 2)
        body = self.templates.render("hr-outreach-note", {
            "greeting_name": self._candidate_greeting(cand), "slot_time": _when(start),
            "minutes": str(SLOT_MINUTES)})
        self._with_retry("outreach email", lambda: self.ports.mail.send(
            OutMail(to=cand["reply_to"], subject="A paid 1-hour review gig?", body=body,
                    inbox="hiring"), key))
        self._emit([
            NewEvent(type="hr.outreach", actor="hr", provider=self.ports.mail.provider,
                     payload={"candidate_id": candidate_id, "label": label, "slot": start},
                     idempotency_key=key),
        ])
        return {"candidate_id": candidate_id, "label": label, "slot": start,
                "next": "the invite goes out when they reply"}

    def _candidate_greeting(self, cand: dict) -> str:
        """Task 32: the first name from HR_CANDIDATES ("Jane Roe <jane@...>": "Jane"), the
        consented list's own words; "there" for anyone else."""
        from email.utils import getaddresses

        for name, address in getaddresses([self.settings.hr_candidates or ""]):
            if address.strip().lower() == cand.get("sender_key") and name.strip():
                return _form_greeting(name.split()[0])
        return "there"

    def _send_candidate_invite(self, candidate_id: str, reply_id: str | None = None) -> str:
        """Task 32: a contacted candidate replied: book the intro call (the slot held at the
        outreach if it is still ahead, else the first one offered) and send email 2 with
        the invite and the booking link, in their thread. Once."""
        cand = self.state.candidates[candidate_id]
        key = f"hr_outreach:{candidate_id}:call"
        if self._done_from(key) is not None:
            return "sent"
        start = cand.get("contacted")
        soon = self._now() + timedelta(minutes=30)
        if not start or parse_ts(start) < soon:
            offered = self.offered_slots(None)
            if not offered:
                return "no_slot"
            start = offered[0]
        label = cand["name_masked"]
        try:  # the CEO approved this invite with the outreach (same slot, same key)
            self._gate("calendar_invite", f"hr_outreach:{candidate_id}:{start}:call", _cap(
                f"{self._first('calendar_invite')}calendar invite: {label} at {start}"), None)
        except NeedsApproval:
            return "needs_approval"
        except Refused as err:
            return err.code
        body = self.templates.render("hr-outreach-invite", {
            "greeting_name": self._candidate_greeting(cand), "slot_time": _when(start),
            "minutes": str(SLOT_MINUTES), "booking_url": self._booking_line()})
        try:
            self._with_retry("candidate call", lambda: self.ports.calendar.book(
                Slot(start=start, minutes=SLOT_MINUTES), cand["reply_to"],
                "U-Tax: intro call for the memo review gig", key))
            self._with_retry("invite email", lambda: self.ports.mail.send(
                OutMail(to=cand["reply_to"], subject="Re: A paid 1-hour review gig?",
                        body=body, in_reply_to=reply_id, inbox="hiring"), f"{key}:mail"))
        except ToolError as err:
            log.warning("candidate invite for %s failed: %s", candidate_id, err)
            return "failed"
        self._emit([NewEvent(type="call.booked", actor="hr", provider=self.ports.calendar.provider,
                             payload={"start": start, "minutes": SLOT_MINUTES,
                                      "invitee_masked": label, "kind": "candidate"},
                             idempotency_key=key)])
        return "sent"

    def retry_candidate_invites(self) -> list[str]:
        """Task 32: invites waiting on the CEO's OK (or a failed send): the tick retries."""
        with self._lock:
            todo = [cid for cid, c in sorted(self.state.candidates.items())
                    if c.get("replied") and c.get("contacted")
                    and self._done_from(f"hr_outreach:{cid}:call") is None]
            return [cid for cid in todo if self._send_candidate_invite(cid) == "sent"]

    FOLLOW_UP_STATUSES = {"memo_check": ("SENT", "CALL_BOOKED"),
                          "missing_facts": ("INTAKE_PENDING", "STALLED"),
                          "call_reminder": ("CALL_BOOKED",)}

    def _follow_up(self, args: dict) -> dict:
        """v1.11 (intake): a templated follow-up in the client's own thread, 1 a day a case."""
        case = self._case(args)
        kind = _one_of(args, "kind", tuple(self.FOLLOW_UP_STATUSES))
        if case.status not in self.FOLLOW_UP_STATUSES[kind]:
            raise Refused("wrong_status", f"{kind} needs the case in "
                                          f"{' or '.join(self.FOLLOW_UP_STATUSES[kind])}")
        if kind == "call_reminder" and not case.call:
            raise Refused("wrong_status", "no review call is booked")
        if kind == "call_reminder" and parse_ts(case.call["start"]) <= self._now():
            raise Refused("wrong_status", "the review call has already started")
        if kind == "missing_facts" and not case.missing:
            raise Refused("wrong_status", "nothing is missing on this case")
        if not case.reply_to:
            raise Refused("no_address", "the client's address isn't known")
        day = iso_ts(self._now())[:10]
        key = f"follow_up:{case.case_id}:{day}"
        done = self._done_from(key)
        if done is not None:
            if done.payload.get("kind") == kind:
                return {"kind": kind, "duplicate": True}
            raise Refused("too_soon", "one follow-up a day per case; this one had "
                                      f"{done.payload.get('kind')} today")
        values = ask_values(case.masked_name, case.missing, [],
                            greeting_name=self._greeting(case))
        values["call_time"] = _when(case.call["start"]) if case.call else ""
        self._gate("email", f"{key}:{kind}", _cap(
            f"{self._first('email')}follow-up ({kind.replace('_', ' ')}): {case.masked_name}"),
            case.case_id, public=case.priority)
        body = self.templates.render(f"follow-up-{kind}", values)
        thread = case.message_ids[-1] if case.message_ids else None
        self._with_retry("follow-up email", lambda: self.ports.mail.send(
            OutMail(to=case.reply_to, subject=f"Re: {case.subject}".strip(), body=body,
                    in_reply_to=thread), key))
        self._emit([NewEvent(type="client.followed_up", actor="intake", case_id=case.case_id,
                             provider=self.ports.mail.provider, payload={"kind": kind},
                             idempotency_key=key)])
        return {"kind": kind}

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
                "questionnaire_sent": bool(cand.get("questionnaire_sent")),
                # v1.11: hr_outreach books one of these; consented = an address with consent
                "offered_slots": self.offered_slots(None),
                "consented": bool(cand.get("reply_to")),
                "contacted": cand.get("contacted")}

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

    # ------------------------------------------------------------ CEO voice console (§5b)

    @contextmanager
    def _channel(self, channel: str | None) -> Iterator[None]:
        """Touches written inside carry `channel` (voice / click / typed)."""
        before, self._touch_channel = self._touch_channel, channel
        try:
            yield
        finally:
            self._touch_channel = before

    @staticmethod
    def _is_read(name: str, args: dict) -> bool:
        """A CEO read tool writes nothing: it works while the log can't be written."""
        from app.ceo_tools import REGISTRY, parse

        if name == "ceo_command" and isinstance(args.get("text"), str):
            parsed = parse(args["text"][:600])
            tool_name = parsed[1][0] if parsed and parsed[0] == "tool" else None
        elif name == "ceo_tool" and isinstance(args.get("name"), str):
            tool_name = args["name"]
        else:
            return False
        tool = REGISTRY.get(tool_name or "")
        return tool is not None and tool.kind == "read"

    # app/ceo_tools.py calls these internals (the same code as the §5 routes): _brief_text,
    # _now, _set_trust, _approve, _unblock, _candidate_decision, _voice, _pending_of,
    # _released, _touch_channel, flagged_injection, UNBLOCK_TARGETS. Rename with care.
    def _ceo_tool(self, args: dict) -> dict:
        """POST /api/ceo/tool: a read, a stop carried out at once, or a start proposed."""
        # task 18b: the console's STOP button is a click; the model's tool call is voice
        channel = _one_of(args, "channel", ("voice", "click"), default="voice")
        return self._ceo_run(_str(args, "name", max_len=40), args.get("args"), "realtime",
                             channel=channel)

    def _ceo_run(self, name: str, raw_args: Any, source: str,
                 channel: str | None = None) -> dict:
        from app import ceo_tools

        tool = ceo_tools.REGISTRY.get(name)
        if tool is None:
            raise Refused("unknown_tool", f"no CEO tool {name!r}; the tools are "
                                          f"{', '.join(ceo_tools.REGISTRY)}")
        try:
            tool_args = tool.check(raw_args)
            if tool.kind == "read":
                return {"kind": "read", "data": tool.read(self, tool_args)}
            if tool.kind == "stop":
                with self._channel(channel or ("voice" if source == "realtime" else "typed")):
                    return {"kind": "done", "summary": tool.carry(self, tool_args)}
            pinned, readback = tool.pin(self, tool_args)
        except ceo_tools.ToolRefused as err:
            raise Refused(err.code, err.message, err.data) from err
        return self._propose(name, pinned, readback, source)

    def _open_proposals(self) -> list[dict]:
        return [p for p in self.state.proposals.values() if p["status"] == "open"]

    def _expired(self, proposal: dict) -> bool:
        return parse_ts(proposal["expires_at"]) <= self._now()

    @staticmethod
    def _proposal_cancel(proposal: dict, reason: str, via: str, heard: str) -> NewEvent:
        return NewEvent(type="ceo.cancelled", actor="ceo",
                        payload={"proposal_id": proposal["proposal_id"], "via": via,
                                 "heard_len": len(heard), "reason": reason},
                        private={"heard": heard} if heard else None)

    def _propose(self, tool: str, pinned: dict, readback: str, source: str) -> dict:
        """A start tool's single-use proposal: pinned to its exact target, 60 s. A newer
        proposal supersedes every open one (confirm words go to the newest)."""
        from app.ceo_tools import PROPOSAL_TTL

        import secrets  # never a counter: a lost write reply would hand out the same id twice

        proposal_id = f"prop_{secrets.token_hex(5)}"
        expires_at = iso_ts(self._now() + PROPOSAL_TTL)
        events = [self._proposal_cancel(p, "expired" if self._expired(p) else "superseded",
                                        "click", "") for p in self._open_proposals()]
        case_id = pinned.get("case_id")
        approval = self.state.approvals.get(pinned.get("approval_id") or "")
        if approval is not None:
            case_id = approval.case_id
        public_args, public_readback, private = pinned, readback, None
        if tool == "order":  # v1.8.5: the CEO's words stay private; the theater sees a length
            public_args = {"text_len": len(pinned["text"])}
            public_readback = self.ORDER_READBACK_PUBLIC
            private = {"args": pinned, "readback": readback}
        elif approval is not None and approval.detail and approval.detail in readback:
            # a post read back word for word: unapproved agent text stays off the public log
            public_readback = readback.replace(approval.detail, approval.summary)
            private = {"readback": readback}
        events.append(NewEvent(type="ceo.proposed", actor="ceo", case_id=case_id, payload={
            "proposal_id": proposal_id, "tool": tool, "args": public_args,
            "readback": public_readback, "expires_at": expires_at, "source": source},
            private=private))
        self._emit(events)
        return {"kind": "proposal", "proposal_id": proposal_id, "readback": readback,
                "expires_at": expires_at}

    NAIVE_NOTICE_EVERY = timedelta(minutes=10)  # one CEO mail per agent and denied tool

    @staticmethod
    def _naive_tool(args: dict) -> str:
        """A Naïve tool name as the log shows it: its own charset, never free text."""
        return re.sub(r"[^A-Za-z0-9._:-]", "?", _str(args, "tool", max_len=120))

    def _naive_confirm(self, args: dict) -> dict:
        """app/naive_confirm.py answered a Naïve confirm: log it once per tool call, and tell
        the CEO about a deny (at most once per agent and tool every 10 minutes)."""
        session_id = _str(args, "session_id", max_len=80)
        call_id = _str(args, "tool_call_id", max_len=120)
        # per session: a provider's call ids ("call_0") may repeat across sessions; per
        # attempt (19b): Naive re-asks with the same call id, and each ask is its own event
        attempt = str(args.get("attempt") or 1)[:40]
        key = f"naive.confirm:{session_id}:{call_id}" + (f":{attempt}" if attempt != "1" else "")
        if self._done_from(key) is not None:
            return {"duplicate": True}
        agent = _one_of(args, "agent", AGENTS)
        tool = self._naive_tool(args)
        decision = _one_of(args, "decision", ("allow", "deny"))
        self._emit([NewEvent(type="naive.confirm", actor="system", payload={
            "session_id": session_id, "agent": agent, "tool": tool,
            "decision": decision, "read_people": args.get("read_people") is True},
            idempotency_key=key)])
        if decision == "deny":
            last = self._naive_noticed.get((agent, tool))
            if last is None or self._now() - last >= self.NAIVE_NOTICE_EVERY:
                self._naive_noticed[(agent, tool)] = self._now()
                self._notify_ceo("flagged", clip(f"Naïve tool denied: {tool}", 120),
                                 f"Naïve asked to run {tool}; denied",
                                 key=f"ceo:naive-deny:{session_id}:{call_id}:{attempt}")
        return {"decision": decision}

    def _naive_question(self, args: dict) -> dict:
        """§6b-bis: an agent's ask_operator question is never answered by the gateway; the
        CEO hears about it once."""
        session_id = _str(args, "session_id", max_len=80)
        call_id = _str(args, "tool_call_id", max_len=120)
        key = f"ceo:naive-question:{session_id}:{call_id}"
        if key in self._naive_questions or self._done_from(key) is not None:
            return {"duplicate": True}  # once per question, mailed or not (no retry storm)
        agent = _one_of(args, "agent", AGENTS)
        self._naive_questions.add(key)
        last = self._naive_noticed.get((agent, "?question"))
        if last is not None and self._now() - last < self.NAIVE_NOTICE_EVERY:
            return {"noticed": False}  # an agent asking in a loop: one mail per 10 minutes
        self._naive_noticed[(agent, "?question")] = self._now()
        self._notify_ceo("flagged", f"Naïve question from {agent}",
                         f"An agent ({agent}) asked a question in Naïve; answer it there",
                         key=key)
        return {"noticed": True}

    def _naive_notice(self, args: dict) -> dict:
        """app/naive_confirm.py: a setup problem (the key refused, agents missing), once."""
        key = _str(args, "key", max_len=200)
        if key in self._naive_questions or self._done_from(key) is not None:
            return {"duplicate": True}
        self._naive_questions.add(key)
        reason = _one_of(args, "reason", ("flagged", "blocked"), default="flagged")
        self._notify_ceo(reason, clip(_str(args, "subject", max_len=200), 120),
                         clip(_str(args, "summary", max_len=600), 500), key=key)
        return {"noticed": True}

    def _naive_recovered(self, args: dict) -> dict:
        """Task 27: app/naive_confirm.py nudged a session that stopped on a platform error."""
        session_id = _str(args, "session_id", max_len=80)
        attempt = _int(args, "attempt") or 1
        self._emit([NewEvent(type="naive.recovered", actor="system", payload={
            "session_id": session_id, "agent": _one_of(args, "agent", AGENTS),
            "attempt": attempt}, idempotency_key=f"naive.recovered:{session_id}:{attempt}")])
        return {"attempt": attempt}

    def agent_done_since(self, actor: str, since: str) -> bool:
        """Task 27: has this seat finished a tool call after `since` (an ISO time)? Then a
        session of it that errored earlier is stale: its card moved on without it."""
        with self._lock:
            done = self.state.agent_done_at.get(actor)
        if not done:
            return False
        try:
            return parse_ts(done) > parse_ts(since)
        except ValueError:
            return False

    def naive_recovery(self, session_id: str) -> tuple[int, str | None]:
        """(nudges so far, the last one's timestamp): from the log, so a restart never
        grants a session more tries."""
        with self._lock:
            count, ts = self.state.naive_recoveries.get(session_id, (0, ""))
        return count, ts or None

    def _ceo_confirm(self, args: dict) -> dict:
        """POST /api/ceo/confirm: carry out a proposal's pinned action, once, within 60 s,
        through the same internals as the §5 routes. A spoken confirm is re-checked here."""
        from app import ceo_tools

        proposal_id = _str(args, "proposal_id", max_len=40)
        decision = _one_of(args, "decision", ("confirm", "cancel"))
        via = _one_of(args, "via", ("voice", "click"))
        spoken_words = _str(args, "heard", required=False, max_len=2000) or ""
        heard = clip(ceo_tools.mask_words(spoken_words), ceo_tools.HEARD_MAX)  # the log's copy
        proposal = self.state.proposals.get(proposal_id)
        if proposal is None:
            raise Refused("not_found", f"no proposal {proposal_id}")
        if proposal["status"] == "confirmed":
            raise Refused("used", f"{proposal_id} was already confirmed")
        if proposal["status"] == "cancelled":
            reason = proposal.get("reason")
            if reason == "superseded":
                raise Refused("superseded", f"{proposal_id} was replaced; confirm the newer one")
            if reason == "expired":
                raise Refused("expired", f"{proposal_id} expired; ask again")
            raise Refused("used", f"{proposal_id} was cancelled")
        if self._expired(proposal):
            self._emit([self._proposal_cancel(proposal, "expired", via, heard)])
            raise Refused("expired", f"{proposal_id} expired; ask again")
        spoken = via == "voice"
        if decision == "cancel" or (spoken and ceo_tools.heard_cancels(spoken_words)):
            self._emit([self._proposal_cancel(proposal, "spoken" if spoken else "clicked",
                                              via, heard)])
            return {"kind": "done", "summary": "Cancelled."}
        if spoken and not ceo_tools.heard_confirms(spoken_words, proposal["tool"],
                                                   proposal["readback"]):
            raise Refused("not_a_confirm", "that was not a confirm: say confirm, or cancel")
        pinned = dict(proposal["args"])
        if proposal["tool"] in ("approve", "deny"):
            approval = self.state.approvals.get(pinned["approval_id"])
            if approval is not None and approval.decision == "superseded":
                by = self._newest(approval)  # §5 body: never a figure the CEO didn't hear
                self._emit([self._proposal_cancel(proposal, "superseded", via, heard)])
                raise Refused("superseded", f"{approval.approval_id} is out of date: "
                                            + (f"decide {by} instead" if by else
                                               "the case changed; the crew asks again"),
                              {"approval_id": approval.approval_id, "superseded_by": by})
        tool = ceo_tools.REGISTRY[proposal["tool"]]
        why = tool.stale(self, pinned) if tool.stale else None
        if why:  # the target moved after the readback: never act on what wasn't heard
            self._emit([self._proposal_cancel(proposal, "superseded", via, heard)])
            raise Refused("superseded", why)
        # used before it runs: a failure after this never lets the same words act twice
        self._emit([NewEvent(type="ceo.confirmed", actor="ceo",
                             payload={"proposal_id": proposal_id, "via": via,
                                      "heard_len": len(heard)},
                             private={"heard": heard} if heard else None)])
        try:
            with self._channel(via):
                summary = tool.carry(self, pinned)
        except ceo_tools.ToolRefused as err:
            raise Refused(err.code, err.message, err.data) from err
        except EventWriteError as err:  # this proposal is used: a retry would answer "used"
            raise ToolError("store_unavailable", "event log unavailable: the action may not "
                                                 "have run; check, then ask again") from err
        return {"kind": "done", "summary": summary}

    def _ceo_command(self, args: dict) -> dict:
        """POST /api/ceo/command: the deterministic grammar (no LLM) for browser speech or
        typing. Confirm and cancel words point at the newest open proposal."""
        from app import ceo_tools

        raw = _str(args, "text", max_len=600)
        parsed = ceo_tools.parse(raw)
        live = [p for p in self._open_proposals() if not self._expired(p)]
        kind = parsed[0] if parsed else None
        confirms = kind in ("confirm", "cancel") or (
            kind == "approve_it" and bool(live) and live[-1]["tool"] == "approve")
        # v1.13.1: a stop ("stop posting", "pause emails", "okay, stop everything") always
        # runs at once; the open card is never in its way (the pause then cancels it, §5b)
        stops = kind == "tool" and ceo_tools.REGISTRY[parsed[1][0]].kind == "stop"
        if live and not confirms and not stops:  # v1.13: one decision at a time
            return {"kind": "say", "say": ceo_tools.CONFIRM_OR_CANCEL}
        if ceo_tools.is_greeting(raw):  # v1.13: hello / mic checks are never orders
            return {"kind": "say", "say": ceo_tools.LISTENING}
        if parsed is None:
            # v1.12.3: anything else said as an order goes to the Chief of Staff, read back
            # whole and confirmed like any order ("advertise our firm", "hire 2 people")
            order = ceo_tools.free_order(raw)
            if order is None:
                return {"kind": "unknown", "say": ceo_tools.EXAMPLES}
            try:
                pinned, _ = ceo_tools.pin_order(self, {"text": order})
            except ceo_tools.ToolRefused as err:
                raise Refused(err.code, err.message) from err
            readback = (f'Send this order to the Chief of Staff: "{pinned["text"]}". '
                        "Say confirm.")
            return self._propose("order", pinned, readback, "command")
        kind, value = parsed
        if kind == "approve_it":  # confirms an open approve, else proposes the latest
            if live and live[-1]["tool"] == "approve":
                kind = "confirm"
            else:
                kind, value = "tool", ("approve", {"approval": "latest"})
        if kind == "tool":
            return self._ceo_run(value[0], value[1], "command")
        if not live:
            return {"kind": "unknown", "say": "Nothing is waiting for a confirm. "
                                              + ceo_tools.EXAMPLES}
        return {"kind": kind, "proposal_id": live[-1]["proposal_id"],
                "readback": live[-1]["readback"]}

    def _expire_proposals(self) -> None:
        """At boot: open proposals are rebuilt from the log; expired ones are cancelled."""
        try:
            stale = [p for p in self._open_proposals() if self._expired(p)]
            if stale:
                with self._lock:
                    self._emit([self._proposal_cancel(p, "expired", "click", "") for p in stale])
        except Exception:  # noqa: BLE001 - a store hiccup never stops the boot; confirm checks expiry
            log.exception("expired CEO proposals not cancelled at boot")

    def _set_trust(self, args: dict) -> dict:
        """POST /api/trust: the CEO's standing order ("stop posting" = social_post PAUSED).
        AUTO and ASK set the state; RESUME (task 14) goes back to the state before the
        pause (ASK if none is known). Leaving PAUSED carries out the approvals allowed
        while paused. trust.changed + touch.operating."""
        if args.get("action_type") == "*":  # v1.8: "pause everything"
            if args.get("state") != "PAUSED":
                raise Refused("bad_input", 'action_type "*" takes only state PAUSED')
            return {"action_type": "*",
                    "trust": {t: self._set_trust({"action_type": t, "state": "PAUSED"})["trust"]
                              for t in TRUST_TYPES}}
        action_type = _one_of(args, "action_type", TRUST_TYPES)
        requested = _one_of(args, "state", ("PAUSED", "AUTO", "ASK", "RESUME"))
        current = self.state.trust[action_type]
        to = requested
        if requested == "RESUME":
            to = self.state.trust_before_pause.get(action_type, "ASK") \
                if current == "PAUSED" else current
        if current == to:
            if to == "PAUSED":  # a repeat stop still stops what waits for a confirm (18b)
                stopped = [self._proposal_cancel(p, "superseded", "click", "")
                           for p in self._open_proposals()]
                if stopped:
                    self._emit(stopped)
            return {"action_type": action_type, "trust": to, "duplicate": True}
        waiting = self._pending_of(action_type) if to == "AUTO" else []
        released = self._released(action_type, current)
        reason = "CEO resume" if requested == "RESUME" else "CEO order"
        stopped = [self._proposal_cancel(p, "superseded", "click", "")
                   for p in self._open_proposals()] if to == "PAUSED" else []
        self._emit([NewEvent(type="touch.operating", actor="ceo",
                             payload={"note": f"{action_type} \u2192 {to}",
                                      "approval_id": None}),
                    NewEvent(type="trust.changed", actor="system",
                             payload={"action_type": action_type, "from": current, "to": to,
                                      "reason": reason}),
                    *stopped])
        self._wake_superseded(waiting)
        self._carry_out(released)
        if to == "AUTO" or current == "PAUSED":
            self._wake_held()
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
                if tries < 3:  # a stage that keeps failing stops holding the later ones
                    held.add(case.case_id)  # later stages of this case wait for this one
                if now - last >= wait and sync_key not in self._outbox_inflight:
                    self._outbox_inflight.add(sync_key)  # claimed: no double mirror
                    jobs.append((sync_key, dict(job), case.case_id, case.masked_name))
        done = 0
        try:
            for index, (sync_key, job, case_id, masked_name) in enumerate(jobs):
                done = index
                if not self._drain_one(sync_key, job, case_id, masked_name):
                    return  # rate limited, the CRM is down, or the log is: stop the batch
            done = len(jobs)
        finally:  # every claimed job not processed is released, whatever happened
            with self._lock:
                for rest_key, *_ in jobs[done:]:
                    self._outbox_inflight.discard(rest_key)

    def _drain_one(self, sync_key: str, job: dict, case_id: str, masked_name: str) -> bool:
        """One CRM job; False stops the batch."""
        limited: RateLimited | None = None
        down = False
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
            down = isinstance(err, Unavailable)  # one timeout: don't wait on every job
        with self._lock:
            self._outbox_inflight.discard(sync_key)
            if limited is not None:
                self._outbox_paused_until = time.monotonic() + \
                    min(max(limited.retry_after or 30.0, 1.0), 600.0)
                hits = self._outbox_limited.get(sync_key, 0) + 1
                self._outbox_limited[sync_key] = hits
                if hits < 3:  # not this job's fault yet: no backoff step, no failure event
                    return False
                # the same job keeps hitting a limit: it backs off like a failure, so it
                # can't stall every other case forever
                self._outbox_limited.pop(sync_key, None)
                tries, _ = self._outbox_tries.get(sync_key, (0, 0.0))
                self._outbox_tries[sync_key] = (tries + 1, time.monotonic())
                return False
            tries, _ = self._outbox_tries.get(sync_key, (0, 0.0))
            self._outbox_tries[sync_key] = (tries + 1, time.monotonic())
            if not ok and sync_key in self.state.crm_failed:
                return not down  # the failure is already on the log; retry later
            try:
                self._emit([NewEvent(
                    type="crm.synced", actor="system", case_id=case_id,
                    provider=self.ports.crm.provider, status="ok" if ok else "failed",
                    payload={"object": job["object"], "stage": job["stage"]},
                    idempotency_key=f"{'crmok' if ok else 'crmfail'}:{sync_key}",
                    private={"sync_key": sync_key})])
            except EventWriteError as err:
                log.error("outbox result not logged: %s", err)
                return False
            if ok:
                self._outbox_tries.pop(sync_key, None)
                self._outbox_limited.pop(sync_key, None)
            return not down


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
