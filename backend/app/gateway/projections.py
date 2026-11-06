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
# message re-opens intake except from BLOCKED, SENT, CALL_BOOKED and NOT_ELIGIBLE, which
# the CEO reopens with POST /api/cases/{id}/unblock.
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

_STAGE_RANK = {"applicant": 0, "screened": 1, "shortlisted": 2, "declined": 3, "hired": 3}

# Event types that count against an action type's hourly cap.
CAP_EVENTS = {
    "email": ("memo.sent", "client.asked", "case.not_eligible", "hr.questionnaire_sent"),
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
    facts_event_id: int = 0
    reopens: int = 0
    facts_ts: str | None = None
    missing: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    unrealistic: list[str] = field(default_factory=list)
    asks: int = 0
    asked_after_text: int = 0  # len(texts) when the last ask went out
    last_ask_ts: str | None = None
    result: dict[str, Any] | None = None
    result_version: int = 0
    qa_version: int = 0
    qa_checks: list[dict] = field(default_factory=list)
    memo_message_id: str | None = None
    memo_version: int = 0
    memo_ts: str | None = None
    call: dict[str, Any] | None = None
    blocked_reason: str | None = None
    not_eligible_reason: str | None = None
    phone: dict[str, Any] | None = None
    laws: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

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
    last_login_brief: dict | None = None
    memo_log: dict[str, list[str]] = field(default_factory=dict)  # reply address -> ts
    flag_notice_log: list[str] = field(default_factory=list)
    public_notice_log: list[str] = field(default_factory=list)
    crm_failed: set[str] = field(default_factory=set)
    posts: int = 0
    briefs: list[int] = field(default_factory=list)

    # ---------------------------------------------------------------- reads

    @property
    def tax_saved_cents(self) -> int:
        """Contract §4 (v1.4.1): Σ over sent cases of max(0, authorized − (total − fee)).
        The annual report fee is paid under either method, so it is never a saving."""
        return sum(max(0, c.result["authorized_method_cents"]
                       - (c.result["total_cents"] - c.result["annual_report_fee_cents"]))
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
            # v1.5: every screened candidate is listed for the CEO (no silent rejection)
            "hiring": {**self.hiring, "candidates": [
                {"candidate_id": c["candidate_id"], "name_masked": c["name_masked"],
                 "stage": c.get("stage"), "credentials": c.get("credentials")}
                for c in sorted(self.candidates.values(), key=lambda c: c["candidate_id"])
                if c.get("stage") in ("screened", "shortlisted", "hired", "declined")]},
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
                # sends for anonymous /talk cases have their own bucket (never pause the firm)
                bucket = f"{action_type}_public" if (e.private or {}).get("public") else action_type
                self.cap_log.setdefault(bucket, []).append(e.ts)
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
        case.facts_event_id = e.id
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
            case.last_ask_ts = e.ts

    def _on_case_status(self, e: Event) -> None:
        case = self._case(e)
        if case is not None:
            if e.payload["from"] in ("BLOCKED", "SENT", "CALL_BOOKED", "NOT_ELIGIBLE"):
                case.reopens += 1  # a human reopened it: the next run is a fresh round
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

    def _on_case_flagged(self, e: Event) -> None:
        case = self._case(e)
        if case is not None and e.payload["kind"] not in case.flags:
            case.flags.append(e.payload["kind"])

    def _on_hr_questionnaire_sent(self, e: Event) -> None:
        cand = self.candidates.get(e.payload["candidate_id"])
        if cand is not None:
            cand["questionnaire_sent"] = True

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
        if case is not None and e.status == "ok" and case.reply_to:
            self.memo_log.setdefault(case.reply_to.lower(), []).append(e.ts)
        if case is not None and e.status == "ok":
            case.memo_message_id = e.payload["message_id"]
            case.memo_version = case.facts_version
            case.memo_ts = e.ts

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
        case = self.cases.get(e.case_id) if e.case_id else None
        if case is not None and case.priority:
            self.public_notice_log.append(e.ts)

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
            # requests still waiting for this type are moot once it runs on its own
            for approval in self.approvals.values():
                if approval.action_type == action_type and approval.decision is None:
                    approval.decision = "superseded"

    def _hr(self, e: Event, stage: str) -> None:
        cid = e.payload["candidate_id"]
        cand = self.candidates.setdefault(cid, {"candidate_id": cid, "stages": set()})
        cand["name_masked"] = e.payload["name_masked"]
        private = e.private or {}
        if private.get("sender_key"):  # applicant mail: gateway-only contact details
            cand["sender_key"] = private["sender_key"]
            cand["reply_to"] = private.get("reply_to")
        if stage == "applicant" and private.get("text"):
            cand.setdefault("texts", []).append(private["text"])  # contact-free answers
            if cand.get("questionnaire_sent"):
                cand["answers"] = cand.get("answers", 0) + 1
        if stage == "applicant" and e.payload["note"] and "credentials" not in cand:
            cand["credentials"] = e.payload["note"]
        # stages only move forward: a questionnaire reply never demotes a shortlisted person
        if _STAGE_RANK.get(stage, 0) >= _STAGE_RANK.get(cand.get("stage"), -1):
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
        self._last_brief = e

    def _on_ceo_notified(self, e: Event) -> None:
        if e.payload["reason"] == "flagged":
            self.flag_notice_log.append(e.ts)
        if e.payload["reason"] == "login" and getattr(self, "_last_brief", None) is not None:
            b = self._last_brief
            self.last_login_brief = {"brief_id": b.id, "notice_id": e.id, "ts": e.ts,
                                     "text": b.payload["text"], "counts": b.payload["counts"]}

    def _on_approval_requested_public(self, e: Event) -> None:  # pragma: no cover - alias
        pass


def _money(value: Decimal) -> str:
    """Spend for the rail: 2 places when whole cents, else up to 4 ("11.38", "0.0034")."""
    text = usd_string(value)
    return text[:-2] if text.endswith("00") else text


def rebuild(events) -> State:
    state = State()
    for e in events:
        state.apply(e)
    return state
