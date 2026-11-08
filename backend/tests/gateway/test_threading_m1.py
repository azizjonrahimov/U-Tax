"""D42 M1: every client mail goes out in the client's thread (In-Reply-To + References), with
"Re: <the client's first subject>"."""

from __future__ import annotations

import json

import httpx

from app.gateway.adapters.agentmail import AgentMailMail
from app.gateway.ports import OutMail

from ..adapters.fakes import FakeAgentMail
from .conftest import HAPPY_BODY, HAPPY_QUOTES

PARTIAL = "We're a Delaware C-corp. We have 10,000,000 authorized shares."


def outbox(h) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in h.outbox()]


def mail(h, body, message_id, subject="free franchise tax checkup?",
         sender="founder@team7.dev"):
    return h.ok("inject_email", **{"from": sender, "subject": subject,
                                   "body": body, "message_id": message_id})


def test_ask_reply_memo_all_carry_the_thread(h):
    case_id = mail(h, PARTIAL, "<first@mail.gmail.com>")["case_id"]
    case = h.gw.state.cases[case_id]
    assert case.last_inbound_message_id == "<first@mail.gmail.com>"
    assert case.first_subject == "free franchise tax checkup?"
    asked = h.do("ask_client", case_id=case_id, missing=["issued_shares", "gross_assets"])
    if asked.status == "needs_approval":
        h.approve_pending(asked)
        h.ok("ask_client", case_id=case_id, missing=["issued_shares", "gross_assets"])
    [ask] = outbox(h)
    assert ask["in_reply_to"] == "<first@mail.gmail.com>"
    assert ask["subject"] == "Re: free franchise tax checkup?"
    # the client answers in the thread: the newest inbound id is the one we reply to
    mail(h, HAPPY_BODY, "<second@mail.gmail.com>", subject="Re: free franchise tax checkup?")
    assert case.last_inbound_message_id == "<second@mail.gmail.com>"
    assert case.first_subject == "free franchise tax checkup?"  # never the reply's subject
    h.to_sent(case_id)
    memo = outbox(h)[-1]
    assert memo["in_reply_to"] == "<second@mail.gmail.com>"


def test_not_eligible_and_follow_ups_are_threaded(h):
    case_id = mail(h, PARTIAL, "<n@mail.gmail.com>", subject="Re: hello")["case_id"]
    out = h.do("mark_not_eligible", case_id=case_id, reason="outside_delaware")
    if out.status == "needs_approval":
        h.approve_pending(out)
        h.ok("mark_not_eligible", case_id=case_id, reason="outside_delaware")
    sent = outbox(h)[-1]
    assert sent["in_reply_to"] == "<n@mail.gmail.com>"
    assert sent["subject"] == "Re: hello"  # never "Re: Re:"
    other = mail(h, PARTIAL, "<f@mail.gmail.com>", subject="checkup",
                 sender="cfo@other.dev")["case_id"]
    h.ok("extract_facts", case_id=other, quotes={"authorized_shares": HAPPY_QUOTES[
        "authorized_shares"]}, incorporated_delaware=True, foreign_owner_25=False)
    out = h.do("follow_up", case_id=other, kind="missing_facts")
    if out.status == "needs_approval":
        h.approve_pending(out)
        h.ok("follow_up", case_id=other, kind="missing_facts")
    assert outbox(h)[-1]["in_reply_to"] == "<f@mail.gmail.com>"


def test_agentmail_sends_in_reply_to_and_references(tmp_path):
    fake = FakeAgentMail()
    port = AgentMailMail("am_test", inboxes={"clients": "checkup@u-tax.test"},
                         data_dir=tmp_path, transport=fake.transport())
    port.send(OutMail(to="founder@team7.dev", subject="Re: hi", body="b",
                      in_reply_to="first@mail.gmail.com"), key="ask:case_1:1")
    body = json.loads(fake.requests[-1].content)
    assert body["headers"] == {"In-Reply-To": "<first@mail.gmail.com>",
                               "References": "<first@mail.gmail.com>"}
    port.send(OutMail(to="founder@team7.dev", subject="Re: hi", body="c"), key="k2")
    assert "headers" not in json.loads(fake.requests[-1].content)  # nothing to thread on


def test_agentmail_refusing_the_headers_still_sends_the_mail(tmp_path):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "headers" in body:
            return httpx.Response(400, json={"name": "ValidationError", "message": "headers"})
        return httpx.Response(200, json={"message_id": "<am-1@agentmail.to>"})

    port = AgentMailMail("am_test", inboxes={"clients": "checkup@u-tax.test"},
                         data_dir=tmp_path, transport=httpx.MockTransport(handler))
    sent = port.send(OutMail(to="founder@team7.dev", subject="Re: hi", body="b",
                             in_reply_to="<x@y>"), key="ask:case_1:1")
    assert sent.message_id == "<am-1@agentmail.to>"
    assert "headers" in seen[0] and "headers" not in seen[1]
