"""AgentMail adapter (task 8): send, the Svix-signed webhook, both inboxes. Built from
docs.agentmail.to; the live smoke test at the end runs only with real keys."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway.adapters import _http
from app.gateway.adapters.agentmail import AgentMailMail, verify_svix
from app.gateway.ports import NotConfigured, OutMail, RateLimited, Rejected, Unavailable
from app.main import create_app

from ..gateway.conftest import HAPPY_BODY
from .fakes import FakeAgentMail

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
CLIENTS, HIRING = "checkup@u-tax.test", "jobs@u-tax.test"


def sign(body: bytes, *, secret: str = SECRET, msg_id: str = "msg_1", at: int | None = None):
    stamp = str(at if at is not None else int(time.time()))
    key = base64.b64decode(secret.removeprefix("whsec_"))
    sig = base64.b64encode(hmac.new(key, f"{msg_id}.{stamp}.".encode() + body,
                                    hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": stamp, "svix-signature": f"v1,{sig}",
            "content-type": "application/json"}


def received(message_id="<m1@team7.dev>", sender="Founder <founder@team7.dev>",
             inbox=CLIENTS, text=HAPPY_BODY, **extra):
    message = {"inbox_id": inbox, "thread_id": "t1", "message_id": message_id,
               "from": sender, "to": [inbox], "subject": "free franchise tax checkup?",
               "labels": ["received"], "timestamp": "2026-11-07T21:00:00Z", **extra}
    if text is not None:
        message["text"] = text
    return {"type": "event", "event_type": "message.received", "event_id": f"ev-{message_id}",
            "message": message, "thread": {"thread_id": "t1", "message_count": 1}}


def adapter(tmp_path, fake=None, **kw):
    fake = fake or FakeAgentMail()
    return AgentMailMail("am_test", inboxes={"clients": CLIENTS, "hiring": HIRING},
                         data_dir=tmp_path, transport=fake.transport(), **kw), fake


# ---------------------------------------------------------------- signature


def test_svix_signature_accepts_only_a_fresh_exact_body():
    body = json.dumps(received()).encode()
    headers = sign(body)
    assert verify_svix(SECRET, headers, body)
    assert not verify_svix(SECRET, headers, body + b" ")  # tampered
    assert not verify_svix(SECRET, {**headers, "svix-signature": "v1,AAAA"}, body)
    old = sign(body, at=int(time.time()) - 3600)
    assert not verify_svix(SECRET, old, body)  # a replay an hour later
    other = "whsec_" + base64.b64encode(b"x" * 32).decode()
    assert not verify_svix(other, headers, body)
    assert not verify_svix("", headers, body)
    rotated = {**headers, "svix-signature": "v1,AAAA " + headers["svix-signature"]}
    assert verify_svix(SECRET, rotated, body)  # any listed signature may match


# ---------------------------------------------------------------- send


def test_send_uses_the_documented_request_and_a_safe_idempotency_key(tmp_path):
    port, fake = adapter(tmp_path)
    sent = port.send(OutMail(to="founder@team7.dev", subject="Your estimate", body="Hi"),
                     key="memo:case_1:v1r0")
    request = fake.requests[-1]
    assert request.url.path == "/v0/inboxes/checkup@u-tax.test/messages/send"
    assert request.headers["authorization"] == "Bearer am_test"
    key = request.headers["idempotency-key"]
    assert key == _http.safe_key("memo:case_1:v1r0") and len(key) <= 256
    assert all(c.isalnum() or c in "-._~" for c in key) and "case" not in key
    assert json.loads(request.content) == {"to": ["founder@team7.dev"],
                                           "subject": "Your estimate", "text": "Hi"}
    assert sent.message_id == "<am-1@agentmail.to>"


def test_hr_mail_goes_out_from_the_hiring_inbox(tmp_path):
    port, fake = adapter(tmp_path)
    port.send(OutMail(to="ann@x.io", subject="q", body="b", inbox="hiring"), key="q1")
    assert fake.sent[-1]["inbox"] == HIRING


def test_a_retry_after_a_lost_answer_sends_no_second_mail(tmp_path):
    # the first call reached AgentMail, the answer was lost: the same Idempotency-Key replays
    fake = FakeAgentMail()
    port, _ = adapter(tmp_path, fake)
    first = port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k")
    restarted, _ = adapter(tmp_path / "fresh", fake)  # no local index
    assert restarted.send(OutMail(to="a@b.co", subject="s", body="b"), key="k") == first
    assert len(fake.sent) == 1


@pytest.mark.parametrize("status,error", [(429, RateLimited), (503, Unavailable),
                                          (401, NotConfigured), (403, Rejected),
                                          (400, Rejected), (409, Rejected)])
def test_answers_map_onto_port_errors(tmp_path, status, error):
    transport = httpx.MockTransport(lambda r: httpx.Response(
        status, headers={"Retry-After": "7"}, json={"name": "E", "message": "m"}))
    port = AgentMailMail("am_test", inboxes={"clients": CLIENTS}, data_dir=tmp_path,
                         transport=transport)
    with pytest.raises(error) as info:
        port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k")
    if status == 429:
        assert info.value.retry_after == 7


def test_timeouts_are_retryable_and_claim_no_key(tmp_path):
    calls = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"message_id": "m-2", "thread_id": "t"})

    port = AgentMailMail("am_test", inboxes={"clients": CLIENTS}, data_dir=tmp_path,
                         transport=httpx.MockTransport(flaky))
    with pytest.raises(Unavailable):
        port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k")
    assert port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k").message_id == "m-2"
    assert calls[0].headers["idempotency-key"] == calls[1].headers["idempotency-key"]


def test_no_key_or_no_inbox_fails_clearly(tmp_path):
    with pytest.raises(NotConfigured):
        AgentMailMail("", inboxes={"clients": CLIENTS}, data_dir=tmp_path)
    port = AgentMailMail("am_test", inboxes={}, data_dir=tmp_path,
                         transport=FakeAgentMail().transport())
    with pytest.raises(NotConfigured):
        port.send(OutMail(to="a@b.co", subject="s", body="b"), key="k")


def test_retry_after_reads_seconds_and_dates():
    assert _http.retry_after(httpx.Response(429, headers={"Retry-After": "3"})) == 3
    assert _http.retry_after(httpx.Response(429, headers={"Retry-After": "9999"})) == 60
    assert _http.retry_after(httpx.Response(429, headers={"Retry-After": "soon"})) is None
    past = "Wed, 21 Oct 2015 07:28:00 GMT"
    assert _http.retry_after(httpx.Response(429, headers={"Retry-After": past})) == 0


# ---------------------------------------------------------------- inbound


def test_received_payload_becomes_an_inmail(tmp_path):
    port, _ = adapter(tmp_path)
    mail = port.parse_inbound(received(headers={"Auto-Submitted": "no"}))
    assert (mail.sender, mail.message_id, mail.auto_reply) == ("founder@team7.dev",
                                                               "<m1@team7.dev>", False)
    assert "10,000,000 authorized shares" in mail.body
    auto = port.parse_inbound(received(headers={"Auto-Submitted": "auto-replied"}))
    assert auto.auto_reply is True
    html = port.parse_inbound(received(text=None, html="<p>We're a <b>Delaware</b> corp</p>"))
    assert html.body == "We're a Delaware corp"
    assert port.parse_inbound({"event_type": "message.delivered", "delivery": {}}) is None
    assert port.inbox_of(received(inbox=HIRING.upper())) == "hiring"
    assert port.inbox_of(received()) == "clients"


def test_a_body_over_64_kib_is_fetched_from_the_api(tmp_path):
    fake = FakeAgentMail()
    fake.stored["<big@team7.dev>"] = {"message_id": "<big@team7.dev>", "text": HAPPY_BODY}
    port, _ = adapter(tmp_path, fake)
    payload = received(message_id="<big@team7.dev>", text=None,
                       body_url="https://s3.example/presigned", body_expires_at="later")
    mail = port.parse_inbound(payload)
    assert "8,000,000 issued" in mail.body
    # the message id is one path segment, encoded
    assert fake.requests[-1].url.raw_path.decode().endswith("/messages/%3Cbig%40team7.dev%3E")


def test_eml_files_still_parse(tmp_path):
    from app.gateway.settings import REPO_ROOT

    port, _ = adapter(tmp_path)
    eml = sorted((REPO_ROOT / "fixtures" / "inbox").glob("*.eml"))[0]
    assert "@" in port.parse_inbound(eml.read_bytes()).sender


# ---------------------------------------------------------------- the webhook route


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


def test_a_signed_message_opens_a_case_once(hooked):
    h, client, _ = hooked
    first = post(client, received())
    assert first.status_code == 200 and first.json()["data"]["case_id"] == "case_1"
    again = post(client, received(), msg_id="msg_retry")  # at-least-once redelivery
    assert again.json()["data"]["duplicate"] is True
    assert len(h.events("email.received")) == 1
    assert h.events("email.received")[0].provider == "agentmail"


def test_the_hiring_inbox_opens_an_applicant(hooked):
    h, client, _ = hooked
    r = post(client, received(message_id="<a1@x.io>", sender="Ann <ann@x.io>", inbox=HIRING,
                              text="I'm an EA with 5 years of tax experience."))
    assert r.json()["data"]["candidate_id"] == "cand_1"
    assert "email.received" not in h.types()


def test_unsigned_or_stale_hooks_are_refused(hooked):
    h, client, _ = hooked
    body = json.dumps(received()).encode()
    assert client.post("/hooks/agentmail", content=body).status_code == 401
    assert post(client, received(), at=int(time.time()) - 3600).status_code == 401
    assert h.types() == []


def test_other_events_are_acknowledged_and_ignored(hooked):
    h, client, _ = hooked
    r = post(client, {"type": "event", "event_type": "message.bounced", "event_id": "e",
                      "bounce": {}})
    assert r.status_code == 200 and r.json()["ignored"] == "message.bounced"
    assert h.types() == []


def test_the_hook_is_closed_without_a_secret_or_token(make_harness):
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        body = json.dumps(received()).encode()
        assert client.post("/hooks/agentmail", content=body).status_code == 503
    t = make_harness(hook_token="t" * 40)
    with TestClient(create_app(t.gw, start_background=False)) as client:
        local = client.post("/hooks/agentmail", content=body, params={"token": "t" * 40})
        assert local.status_code == 503  # ADAPTER_MAIL=local: retry later, never drop
    t.gw.ports.mail = AgentMailMail("am_test", inboxes={"clients": CLIENTS},
                                    data_dir=t.tmp / "am", transport=FakeAgentMail().transport())
    with TestClient(create_app(t.gw, start_background=False)) as client:
        assert client.post("/hooks/agentmail", content=body,
                           params={"token": "wrong"}).status_code == 401
        ok = client.post("/hooks/agentmail", content=body, params={"token": "t" * 40})
        assert ok.status_code == 200 and ok.json()["data"]["case_id"] == "case_1"


def test_registry_builds_agentmail_and_refuses_it_without_a_key(tmp_path):
    from dataclasses import replace

    from app.gateway.registry import AdapterNotReady, build_port
    from app.gateway.settings import Settings

    base = replace(Settings(), data_dir=tmp_path, adapters={"mail": "agentmail"})
    with pytest.raises(AdapterNotReady, match="AGENTMAIL_API_KEY"):
        build_port("mail", base)
    with pytest.raises(AdapterNotReady, match="MAIL_INBOX_CLIENTS"):
        build_port("mail", replace(base, agentmail_api_key="am_x"))
    port = build_port("mail", replace(base, agentmail_api_key="am_x", inbox_clients=CLIENTS))
    assert port.provider == "agentmail"


# ---------------------------------------------------------------- live (keys only)


@pytest.mark.skipif(not (os.environ.get("AGENTMAIL_API_KEY")
                         and os.environ.get("MAIL_INBOX_CLIENTS")
                         and os.environ.get("AGENTMAIL_TEST_TO")),
                    reason="needs AGENTMAIL_API_KEY, MAIL_INBOX_CLIENTS and AGENTMAIL_TEST_TO")
def test_live_send_once(tmp_path):
    port = AgentMailMail(os.environ["AGENTMAIL_API_KEY"],
                         inboxes={"clients": os.environ["MAIL_INBOX_CLIENTS"]},
                         data_dir=tmp_path)
    mail = OutMail(to=os.environ["AGENTMAIL_TEST_TO"], subject="U-Tax adapter smoke test",
                   body="One test mail from the U-Tax gateway's AgentMail adapter.")
    key = f"smoke-{int(time.time())}"
    first = port.send(mail, key)
    assert first.message_id and port.send(mail, key) == first
