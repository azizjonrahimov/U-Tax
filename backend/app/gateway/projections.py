"""Projections: cases, candidates, trust, touches, money, approvals, outbox.

Never written by hand: `apply(event)` is the only way state changes, at boot (replaying
the whole log) and after every append. So a restart rebuilds exactly the same state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.gateway.events import Event
from app.gateway.pricing import usd_string

TRUST_TYPES = ("email", "social_post", "calendar_invite", "voice_screen")
OPEN_STATUSES = ("NEW", "INTAKE_PENDING", "READY", "COMPUTED", "QA_PASSED", "QA_FAILED",
                 "STALLED", "BLOCKED")
DONE_STATUSES = ("SENT", "CALL_BOOKED", "NOT_ELIGIBLE")
# Statuses a case may move to from each status (design doc state machine). A new client
# message re-opens intake from anywhere but BLOCKED, which waits for a human.
TRANSITIONS: dict[str, set[str]] = {
    "NEW": {"INTAKE_PENDING", "NOT_ELIGIBLE"},
    "INTAKE_PENDING": {"READY", "STALLED", "NOT_ELIGIBLE", "BLOCKED"},
    "READY": {"COMPUTED", "INTAKE_PENDING", "BLOCKED", "NOT_ELIGIBLE"},
    "COMPUTED": {"QA_PASSED", "QA_FAILED", "INTAKE_PENDING", "BLOCKED"},
    "QA_PASSED": {"SENT", "INTAKE_PENDING", "BLOCKED"},
    "QA_FAILED": {"BLOCKED", "INTAKE_PENDING"},
    "SENT": {"CALL_BOOKED", "INTAKE_PENDING", "BLOCKED"},
    "CALL_BOOKED": {"INTAKE_PENDING"},
    "STALLED": {"INTAKE_PENDING", "BLOCKED", "NOT_ELIGIBLE"},
    "NOT_ELIGIBLE": {"INTAKE_PENDING"},
    "BLOCKED": {"INTAKE_PENDING", "READY", "NOT_ELIGIBLE"},
}

# Event types that count against an action type's hourly cap.
CAP_EVENTS = {
    "email": ("memo.sent", "client.asked", "case.not_eligible"),
    "social_post": ("post.published",),
    "calendar_invite": ("call.booked",),
    "voice_screen": ("screen.call",),
}


@dataclass
class Case:
    case_id: str
    number: int
    masked_name: str
    sender_key: str
    status: str = "NEW"
    show_on_screen: bool = False
    priority: bool = False
    reply_to: str | None = None  # real address (from private); never served
    company: str | None = None
    subject: str = ""
    texts: list[str] = field(default_factory=list)  # masked client texts, oldest first
    message_ids: list[str] = field(default_factory=list)
    facts: dict[str, Any] | None = None
    quotes: dict[str, str] = field(default_factory=dict)
    assumed: list[str] = field(default_factory=list)
    facts_version: int = 0
    facts_ts: str | None = None
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unrealistic: list[str] = field(default_factory=list)
    asks: int = 0
    asked_after_text: int = 0  # len(texts) when the last ask went out
    result: dict[str, Any] | None = None
    result_version: int = 0
    qa_version: int = 0
    qa_checks: list[dict] = field(default_factory=list)
    memo_message_id: str | None = None
    memo_version: int = 0
    call: dict[str, Any] | None = None
    blocked_reason: str | None = None
    not_eligible_reason: str | None = None
    phone: dict[str, Any] | None = None
    laws: list[dict] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        result = None
        if self.result:
            result = {"total_cents": self.result["total_cents"],
                      "authorized_method_cents": self.result["authorized_method_cents"]}
        return {"case_id": self.case_id, "masked_name": self.masked_name,
                "status": self.status, "result": result}

    def detail(self) -> dict[str, Any]:
        """What get_case returns to an agent: masked, this case only."""
        return {
            "case_id": self.case_id, "masked_name": self.masked_name, "status": self.status,
            "priority": self.priority, "subject": self.subject, "messages": list(self.texts),
            "facts": self.facts, "quotes": dict(self.quotes), "assumed": list(self.assumed),
            "missing": list(self.missing), "problems": list(self.problems),
            "asks_so_far": self.asks, "result": self.result, "qa_checks": list(self.qa_checks),
            "memo_sent": self.memo_message_id is not None, "call": self.call,
            "blocked_reason": self.blocked_reason,
        }


@dataclass
class Approval:
    approval_id: str
    action_type: str
    action_key: str
    case_id: str | None
    summary: str
    decision: str | None = None  # allow | deny


@dataclass
class State:
    last_event_id: int = 0
    cases: dict[str, Case] = field(default_factory=dict)
    case_by_sender: dict[str, str] = field(default_factory=dict)
    trust: dict[str, str] = field(default_factory=lambda: {t: "ASK" for t in TRUST_TYPES})
    trust_auto_since: dict[str, str] = field(default_factory=dict)
    trusted_once: set[str] = field(default_factory=set)
    approvals: dict[str, Approval] = field(default_factory=dict)
    approval_by_key: dict[str, str] = field(default_factory=dict)
    touches: dict[str, int] = field(default_factory=lambda: {"training": 0, "operating": 0,
                                                             "engineering": 0})
    touch_log: list[dict] = field(default_factory=list)
    spent_by_provider: dict[str, Decimal] = field(default_factory=dict)
    candidates: dict[str, dict] = field(default_factory=dict)
    hiring: dict[str, int] = field(default_factory=lambda: {"applicants": 0, "screened": 0,
                                                           "shortlisted": 0, "hired": 0})
    cap_log: dict[str, list[str]] = field(default_factory=lambda: {t: [] for t in TRUST_TYPES})
    crm_pending: dict[str, dict] = field(default_factory=dict)  # key -> {case_id, object, stage}
    crm_failed: set[str] = field(default_factory=set)
    posts: int = 0
    briefs: list[int] = field(default_factory=list)

    # ---------------------------------------------------------------- reads

    @property
    def tax_saved_cents(self) -> int:
        return sum(c.result["authorized_method_cents"] - c.result["total_cents"]
                   for c in self.cases.values()
                   if c.result and c.memo_message_id is not None)

    def spent_usd(self) -> Decimal:
        return sum(self.spent_by_provider.values(), Decimal(0))

    def public(self) -> dict[str, Any]:
        """GET /api/state (contract §4)."""
        cases = sorted(self.cases.values(), key=lambda c: c.number)
        return {
            "last_event_id": self.last_event_id,
            "firm": {"agents": 7, "clients": len(self.cases)},
            "cases": [c.public() for c in cases],
            "touches": {**self.touches, "log": list(self.touch_log)},
            "trust": dict(self.trust),
            "money": {
                "tax_saved_cents": self.tax_saved_cents,
                "spent_usd": _money(self.spent_usd()),
                "spent_by_provider": {p: _money(v) for p, v in sorted(self.spent_by_provider.items())},
            },
            "hiring": dict(self.hiring),
            "queue": {
                "waiting": sum(1 for c in cases if c.status in OPEN_STATUSES and c.status != "BLOCKED"),
                "blocked": sum(1 for c in cases if c.status == "BLOCKED"),
            },
        }

    # ---------------------------------------------------------------- apply

    def apply(self, e: Event) -> None:
        self.last_event_id = max(self.last_event_id, e.id)
        if e.cost_usd is not None:
            provider = e.provider or "unknown"
            self.spent_by_provider[provider] = (self.spent_by_provider.get(provider, Decimal(0))
                                                + Decimal(e.cost_usd))
        for action_type, types in CAP_EVENTS.items():
            if e.type in types and e.status == "ok":
                self.cap_log[action_type].append(e.ts)
        handler = getattr(self, "_on_" + e.type.replace(".", "_"), None)
        if handler is not None:
            handler(e)

    def _case(self, e: Event) -> Case | None:
        return self.cases.get(e.case_id) if e.case_id else None

    def _on_email_received(self, e: Event) -> None:
        self._open_case(e, text=e.payload["body"], subject=e.payload["subject"])

    def _on_voice_command(self, e: Event) -> None:
        if e.payload.get("speaker") == "customer" and e.case_id:
            self._open_case(e, text=e.payload["text"], subject="Voice case", priority=True)

    def _open_case(self, e: Event, *, text: str, subject: str, priority: bool = False) -> None:
        private = e.private or {}
        case = self.cases.get(e.case_id)
        if case is None:
            number = int(e.case_id.rsplit("_", 1)[1])
            case = Case(case_id=e.case_id, number=number,
                        masked_name=private.get("masked_name") or f"Team #{number}",
                        sender_key=private.get("sender_key", e.case_id), subject=subject,
                        priority=priority)
            self.cases[e.case_id] = case
            self.case_by_sender[case.sender_key] = e.case_id
        case.texts.append(text)
        case.reply_to = private.get("reply_to") or case.reply_to
        case.company = private.get("company") or case.company
        if private.get("message_id"):
            case.message_ids.append(private["message_id"])

    def _on_facts_extracted(self, e: Event) -> None:
        case = self._case(e)
        if case is None:
            return
        case.facts = e.payload["facts"]
        case.quotes = e.payload["quotes"]
        case.assumed = e.payload["assumed"]
        case.facts_version += 1
        case.facts_ts = e.ts
        case.missing, case.problems = [], []
        case.unrealistic = list((e.private or {}).get("unrealistic", []))

    def _on_facts_missing(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.missing = list(e.payload["missing"])
            case.problems = list((e.private or {}).get("problems", []))

    def _on_client_asked(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.asks += 1
            case.asked_after_text = len(case.texts)

    def _on_case_status(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.status = e.payload["to"]
            if case.status != "BLOCKED":
                case.blocked_reason = None

    def _on_case_blocked(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.blocked_reason = e.payload["reason"]

    def _on_case_not_eligible(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.not_eligible_reason = e.payload["reason"]

    def _on_law_searched(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.laws = e.payload["results"]

    def _on_tax_computed(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.result = dict(e.payload)
            case.result_version = case.facts_version

    def _on_qa_passed(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.qa_checks = e.payload["checks"]
            case.qa_version = case.facts_version

    _on_qa_failed = _on_qa_passed

    def _on_memo_sent(self, e: Event) -> None:
        case = self._case(e)
        if case is not None and e.status == "ok":
            case.memo_message_id = e.payload["message_id"]
            case.memo_version = case.facts_version

    def _on_call_booked(self, e: Event) -> None:
        case = self._case(e)
        if case is not None and e.status == "ok":
            case.call = dict(e.payload)

    def _on_phone_checked(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            case.phone = dict(e.payload)

    def _on_crm_sync_requested(self, e: Event) -> None:
        key = (e.private or {}).get("sync_key") or f"{e.case_id}:{e.payload['stage']}"
        self.crm_pending[key] = {"case_id": e.case_id, **e.payload}

    def _on_crm_synced(self, e: Event) -> None:
        key = (e.private or {}).get("sync_key") or f"{e.case_id}:{e.payload['stage']}"
        if e.status == "ok":
            self.crm_pending.pop(key, None)
            self.crm_failed.discard(key)
        else:
            self.crm_failed.add(key)

    def _on_approval_requested(self, e: Event) -> None:
        p, private = e.payload, e.private or {}
        approval = Approval(approval_id=p["approval_id"], action_type=p["action_type"],
                            action_key=private.get("action_key", p["approval_id"]),
                            case_id=e.case_id, summary=p["summary"])
        self.approvals[approval.approval_id] = approval
        self.approval_by_key[approval.action_key] = approval.approval_id

    def _touch(self, kind: str, e: Event) -> None:
        self.touches[kind] += 1
        self.touch_log.append({"event_id": e.id, "ts": e.ts, "kind": kind,
                               "note": e.payload["note"]})
        approval_id = e.payload.get("approval_id")
        decision = (e.private or {}).get("decision")
        if approval_id in self.approvals and decision:
            self.approvals[approval_id].decision = decision

    def _on_touch_training(self, e: Event) -> None:
        self._touch("training", e)

    def _on_touch_operating(self, e: Event) -> None:
        self._touch("operating", e)

    def _on_touch_engineering(self, e: Event) -> None:
        self._touch("engineering", e)

    def _on_trust_changed(self, e: Event) -> None:
        action_type, to = e.payload["action_type"], e.payload["to"]
        self.trust[action_type] = to
        if to == "AUTO":
            self.trust_auto_since[action_type] = e.ts
            self.trusted_once.add(action_type)

    def _hr(self, e: Event, stage: str) -> None:
        cid = e.payload["candidate_id"]
        cand = self.candidates.setdefault(cid, {"candidate_id": cid, "stages": set()})
        cand["name_masked"] = e.payload["name_masked"]
        private = e.private or {}
        if private.get("sender_key"):  # applicant mail: gateway-only contact details
            cand["sender_key"] = private["sender_key"]
            cand["reply_to"] = private.get("reply_to")
        cand["stage"] = stage
        if stage not in cand["stages"]:
            cand["stages"].add(stage)
            key = {"applicant": "applicants", "screened": "screened",
                   "shortlisted": "shortlisted", "hired": "hired"}.get(stage)
            if key:
                self.hiring[key] += 1

    def _on_hr_applicant(self, e: Event) -> None:
        self._hr(e, "applicant")

    def _on_hr_screened(self, e: Event) -> None:
        self._hr(e, "screened")

    def _on_hr_shortlisted(self, e: Event) -> None:
        self._hr(e, "shortlisted")

    def _on_hr_hired(self, e: Event) -> None:
        self._hr(e, "hired")

    def _on_hr_declined(self, e: Event) -> None:
        self._hr(e, "declined")

    def _on_post_published(self, e: Event) -> None:
        if e.status == "ok":
            self.posts += 1

    def _on_brief_generated(self, e: Event) -> None:
        self.briefs.append(e.id)


def _money(value: Decimal) -> str:
    """Spend for the rail: 2 places when whole cents, else up to 4 ("11.38", "0.0034")."""
    text = usd_string(value)
    return text[:-2] if text.endswith("00") else text


def rebuild(events) -> State:
    state = State()
    for e in events:
        state.apply(e)
    return state
