"""Task 7: per-seat MCP (`POST /mcp/<actor>`, contract §6 v1.4)."""

import pytest
from fastapi.testclient import TestClient

from app.gateway.perform import AGENTS, TOOL_ACTORS
from app.main import create_app

from .conftest import HAPPY_QUOTES

TOKENS = {a: f"tok-{a}-" + "x" * 32 for a in (*AGENTS, "ceo")}  # v1.14: + the ceo seat


@pytest.fixture
def mcp(make_harness):
    h = make_harness(tool_tokens=dict(TOKENS))
    with TestClient(create_app(h.gw, start_background=False)) as client:
        yield h, client


def call(client, actor, method, params=None, *, token=None, id_=1, headers=None):
    hdrs = {"Authorization": f"Bearer {token or TOKENS[actor]}", **(headers or {})}
    body = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        body["params"] = params
    return client.post(f"/mcp/{actor}", json=body, headers=hdrs)


def tool(client, actor, name, **args):
    r = call(client, actor, "tools/call", {"name": name, "arguments": args})
    assert r.status_code == 200, r.text
    return r.json()["result"]["structuredContent"]


def test_the_token_must_belong_to_the_seat(mcp):
    h, client = mcp
    assert call(client, "intake", "ping").status_code == 200
    assert call(client, "intake", "ping", token=TOKENS["tax"]).status_code == 401  # other seat
    assert call(client, "intake", "ping", token="nope").status_code == 401
    r = client.post("/mcp/intake", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert call(client, "intake", "ping", headers={"X-UTax-Actor": "tax"}).status_code == 401
    assert call(client, "intake", "ping", headers={"X-UTax-Actor": "intake"}).status_code == 200
    assert client.post("/mcp/boss", json={}).status_code == 404  # not a seat
    assert client.post("/mcp/ceo", json={}).status_code == 401  # v1.14: a seat, its own token


@pytest.mark.parametrize("tokens", [{}, {"intake": ""}, {"intake": "short"}])
def test_an_empty_or_short_token_closes_the_seat(make_harness, tokens):
    h = make_harness(tool_tokens=tokens)
    with TestClient(create_app(h.gw, start_background=False)) as client:
        r = client.post("/mcp/intake", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                        headers={"Authorization": f"Bearer {tokens.get('intake', '')}"})
    assert r.status_code == 401  # no empty-token bypass, even from localhost


def test_initialize_negotiates_the_protocol(mcp):
    h, client = mcp
    r = call(client, "cos", "initialize", {"protocolVersion": "2025-03-26",
                                           "capabilities": {}, "clientInfo": {"name": "naive"}})
    result = r.json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert r.headers["mcp-protocol-version"] == "2025-03-26"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    odd = call(client, "cos", "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert odd["protocolVersion"] == "2025-06-18"
    note = client.post("/mcp/cos", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                       headers={"Authorization": f"Bearer {TOKENS['cos']}"})
    assert note.status_code == 202


@pytest.mark.parametrize("actor", AGENTS)
def test_each_seat_lists_only_its_own_tools(mcp, actor):
    h, client = mcp
    names = {t["name"] for t in call(client, actor, "tools/list").json()["result"]["tools"]}
    assert names == {n for n, actors in TOOL_ACTORS.items() if actor in actors}
    assert "get_case" in names  # every seat may read a case


def test_calls_run_through_perform_under_the_seat(mcp):
    h, client = mcp
    case_id = h.new_case()
    out = tool(client, "intake", "extract_facts", case_id=case_id, quotes=HAPPY_QUOTES,
               incorporated_delaware=True, foreign_owner_25=False)
    assert out["ok"] and out["status"] == "done"
    assert h.events("facts.extracted")[0].actor == "intake"
    refused = call(client, "intake", "tools/call",
                   {"name": "compute_tax", "arguments": {"case_id": case_id}}).json()["result"]
    assert refused["isError"] is True
    assert refused["structuredContent"]["error"]["code"] == "tool_not_allowed"
    assert "tax.computed" not in h.types()


def test_full_case_over_mcp(mcp):
    h, client = mcp
    case_id = h.new_case()
    tool(client, "intake", "extract_facts", case_id=case_id, quotes=HAPPY_QUOTES,
         incorporated_delaware=True, foreign_owner_25=False)
    tool(client, "tax", "law_search", case_id=case_id, query="delaware assumed par")
    tool(client, "tax", "compute_tax", case_id=case_id)
    assert tool(client, "qa", "qa_check", case_id=case_id)["data"]["passed"] is True
    early = tool(client, "acct", "send_memo", case_id=case_id)
    assert early["error"]["code"] == "docs_missing"  # v1.15: the documents come first
    assert tool(client, "acct", "prepare_documents", case_id=case_id)["data"]["ok"] is True
    pending = tool(client, "acct", "send_memo", case_id=case_id)
    assert pending["status"] == "needs_approval"
    h.ok("approve", approval_id=pending["approval_id"], decision="allow")
    assert tool(client, "acct", "send_memo", case_id=case_id)["status"] == "done"
    assert h.gw.state.cases[case_id].status == "SENT"


def test_protocol_errors(mcp):
    h, client = mcp
    bad = client.post("/mcp/cos", content=b"{not json",
                      headers={"Authorization": f"Bearer {TOKENS['cos']}",
                               "Content-Type": "application/json"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == -32700
    assert call(client, "cos", "nope/nope").json()["error"]["code"] == -32601
    assert call(client, "cos", "tools/call", {"name": "rm_rf"}).json()["error"]["code"] == -32602
    assert client.get("/mcp/cos").status_code == 405


def test_oversized_bodies_and_batches_are_refused(mcp):
    h, client = mcp
    auth = {"Authorization": f"Bearer {TOKENS['cos']}", "Content-Type": "application/json"}
    big = client.post("/mcp/cos", content=b'{"x":"' + b"a" * 300_000 + b'"}', headers=auth)
    assert big.status_code == 413
    batch = [{"jsonrpc": "2.0", "id": i, "method": "ping"} for i in range(21)]
    assert client.post("/mcp/cos", json=batch, headers=auth).status_code == 400
    ok = client.post("/mcp/cos", json=batch[:2], headers=auth)
    assert [r["id"] for r in ok.json()] == [0, 1]


# ---------------------------------------------------------------- review of task 7


def _post(client, seat, body, **kw):
    hdrs = {"Authorization": f"Bearer {TOKENS[seat]}", "Content-Type": "application/json"}
    return client.post(f"/mcp/{seat}", content=body, headers=hdrs, **kw)


def test_bad_params_and_odd_json_are_rpc_errors_not_500s(mcp):
    h, client = mcp
    for params in ([1], "x", 5):
        r = call(client, "cos", "tools/call", params)
        assert r.status_code == 200 and r.json()["error"]["code"] == -32602
    assert call(client, "cos", "initialize", "x").json()["error"]["code"] == -32602
    batch = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": [1]},
             {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
    replies = client.post("/mcp/cos", json=batch,
                          headers={"Authorization": f"Bearer {TOKENS['cos']}"}).json()
    assert [r.get("error", {}).get("code") for r in replies] == [-32602, None]
    assert _post(client, "cos", b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}').status_code == 400
    assert _post(client, "cos", b"[" * 100_000).json()["error"]["code"] == -32700
    old = _post(client, "cos", b'{"jsonrpc":"1.0","id":5,"method":"ping"}').json()
    assert old["id"] == 5 and old["error"]["code"] == -32600
    assert _post(client, "cos", b'{"jsonrpc":"2.0","id":9,"result":{}}').status_code == 202


def test_a_token_shared_between_seats_opens_neither(make_harness):
    from app.mcp import seat_problems

    shared = "s" * 40
    h = make_harness(tool_tokens={**TOKENS, "tax": shared, "qa": shared, "hr": "short"})
    problems = seat_problems(h.gw.settings.tool_tokens)
    assert problems == {"tax": "token shared with another seat",
                        "qa": "token shared with another seat",
                        "hr": "token shorter than 32 characters"}
    with TestClient(create_app(h.gw, start_background=False)) as client:
        for seat in ("tax", "qa"):
            r = client.post(f"/mcp/{seat}", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                            headers={"Authorization": f"Bearer {shared}"})
            assert r.status_code == 401
        assert call(client, "cos", "ping").status_code == 200


def test_a_seat_with_calls_running_gets_429(mcp, monkeypatch):
    from app import mcp as mcp_mod

    h, client = mcp
    monkeypatch.setitem(mcp_mod._in_flight, "intake", mcp_mod.SEAT_CONCURRENCY)
    r = call(client, "intake", "ping")
    assert r.status_code == 429 and r.headers["retry-after"] == "2"
    assert call(client, "tax", "ping").status_code == 200  # other seats unaffected
    monkeypatch.setitem(mcp_mod._in_flight, "intake", 0)
    assert call(client, "intake", "ping").status_code == 200


def test_control_actions_are_not_tools_and_actor_cant_be_forged(mcp):
    h, client = mcp
    for name in ("approve", "inject_email", "set_trust", "voice", "candidate_decision"):
        r = call(client, "cos", "tools/call", {"name": name, "arguments": {}})
        assert r.json()["error"]["code"] == -32602
    case_id = h.new_case(body="We're Zebra Robotics, a Delaware corporation. 10,000,000 "
                              "authorized shares.", sender="ceo@zebrarobotics.ai")
    seen = tool(client, "cos", "get_case", case_id=case_id, _actor="intake")["data"]
    assert "Zebra" not in seen["messages"][0]  # cos still gets the masked copy


def test_schemas_use_plain_types():
    from app.mcp import TOOLS

    def walk(node):
        if isinstance(node, dict):
            assert not isinstance(node.get("type"), list), node
            assert "additionalProperties" not in node, node
            for v in node.values():
                walk(v)

    walk(TOOLS)
