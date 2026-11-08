"""Task 30 (contract v1.13 §5a-bis, design D33): CEO password sign-in; the voice loop fix."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from app import devtools
from app.auth import hash_password
from app.main import create_app

CEO = "ceo@u-tax.test"
PASSWORD = "a long enough password"
ADMIN = "admin-" + "a" * 30
THEATER = "theater-" + "b" * 30


@pytest.fixture
def site(make_harness):
    h = make_harness(ceo_email=CEO, ceo_password_hash=hash_password(PASSWORD),
                     admin_token=ADMIN, theater_token=THEATER, session_secret="s" * 48)
    with TestClient(create_app(h.gw, start_background=False)) as client:
        yield h, client


def login(client, email=CEO, password=PASSWORD):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------- sign-in


def test_the_ceo_signs_in_and_uses_every_admin_route(site):
    h, client = site
    r = login(client, email="CEO@U-Tax.test")  # any case
    assert r.status_code == 200 and set(r.json()) == {"session", "expires_at"}
    token = r.json()["session"]
    assert client.post("/api/ceo/command", headers=bearer(token),
                       json={"text": "trust"}).status_code == 200
    assert client.get("/api/state", headers=bearer(token)).json()["hiring"].get(
        "candidates") is not None  # admin view
    assert client.get("/api/state", params={"token": token}).status_code == 200  # ?token=
    assert client.post("/api/ceo/command", json={"text": "trust"}).status_code == 401


def test_a_wrong_email_or_password_gets_the_same_401(site):
    h, client = site
    bad_email = login(client, email="someone@else.dev")
    bad_password = login(client, password="not it")
    assert bad_email.status_code == bad_password.status_code == 401
    assert bad_email.json() == bad_password.json()
    assert bad_email.json()["error"]["code"] == "bad_login"
    assert login(client, password=None).status_code == 401


def test_10_tries_per_ip_per_15_minutes(site):
    h, client = site
    codes = [login(client, password="nope").status_code for _ in range(11)]
    assert codes == [401] * 10 + [429]


def test_logout_revokes_the_session(site):
    h, client = site
    token = login(client).json()["session"]
    assert client.post("/api/auth/logout", headers=bearer(token)).json() == {"ok": True}
    assert client.post("/api/ceo/command", headers=bearer(token),
                       json={"text": "trust"}).status_code == 401


def test_a_forged_or_tampered_session_is_refused(site):
    h, client = site
    token = login(client).json()["session"]
    payload, sig = token.split(".")
    for bad in (payload + "." + sig[:-2] + "AA", "x" + token, "not.a.token", ADMIN[:-1]):
        assert client.post("/api/ceo/command", headers=bearer(bad),
                           json={"text": "trust"}).status_code == 401, bad
    assert client.post("/api/ceo/command", headers=bearer(ADMIN),
                       json={"text": "trust"}).status_code == 200  # the admin token stays


def test_a_session_signed_with_another_key_is_refused(make_harness):
    from app.auth import Sessions

    h = make_harness(ceo_email=CEO, admin_token=ADMIN, session_secret="s" * 48)
    foreign, _ = Sessions("t" * 48).issue()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/ceo/command", headers=bearer(foreign),
                           json={"text": "trust"}).status_code == 401


def test_no_password_hash_means_no_sign_in(make_harness):
    h = make_harness(ceo_email=CEO, admin_token=ADMIN, session_secret="s" * 48)
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert login(client).status_code == 401


def test_a_session_expires_after_12_hours(site, monkeypatch):
    import app.auth as auth

    h, client = site
    token = login(client).json()["session"]
    real = auth.time.time
    monkeypatch.setattr(auth.time, "time", lambda: real() + 12 * 3600 + 1)
    assert client.post("/api/ceo/command", headers=bearer(token),
                       json={"text": "trust"}).status_code == 401


def test_set_ceo_password_writes_the_hash_and_never_echoes(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text("CEO_EMAIL=ceo@u-tax.test\n", encoding="utf-8")
    monkeypatch.setattr("sys.stdin", io.StringIO("my new secret password\n"))
    assert devtools.main(["set-ceo-password", "--env", str(env)]) == 0
    out = capsys.readouterr()
    assert "my new secret password" not in out.out + out.err
    stored = devtools.env_file_values(env)["CEO_PASSWORD_HASH"]
    from app.auth import verify_password

    assert stored.startswith("scrypt$") and verify_password("my new secret password", stored)
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    assert devtools.main(["set-ceo-password", "--env", str(env)]) == 1


def test_first_boot_generates_and_saves_the_session_secret(make_harness, monkeypatch, tmp_path):
    import app.main as main

    saved = {}
    monkeypatch.delenv("UTAX_SESSION_SECRET", raising=False)
    monkeypatch.setattr("app.devtools.set_env_values",
                        lambda path, values: saved.update(values))
    h = make_harness()
    main._ensure_session_secret(h.gw)
    assert len(saved["UTAX_SESSION_SECRET"]) >= 48
    import os

    assert os.environ["UTAX_SESSION_SECRET"] == saved["UTAX_SESSION_SECRET"]
    monkeypatch.delenv("UTAX_SESSION_SECRET", raising=False)


# ---------------------------------------------------------------- the voice loop


def test_while_a_proposal_is_open_only_confirm_or_cancel(h):
    first = h.gw.perform("ceo_command", {"text": "advertise our firm"}).data
    assert first["kind"] == "proposal"
    for said in ("hire 2 people", "trust", "what's pending"):
        assert h.gw.perform("ceo_command", {"text": said}).data == {
            "kind": "say", "say": "Say confirm or cancel."}, said
    assert len(h.events("ceo.proposed")) == 1  # no second order
    assert h.gw.perform("ceo_command", {"text": "yes"}).data["kind"] == "confirm"


@pytest.mark.parametrize("said", ["hey", "hi", "hello", "test", "testing", "okay",
                                  "hey can you hear me", "Hello? Are you there?",
                                  "is this working", "okay okay testing"])
def test_greetings_and_mic_checks_are_never_orders(h, said):
    r = h.gw.perform("ceo_command", {"text": said}).data
    assert r == {"kind": "say", "say": "I'm listening. Tell me an order."}
    assert not h.events("ceo.proposed")


# ---------------------------------------------------------------- review 1


def test_a_blank_admin_token_never_opens_the_console_once_a_password_is_set(make_harness):
    h = make_harness(ceo_email=CEO, ceo_password_hash=hash_password(PASSWORD),
                     session_secret="s" * 48)  # no ADMIN_TOKEN
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.post("/api/ceo/command", json={"text": "trust"}).status_code == 401
        token = login(client).json()["session"]
        assert client.post("/api/ceo/command", headers=bearer(token),
                           json={"text": "trust"}).status_code == 200


def test_another_email_from_the_same_ip_cant_lock_the_ceo_out(site):
    h, client = site
    for _ in range(12):
        login(client, email="attendee@venue.dev", password="guess")
    assert login(client).status_code == 200


def test_a_short_session_secret_fails_at_boot(make_harness, monkeypatch):
    import app.main as main

    monkeypatch.delenv("UTAX_SESSION_SECRET", raising=False)
    h = make_harness(session_secret="too-short")
    with pytest.raises(RuntimeError):
        main._ensure_session_secret(h.gw)
