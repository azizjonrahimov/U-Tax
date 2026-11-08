"""D42 M4: GET /api/admin/mail-health, the console's "Email · live" chip."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway.adapters.agentmail import AgentMailMail
from app.gateway.ports import OutMail, PortError
from app.main import create_app

ADMIN = "admin-" + "a" * 30
INBOX = "checkup@u-tax.test"


class Mailbox:
    """GET /v0/inboxes/{inbox} (the live check) and POST .../messages/send."""

    def __init__(self) -> None:
        self.up, self.send_ok, self.checks = True, True, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            self.checks += 1
            return httpx.Response(200 if self.up else 503, json={"inbox_id": INBOX})
        if not self.send_ok:
            return httpx.Response(503, json={"message": "down"})
        return httpx.Response(200, json={"message_id": "<am-1@agentmail.to>"})


@pytest.fixture
def m(make_harness):
    h = make_harness(admin_token=ADMIN)
    h.box = Mailbox()
    h.ports.mail = AgentMailMail("am_test", inboxes={"clients": INBOX},
                                 data_dir=h.tmp / "am", transport=httpx.MockTransport(h.box))
    return h


def test_the_route_is_admin_only_and_local_mail_is_healthy(make_harness):
    h = make_harness(admin_token=ADMIN)
    h.new_case()
    with TestClient(create_app(h.gw, start_background=False)) as c:
        assert c.get("/api/admin/mail-health").status_code == 401
        out = c.get("/api/admin/mail-health", headers={"Authorization": f"Bearer {ADMIN}"})
    body = out.json()
    assert out.status_code == 200 and body["ok"] is True and body["provider"] == "local"
    assert body["last_inbound_at"] == h.events("email.received")[-1].ts
    assert set(body) == {"last_poll_at", "last_inbound_at", "last_outbound_at", "provider",
                         "ok"}


def test_agentmail_live_check_cached_a_minute(m, monkeypatch):
    import app.gateway.perform as perform

    clock = [1000.0]
    monkeypatch.setattr(perform.time, "monotonic", lambda: clock[0])
    first = m.gw.mail_health()
    assert first["ok"] is True and first["provider"] == "agentmail" and m.box.checks == 1
    clock[0] += 30
    m.gw.mail_health()
    assert m.box.checks == 1  # cached
    clock[0] += 31
    m.gw.mail_health()
    assert m.box.checks == 2


def test_a_failed_send_or_a_dead_provider_is_not_ok(m, monkeypatch):
    import app.gateway.perform as perform

    clock = [1000.0]
    monkeypatch.setattr(perform.time, "monotonic", lambda: clock[0])
    assert m.gw.mail_health()["ok"] is True
    m.box.send_ok = False
    with pytest.raises(PortError):
        m.ports.mail.send(OutMail(to="a@b.co", subject="s", body="b"), key="k1")
    assert m.gw.mail_health()["ok"] is False  # the last send failed
    m.box.send_ok = True
    m.ports.mail.send(OutMail(to="a@b.co", subject="s", body="c"), key="k2")
    assert m.gw.mail_health()["ok"] is True
    m.box.up = False
    clock[0] += 61
    m.h = m.gw.mail_health()  # the check fails: last_poll_at stays the last good one
    assert m.h["ok"] is True  # still within 2 minutes of the last good check
    m.clock.advance(minutes=3)
    clock[0] += 61
    assert m.gw.mail_health()["ok"] is False
