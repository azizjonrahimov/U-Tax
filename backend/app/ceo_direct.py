"""Contract v1.14 §5a-ter (design D34) and v1.14.1: the CEO assistant's direct actions.

The owner's words: the CEO "doesn't have to say confirm". On the `ceo` MCP seat the §5b
registry runs DIRECTLY: a start tool is carried out at once (no proposal), through the same
internals as the console. What keeps that safe:

- Ambiguous references are refused, never guessed: "latest", "it", a pronoun, or one that
  matches more than one item answers `{code: "ambiguous", candidates: [{id, summary}]}`.
- Task 41: EVERY start tool (approve, deny, decide_candidate, order, set_trust,
  unblock_case) and an undo that would leave a pause, from a turn that began before the
  latest stop, is refused (`code: "stopped"`): a stop always wins over an LLM that was
  still mid-turn. Reads, pause and cancel_order stay allowed.
- Undo: `pause` <-> RESUME, `set_trust` <-> the previous state, `order` -> `cancel_order`.
  `approve`, `deny` and `decide_candidate` can't be undone, and the summary says so.
- The assistant's events carry the actor `ceo_assistant`; touches stay the CEO's (the
  human spoke the request), with channel "voice".
"""

from __future__ import annotations

import re
import secrets
from contextlib import contextmanager
from typing import Any

from app import ceo_tools
from app.ceo_tools import REGISTRY, ToolRefused, find_case, pending_approvals, trust_label

ASSISTANT = "ceo_assistant"
STOP_NOTE = "System: the CEO said stop; the gateway paused {types}. Undo id {undo_id}."
NO_UNDO = " This can't be undone."
_SYSTEM = re.compile(r"^\s*(?:system\s*:\s*)+", re.I)
VAGUE = {"latest", "the latest", "last", "the last", "the last one", "it", "that", "this",
         "that one", "this one", "the one", "them", "they", "those", "these", "he", "she",
         "him", "her", "his", "hers", "their", "theirs", "one", "the same", "same"}

# decision 2: the ceo seat's own tool definitions (direct, no "the CEO confirms")
DIRECT = {
    "approve": "Approve a waiting approval now. Name it exactly: its approval_id, or a case "
               "with one waiting approval. Can't be undone.",
    "deny": "Deny a waiting approval now. Name it exactly: its approval_id, or a case with "
            "one waiting approval. Can't be undone.",
    "set_trust": "Change an action type's trust now (AUTO, ASK, or RESUME after a pause). "
                 "Returns an undo_id.",
    "unblock_case": "Move a blocked (or sent) case on now.",
    "decide_candidate": "Hire or decline a candidate now (cand_N or \"Candidate B\"). Can't "
                        "be undone.",
    "order": "Send an order to the Chief of Staff now (it becomes a card). Returns an "
             "undo_id (cancel_order).",
    "pause": "Stop an action type now (or everything). Returns an undo_id.",
}
EXTRA = {
    "cancel_order": {"description": "Cancel a CEO order you sent (its order_id): the Chief of "
                                    "Staff closes its open cards, and no tool runs for it.",
                     "inputSchema": {"type": "object", "properties": {
                         "order_id": {"type": "string", "pattern": "^order_[0-9]{1,12}$"}},
                         "required": ["order_id"], "additionalProperties": False}},
    "undo": {"description": "Undo an earlier action by its undo_id (from that action's "
                            "result, or a System note).",
             "inputSchema": {"type": "object", "properties": {
                 "undo_id": {"type": "string", "pattern": "^undo_[0-9a-f]{10}$"}},
                 "required": ["undo_id"], "additionalProperties": False}},
}


def seat_tools() -> list[dict]:
    """The MCP tools/list for /mcp/ceo."""
    out = []
    for tool in REGISTRY.values():
        out.append({"name": tool.name, "description": DIRECT.get(tool.name, tool.description),
                    "inputSchema": {"type": "object", "properties": tool.properties,
                                    "required": list(tool.required),
                                    "additionalProperties": False}})
    return out + [{"name": name, **spec} for name, spec in EXTRA.items()]


SEAT_TOOLS = frozenset([*REGISTRY, *EXTRA])


def strip_system(text: str) -> str:
    """v1.14.1: only the gateway writes a "System:" note into the assistant's session."""
    return _SYSTEM.sub("", text or "")


def _vague(ref: Any) -> bool:
    return isinstance(ref, str) and " ".join(ref.lower().split()).strip(" .!?") in VAGUE


def _ambiguous(what: str, items: list[tuple[str, str]]) -> ToolRefused:
    return ToolRefused("ambiguous", f"which {what}? name one exactly",
                       {"candidates": [{"id": i, "summary": s} for i, s in items]})


def check_reference(gw: Any, name: str, args: dict) -> None:
    """v1.14.1: refuse a vague or many-matching approval or candidate reference."""
    if name in ("approve", "deny"):
        ref = args.get("approval")
        pending = pending_approvals(gw)
        if _vague(ref):
            raise _ambiguous("approval", [(a.approval_id, a.summary) for a in pending])
        if isinstance(ref, str) and ref.strip() in gw.state.approvals:
            return
        number = ceo_tools.case_number(ref)
        mine = [a for a in pending if number is not None and a.case_id == f"case_{number}"]
        if len(mine) > 1:
            raise _ambiguous("approval", [(a.approval_id, a.summary) for a in mine])
    if name == "decide_candidate":
        ref = args.get("candidate")
        cands = list(gw.state.candidates.values())
        if _vague(ref):
            raise _ambiguous("candidate", [(c["candidate_id"], c.get("name_masked", ""))
                                           for c in cands])
        if isinstance(ref, str) and ref.strip() not in gw.state.candidates:
            key = ref.strip().lower()
            same = [c for c in cands if str(c.get("name_masked", "")).lower() in (
                key, f"candidate {key}")]
            if len(same) > 1:
                raise _ambiguous("candidate", [(c["candidate_id"], c.get("name_masked", ""))
                                               for c in same])


@contextmanager
def acting_as(gw: Any, actor: str):
    before = getattr(gw, "_actor_as", None)
    gw._actor_as = actor
    try:
        yield
    finally:
        gw._actor_as = before


def _undo_id(gw: Any, record: dict) -> str:
    undo_id = f"undo_{secrets.token_hex(5)}"
    gw._undos[undo_id] = record
    return undo_id


def _after_stop(gw: Any) -> None:
    """v1.14.1 / task 41: nothing the assistant started before the latest stop may start
    anything (the oldest open turn is the one under way)."""
    stops, turn = gw._stops, gw.ceo_turn_stops()
    if stops and (turn is None or turn < stops):
        raise ToolRefused("stopped", "the CEO said stop after this request began; ask the "
                                     "CEO again before resuming")


def run(gw: Any, name: str, raw_args: Any, *, actor: str = ASSISTANT) -> dict:
    """One direct action (under the gateway lock). Returns {kind, summary|data, undo_id?}."""
    if name == "cancel_order":
        return cancel_order(gw, raw_args or {}, actor=actor)
    if name == "undo":
        return undo(gw, (raw_args or {}).get("undo_id"), actor=actor)
    tool = REGISTRY.get(name)
    if tool is None:
        raise ToolRefused("unknown_tool", f"no CEO tool {name!r}")
    args = tool.check(raw_args)
    if tool.kind == "read":
        return {"kind": "read", "data": tool.read(gw, args)}
    check_reference(gw, name, args)
    if tool.kind == "start" and actor == ASSISTANT:
        _after_stop(gw)  # task 41: a stop wins over every start tool of an older turn
    with acting_as(gw, actor), gw._channel("voice"):
        if tool.kind == "stop":
            target = args["action_type"]
            types = ceo_tools.TRUST_TYPES if target == "everything" else (target,)
            before = {t: gw.state.trust.get(t) for t in types}
            summary = tool.carry(gw, args)
            changed = [t for t in types if before[t] != "PAUSED"]
            out = {"kind": "done", "summary": summary, "paused": changed}
            if changed:
                out["undo_id"] = _undo_id(gw, {"kind": "pause", "types": changed})
            return out
        pinned, _readback = tool.pin(gw, args)  # validation, flagged and console-only checks
        previous = gw.state.trust.get(args.get("action_type")) if name == "set_trust" else None
        last_order = gw.state.last_event_id
        summary = tool.carry(gw, pinned)
    out = {"kind": "done", "summary": summary}
    if name == "set_trust" and previous is not None and previous != gw.state.trust.get(
            args["action_type"]):
        out["undo_id"] = _undo_id(gw, {"kind": "trust", "action_type": args["action_type"],
                                       "state": previous})
    elif name == "order":
        order_id = next((f"order_{e.id}" for e in gw.store.since(last_order)
                         if e.type == "voice.command" and e.payload.get("speaker") == "ceo"),
                        None)
        if order_id:
            out["order_id"] = order_id
            out["undo_id"] = _undo_id(gw, {"kind": "order", "order_id": order_id})
    elif name in ("approve", "deny", "decide_candidate"):
        out["summary"] = summary + NO_UNDO
    return out


def cancel_order(gw: Any, args: dict, *, actor: str = ASSISTANT) -> dict:
    order_id = args.get("order_id") if isinstance(args, dict) else None
    order = gw._valid_order(order_id)
    if order is None:
        raise ToolRefused("not_found", f"no CEO order {order_id!r}")
    if order in gw._cancelled_orders:
        return {"kind": "done", "summary": f"{order} was already cancelled.", "duplicate": True}
    gw._cancelled_orders.add(order)  # tools carrying it are refused from now on
    with acting_as(gw, actor), gw._channel("voice"):
        gw._voice({"speaker": "ceo", "channel": "voice",
                   "text": f"Cancel {order}: close its open cards and stop its work."})
    return {"kind": "done", "summary": f"Cancelled {order}; the Chief of Staff closes its "
                                       "cards."}


def undo(gw: Any, undo_id: Any, *, actor: str = ASSISTANT) -> dict:
    record = gw._undos.get(undo_id) if isinstance(undo_id, str) else None
    if record is None:
        raise ToolRefused("not_found", f"no undo {undo_id!r} (each undo works once)")
    if record.get("used"):
        raise ToolRefused("used", f"{undo_id} was already undone")
    if actor == ASSISTANT and (record["kind"] == "pause" or record.get("state") != "PAUSED"
                               and gw.state.trust.get(record.get("action_type")) == "PAUSED"):
        _after_stop(gw)  # an undo that would leave a pause is a resume too
    with acting_as(gw, actor), gw._channel("voice"):
        if record["kind"] == "pause":
            back = [t for t in record["types"] if gw.state.trust.get(t) == "PAUSED"]
            for action_type in back:
                gw._set_trust({"action_type": action_type, "state": "RESUME"})
            summary = ("Resumed " + ", ".join(trust_label(t) for t in back) + ".") if back \
                else "Nothing to resume: those aren't paused any more."
        elif record["kind"] == "trust":
            out = gw._set_trust({"action_type": record["action_type"], "state": record["state"]})
            summary = f"{trust_label(record['action_type']).capitalize()} is back to {out['trust']}."
        else:
            summary = cancel_order(gw, {"order_id": record["order_id"]}, actor=actor)["summary"]
    record["used"] = True
    return {"kind": "done", "summary": summary}


def stop_note(paused: list[str], undo_id: str | None) -> str:
    types = ", ".join(paused) or "nothing new (already paused)"
    return STOP_NOTE.format(types=types, undo_id=undo_id or "none")


__all__ = ["ASSISTANT", "SEAT_TOOLS", "check_reference", "find_case", "run", "seat_tools",
           "stop_note", "strip_system", "undo"]
