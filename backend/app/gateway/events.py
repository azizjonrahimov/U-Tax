"""Event envelope and the type catalog (contracts/api.md v1, sections 2 and 3).

Events are append-only and the only truth. Two fields never leave the gateway:
- `idempotency_key`: unique; a second append with the same key is a no-op.
- `private`: data the gateway needs but the theater must never see (the client's real
  email address for replies, the problems behind a re-ask). Payloads are masked.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

ACTORS = {"cos", "intake", "tax", "qa", "acct", "mkt", "hr", "ceo", "system",
          "ceo_assistant"}  # v1.14.1: the CEO assistant's direct actions
PROVIDERS = {"local", "naive", "agentmail", "openrouter", "openai", "elevenlabs", "hubspot", "mobilerun",
             "apify", "monid", "google", "stripe", "tempo", None}  # tempo: D38
STATUSES = {"ok", "failed", "skipped"}

_HR = ("candidate_id", "name_masked", "note")
_TOUCH = ("note", "approval_id", "channel")  # channel v1.8: absent on older events = click
_CRM = ("object", "stage")
_DM = ("recipient_label", "text")
_QA = ("checks",)

# type -> exact payload keys
PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "email.received": ("masked_name", "from_masked", "subject", "body"),
    "facts.extracted": ("facts", "quotes", "assumed"),
    "facts.missing": ("missing", "asks_so_far"),
    "client.asked": ("missing", "body"),
    "case.not_eligible": ("reason",),
    "case.status": ("from", "to"),
    "case.blocked": ("reason", "needs"),
    "law.searched": ("query", "results"),
    "tax.computed": ("authorized_method_cents", "assumed_par_method_cents", "chosen",
                     "annual_report_fee_cents", "fee_source", "total_cents", "due_date",
                     "form5472_flag", "rnd_flag", "engine_version"),
    "qa.passed": _QA,
    "qa.failed": _QA,
    "memo.sent": ("to_masked", "subject", "body", "message_id", "seconds_since_last_fact", "memo_ref"),
    "call.booked": ("start", "minutes", "invitee_masked"),
    "phone.checked": ("rendered_ok", "status_note"),  # v1.7: the screenshot url is private
    "crm.sync_requested": _CRM,
    "crm.synced": _CRM,
    "post.published": ("channel", "text", "url", "image_url"),
    "dm.drafted": _DM,
    "dm.sent_by_human": _DM,
    "hr.applicant": _HR,
    "hr.screened": _HR,
    "hr.shortlisted": _HR,
    "hr.hired": _HR,
    "hr.declined": _HR,
    "hr.questionnaire_sent": _HR,
    "hr.decision_mailed": _HR,  # v1.6.1
    "hr.outreach_declined": _HR,  # task 38b: HR read the reply as a no (not the CEO's call)
    "screen.call": ("candidate_id", "transcript_excerpt", "seconds"),
    "voice.command": ("text", "channel", "speaker", "case_id", "masked_name"),  # v1.6.4
    "ceo.notified": ("channel", "subject", "summary", "reason"),
    "naive.confirm": ("session_id", "agent", "tool", "decision", "read_people"),  # v1.9 §6b-bis
    "tool.called": ("tool", "status"),  # v1.12 §5a: every MCP tool call, no args or data
    "naive.recovered": ("session_id", "agent", "attempt"),  # task 27
    # v1.11.1 (D31): the live CEO orders
    "ad.created": ("kind", "headline", "image_url"),
    "hr.sourced": ("count", "source"),
    "hr.outreach": ("candidate_id", "label", "slot"),
    # v1.15 (D35): the tax documents, built live (no client names: file names use case ids)
    "doc.step": ("doc", "step", "detail"),  # step: 1, 2, ... (optional "of": the count)
    "doc.created": ("doc", "file_name", "sha256", "pages_or_sheets"),
    "doc.verified": ("doc", "ok"),  # one per document (optional "failed": the checks)
    "client.followed_up": ("kind",),
    # v1.19 §9.1 (D38): the agent market (all masked; no key ever)
    "job.posted": ("job_id", "template", "price_usd", "headline"),
    "job.claimed": ("job_id", "contractor"),
    "job.submitted": ("job_id", "contractor", "sha256"),
    "job.verified": ("job_id", "checks"),
    "job.failed": ("job_id", "checks", "reason"),
    "payment.sent": ("job_id", "contractor", "amount_usd", "token", "tx_hash", "explorer_url"),
    "job.delivered": ("job_id", "image_url"),
    "agent.sale": ("amount_usd", "token", "tx_hash", "explorer_url", "payer_masked"),
    # task 42 (D37): an agent's live Naive session, for admin/session readers only
    "agent.activity": ("agent", "session_id", "kind"),
    # task 40b (D36): the $49 filing offer (the link's URL is private) and its payment
    "payment.link_created": ("link_id", "amount_cents", "currency"),
    "payment.received": ("case_id", "amount_cents", "currency"),
    "approval.requested": ("approval_id", "action_type", "summary"),
    "touch.training": _TOUCH,
    "touch.operating": _TOUCH,
    "touch.engineering": _TOUCH,
    "trust.changed": ("action_type", "from", "to", "reason"),
    "cost.booked": ("provider", "model", "estimated"),
    "tool.bought": ("tool", "provider"),
    "brief.generated": ("text", "counts"),
    "case.flagged": ("kind",),
    # v1.8 §5b: the CEO voice console's proposals
    "ceo.proposed": ("proposal_id", "tool", "args", "readback", "expires_at", "source"),
    "ceo.confirmed": ("proposal_id", "via", "heard_len"),  # v1.8.5: heard is private
    "ceo.said": ("conversation_id", "text_len"),  # v1.14: the text is private
    "ceo.replied": ("conversation_id", "actions"),
    "ceo.cancelled": ("proposal_id", "via", "heard_len", "reason"),
    # v1.20 §10 (D41): an order is 1-6 tasks, run one at a time across the firm
    "order.queued": ("order_id", "of", "tasks"),
    "order.task_started": ("order_id", "task_no", "of", "text"),
    "order.task_done": ("order_id", "task_no", "of", "status", "summary", "by"),
    "order.done": ("order_id", "of", "done", "waiting_on_you", "other"),
}
ORDER_LINK_KEYS = frozenset({"order_id", "task_no"})  # any event may carry them (v1.11.1, v1.20)

FACT_KEYS = ("incorporated_delaware", "authorized_shares", "issued_shares", "stated_par",
             "gross_assets_cents", "foreign_owner_25")
# keys a payload may leave out: v1.6.4 masked_name is set on customer voice.command only
OPTIONAL_PAYLOAD_KEYS = {"voice.command": frozenset({"masked_name", "tasks"}),  # tasks: D41
                         "call.booked": frozenset({"kind"}),  # v1.11.1: "candidate"
                         "email.received": frozenset({"source"}),  # v1.12: "signup"
                         "doc.step": frozenset({"of"}), "doc.verified": frozenset({"failed"}),
                         "agent.activity": frozenset({"tool", "text", "order_id"}),
                         **{t: frozenset({"channel"})
                            for t in ("touch.training", "touch.operating", "touch.engineering")}}
OPTIONAL_FACT_KEYS = ("gross_receipts_cents", "years_with_receipts")  # v1.1, R&D flag only


def iso_ts(when: datetime) -> str:
    """2026-11-07T21:14:03.120Z"""
    utc = when.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"


def parse_ts(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def validate(type_: str, actor: str, provider: str | None, status: str, payload: dict) -> None:
    if type_ not in PAYLOAD_KEYS:
        raise ValueError(f"unknown event type {type_!r}")
    if actor not in ACTORS:
        raise ValueError(f"unknown actor {actor!r}")
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    expected = set(PAYLOAD_KEYS[type_])
    # v1.11.1: any event of an agent's tool call may carry the CEO order it serves; v1.20:
    # and its task (a type that lists either key requires it)
    optional = OPTIONAL_PAYLOAD_KEYS.get(type_, frozenset()) | (
        ORDER_LINK_KEYS - set(PAYLOAD_KEYS[type_]))
    expected = set(expected) | optional  # an optional key is allowed, never required
    if not (expected - optional <= set(payload) <= expected):
        raise ValueError(
            f"{type_} payload keys {sorted(payload)} != contract {sorted(expected)}"
        )


@dataclass(frozen=True)
class NewEvent:
    type: str
    actor: str
    payload: dict[str, Any]
    case_id: str | None = None
    provider: str | None = None
    status: str = "ok"
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: str | None = None
    idempotency_key: str | None = None
    private: dict[str, Any] | None = None
    ts: str | None = None  # set by the gateway clock when None

    def __post_init__(self) -> None:
        validate(self.type, self.actor, self.provider, self.status, self.payload)


@dataclass(frozen=True)
class Event:
    id: int
    ts: str
    type: str
    actor: str
    case_id: str | None
    provider: str | None
    status: str
    payload: dict[str, Any]
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: str | None = None
    idempotency_key: str | None = None
    private: dict[str, Any] | None = field(default=None, compare=False)

    @classmethod
    def from_new(cls, new: NewEvent, *, id: int, ts: str) -> "Event":
        return cls(id=id, ts=new.ts or ts, type=new.type, actor=new.actor, case_id=new.case_id,
                   provider=new.provider, status=new.status, payload=new.payload,
                   tokens_in=new.tokens_in, tokens_out=new.tokens_out, cost_usd=new.cost_usd,
                   idempotency_key=new.idempotency_key, private=new.private)

    def public(self) -> dict[str, Any]:
        """The contract envelope: what the theater and the API may see."""
        return {"id": self.id, "ts": self.ts, "type": self.type, "actor": self.actor,
                "case_id": self.case_id, "provider": self.provider, "status": self.status,
                "payload": self.payload, "tokens_in": self.tokens_in,
                "tokens_out": self.tokens_out, "cost_usd": self.cost_usd}

    def record(self) -> dict[str, Any]:
        """Everything, for the store."""
        out = self.public()
        out["idempotency_key"] = self.idempotency_key
        out["private"] = self.private
        return out

    @classmethod
    def from_record(cls, rec: dict[str, Any]) -> "Event":
        return cls(id=int(rec["id"]), ts=rec["ts"], type=rec["type"], actor=rec["actor"],
                   case_id=rec.get("case_id"), provider=rec.get("provider"),
                   status=rec["status"], payload=rec["payload"], tokens_in=rec.get("tokens_in"),
                   tokens_out=rec.get("tokens_out"), cost_usd=rec.get("cost_usd"),
                   idempotency_key=rec.get("idempotency_key"), private=rec.get("private"))

    def with_id(self, id: int) -> "Event":
        return replace(self, id=id)
