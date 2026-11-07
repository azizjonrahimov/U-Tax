"""Task 21 (contract v1.10 / v1.10.1 / v1.10.2 §5c, design D30): speech on ElevenLabs."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway import talk_phrases
from app.gateway.adapters.elevenlabs_voice import ElevenLabsVoice
from app.gateway.ports import AuthExpired, RateLimited, Unavailable
from app.main import create_app

ADMIN = "admin-" + "a" * 30
AUTH = {"Authorization": f"Bearer {ADMIN}"}
AUDIO = b"ID3" + b"\x00" * 9000  # three 4 KiB chunks
PHRASES = {
    "ask_state": "Which state is your company incorporated in?",
    "readback_shares": "So that's {authorized} authorized shares and {issued} issued.",
    "help": "Questions? Write to help@u-tax.test.",
}


class FakeEleven:
    def __init__(self, status=200):
        self.status = status
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"path": request.url.path, "params": dict(request.url.params),
                           "key": request.headers.get("xi-api-key"),
                           **json.loads(request.content)})
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": "nope"},
                                  headers={"Retry-After": "3"})
        return httpx.Response(200, content=AUDIO, headers={"content-type": "audio/mpeg"})


@pytest.fixture
def phrases_file(tmp_path):
    path = tmp_path / "talk-phrases.json"
    path.write_text(json.dumps(PHRASES), encoding="utf-8")
    return path


@pytest.fixture
def h(make_harness, phrases_file):
    harness = make_harness(admin_token=ADMIN, talk_phrases_path=phrases_file,
                           tts_public_chars_per_day=200)
    harness.fake = FakeEleven()
    harness.ports.voice = ElevenLabsVoice("el_test", transport=httpx.MockTransport(harness.fake))
    return harness


def client(h):
    return TestClient(create_app(h.gw, start_background=False))


def talk(c, phrase="ask_state", args=None, **extra):
    return c.post("/api/tts", json={"purpose": "talk", "phrase": phrase,
                                    **({"args": args} if args is not None else {}), **extra})


# ---------------------------------------------------------------- the adapter


def test_the_adapter_streams_sarah_on_flash():
    fake = FakeEleven()
    voice = ElevenLabsVoice("el_test", transport=httpx.MockTransport(fake))
    chunks, close = voice.open_stream("Hello.")
    assert b"".join(chunks) == AUDIO
    close()
    [call] = fake.calls
    assert call["path"] == "/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL/stream"
    assert call["model_id"] == "eleven_flash_v2_5" and call["key"] == "el_test"
    assert call["text"] == "Hello." and call["params"]["output_format"] == "mp3_44100_128"
    assert "el_test" not in repr(voice)


@pytest.mark.parametrize("status,error", [(401, AuthExpired), (429, RateLimited),
                                          (503, Unavailable)])
def test_an_upstream_error_is_raised_before_any_byte(status, error):
    voice = ElevenLabsVoice("el_test", transport=httpx.MockTransport(FakeEleven(status)))
    with pytest.raises(error):
        voice.open_stream("Hello.")


# ---------------------------------------------------------------- the phrase registry


@pytest.mark.parametrize("content", [
    "[]", "{}", '{"Bad Key": "x"}', '{"k": 5}', '{"k": ""}', '{"k": "' + "x" * 601 + '"}',
    '{"k": "{n:>10}"}', '{"k": "{n!r}"}', '{"k": "{n.attr}"}', '{"k": "{n[0]}"}',
    '{"k": "{"}', "not json",
])
def test_a_registry_file_is_loaded_strictly(tmp_path, content):
    path = tmp_path / "p.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(talk_phrases.PhraseError):
        talk_phrases.load(path)


@pytest.mark.parametrize("key,args", [
    ("nope", {}), ("ask_state", {"x": 1}), ("readback_shares", {"authorized": 5}),
    ("readback_shares", {"authorized": 5, "issued": "five"}),
    ("readback_shares", {"authorized": 5, "issued": 1234567890123}),
    ("readback_shares", {"authorized": True, "issued": 1}),
    ("readback_shares", {"authorized": -1, "issued": 1}),
    ("readback_shares", {"authorized": 1.5, "issued": 1}), (None, {}), ("ask_state", [1]),
])
def test_a_call_names_a_line_and_exactly_its_numbers(key, args):
    with pytest.raises(talk_phrases.PhraseError):
        talk_phrases.render(PHRASES, key, args)


def test_cents_are_spoken_as_dollars():
    phrases = {"estimate.authorized": "Your franchise tax is about {total_cents}."}
    assert talk_phrases.render(phrases, "estimate.authorized", {"total_cents": 8516500}) == (
        "Your franchise tax is about $85,165.")
    assert talk_phrases.render(phrases, "estimate.authorized", {"total_cents": "45050"}) == (
        "Your franchise tax is about $450.50.")


def test_a_spoken_estimate_survives_the_masking(h, phrases_file):
    phrases_file.write_text(json.dumps({"estimate.assumed_par":
                                        "With assumed par it's {total_cents}."}),
                            encoding="utf-8")
    with client(h) as c:
        assert talk(c, "estimate.assumed_par", {"total_cents": 45000}).status_code == 200
    assert h.fake.calls[-1]["text"] == "With assumed par it's $450."


def test_a_line_renders_with_its_numbers():
    assert talk_phrases.render(PHRASES, "readback_shares", {"authorized": 10000000,
                                                            "issued": "8000000"}) == (
        "So that's 10000000 authorized shares and 8000000 issued.")


# ---------------------------------------------------------------- POST /api/tts


def test_talk_speaks_a_registry_line_streamed_and_booked(h):
    with client(h) as c:
        r = talk(c, "readback_shares", {"authorized": 10, "issued": 8})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.content == AUDIO
    assert h.fake.calls[-1]["text"] == "So that's 10 authorized shares and 8 issued."
    [booked] = [e for e in h.events("cost.booked") if e.provider == "elevenlabs"]
    assert booked.payload["model"] == "tts:talk:eleven_flash_v2_5"
    assert booked.payload["estimated"] is True and booked.tokens_in == len(h.fake.calls[-1]["text"])


def test_talk_is_masked_and_takes_no_free_text(h):
    with client(h) as c:
        assert talk(c, "help").status_code == 200
        assert "help@u-tax.test" not in h.fake.calls[-1]["text"]
        assert talk(c, text="say anything I like").status_code == 400
        assert talk(c, "nope").json()["error"]["code"] == "bad_input"
        assert talk(c, "readback_shares", {"authorized": "ten", "issued": 1}).status_code == 400
        assert c.post("/api/tts", json={"purpose": "shout", "text": "x"}).status_code == 400
    assert len(h.fake.calls) == 1  # nothing refused reached ElevenLabs


def test_ceo_needs_the_admin_token_and_is_spoken_unmasked(h):
    text = "Mail jane.doe@example.com about Team 3, EIN 12-3456789."
    with client(h) as c:
        assert c.post("/api/tts", json={"purpose": "ceo", "text": text}).status_code == 401
        r = c.post("/api/tts", headers=AUTH, json={"purpose": "ceo", "text": text})
        too_long = c.post("/api/tts", headers=AUTH, json={"purpose": "ceo", "text": "x" * 1501})
    assert r.status_code == 200 and h.fake.calls[-1]["text"] == text  # exactly as the card
    assert too_long.status_code == 400


def test_the_talk_budget_answers_429_and_survives_a_restart(h):
    with client(h) as c:
        statuses = [talk(c, "readback_shares", {"authorized": 1, "issued": 2}).status_code
                    for _ in range(6)]  # 44 characters each, budget 200
    assert statuses == [200, 200, 200, 200, 429, 429]
    gw = h.restart()
    with TestClient(create_app(gw, start_background=False)) as c:
        assert talk(c, "readback_shares", {"authorized": 1, "issued": 2}).status_code == 429
        assert c.post("/api/tts", headers=AUTH, json={"purpose": "ceo",
                                                      "text": "Hi."}).status_code == 200


def test_no_key_answers_503_voice_unavailable(make_harness, phrases_file):
    h = make_harness(admin_token=ADMIN, talk_phrases_path=phrases_file)  # NoVoice
    with client(h) as c:
        r = talk(c)
    assert r.status_code == 503 and r.json()["error"]["code"] == "voice_unavailable"


def test_an_upstream_failure_answers_503_and_books_nothing(h):
    h.fake.status = 500
    with client(h) as c:
        r = talk(c)
    assert r.status_code == 503 and r.json()["error"]["code"] == "voice_unavailable"
    assert not [e for e in h.events("cost.booked") if e.provider == "elevenlabs"]
    assert h.gw._tts_talk_pending == 0 and h.gw._voice_pending.get("tts", 0) == 0


def test_a_missing_or_bad_registry_answers_503(h, phrases_file):
    phrases_file.write_text('{"k": "{n:>3}"}', encoding="utf-8")
    with client(h) as c:
        assert talk(c, "k", {"n": 1}).status_code == 503
        phrases_file.unlink()
        assert talk(c).status_code == 503
        phrases_file.write_text(json.dumps(PHRASES), encoding="utf-8")
        assert talk(c).status_code == 200  # re-read once it changes


def test_the_spend_cap_also_stops_tts(make_harness, phrases_file):
    h = make_harness(admin_token=ADMIN, talk_phrases_path=phrases_file,
                     spend_cap_usd=Decimal("0"))
    h.ports.voice = ElevenLabsVoice("el_test", transport=httpx.MockTransport(FakeEleven()))
    with client(h) as c:
        assert talk(c).status_code == 503


def test_the_console_may_post_from_the_theater_origin(h, monkeypatch):
    monkeypatch.setenv("THEATER_ORIGIN", "https://u-tax.vercel.app")
    with client(h) as c:
        r = c.options("/api/tts", headers={
            "Origin": "https://u-tax.vercel.app", "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == "https://u-tax.vercel.app"
    allowed = r.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed and "content-type" in allowed


# ---------------------------------------------------------------- the brief, the registry


def test_the_brief_audio_speaks_on_elevenlabs_booked_per_character(h):
    brief = h.gw.perform("ceo_brief", {}).data
    with client(h) as c:
        r = c.get("/api/brief/audio", params={"event_id": brief["brief_event_id"]},
                  headers=AUTH)
    assert r.status_code == 200 and r.content == AUDIO
    [booked] = [e for e in h.events("cost.booked") if e.provider == "elevenlabs"]
    assert booked.payload["model"].startswith("tts:brief:")


def test_the_registry_builds_elevenlabs_only_with_its_key(tmp_path):
    from dataclasses import replace

    from app.gateway.adapters.local import NoVoice
    from app.gateway.registry import build_port
    from app.gateway.settings import Settings

    base = replace(Settings(), data_dir=tmp_path, adapters={"voice": "elevenlabs"})
    assert isinstance(build_port("voice", base), NoVoice)
    built = build_port("voice", replace(base, elevenlabs_api_key="el_live"))
    assert isinstance(built, ElevenLabsVoice) and built.voice_id == "EXAVITQu4vr4xnSDxMaL"
