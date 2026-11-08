"""Task 28 (contract v1.12 §5a, design D32): tool.called and the public sign-up."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from .test_mcp import TOKENS, call, mcp, tool  # noqa: F401  (the mcp fixture)

FORM = {"name": "Jane Roe", "company": "Acme Rockets Inc", "email": "jane@acmerockets.dev"}


def wait_for(h, type_, n=1, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        found = h.events(type_)
        if len(found) >= n:
            return found
        time.sleep(0.02)
    return h.events(type_)


# ---------------------------------------------------------------- tool.called


def test_every_mcp_tool_call_writes_tool_called_without_data(mcp):  # noqa: F811
    h, client = mcp
    h.new_case()
    tool(client, "cos", "list_open_cases")
    r = call(client, "tax", "tools/call", {"name": "send_memo", "arguments": {"case_id": "x"}})
    assert r.status_code == 200
    called = wait_for(h, "tool.called", 2)
    assert [(e.actor, e.payload) for e in called] == [
        ("cos", {"tool": "list_open_cases", "status": "done"}),
        ("tax", {"tool": "send_memo", "status": "refused"})]  # not tax's tool


def test_tool_called_carries_a_valid_order_id_only(mcp):  # noqa: F811
    h, client = mcp
    h.ok("voice", text="what's open?", speaker="ceo", channel="typed")
    oid = f"order_{h.events('voice.command')[-1].id}"
    tool(client, "cos", "list_open_cases", order_id=oid)
    tool(client, "cos", "list_open_cases", order_id="order_99999")
    called = wait_for(h, "tool.called", 2)
    assert called[0].payload.get("order_id") == oid and "order_id" not in called[1].payload


def test_a_failed_tool_called_write_never_fails_the_tool(mcp, monkeypatch):  # noqa: F811
    h, client = mcp

    real = h.gw._emit

    def broken(events):
        if any(e.type == "tool.called" for e in events):
            raise RuntimeError("store hiccup")
        return real(events)

    monkeypatch.setattr(h.gw, "_emit", broken)
    out = tool(client, "cos", "list_open_cases")
    assert out["status"] == "done"
    time.sleep(0.2)  # the background write ran and failed quietly
    assert not h.events("tool.called")


# ---------------------------------------------------------------- the sign-up


@pytest.fixture
def site(make_harness):
    h = make_harness(inbox_clients="hello@u-tax.test")
    with TestClient(create_app(h.gw, start_background=False)) as client:
        yield h, client


def test_a_signup_opens_a_case_wakes_the_crew_and_sends_the_welcome(site):
    h, client = site
    h.ok("set_trust", action_type="email", state="AUTO")
    woke = []
    h.gw.on_wake(woke.append)
    r = client.post("/api/signup", json=FORM)
    assert r.status_code == 200 and r.json() == {"case_id": "case_1", "masked_name": "Team #1"}
    [received] = h.events("email.received")
    assert received.payload["source"] == "signup" and received.payload["masked_name"] == "Team #1"
    blob = json.dumps(received.payload)
    for leak in ("Jane", "Roe", "Acme", "jane@acmerockets.dev"):
        assert leak not in blob, leak
    assert woke == ["New case case_1"]
    assert h.gw.state.cases["case_1"].status == "INTAKE_PENDING"
    [mail] = [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()]
    assert mail["to"] == "jane@acmerockets.dev" and "authorized" in mail["body"]
    assert h.events("client.followed_up")[-1].payload == {"kind": "signup_welcome"}


def test_the_clients_reply_threads_onto_the_signup_case(site):
    h, client = site
    client.post("/api/signup", json=FORM)
    h.ok("inject_email", **{"from": FORM["email"], "subject": "Re: Your free check",
                            "body": "Delaware C-corp, 10,000,000 authorized, 8,000,000 issued."})
    assert {e.case_id for e in h.events("email.received")} == {"case_1"}


def test_on_ask_the_welcome_waits_for_the_ceo_then_goes(site):
    h, client = site
    client.post("/api/signup", json=FORM)
    assert h.outbox() == []
    [approval] = [a for a in h.gw.state.approvals.values() if "welcome" in a.summary]
    h.ok("approve", approval_id=approval.approval_id, decision="allow")
    assert h.gw.retry_signup_welcomes() == ["case_1"] and len(h.outbox()) == 1
    assert h.gw.retry_signup_welcomes() == []  # once


@pytest.mark.parametrize("body", [{**FORM, "email": "not-an-email"}, {**FORM, "name": ""},
                                  {**FORM, "company": "  "}, {"email": FORM["email"]}])
def test_bad_input_is_400(site, body):
    h, client = site
    r = client.post("/api/signup", json=body)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_input"


def test_three_signups_per_email_a_day_and_a_retry_is_the_same(site):
    h, client = site
    first = client.post("/api/signup", json={**FORM, "request_id": "r1"}).json()
    assert client.post("/api/signup", json={**FORM, "request_id": "r1"}).json() == first
    for _ in range(2):
        assert client.post("/api/signup", json=FORM).status_code == 200
    r = client.post("/api/signup", json=FORM)
    assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"


def test_the_public_case_cap_covers_signups(make_harness):
    h = make_harness(public_cases_per_day=1)
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/signup", json=FORM).status_code == 200
        r = client.post("/api/signup", json={**FORM, "email": "b@other.dev"})
    assert r.status_code == 429 and r.json()["error"]["code"] == "at_capacity"


def test_a_signup_never_joins_another_clients_case(site):
    h, client = site
    h.new_case(sender=FORM["email"])  # this address already owns case_1
    r = client.post("/api/signup", json=FORM).json()
    assert r["case_id"] == "case_2"
    assert h.gw.state.cases["case_1"].status != "CLOSED"


def test_the_site_may_post_a_signup_from_the_theater_origin(make_harness, monkeypatch):
    monkeypatch.setenv("THEATER_ORIGIN", "https://u-tax.vercel.app")
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        r = client.options("/api/signup", headers={
            "Origin": "https://u-tax.vercel.app", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://u-tax.vercel.app"
