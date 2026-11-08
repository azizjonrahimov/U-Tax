"""Task 40a (design D36): the voice ports split (TTS / Realtime / STT) and OpenAI
speech-to-text at POST /api/stt."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from app.gateway.adapters import local
from app.gateway.adapters.elevenlabs_voice import ElevenLabsVoice
from app.gateway.adapters.openai_voice import OpenAISTT, OpenAIVoice
from app.gateway.registry import AdapterNotReady, build_ports
from app.gateway.settings import Settings
from app.main import create_app

ADMIN = "admin-" + "a" * 30
AUTH = {"Authorization": f"Bearer {ADMIN}"}
WEBM = {"Content-Type": "audio/webm"}
AUDIO = b"\x1aE\xdf\xa3" + b"\x00" * 4000
# task 45: a clip's bytes bound its booked length (6 kbps webm floor): 2,000 bytes is
# at most 2.7 s, so an honest 3.4 s declaration stands
SHORT = b"\x1aE\xdf\xa3" + b"\x00" * 1996
TINY = b"\x1aE\xdf\xa3" + b"\x00" * 596  # 0.8 s


def _field(body: bytes, name: str) -> str | None:
    marker = f'name="{name}"\r\n\r\n'.encode()
    if marker not in body:
        return None
    return body.split(marker, 1)[1].split(b"\r\n", 1)[0].decode()


class FakeOpenAI:
    """POST /v1/audio/transcriptions. `refuse_models`: models answered 400 model_not_found."""

    def __init__(self, status=200, text="We're a Delaware C-corp.", refuse_models=(),
                 usage=None):
        self.status, self.text, self.refuse, self.usage = status, text, refuse_models, usage
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = request.read()
        model = _field(body, "model")
        self.calls.append({"path": request.url.path, "model": model,
                           "format": _field(body, "response_format"),
                           "language": _field(body, "language"),
                           "auth": request.headers.get("authorization"),
                           "file": b'filename="speech.webm"' in body, "audio": AUDIO in body})
        if model in self.refuse:
            return httpx.Response(400, json={"error": {"message": f"The model `{model}` does "
                                                                  "not exist",
                                                       "code": "model_not_found"}})
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope"}})
        out = {"text": f"  {self.text}  "}
        if self.usage:
            out["usage"] = self.usage
        return httpx.Response(200, json=out)


@pytest.fixture
def h(make_harness):
    harness = make_harness(admin_token=ADMIN, stt_public_seconds_per_day=60)
    harness.fake = FakeOpenAI()
    harness.ports.stt = OpenAISTT("sk-test", transport=httpx.MockTransport(harness.fake))
    return harness


@pytest.fixture
def c(h):
    with TestClient(create_app(h.gw, start_background=False)) as test_client:
        yield test_client


def stt(c, purpose="talk", seconds=4, body=AUDIO, headers=None, **extra):
    params = {"purpose": purpose, **({"seconds": seconds} if seconds is not None else {})}
    return c.post("/api/stt", params=params, content=body, headers={**WEBM, **(headers or {})},
                  **extra)


def stt_bookings(h):
    return [e for e in h.events("cost.booked") if str(e.payload["model"]).startswith("stt:")]


# ---------------------------------------------------------------- the adapter


def test_the_adapter_posts_multipart_on_gpt_4o_transcribe():
    fake = FakeOpenAI()
    heard = OpenAISTT("sk-test", transport=httpx.MockTransport(fake)).transcribe(
        AUDIO, "audio/webm")
    assert heard.text == "We're a Delaware C-corp." and heard.model == "gpt-4o-transcribe"
    [call] = fake.calls
    assert call["path"] == "/v1/audio/transcriptions" and call["auth"] == "Bearer sk-test"
    assert call["model"] == "gpt-4o-transcribe" and call["format"] == "json"
    assert call["language"] == "en" and call["file"] and call["audio"]
    assert "sk-test" not in repr(OpenAISTT("sk-test"))


def test_a_key_without_the_new_model_falls_back_to_whisper():
    fake = FakeOpenAI(refuse_models=("gpt-4o-transcribe",),
                      usage={"type": "duration", "seconds": 3.2})
    heard = OpenAISTT("sk-test", transport=httpx.MockTransport(fake)).transcribe(
        AUDIO, "audio/webm")
    assert [c["model"] for c in fake.calls] == ["gpt-4o-transcribe", "whisper-1"]
    assert heard.model == "whisper-1" and heard.seconds == 3.2


# ---------------------------------------------------------------- the ports split


def _ports(tmp_path, **kw):
    return build_ports(Settings(data_dir=tmp_path, **kw))


def test_the_old_one_switch_setups_keep_working(tmp_path):
    ports = _ports(tmp_path, adapters={"voice": "openai"}, openai_api_key="sk-test")
    assert isinstance(ports.voice, OpenAIVoice) and ports.realtime_port() is ports.voice
    ports = _ports(tmp_path, adapters={"voice": "elevenlabs"}, elevenlabs_api_key="el")
    assert isinstance(ports.voice, ElevenLabsVoice)
    assert isinstance(ports.realtime_port(), local.NoVoice)  # no Realtime, as before
    assert isinstance(ports.stt, local.NoSTT)  # STT is opt-in


def test_elevenlabs_speaks_while_openai_listens_and_mints(tmp_path):
    ports = _ports(tmp_path, adapters={"tts": "elevenlabs", "realtime": "openai",
                                       "stt": "openai"},
                   elevenlabs_api_key="el", openai_api_key="sk-test")
    assert isinstance(ports.voice, ElevenLabsVoice)
    assert isinstance(ports.realtime_port(), OpenAIVoice)
    assert isinstance(ports.stt, OpenAISTT)


def test_no_key_means_503_not_a_boot_failure_and_a_typo_fails_at_boot(tmp_path):
    ports = _ports(tmp_path, adapters={"tts": "elevenlabs", "realtime": "openai",
                                       "stt": "openai"}, elevenlabs_api_key="el")
    assert isinstance(ports.realtime_port(), local.NoVoice)
    assert isinstance(ports.stt, local.NoSTT)
    with pytest.raises(AdapterNotReady, match="ADAPTER_STT"):
        _ports(tmp_path, adapters={"stt": "elevenlabs"})


def test_the_env_switches(monkeypatch):
    for name in ("ADAPTER_VOICE", "ADAPTER_TTS", "ADAPTER_REALTIME", "ADAPTER_STT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADAPTER_VOICE", "openai")
    monkeypatch.setenv("ADAPTER_TTS", "elevenlabs")
    adapters = Settings.from_env().adapters
    assert (adapters["tts"], adapters["realtime"], adapters["stt"]) == (
        "elevenlabs", "openai", "none")
    monkeypatch.setenv("ADAPTER_VOICE", "elevenlabs")
    monkeypatch.delenv("ADAPTER_TTS")
    monkeypatch.setenv("ADAPTER_STT", "OpenAI")
    adapters = Settings.from_env().adapters
    assert (adapters["tts"], adapters["realtime"], adapters["stt"]) == (
        "elevenlabs", "none", "openai")


def test_the_mint_uses_the_realtime_port_not_the_tts_one(make_harness, tmp_path):
    from app.gateway.ports import VoiceSession

    class Minting:
        provider = "openai"
        calls = 0

        def create_session(self, instructions="", tools=None):
            Minting.calls += 1
            return VoiceSession(client_secret="ek_1", model="gpt-realtime",
                                expires_at="", realtime_url="https://x.test/rt")

    prompt = tmp_path / "voice.md"
    prompt.write_text("You are U-Tax intake.", encoding="utf-8")
    h = make_harness(admin_token=ADMIN, voice_prompt_path=prompt)
    h.ports.voice = ElevenLabsVoice("el_test")  # TTS only: it would refuse a session
    h.ports.realtime = Minting()
    with TestClient(create_app(h.gw, start_background=False)) as c:
        out = c.post("/api/voice/session", json={"mode": "customer"})
    assert out.status_code == 200 and out.json()["client_secret"] == "ek_1"
    assert Minting.calls == 1


# ---------------------------------------------------------------- POST /api/stt


def test_talk_transcribes_and_books_the_seconds(h, c):
    out = stt(c, seconds=3.4, body=SHORT)
    assert out.status_code == 200 and out.json() == {"text": "We're a Delaware C-corp."}
    [booked] = stt_bookings(h)
    assert booked.provider == "openai" and booked.payload["model"] == \
        "stt:talk:gpt-4o-transcribe"
    assert booked.tokens_in == 4 and Decimal(booked.cost_usd) == Decimal("0.0004")
    assert h.gw.state.stt_talk_seconds_by_day["2026-11-07"] == 4


def test_the_providers_own_seconds_win_over_the_declared_ones(h, c):
    h.fake.usage = {"type": "duration", "seconds": 7.6}
    stt(c, seconds=2)
    [booked] = stt_bookings(h)
    assert booked.tokens_in == 8


def test_no_declared_length_holds_and_books_30_seconds(h, c):
    stt(c, seconds=None)
    [booked] = stt_bookings(h)
    assert booked.tokens_in == 30


def test_the_ceo_needs_the_admin_bearer(h, c):
    assert stt(c, purpose="ceo").status_code == 401
    out = stt(c, purpose="ceo", headers=AUTH)
    assert out.status_code == 200
    [booked] = stt_bookings(h)
    assert booked.payload["model"] == "stt:ceo:gpt-4o-transcribe"
    assert "2026-11-07" not in h.gw.state.stt_talk_seconds_by_day  # not /talk's budget


@pytest.mark.parametrize("kw, status", [
    ({"purpose": "boss"}, 400),
    ({"seconds": 31}, 400),
    ({"seconds": 0}, 400),
    ({"body": b""}, 400),
    ({"body": b"x" * 1_000_001}, 413),
    ({"headers": {"Content-Type": "application/json"}}, 415),
])
def test_bad_requests_are_refused_before_any_paid_call(h, c, kw, status):
    assert stt(c, **kw).status_code == status
    assert h.fake.calls == [] and stt_bookings(h) == []


def test_no_stt_adapter_answers_503(h, c):
    h.ports.stt = local.NoSTT()
    out = stt(c)
    assert out.status_code == 503 and out.json()["error"]["code"] == "voice_unavailable"


def test_the_daily_talk_budget_answers_429_and_ceo_is_not_counted(h, c):
    for _ in range(2):
        assert stt(c, seconds=25).status_code == 200  # 50 of 60 seconds
    out = stt(c, seconds=25)
    assert out.status_code == 429 and out.json()["error"]["code"] == "rate_limited"
    assert stt(c, seconds=10).status_code == 200  # 60 of 60
    assert stt(c, purpose="ceo", headers=AUTH, seconds=25).status_code == 200
    assert len(h.fake.calls) == 4


def test_a_provider_failure_releases_the_hold_and_books_nothing(h, c):
    h.fake.status = 503
    assert stt(c, seconds=30).status_code == 503
    h.fake.status = 400  # the audio itself was refused
    assert stt(c, seconds=30).status_code == 400
    assert stt_bookings(h) == []
    h.fake.status = 200
    assert stt(c, seconds=30).status_code == 200  # the failed holds came back: 30 of 60
    assert h.gw._voice_pending.get("stt") == 0 and h.gw._stt_talk_pending == 0


def test_the_spend_cap_stops_stt(make_harness):
    h = make_harness(admin_token=ADMIN, spend_cap_usd=Decimal("0"))
    fake = FakeOpenAI()
    h.ports.stt = OpenAISTT("sk-test", transport=httpx.MockTransport(fake))
    with TestClient(create_app(h.gw, start_background=False)) as c:
        assert stt(c).status_code == 503 and fake.calls == []


def test_talk_uses_its_own_per_ip_bucket(h, c):
    codes = [stt(c, seconds=1, body=TINY).status_code
             for _ in range(31)]  # 31 s: inside the budget
    assert codes[:30] == [200] * 30 and codes[30] == 429  # SEND_LIMITS: 30 a minute per IP


# ---------------------------------------------------------------- preflight


def _preflight_openai(tmp_path, models, **kw):
    from app.preflight import check_openai

    seen = []

    def world(request):
        seen.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"data": [{"id": m} for m in models]})

    return check_openai(Settings(data_dir=tmp_path, **kw), httpx.MockTransport(world)), seen


def test_preflight_has_no_openai_line_unless_a_port_uses_it(tmp_path):
    checks, seen = _preflight_openai(tmp_path, [], adapters={"tts": "elevenlabs"})
    assert checks == [] and seen == []


def test_preflight_checks_the_key_and_each_ports_model(tmp_path):
    kw = dict(adapters={"stt": "openai", "realtime": "openai"}, openai_api_key="sk-secret")
    [check], seen = _preflight_openai(tmp_path, ["whisper-1", "gpt-realtime"], **kw)
    assert check.ok and "stt, realtime" in check.reason and "sk-secret" not in check.line()
    assert seen == [("GET", "/v1/models", "Bearer sk-secret")]
    [check], _ = _preflight_openai(tmp_path, ["whisper-1"], **kw)
    assert not check.ok and "realtime (gpt-realtime)" in check.reason
    [check], _ = _preflight_openai(tmp_path, [], adapters={"stt": "openai"})
    assert not check.ok and "OPENAI_API_KEY" in check.reason
