"""The CEO voice console's one tool registry (contract v1.8 §5b, design D28).

Each tool has a name, a kind (read / stop / start), an args schema and its handler. The
Realtime function schemas (`realtime_tools`) and the fallback grammar's targets (`parse`)
are both generated from this table, so the two paths can never drift apart.

- read: masked data only (labels; no mail bodies, addresses or quotes; a case QA flagged
  as a possible injection reads as FLAGGED).
- stop: carried out at once (`pause`), idempotent.
- start: never carried out here. `pin` resolves the target now (the exact approval, case,
  candidate, figure) and builds the spoken readback in code; the gateway stores that as a
  single-use 60-second proposal, and `carry` runs it on confirm through the same internals
  as the §5 routes.

Handlers take the Gateway and run under its lock (inside perform)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from app.gateway.projections import TRUST_TYPES  # one list for the registry and the gateway

FLAGGED = "flagged by QA, content hidden"
PROPOSAL_TTL = timedelta(seconds=60)
ORDER_MAX = 500
ACTIVITY_DEFAULT, ACTIVITY_MAX = 60, 720
ACTIVITY_ITEMS = 40
ACTIVITY_SCAN = 2000  # events looked back over, at most (a busy log shortens the window)
BRIEF_NEW_MAX = 1000
HEARD_MAX = 200

TRUST_WORDS = {"email": "emails", "social_post": "posting", "calendar_invite": "invites",
               "voice_screen": "screens"}


class ToolRefused(Exception):
    """A refusal with a contract code (the gateway turns it into Refused)."""

    def __init__(self, code: str, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.code, self.message, self.data = code, message, data or {}


@dataclass(frozen=True)
class CeoTool:
    name: str
    kind: str  # read | stop | start
    description: str
    properties: dict[str, dict] = field(default_factory=dict)
    required: tuple[str, ...] = ()
    read: Callable[[Any, dict], Any] | None = None
    pin: Callable[[Any, dict], tuple[dict, str]] | None = None  # -> (pinned args, readback)
    carry: Callable[[Any, dict], str] | None = None  # pinned args -> spoken summary
    # at confirm: why the pinned target no longer matches what was read back, else None
    stale: Callable[[Any, dict], str | None] | None = None

    def schema(self) -> dict:
        """The Realtime function schema."""
        return {"type": "function", "name": self.name, "description": self.description,
                "parameters": {"type": "object", "properties": self.properties,
                               "required": list(self.required), "additionalProperties": False}}

    def check(self, args: Any) -> dict:
        """Args against the schema (types, enums, required, no extras): bad_args otherwise."""
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ToolRefused("bad_args", f"{self.name} takes an object of args")
        extra = set(args) - set(self.properties)
        if extra:
            raise ToolRefused("bad_args", f"{self.name} takes no {sorted(extra)}")
        for name in self.required:
            if args.get(name) in (None, ""):
                raise ToolRefused("bad_args", f"{self.name} needs {name}")
        for name, value in args.items():
            spec = self.properties[name]
            if value is None:
                continue
            kind = spec.get("type")
            if kind == "string" and not isinstance(value, str):
                raise ToolRefused("bad_args", f"{name} must be a string")
            if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
                raise ToolRefused("bad_args", f"{name} must be a whole number")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolRefused("bad_args", f"{name} must be one of {spec['enum']}")
            if kind == "string" and len(value) > spec.get("maxLength", 200):
                raise ToolRefused("bad_args", f"{name} is too long")
        return args


# ------------------------------------------------------------------ references


def case_number(ref: Any) -> int | None:
    """`case_7`, `7`, `Team 7`, `team #7` -> 7."""
    if isinstance(ref, int) and not isinstance(ref, bool):
        return ref
    if not isinstance(ref, str):
        return None
    m = re.fullmatch(r"\s*(?:case_|(?:team|case)\s*#?\s*)?(\d{1,6})\s*", ref, re.I)
    return int(m.group(1)) if m else None


def find_case(gw: Any, ref: Any):
    number = case_number(ref)
    case = gw.state.cases.get(f"case_{number}") if number is not None else None
    if case is None:
        open_ = [c.masked_name for c in sorted(gw.state.cases.values(), key=lambda c: c.number)]
        raise ToolRefused("not_found", f"no case {ref!r}; the cases are: "
                                       f"{', '.join(open_) or 'none yet'}")
    return case


def pending_approvals(gw: Any) -> list:
    return [a for a in gw.state.approvals.values() if a.decision is None]


def find_approval(gw: Any, ref: Any):
    """An approval_id, "latest" (the newest pending), or a case (its newest pending)."""
    pending = pending_approvals(gw)
    if isinstance(ref, str) and ref.strip().lower() in ("latest", "it", "the latest"):
        if not pending:
            raise ToolRefused("nothing_pending", "no approval is waiting")
        return pending[-1]
    if isinstance(ref, str) and ref.strip() in gw.state.approvals:
        return gw.state.approvals[ref.strip()]
    number = case_number(ref)
    if number is not None:
        mine = [a for a in pending if a.case_id == f"case_{number}"]
        if mine:
            return mine[-1]
    waiting = ", ".join(f"{a.approval_id} ({a.summary})" for a in pending) or "none"
    raise ToolRefused("not_found", f"no pending approval for {ref!r}; waiting: {waiting}")


def find_candidate(gw: Any, ref: Any) -> dict:
    if isinstance(ref, str):
        key = ref.strip()
        if key in gw.state.candidates:
            return gw.state.candidates[key]
        letter = re.fullmatch(r"(?:candidate\s+)?([a-z]{1,2})", key, re.I)
        for cand in gw.state.candidates.values():
            name = str(cand.get("name_masked", ""))
            if name.lower() == key.lower() or (
                    letter and name.lower() == f"candidate {letter.group(1).lower()}"):
                return cand
    names = ", ".join(c.get("name_masked", c["candidate_id"])
                      for c in gw.state.candidates.values()) or "none"
    raise ToolRefused("not_found", f"no candidate {ref!r}; candidates: {names}")


def trust_label(action_type: str) -> str:
    return TRUST_WORDS.get(action_type, action_type)


# ------------------------------------------------------------------ read tools


def _summary(gw: Any, approval) -> str:
    """An approval's code-built summary; a flagged case's reads as FLAGGED (§5b)."""
    return FLAGGED if gw.flagged_injection(approval.case_id) else approval.summary


def _case_view(gw: Any, case) -> dict:
    view = case.public()
    if gw.flagged_injection(case.case_id):
        view["note"] = FLAGGED
    view["pending_approvals"] = [{"approval_id": a.approval_id, "summary": _summary(gw, a)}
                                 for a in pending_approvals(gw) if a.case_id == case.case_id]
    return view


def read_brief(gw: Any, args: dict) -> dict:
    text, counts = gw._brief_text()
    out = {"text": text, "counts": counts, "last_event_id": gw.state.last_event_id}
    since = args.get("since_id")
    if since is not None:  # how much happened after the id the CEO last saw
        out["new_events"] = sum(1 for _ in gw.store.since(max(0, since), limit=BRIEF_NEW_MAX))
    return out


def read_approvals(gw: Any, args: dict) -> list[dict]:
    out = []
    for a in pending_approvals(gw):
        case = gw.state.cases.get(a.case_id) if a.case_id else None
        out.append({"approval_id": a.approval_id, "action_type": a.action_type,
                    "case": case.masked_name if case else "firm", "summary": _summary(gw, a)})
    return out


def read_case(gw: Any, args: dict) -> dict:
    return _case_view(gw, find_case(gw, args.get("case")))


def read_money(gw: Any, args: dict) -> dict:
    return gw.state.public()["money"]


def read_trust(gw: Any, args: dict) -> dict:
    return dict(gw.state.trust)


def read_hiring(gw: Any, args: dict) -> dict:
    counts = {k: v for k, v in gw.state.public()["hiring"].items() if k != "candidates"}
    # labels only: an applicant's own words (credentials) never reach the voice model
    candidates = [{"candidate_id": c["candidate_id"], "name_masked": c.get("name_masked"),
                   "stage": c.get("stage")}
                  for c in sorted(gw.state.candidates.values(), key=lambda c: c["candidate_id"])]
    return {**counts, "candidates": candidates}


# payload fields that are labels the gateway wrote, never a client's (or applicant's) words
_SAFE_FIELDS = ("from", "to", "action_type", "decision", "reason", "stage", "tool",
                "approval_id", "summary", "kind", "masked_name", "name_masked", "status",
                "readback", "via")


def read_activity(gw: Any, args: dict) -> list[dict]:
    from app.gateway.events import parse_ts

    minutes = args.get("minutes") or ACTIVITY_DEFAULT
    minutes = max(1, min(int(minutes), ACTIVITY_MAX))
    cutoff = gw._now() - timedelta(minutes=minutes)
    start = max(0, gw.state.last_event_id - ACTIVITY_SCAN)  # a bounded window, newest last
    items = []
    for e in gw.store.since(start):
        if parse_ts(e.ts) < cutoff:
            continue
        case_id = e.case_id
        approval = gw.state.approvals.get(e.payload.get("approval_id") or "")
        if case_id is None and approval is not None:
            case_id = approval.case_id  # touches and requests about a case's approval
        case = gw.state.cases.get(case_id) if case_id else None
        item = {"id": e.id, "ts": e.ts, "type": e.type,
                "case": case.masked_name if case else None}
        if case is not None and gw.flagged_injection(case.case_id):
            item["note"] = FLAGGED
        else:
            item.update({k: e.payload[k] for k in _SAFE_FIELDS
                         if k in e.payload and isinstance(e.payload[k], (str, int, float))})
            if e.type.startswith("touch.") and isinstance(e.payload.get("note"), str):
                item["note"] = e.payload["note"]  # the CEO's own touches, code-built notes
        items.append(item)
    return items[-ACTIVITY_ITEMS:]


# ------------------------------------------------------------------ stop / start tools


def carry_pause(gw: Any, args: dict) -> str:
    target = args["action_type"]
    types = TRUST_TYPES if target == "everything" else (target,)
    for action_type in types:
        gw._set_trust({"action_type": action_type, "state": "PAUSED"})
    label = "everything" if target == "everything" else trust_label(target)
    return f"Paused {label}."


def pin_decision(verb: str) -> Callable[[Any, dict], tuple[dict, str]]:
    def pin(gw: Any, args: dict) -> tuple[dict, str]:
        approval = find_approval(gw, args.get("approval"))
        if approval.decision is not None:
            raise ToolRefused("nothing_pending", f"{approval.approval_id} is already "
                                                 f"{approval.decision}")
        also = ""
        if verb == "Approve" and gw.state.trust.get(approval.action_type) == "ASK":
            also = f" That also turns {trust_label(approval.action_type)} automatic."
        return ({"approval_id": approval.approval_id},
                f"{verb} {approval.summary}.{also} Say confirm.")
    return pin


def carry_decision(decision: str) -> Callable[[Any, dict], str]:
    def carry(gw: Any, pinned: dict) -> str:
        approval = gw.state.approvals[pinned["approval_id"]]
        out = gw._approve({"approval_id": pinned["approval_id"], "decision": decision})
        if out.get("duplicate"):
            return f"Already {out.get('decision')}: {approval.summary}."
        if decision == "allow" and approval.while_paused:  # recorded; runs on resume
            return (f"Approved, held until {trust_label(approval.action_type)} resumes: "
                    f"{approval.summary}.")
        return f"{'Approved' if decision == 'allow' else 'Denied'}: {approval.summary}."
    return carry


def pin_trust(gw: Any, args: dict) -> tuple[dict, str]:
    action_type, state = args["action_type"], args["state"]
    word = {"AUTO": "automatic", "ASK": "ask first", "RESUME": "what it was before the pause"}
    also = ""
    current = gw.state.trust.get(action_type)
    if state == "AUTO":
        waiting = len(gw._pending_of(action_type))
        if waiting:
            also = f" That also runs the {waiting} waiting {trust_label(action_type)}."
    if current == "PAUSED" and state != "PAUSED":
        held = len(gw._released(action_type, current))
        if held:
            also = f" That carries out {held} approval{'s' if held != 1 else ''} held by the pause."
    return ({"action_type": action_type, "state": state},
            f"Set {trust_label(action_type)} to {word[state]}.{also} Say confirm.")


def carry_trust(gw: Any, pinned: dict) -> str:
    out = gw._set_trust(dict(pinned))
    return f"{trust_label(pinned['action_type']).capitalize()} is now {out['trust']}."


def pin_unblock(gw: Any, args: dict) -> tuple[dict, str]:
    case = find_case(gw, args.get("case"))
    targets = gw.UNBLOCK_TARGETS.get(case.status)
    if not targets:
        raise ToolRefused("wrong_status", f"{case.masked_name} is {case.status}; "
                                          "nothing to unblock")
    to = args.get("to") or targets[0]
    if to not in targets:
        raise ToolRefused("bad_args", f"{case.masked_name} can go to {', '.join(targets)}")
    if to == "READY" and not case.facts:
        raise ToolRefused("bad_args", f"{case.masked_name} has no facts yet; use INTAKE_PENDING")
    return ({"case_id": case.case_id, "from": case.status, "to": to},
            f"Move {case.masked_name} from {case.status} to {to}. Say confirm.")


def stale_unblock(gw: Any, pinned: dict) -> str | None:
    case = gw.state.cases[pinned["case_id"]]
    if case.status != pinned["from"]:
        return f"{case.masked_name} moved from {pinned['from']} to {case.status}; ask again"
    return None


def carry_unblock(gw: Any, pinned: dict) -> str:
    case = gw.state.cases[pinned["case_id"]]
    gw._unblock({"case_id": pinned["case_id"], "to": pinned["to"]})
    return f"{case.masked_name} is now {pinned['to']}."


def pin_candidate(gw: Any, args: dict) -> tuple[dict, str]:
    cand = find_candidate(gw, args.get("candidate"))
    if cand.get("stage") in ("hired", "declined"):
        raise ToolRefused("nothing_pending", f"{cand.get('name_masked')} is already "
                                             f"{cand['stage']}")
    decision = args["decision"]
    return ({"candidate_id": cand["candidate_id"], "decision": decision},
            f"{decision.capitalize()} {cand.get('name_masked', cand['candidate_id'])}. "
            "Say confirm.")


def stale_candidate(gw: Any, pinned: dict) -> str | None:
    cand = gw.state.candidates[pinned["candidate_id"]]
    if cand.get("stage") in ("hired", "declined"):
        return f"{cand.get('name_masked')} is already {cand['stage']}; nothing to decide"
    return None


def carry_candidate(gw: Any, pinned: dict) -> str:
    out = gw._candidate_decision(dict(pinned))
    cand = gw.state.candidates[pinned["candidate_id"]]
    return f"{cand.get('name_masked')} {out['stage']}."


def pin_order(gw: Any, args: dict) -> tuple[dict, str]:
    text = " ".join(str(args["text"]).split())
    if not text:
        raise ToolRefused("bad_args", "order needs text")
    # the whole order is read back: confirm never sends words the CEO didn't hear
    return {"text": text}, f'Tell the Chief of Staff: "{text}". Say confirm.'


def carry_order(gw: Any, pinned: dict) -> str:
    channel = "voice" if gw._touch_channel == "voice" else "typed"
    gw._voice({"speaker": "ceo", "text": pinned["text"], "channel": channel})
    return "Sent to the Chief of Staff."


# ------------------------------------------------------------------ the registry

_CASE = {"type": "string", "description": 'a case: "case_7", "7" or "Team 7"', "maxLength": 40}
_APPROVAL = {"type": "string", "maxLength": 64,
             "description": 'an approval_id, "latest", or a case like "Team 7"'}

REGISTRY: dict[str, CeoTool] = {t.name: t for t in (
    CeoTool("get_brief", "read", "The firm in a few sentences: cases, waiting approvals.",
            {"since_id": {"type": "integer", "description": "events after this id"}},
            read=read_brief),
    CeoTool("list_approvals", "read", "Approvals waiting on the CEO.", read=read_approvals),
    CeoTool("get_case", "read", "One case's status (masked).", {"case": _CASE}, ("case",),
            read=read_case),
    CeoTool("get_money", "read", "Tax saved and money spent.", read=read_money),
    CeoTool("get_trust", "read", "Each action type: AUTO, ASK or PAUSED.", read=read_trust),
    CeoTool("get_hiring", "read", "Hiring counts and candidates (masked).", read=read_hiring),
    CeoTool("recent_activity", "read", "What happened in the last minutes (labels only; "
            f"the newest {ACTIVITY_ITEMS} of at most {ACTIVITY_SCAN} events).",
            {"minutes": {"type": "integer", "description": "default 60, max 720"}},
            read=read_activity),
    CeoTool("pause", "stop", "Stop an action type now (or everything). Runs at once.",
            {"action_type": {"type": "string", "enum": [*TRUST_TYPES, "everything"]}},
            ("action_type",), carry=carry_pause),
    CeoTool("approve", "start", "Propose approving a waiting approval (the CEO confirms).",
            {"approval": _APPROVAL}, ("approval",), pin=pin_decision("Approve"),
            carry=carry_decision("allow")),
    CeoTool("deny", "start", "Propose denying a waiting approval (the CEO confirms).",
            {"approval": _APPROVAL}, ("approval",), pin=pin_decision("Deny"),
            carry=carry_decision("deny")),
    CeoTool("set_trust", "start", "Propose a trust change for an action type.",
            {"action_type": {"type": "string", "enum": list(TRUST_TYPES)},
             "state": {"type": "string", "enum": ["AUTO", "ASK", "RESUME"]}},
            ("action_type", "state"), pin=pin_trust, carry=carry_trust),
    CeoTool("unblock_case", "start", "Propose moving a blocked (or sent) case on.",
            {"case": _CASE,
             "to": {"type": "string", "enum": ["INTAKE_PENDING", "READY", "NOT_ELIGIBLE"]}},
            ("case",), pin=pin_unblock, carry=carry_unblock, stale=stale_unblock),
    CeoTool("decide_candidate", "start", "Propose hiring or declining a candidate.",
            {"candidate": {"type": "string", "maxLength": 40,
                           "description": 'cand_N or "Candidate B"'},
             "decision": {"type": "string", "enum": ["hire", "decline"]}},
            ("candidate", "decision"), pin=pin_candidate, carry=carry_candidate,
            stale=stale_candidate),
    CeoTool("order", "start", "Propose an order for the Chief of Staff.",
            {"text": {"type": "string", "maxLength": ORDER_MAX}}, ("text",), pin=pin_order,
            carry=carry_order),
)}


def realtime_tools() -> list[dict]:
    return [t.schema() for t in REGISTRY.values()]


# ------------------------------------------------------------------ confirm words

_CONFIRM = re.compile(r"\b(confirm|yes|do it|go ahead)\b", re.I)
_APPROVE_IT = re.compile(r"\bapprove it\b", re.I)  # only ever confirms an approve
# the contract's cancel words, plus negations: "don't do it" is never a confirm
_CANCEL = re.compile(r"\b(no|nope|cancel|stop|wait|hold on|never ?mind|don'?t|do not|not|never)\b",
                     re.I)
# v1.8.1: the readback's own words, and the model asking, are never the CEO confirming
_ECHO = re.compile(r"\bsay\s+confirm\b|\b(?:shall|should) i\b|\bdo you want\b"
                   r"|\bwould you like\b|\bwant me to\b", re.I)


def _speech(heard: str) -> str:
    return (heard or "").replace("\u2019", "'")


def heard_cancels(heard: str) -> bool:
    return bool(_CANCEL.search(_speech(heard)))


def heard_confirms(heard: str, tool: str | None = None) -> bool:
    """The confirm grammar (whole words, any case). "approve it" confirms only an approve
    proposal; the readback echoed back, or a question, never confirms."""
    heard = _speech(heard)
    if _ECHO.search(heard):
        return False
    return bool(_CONFIRM.search(heard)) or (tool == "approve" and bool(_APPROVE_IT.search(heard)))


# ------------------------------------------------------------------ the fallback grammar

EXAMPLES = ('Try: "what happened overnight", "brief", "what\'s pending", "status of team 7", '
            '"money", "trust", "pause posting", "resume posting", "approve the latest", '
            '"deny team 7", "unblock team 7", "hire candidate B", '
            '"tell the chief of staff ...", "confirm", "cancel".')

_WORD_TYPES = {"posting": "social_post", "posts": "social_post", "post": "social_post",
               "emails": "email", "email": "email", "mail": "email", "invites": "calendar_invite",
               "invite": "calendar_invite", "calendar": "calendar_invite",
               "screens": "voice_screen", "screen": "voice_screen", "calls": "voice_screen",
               "everything": "everything", "all": "everything"}


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9#'_ ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^(please|ok|okay|hey|so) ", "", text)
    return re.sub(r" please$", "", text)


def parse(text: str) -> tuple[str, Any] | None:
    """Text -> ("tool", (name, args)) | ("confirm", None) | ("cancel", None) | None."""
    raw = (text or "").strip()
    t = _norm(raw)
    if not t:
        return None
    if t == "approve it":  # a confirm for an open approve proposal, else "approve the latest"
        return "approve_it", None
    if re.fullmatch(r"(confirm|yes|do it|go ahead)", t):
        return "confirm", None
    if re.fullmatch(r"(cancel|no|nope|stop|wait|hold on|never mind|don't|do not)", t):
        return "cancel", None
    m = re.fullmatch(r"tell (?:the )?chief of staff (?:to |that )?(.+)", t)
    if m:
        order = re.search(r"chief\s+of\s+staff[\s,:]+(?:to\s+|that\s+)?(.+)", raw, re.I | re.S)
        return "tool", ("order", {"text": (order.group(1) if order else m.group(1)).strip()})
    m = re.fullmatch(r"what happened(?: (overnight|today|last night))?", t)
    if m:
        return "tool", ("recent_activity", {"minutes": ACTIVITY_MAX if m.group(1)
                                            else ACTIVITY_DEFAULT})
    if re.fullmatch(r"(?:the |morning |give me the )?brief(?: me)?", t):
        return "tool", ("get_brief", {})
    if re.fullmatch(r"what'?s pending|what is pending|pending|approvals|what's waiting", t):
        return "tool", ("list_approvals", {})
    m = re.fullmatch(r"(?:status of |how is |what about )?(?:team|case) #?(\d+)", t)
    if m:
        return "tool", ("get_case", {"case": m.group(1)})
    if re.fullmatch(r"money|spend|spending|how much did we spend", t):
        return "tool", ("get_money", {})
    if re.fullmatch(r"trust|trust levels", t):
        return "tool", ("get_trust", {})
    if re.fullmatch(r"hiring|candidates", t):
        return "tool", ("get_hiring", {})
    m = re.fullmatch(r"(?:stop|pause) (\w+)", t)
    if m and m.group(1) in _WORD_TYPES:
        return "tool", ("pause", {"action_type": _WORD_TYPES[m.group(1)]})
    m = re.fullmatch(r"resume (\w+)", t)
    if m and _WORD_TYPES.get(m.group(1)) in TRUST_TYPES:
        return "tool", ("set_trust", {"action_type": _WORD_TYPES[m.group(1)],
                                      "state": "RESUME"})
    m = re.fullmatch(r"(approve|deny) (it|the latest|latest|(?:team|case) #?(\d+)|apr_\d+)", t)
    if m:
        target = m.group(3) or ("latest" if m.group(2) in ("it", "the latest", "latest")
                                else m.group(2))
        return "tool", (m.group(1), {"approval": target})
    m = re.fullmatch(r"unblock (?:team|case) #?(\d+)", t)
    if m:
        return "tool", ("unblock_case", {"case": m.group(1)})
    m = re.fullmatch(r"(hire|decline) (?:candidate )?(cand_\d+|[a-z])", t)
    if m:
        ref = m.group(2) if m.group(2).startswith("cand_") else f"Candidate {m.group(2).upper()}"
        return "tool", ("decide_candidate", {"candidate": ref, "decision": m.group(1)})
    return None
