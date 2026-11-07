"""VoicePort on OpenAI: an ephemeral Realtime session for the /talk page, and TTS for the
CEO's brief. The API key stays in the gateway; the browser only gets the short-lived
client secret.

UNVERIFIED (no key tonight): endpoint shapes follow OpenAI's public REST docs as known
when this was written (POST /v1/realtime/sessions, POST /v1/audio/speech); the model names
are env settings (OPENAI_REALTIME_MODEL, OPENAI_TTS_MODEL, OPENAI_TTS_VOICE) so they can
be changed without code. Spend: every call costs money, so it only runs with a key.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from app.gateway.events import iso_ts
from app.gateway.ports import (
    AuthExpired,
    NotConfigured,
    PortError,
    RateLimited,
    Rejected,
    Unavailable,
    VoiceSession,
)

API = "https://api.openai.com/v1"
# v1.7.5: the secret is minted on POST {API}/realtime/sessions (the beta Realtime endpoint),
# so the browser posts its SDP offer to the matching beta URL. Keep the two together.
REALTIME_SDP_URL = API + "/realtime?model={model}"


def _map_error(response: httpx.Response) -> PortError:
    retry = response.headers.get("retry-after")
    try:
        retry_after = float(retry) if retry else None
    except ValueError:  # an HTTP-date Retry-After: let the caller's backoff decide
        retry_after = None
    if response.status_code == 429:
        return RateLimited("openai rate limited", retry_after=retry_after)
    if response.status_code in (401, 403):
        return AuthExpired("openai key rejected")
    if response.status_code >= 500:
        return Unavailable(f"openai {response.status_code}")
    return Rejected(f"openai {response.status_code}: {response.text[:200]}")


class OpenAIVoice:
    provider = "openai"

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 20.0) -> None:
        if not api_key:
            raise NotConfigured("voice_unavailable: no OPENAI_API_KEY")
        self.realtime_model = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime")
        self.tts_model = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        self.tts_voice = os.environ.get("OPENAI_TTS_VOICE", "alloy")
        # v1.7.5: without input transcription Realtime never sends the customer's words as
        # text, so /talk voice could never open a case
        self.transcribe_model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
        self._http = httpx.Client(base_url=API, timeout=timeout, transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def __repr__(self) -> str:  # never show the key
        return "OpenAIVoice()"

    MAX_OUTPUT_TOKENS = 600  # per response: a short intake turn, not an open assistant

    def create_session(self, instructions: str = "", tools: list[dict] | None = None) -> VoiceSession:
        # Contract §6d: instructions and tools are fixed here, server-side, so the browser's
        # short-lived secret is only good for this conversation (no tools for /talk; the
        # CEO tool registry for the CEO console, v1.8 §5b).
        body = {"model": self.realtime_model, "instructions": instructions,
                "tools": list(tools or []), **({"tool_choice": "auto"} if tools else {}),
                "max_response_output_tokens": self.MAX_OUTPUT_TOKENS,
                "input_audio_transcription": {"model": self.transcribe_model}}
        try:
            response = self._http.post("/realtime/sessions", json=body)
        except httpx.TransportError as exc:
            raise Unavailable(f"openai unreachable: {exc}") from exc
        if not response.is_success:
            raise _map_error(response)
        body = response.json()
        secret = body.get("client_secret") or {}
        value = secret.get("value") if isinstance(secret, dict) else secret
        if not value:
            raise Rejected("openai returned no client secret")
        expires = secret.get("expires_at") if isinstance(secret, dict) else None
        expires_at = iso_ts(datetime.fromtimestamp(expires, tz=timezone.utc)) \
            if isinstance(expires, (int, float)) else str(expires or "")
        model = str(body.get("model") or self.realtime_model)
        return VoiceSession(client_secret=value, model=model, expires_at=expires_at,
                            realtime_url=REALTIME_SDP_URL.format(model=quote(model, safe="")))

    def speak(self, text: str) -> bytes:
        try:
            response = self._http.post("/audio/speech", json={
                "model": self.tts_model, "voice": self.tts_voice, "input": text[:4000],
                "response_format": "mp3"})
        except httpx.TransportError as exc:
            raise Unavailable(f"openai unreachable: {exc}") from exc
        if not response.is_success:
            raise _map_error(response)
        return response.content
