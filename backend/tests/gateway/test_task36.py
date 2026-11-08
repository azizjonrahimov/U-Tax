"""Task 36: a 60 s single-use stream ticket replaces the 12 h session in ?token= on
/api/events/stream (a URL lands in access and tunnel logs)."""

from __future__ import annotations

import pytest

import app.api as api_module

from .test_task30 import THEATER, bearer, login, site  # noqa: F401  (the site fixture)


@pytest.fixture
def opened(monkeypatch):
    """The stream's generator, replaced by one frame (a real stream never ends)."""
    calls: list[dict] = []

    async def one_frame(gw, start, *, is_disconnected, heartbeat, view):
        calls.append({"start": start, "view": view})
        yield ": ok\n\n"

    monkeypatch.setattr(api_module, "sse_events", one_frame)
    return calls


def ticket(client, token):
    r = client.post("/api/stream-ticket", headers=bearer(token))
    assert r.status_code == 200, r.text
    assert set(r.json()) == {"ticket", "expires_at"}
    return r.json()["ticket"]


def test_a_ticket_opens_the_stream_once(site, opened):
    h, client = site
    t = ticket(client, login(client).json()["session"])
    r = client.get("/api/events/stream", params={"ticket": t})
    assert r.status_code == 200 and r.text == ": ok\n\n" and len(opened) == 1
    again = client.get("/api/events/stream", params={"ticket": t})
    assert again.status_code == 401 and len(opened) == 1  # replayed from a log: nothing


def test_a_ticket_expires_after_60_seconds(site, opened, monkeypatch):
    h, client = site
    t = ticket(client, login(client).json()["session"])
    import app.auth as auth

    real = auth.time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + 62)
    assert client.get("/api/events/stream", params={"ticket": t}).status_code == 401


def test_the_session_never_opens_the_stream_from_the_url(site, opened):
    h, client = site
    session = login(client).json()["session"]
    assert client.get("/api/events/stream", params={"token": session}).status_code == 401
    assert client.get("/api/events/stream", params={"token": THEATER}).status_code == 200
    assert client.get("/api/events/stream", headers=bearer(session)).status_code == 200


def test_a_ticket_is_never_a_session_and_needs_the_ceo(site, opened):
    h, client = site
    assert client.post("/api/stream-ticket").status_code == 401
    t = ticket(client, login(client).json()["session"])
    assert client.post("/api/ceo/command", headers=bearer(t),
                       json={"text": "trust"}).status_code == 401
    assert client.get("/api/state", params={"token": t}).status_code == 401
    assert client.post("/api/stream-ticket", headers=bearer(t)).status_code == 401
    session = login(client).json()["session"]
    assert client.get("/api/events/stream", params={"ticket": session}).status_code == 401
