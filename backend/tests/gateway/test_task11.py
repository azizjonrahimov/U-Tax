"""Task 11 (contract v1.7.5): the Realtime session mints input transcription and returns
the matching SDP url. Mocked: the real OpenAI call is UNVERIFIED (no key tonight)."""

from __future__ import annotations

import json

import httpx
from fastapi.testclient import TestClient

from app.gateway.adapters.openai_voice import REALTIME_SDP_URL, OpenAIVoice
from app.main import create_app


def minting(seen):
    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"model": "gpt-realtime",
                                         "client_secret": {"value": "ek_test",
                                                           "expires_at": 1794000000}})
    return httpx.MockTransport(handle)


def test_the_session_mints_input_transcription(monkeypatch):
    monkeypatch.delenv("OPENAI_TRANSCRIBE_MODEL", raising=False)
    seen = []
    voice = OpenAIVoice("sk-test", transport=minting(seen))
    session = voice.create_session("You are U-Tax's intake.")
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/realtime/sessions"
    assert body["input_audio_transcription"] == {"model": "whisper-1"}
    assert body["tools"] == [] and body["instructions"] == "You are U-Tax's intake."
    assert session.realtime_url == "https://api.openai.com/v1/realtime?model=gpt-realtime"
    assert REALTIME_SDP_URL.startswith("https://api.openai.com/v1/realtime")


def test_the_transcription_model_is_an_env_setting(monkeypatch):
    monkeypatch.setenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
    seen = []
    OpenAIVoice("sk-test", transport=minting(seen)).create_session("x")
    assert json.loads(seen[0].content)["input_audio_transcription"] == {
        "model": "gpt-4o-mini-transcribe"}


def test_the_session_route_returns_realtime_url(h, tmp_path):
    prompt = tmp_path / "voice-intake.md"
    prompt.write_text("You are U-Tax's intake.", encoding="utf-8")
    h.gw.settings = h.gw.settings.__class__(**{**h.gw.settings.__dict__,
                                               "voice_prompt_path": prompt})
    h.gw.ports.voice = OpenAIVoice("sk-test", transport=minting([]))
    with TestClient(create_app(h.gw, start_background=False)) as client:
        out = client.post("/api/voice/session").json()
    assert out["client_secret"] == "ek_test"
    assert out["realtime_url"] == "https://api.openai.com/v1/realtime?model=gpt-realtime"
