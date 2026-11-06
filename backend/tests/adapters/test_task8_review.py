"""Regression tests for the two /review passes over the task 8 adapters (AgentMail mail and
webhook; Naïve social and the AgentMail calendar)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway.adapters.agentmail import AgentMailCalendar, AgentMailMail, verify_svix
from app.gateway.ports import NotConfigured, OutMail, Rejected, Slot, Unavailable
from app.gateway.registry import AdapterNotReady, build_port
from app.gateway.settings import Settings
from app.main import create_app

from .fakes import FakeAgentMail
from .test_agentmail import CLIENTS, HIRING, SECRET, adapter, received, sign


@pytest.fixture
def hooked(make_harness, tmp_path):
    fake = FakeAgentMail()
    h = make_harness(agentmail_webhook_secret=SECRET, inbox_clients=CLIENTS,
                     inbox_hiring=HIRING)
    h.gw.ports.mail = AgentMailMail("am_test", inboxes={"clients": CLIENTS, "hiring": HIRING},
                                    data_dir=tmp_path / "am", transport=fake.transport())
    with TestClient(create_app(h.gw, start_background=False)) as client:
        yield h, client, fake


def post(client, payload, **sign_kw):
    body = json.dumps(payload).encode()
    return client.post("/hooks/agentmail", content=body, headers=sign(body, **sign_kw))


# ---------------------------------------------------------------- mail and webhook


def test_a_reused_gateway_key_for_another_mail_is_a_new_send(tmp_path):
    # gateway keys restart with a fresh event log; AgentMail keeps keys for 24 h
    fake = FakeAgentMail()
    rehearsal, _ = adapter(tmp_path, fake)
    rehearsal.send(OutMail(to="test@team7.dev", subject="Your estimate", body="A"),
                   key="memo:case_1:v1r0")
    after_reset, _ = adapter(tmp_path, fake)  # same data dir: the local index survived
    real = after_reset.send(OutMail(to="real@client.dev", subject="Your estimate", body="B"),
                            key="memo:case_1:v1r0")
    assert real.message_id == "<am-2@agentmail.to>"  # sent: not swallowed, not a 409
    assert [m["to"] for m in fake.sent] == [["test@team7.dev"], ["real@client.dev"]]


def test_only_our_inboxes_are_accepted_or_fetched(hooked):
    h, client, fake = hooked
    other = post(client, received(inbox="someone-else@agentmail.to"))
    assert other.json()["ignored"] == "other_inbox" and h.types() == []
    sneaky = received(message_id="<victim@x>", inbox="..", text=None)
    assert post(client, sneaky).json()["ignored"] == "other_inbox"
    assert not any(r.method == "GET" for r in fake.requests)


@pytest.mark.parametrize("payload", [
    {"event_type": "message.received", "event_id": "e", "message": "a string"},
    received(headers=[{"name": "Auto-Submitted", "value": "auto-replied"}]),
    received(sender="undisclosed-recipients:;"),
    {"event_type": "message.received.unauthenticated", "event_id": "e2",
     "message": {"inbox_id": CLIENTS}},
])
def test_odd_payloads_are_acknowledged_never_500(hooked, payload):
    h, client, _ = hooked
    r = post(client, payload)
    assert r.status_code == 200, r.text
    assert "email.received" not in h.types()


def test_a_junk_signature_is_refused_without_a_crash():
    body = json.dumps(received()).encode()
    headers = {**sign(body), "svix-signature": "v1,ééé v1,"}
    assert verify_svix(SECRET, headers, body) is False


@pytest.mark.parametrize("secret", ["whsec_", "whsec_====", "whsec_!!!!",
                                    "whsec_" + base64.b64encode(b"short").decode()])
def test_weak_or_broken_secrets_never_verify_and_fail_boot(tmp_path, secret):
    body = b"{}"
    empty = base64.b64encode(hmac.new(b"", b"m.1." + body, hashlib.sha256).digest()).decode()
    headers = {"svix-id": "m", "svix-timestamp": "1", "svix-signature": "v1," + empty}
    assert not verify_svix(secret, headers, body, now=1)
    settings = replace(Settings(), data_dir=tmp_path, adapters={"mail": "agentmail"},
                       agentmail_api_key="am_x", inbox_clients=CLIENTS,
                       agentmail_webhook_secret=secret)
    with pytest.raises(AdapterNotReady):
        build_port("mail", settings)


def test_a_gone_body_opens_the_case_instead_of_retrying_for_a_day(hooked):
    h, client, _ = hooked  # the fake answers 404 for a message it doesn't store
    r = post(client, received(message_id="<gone@team7.dev>", text=None, body_url="x"))
    assert r.status_code == 200 and r.json()["data"]["case_id"] == "case_1"


def test_long_message_ids_subjects_and_errors_are_bounded(tmp_path):
    port, fake = adapter(tmp_path)
    mail = port.parse_inbound(received(message_id="<" + "x" * 5000 + "@a.b>"))
    assert len(mail.message_id) <= 200
    port.send(OutMail(to="a@b.co", subject="Re: hi\r\nBcc: evil@x.io " + "y" * 500,
                      body="b"), key="k")
    subject = fake.sent[-1]["subject"]
    assert "\r" not in subject and "\n" not in subject and len(subject) <= 200
    with pytest.raises(Rejected) as info:
        port.send(OutMail(to="nobody", subject="s", body="b"), key="k2")
    assert "nobody" not in str(info.value)
    echo = httpx.MockTransport(lambda r: httpx.Response(
        400, json={"name": "ValidationError", "message": "bad to: jane@client.dev"}))
    leaky = AgentMailMail("am_test", inboxes={"clients": CLIENTS}, data_dir=tmp_path / "l",
                          transport=echo)
    with pytest.raises(Rejected) as info:
        leaky.send(OutMail(to="jane@client.dev", subject="s", body="b"), key="k")
    assert "jane@client.dev" not in str(info.value) and "[email]" in str(info.value)


def test_plain_http_base_urls_fail_boot(tmp_path):
    settings = replace(Settings(), data_dir=tmp_path, adapters={"mail": "agentmail"},
                       agentmail_api_key="am_x", inbox_clients=CLIENTS,
                       agentmail_base_url="http://api.agentmail.to")
    with pytest.raises(AdapterNotReady, match="https"):
        build_port("mail", settings)


# ---------------------------------------------------------------- calendar and RSVPs


@pytest.mark.parametrize("subject", ["Accepted: U-Tax: your franchise tax review call",
                                     "Declined: U-Tax review", "Tentative: U-Tax review",
                                     "Invitation: U-Tax review"])
def test_calendar_rsvps_never_reopen_a_booked_case(h, subject):
    # "has accepted ... Nov 10, 2026 5pm" carries digits and used to wake Intake
    case_id = h.to_sent()
    out = h.ok("inject_email", **{"from": "founder@team7.dev", "subject": subject,
                                  "body": "Jane has accepted this invitation. Nov 10, 2026 5pm"})
    assert out["dropped"] is True and h.gw.state.cases[case_id].status == "SENT"


def test_calendar_error_codes_come_from_the_body(tmp_path):
    cases = [(409, {"code": "race_condition", "message": "modified concurrently"}, Unavailable),
             (403, {"message": "Calendar isn't available in this region yet"}, NotConfigured)]
    for status, answer, error in cases:
        transport = httpx.MockTransport(lambda r, s=status, a=answer: httpx.Response(s, json=a))
        port = AgentMailCalendar("am_test", inbox=CLIENTS, data_dir=tmp_path / str(status),
                                 transport=transport)
        with pytest.raises(error):
            port.book(Slot(start="2026-11-08T17:30:00Z"), "a@b.co", "t", key="k")


@pytest.mark.parametrize("answer", [{"event_id": "flat-1"}, {}, None, [1]])
def test_any_2xx_booking_is_a_booking(tmp_path, answer):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200) if answer is None else httpx.Response(201, json=answer)

    port = AgentMailCalendar("am_test", inbox=CLIENTS, data_dir=tmp_path,
                             transport=httpx.MockTransport(handle),
                             location="https://meet.u-tax.test/review")
    booking = port.book(Slot(start="2026-11-08T17:30:00Z"), "a@b.co", "t", key="k")
    assert booking.event_id
    assert port.book(Slot(start="2026-11-08T17:30:00Z"), "a@b.co", "t", key="k") == booking
    assert len(calls) == 1


def test_the_invitation_shows_no_pseudonym_and_carries_the_location(h, tmp_path):
    fake = FakeAgentMail()
    h.gw.ports.calendar = AgentMailCalendar("am_test", inbox=CLIENTS, data_dir=tmp_path / "c",
                                            transport=fake.transport(),
                                            location="https://meet.u-tax.test/review")
    case_id = h.to_sent()
    slot = h.ok("get_case", case_id=case_id)["offered_slots"][0]
    h.approve_pending(h.do("book_call", case_id=case_id, start=slot))
    h.ok("book_call", case_id=case_id, start=slot)
    sent = fake.events[0]
    assert "Team #" not in sent["title"] and sent["location"] == "https://meet.u-tax.test/review"
    assert "15-minute" in sent["description"]
