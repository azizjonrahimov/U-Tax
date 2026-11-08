"""Per-seat MCP server for the Naïve agents (contract §6, v1.4: `POST /mcp/<actor>`).

MCP streamable HTTP, answered as single JSON responses (no server-initiated stream):
`initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call`.

Auth: `Authorization: Bearer <that seat's UTAX_TOOL_TOKEN_*>`. The token must belong to the
seat in the path (else 401), must be at least 32 characters, and a seat with no token
configured is closed: there is no empty-token bypass (a tunnel request arrives from
localhost too). An optional `X-UTax-Actor` header is accepted only when it matches.
Every call goes through `perform(name, args, actor=seat)`, which refuses tools outside
the seat's §6 column (`tool_not_allowed`) and writes that actor on the events.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app import __version__
from app.gateway.perform import AGENTS, TOOL_ACTORS, Gateway

log = logging.getLogger("utax.mcp")
CEO_SEAT = "ceo"  # v1.14 (D34): the CEO assistant; its tools are the §5b registry, direct
SEATS = (*AGENTS, CEO_SEAT)
_background: set = set()  # tool.called writes in flight (a reference keeps each task alive)

MIN_TOKEN_CHARS = 32
MAX_BODY_BYTES = 256_000  # a tool call is small; anything bigger is refused unread
MAX_BATCH = 5  # 2025-06-18 dropped batching; older clients get a small one
SEAT_CONCURRENCY = 2  # calls in flight per seat; more get 429 (a looping agent can't
# fill the thread pool or queue the whole gateway behind the lock)
_in_flight: dict[str, int] = {}


def _no_constants(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
FACTS = ("incorporated_delaware", "authorized_shares", "issued_shares", "stated_par",
         "gross_assets", "foreign_owner_25", "gross_receipts", "years_with_receipts")

_CASE = {"case_id": {"type": "string", "description": "e.g. case_7"}}


MARKET_TOOLS = ("hire_contractor", "get_job")  # D38: listed only with the market on


def _obj(props: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


# Contract §6 inputs. Descriptions are short and factual: the agents' prompts carry the how.
TOOLS: dict[str, dict[str, Any]] = {
    "list_open_cases": {"description": "Open cases: status, masked name, waiting reason, next step.",
                        "inputSchema": _obj({})},
    "get_case": {"description": "One case for agents: the client's mail (contacts and SSN/EIN "
                                "removed), facts, quotes, status, result, offered_slots.",
                 "inputSchema": _obj(_CASE, ["case_id"])},
    "extract_facts": {
        "description": "Point at the verbatim span for each fact; the gateway parses and checks "
                       "it. Missing or conflicting facts come back as missing.",
        "inputSchema": _obj({
            **_CASE,
            "quotes": {"type": "object", "description": "fact name -> verbatim span",
                       "properties": {f: {"type": "string"} for f in FACTS}},
            "incorporated_delaware": {"type": "boolean",
                                      "description": "omit when the email doesn't say"},
            "foreign_owner_25": {"type": "boolean",
                                 "description": "omit when the email doesn't say"},
        }, ["case_id", "quotes"])},
    "ask_client": {"description": "Templated re-ask email for the missing facts (max 2).",
                   "inputSchema": _obj({**_CASE, "missing": {
                       "type": "array", "items": {"type": "string", "enum": list(FACTS)}}},
                       ["case_id"])},
    "mark_not_eligible": {"description": "Kind templated reply chosen by the reason.",
                          "inputSchema": _obj({**_CASE, "reason": {
                              "type": "string", "enum": ["not_incorporated", "outside_delaware",
                                                         "not_a_corporation"]}},
                              ["case_id", "reason"])},
    "law_search": {"description": "Search Delaware franchise tax law (canned results tonight).",
                   "inputSchema": _obj({**_CASE, "query": {"type": "string"}},
                                       ["case_id", "query"])},
    "compute_tax": {"description": "Run the tested engine on the extracted facts.",
                    "inputSchema": _obj(_CASE, ["case_id"])},
    "qa_check": {"description": "Re-run the engine and re-match every quote.",
                 "inputSchema": _obj(_CASE, ["case_id"])},
    "prepare_documents": {"description": "Build and check the case's workpaper (.xlsx), "
                                         "memo (.docx) and books entry from the engine's "
                                         "figures (refused unless QA passed). Before "
                                         "send_memo.",
                          "inputSchema": _obj(_CASE, ["case_id"])},
    "send_memo": {"description": "Send the memo with its workpaper and memo files (refused "
                                 "unless QA passed and prepare_documents ran).",
                  "inputSchema": _obj(_CASE, ["case_id"])},
    "book_call": {"description": "Book the 15-minute review call at one of get_case's "
                                 "offered_slots.",
                  "inputSchema": _obj({**_CASE, "start": {"type": "string",
                                                          "description": "ISO 8601 UTC"}},
                                      ["case_id", "start"])},
    "phone_check": {"description": "Open the sent memo on a phone and capture it.",
                    "inputSchema": _obj(_CASE, ["case_id"])},
    "post_social": {"description": "Post (content guard first; the gateway appends the link).",
                    "inputSchema": _obj({
                        "channel": {"type": "string", "enum": ["x", "linkedin"]},
                        "text": {"type": "string", "maxLength": 255},
                        "image_url": {"type": "string", "description": "optional"},
                        "kind": {"type": "string", "enum": ["client", "hiring"]},
                    }, ["channel", "text"])},
    "draft_dm": {"description": "Draft a LinkedIn DM; a human sends it.",
                 "inputSchema": _obj({"recipient_label": {"type": "string"},
                                      "text": {"type": "string"}},
                                     ["recipient_label", "text"])},
    "get_candidate": {"description": "One candidate, masked: credentials, answers, stage.",
                      "inputSchema": _obj({"candidate_id": {"type": "string"}},
                                          ["candidate_id"])},
    "hr_record": {"description": "Record a screening stage; hiring and declining are the "
                                 "CEO's. outreach_declined: the candidate said no to the "
                                 "outreach in other words (frees the slot; no later mail).",
                  "inputSchema": _obj({"stage": {"type": "string",
                                                 "enum": ["screened", "shortlisted",
                                                          "outreach_declined"]},
                                       "candidate_id": {"type": "string"},
                                       "note": {"type": "string"}},
                                      ["stage", "candidate_id"])},
    "flag_case": {"description": "Flag a case for the CEO.",
                  "inputSchema": _obj({**_CASE, "kind": {
                      "type": "string", "enum": ["possible_injection", "unrealistic_inputs"]}},
                      ["case_id", "kind"])},
    "firm_brief": {"description": "Counts, blocked cards and touches for the brief.",
                   "inputSchema": _obj({})},
    "buy_tool": {"description": "Buy one allowlisted data call (Monid) for public startup data. "
                                "Refused for any tool not in MONID_TOOLS, over its price "
                                "ceiling or the daily cap, or for input with an email, phone "
                                "or case id. Reuse the same key to retry: it never pays twice.",
                 "inputSchema": _obj({"tool": {"type": "string"},
                                      "input": {"type": "object"},
                                      "key": {"type": "string",
                                              "description": "your id for this purchase"}},
                                     ["tool", "input", "key"])},
    # v1.11 (D31): the live CEO orders
    "make_ad": {"description": "Render the firm's ad image (a PNG on the gateway's host); "
                               "pass its image_url to post_social.",
                "inputSchema": _obj({"kind": {"type": "string", "enum": ["client", "hiring"]},
                                     "headline": {"type": "string", "maxLength": 80}},
                                    ["kind", "headline"])},
    # v1.19 §9.2 (D38): listed only with MARKET_ENABLED=1
    "hire_contractor": {"description": "Hire an outside contractor agent to make a share card "
                                       "(template share_card) for this headline; the gateway "
                                       "checks the work and pays it in test tokens. The price "
                                       "is the gateway's. Then get_job, then post_social.",
                        "inputSchema": _obj({"template": {"type": "string",
                                                          "enum": ["share_card"]},
                                             "headline": {"type": "string", "maxLength": 80},
                                             "key": {"type": "string", "maxLength": 120}},
                                            ["template", "headline", "key"])},
    "get_job": {"description": "A hired job's status (waits up to ~20 s for a change): open, "
                               "claimed, submitted, verified, paid, delivered (image_url to "
                               "post), failed or expired; approval_id while the payment waits "
                               "on the CEO.",
                "inputSchema": _obj({"job_id": {"type": "string", "maxLength": 40}},
                                    ["job_id"])},
    "source_candidates": {"description": "Public leads for the reviewer gig (Monid search): "
                                         "headline and public URL only, never an address.",
                          "inputSchema": _obj({"count": {"type": "integer", "minimum": 1,
                                                         "maximum": 5}}, ["count"])},
    "list_candidates": {"description": "Every candidate, masked: id, label, stage, "
                                       "has_address, offered_slots.",
                        "inputSchema": _obj({})},
    "hr_outreach": {"description": "Email a candidate the firm holds a consented address for "
                                   "(the gig + a call invite at one of their offered_slots).",
                    "inputSchema": _obj({"candidate_id": {"type": "string"},
                                         "slot": {"type": "string",
                                                  "description": "ISO 8601 UTC"}},
                                        ["candidate_id", "slot"])},
    "follow_up": {"description": "A templated follow-up in the client's thread (1 a day).",
                  "inputSchema": _obj({**_CASE, "kind": {
                      "type": "string", "enum": ["memo_check", "missing_facts",
                                                 "call_reminder"]}}, ["case_id", "kind"])},
}

# v1.20 §10 (D41): any seat ends the CEO order task its card was made for
TOOLS["task_done"] = {
    "description": "D41: the last call of a card made for a CEO order task. Ends that task so "
                   "the next one starts. Call it once, whatever the outcome.",
    # the runbook's schema, less additionalProperties (MCP schemas use plain types: task 7)
    "inputSchema": {"type": "object", "required": ["order_id", "task_no", "status"],
                    "properties": {"order_id": {"type": "string",
                                                "pattern": "^order_[0-9]{1,12}$"},
                                   "task_no": {"type": "integer", "minimum": 1, "maximum": 6},
                                   "status": {"type": "string",
                                              "enum": ["done", "blocked", "failed"]},
                                   "summary": {"type": "string", "maxLength": 200}}}}

# v1.11.1: every tool takes the CEO order it serves (from the card); the gateway checks it
for _name, _spec in TOOLS.items():
    if _name == "task_done":  # its order_id is required and is the subject (schema above)
        continue
    _spec["inputSchema"]["properties"]["order_id"] = {
        "type": "string", "pattern": "^order_[0-9]+$",
        "description": "optional: the order_<id> from the card, when it serves a CEO order"}
    # v1.20 §10.2: and the card's task (`order_<id> task k/N`); an explicit task wins
    _spec["inputSchema"]["properties"]["task_no"] = {
        "type": "integer", "minimum": 1, "maximum": 6,
        "description": "optional: k from the card's `order_<id> task k/N` line"}
assert set(TOOLS) == set(TOOL_ACTORS), "every §6 tool needs a schema"


_last_reject_log: dict[str, float] = {}


def _warn_rejected(actor: str) -> None:
    import time

    now = time.monotonic()
    if now - _last_reject_log.get(actor, -60.0) >= 60:
        _last_reject_log[actor] = now
        log.warning("mcp/%s: rejected token (further rejections muted for 60 s)", actor)


def _rpc_error(id_: Any, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"jsonrpc": "2.0", "id": id_,
                                 "error": {"code": code, "message": message}})


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse(status_code=401, headers={"WWW-Authenticate": "Bearer"},
                        content={"error": {"code": "unauthorized", "message": message}})


def seat_problems(tokens: dict[str, str]) -> dict[str, str]:
    """Seats that are closed, and why: unset, short, or sharing a token with another seat
    (a shared token would let either seat open both, which the contract forbids)."""
    out: dict[str, str] = {}
    for seat in SEATS:
        token = tokens.get(seat, "")
        if not token:
            out[seat] = "no token"
        elif len(token) < MIN_TOKEN_CHARS:
            out[seat] = f"token shorter than {MIN_TOKEN_CHARS} characters"
        elif any(o != seat and tokens.get(o) == token for o in SEATS):
            out[seat] = "token shared with another seat"
    return out


def seat_token_ok(tokens: dict[str, str], actor: str, authorization: str | None) -> bool:
    expected = tokens.get(actor, "")
    if len(expected) < MIN_TOKEN_CHARS:
        return False  # an unset or short token closes the seat: no empty-token bypass
    if any(o != actor and tokens.get(o) == expected for o in SEATS):
        return False  # a token shared between seats opens neither
    scheme, _, given = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not given.strip():
        return False
    return hmac.compare_digest(given.strip().encode(), expected.encode())


def build_mcp_router(get_gateway: Callable[[], Gateway]) -> APIRouter:
    router = APIRouter()
    try:
        closed = seat_problems(get_gateway().settings.tool_tokens)
    except Exception:  # noqa: BLE001 - no gateway yet (tests build it later)
        closed = {}
    for seat, why in closed.items():
        log.warning("MCP seat /mcp/%s is closed: %s", seat, why)

    @router.get("/mcp/{actor}")
    def no_stream(actor: str):  # streamable HTTP: no server-initiated stream here
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.delete("/mcp/{actor}")
    def no_session(actor: str):
        return Response(status_code=405, headers={"Allow": "POST"})

    @router.post("/mcp/{actor}")
    async def mcp(actor: str, request: Request):
        gw = get_gateway()
        if actor not in SEATS:
            return JSONResponse(status_code=404,
                                content={"error": {"code": "not_found",
                                                   "message": f"no seat {actor}"}})
        if not seat_token_ok(gw.settings.tool_tokens, actor,
                             request.headers.get("authorization")):
            _warn_rejected(actor)
            return _unauthorized(f"this seat needs its own tool token ({actor})")
        claimed = request.headers.get("x-utax-actor")
        if claimed is not None and claimed.strip() != actor:
            return _unauthorized("X-UTax-Actor does not match the token's seat")
        declared = request.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
            return _rpc_error(None, -32600, "request too large", 413)
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_BODY_BYTES:
                return _rpc_error(None, -32600, "request too large", 413)
        try:
            message = json.loads(raw or b"null", parse_constant=_no_constants)
        except (ValueError, RecursionError):
            return _rpc_error(None, -32700, "parse error", 400)
        if isinstance(message, list) and not 0 < len(message) <= MAX_BATCH:
            return _rpc_error(None, -32600, f"a batch holds 1 to {MAX_BATCH} messages", 400)
        if _in_flight.get(actor, 0) >= SEAT_CONCURRENCY:
            return JSONResponse(status_code=429, headers={"Retry-After": "2"}, content={
                "jsonrpc": "2.0", "id": message.get("id") if isinstance(message, dict) else None,
                "error": {"code": -32000, "message": f"{actor} already has "
                                                     f"{SEAT_CONCURRENCY} calls running"}})
        _in_flight[actor] = _in_flight.get(actor, 0) + 1
        try:
            return await _respond(gw, actor, message, request)
        finally:
            _in_flight[actor] -= 1

    return router


async def _respond(gw: Gateway, actor: str, message: Any, request: Request) -> Response:
    if isinstance(message, list):  # batches (2025-03-26): answer each request
        replies = []
        for m in message:
            reply = await _handle(gw, actor, m, request)
            if reply is not None:
                replies.append(reply)
        return JSONResponse(content=replies) if replies else Response(status_code=202)
    reply = await _handle(gw, actor, message, request)
    if reply is None:
        return Response(status_code=202)  # a notification or response: accepted
    headers = {}
    if isinstance(message, dict) and message.get("method") == "initialize":
        headers["MCP-Protocol-Version"] = reply.get("result", {}).get(
            "protocolVersion", SUPPORTED_VERSIONS[0])
    return JSONResponse(content=reply, headers=headers)


async def _handle(gw: Gateway, actor: str, message: Any, request: Request) -> dict | None:
    import asyncio

    if not isinstance(message, dict):
        return {"jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "invalid request"}}
    method, id_ = message.get("method"), message.get("id")
    if not isinstance(id_, (str, int, type(None))) or isinstance(id_, bool):
        id_ = None
    if message.get("jsonrpc") != "2.0":
        return {"jsonrpc": "2.0", "id": id_,
                "error": {"code": -32600, "message": "invalid request"}}
    if method is None and ("result" in message or "error" in message):
        return None  # a client's response: this server never asks, so accept and drop
    if "id" not in message:  # notifications (initialized, cancelled ...)
        return None
    params = message.get("params") or {}
    if not isinstance(method, str) or not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": id_,
                "error": {"code": -32602 if isinstance(method, str) else -32600,
                          "message": "params must be an object" if isinstance(method, str)
                          else "invalid request"}}
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": f"u-tax-{actor}", "version": __version__},
            "instructions": f"U-Tax gateway tools for the {actor} seat. Every number is "
                            "computed by the gateway's tested code; agents point at facts.",
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if method == "tools/list":
        if actor == CEO_SEAT:  # v1.14 decision 2: its own definitions (direct runs)
            from app.ceo_direct import seat_tools

            return {"jsonrpc": "2.0", "id": id_, "result": {"tools": seat_tools()}}
        tools = [{"name": name, **spec} for name, spec in TOOLS.items()
                 if actor in TOOL_ACTORS[name]
                 and (name not in MARKET_TOOLS or gw.settings.market_enabled)]
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": tools}}
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return {"jsonrpc": "2.0", "id": id_,
                    "error": {"code": -32602, "message": "tools/call needs name and arguments"}}
        if actor == CEO_SEAT:
            return await _ceo_call(gw, id_, name, args)
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": id_,
                    "error": {"code": -32602, "message": f"unknown tool {name}"}}
        result = await asyncio.to_thread(gw.perform, name, args, actor=actor)
        # v1.12 §5a: tool.called, written off the reply's path (the agent never waits)
        task = asyncio.create_task(asyncio.to_thread(
            gw.record_tool_call, actor, name, result.status, args.get("order_id"),
            args.get("task_no")))
        _background.add(task)
        task.add_done_callback(_background.discard)
        body = result.as_dict()
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
            "structuredContent": body,
            "isError": result.status in ("refused", "error"),
        }}
    return {"jsonrpc": "2.0", "id": id_,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


async def _ceo_call(gw: Gateway, id_: Any, name: str, args: dict) -> dict:
    """v1.14: a /mcp/ceo tool runs directly. Results: {status: "done", summary, undo_id?}
    (or {status: "done", data} for a read); refusals {status: "refused", code, message,
    candidates?} (v1.14.1)."""
    import asyncio

    from app.ceo_direct import ASSISTANT, SEAT_TOOLS

    if name not in SEAT_TOOLS:
        return {"jsonrpc": "2.0", "id": id_,
                "error": {"code": -32602, "message": f"unknown tool {name}"}}
    result = await asyncio.to_thread(gw.perform, "ceo_direct", {"name": name, "args": args})
    task = asyncio.create_task(asyncio.to_thread(
        gw.record_tool_call, ASSISTANT, name, result.status, None))
    _background.add(task)
    task.add_done_callback(_background.discard)
    if result.status == "done":
        data = dict(result.data or {})
        kind = data.pop("kind", None)
        body = {"status": "done", **({"data": data["data"]} if kind == "read" else data)}
    else:
        error = result.error or {}
        body = {"status": result.status, "code": error.get("code"),
                "message": error.get("message"), **(result.data or {})}
    return {"jsonrpc": "2.0", "id": id_, "result": {
        "content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
        "structuredContent": body, "isError": result.status != "done"}}
