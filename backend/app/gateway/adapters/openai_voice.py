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
    Transcript,
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

    def speak(self, text: str) -> bytes:  # (TTS: ADAPTER_TTS=openai)
        try:
            response = self._http.post("/audio/speech", json={
                "model": self.tts_model, "voice": self.tts_voice, "input": text[:4000],
                "response_format": "mp3"})
        except httpx.TransportError as exc:
            raise Unavailable(f"openai unreachable: {exc}") from exc
        if not response.is_success:
            raise _map_error(response)
        return response.content


# ------------------------------------------------------------------ task 40a: STT

# file extension per audio type: OpenAI reads the format from the upload's file name
# (flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm)
STT_TYPES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav",
             "audio/x-wav": "wav", "audio/wave": "wav", "audio/mpeg": "mp3",
             "audio/mp3": "mp3", "audio/mp4": "mp4", "audio/m4a": "m4a",
             "audio/x-m4a": "m4a", "audio/aac": "m4a", "audio/flac": "flac",
             "audio/x-flac": "flac"}
STT_FALLBACK = "whisper-1"

# task 45: the LOWEST bitrate a clip of each type plausibly has (bits a second), so
# bytes * 8 / rate is the longest the audio could be: a caller can't send 30 s of audio
# and declare 1 s. Opus speech goes down to 6 kbps; AAC and MP3 to about 8; FLAC (lossless)
# never below 64. WAV is read exactly from its header.
MIN_BITRATE = {"webm": 6000, "ogg": 6000, "mp4": 8000, "m4a": 8000, "mp3": 8000,
               "flac": 64000, "wav": 64000}


def wav_seconds(audio: bytes) -> float | None:
    """A RIFF/WAVE clip's exact length (fmt byte rate, data size), else None."""
    import struct

    if len(audio) < 12 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return None
    pos, byte_rate = 12, None
    while pos + 8 <= len(audio):
        chunk, size = audio[pos:pos + 4], struct.unpack("<I", audio[pos + 4:pos + 8])[0]
        if chunk == b"fmt " and size >= 12 and pos + 20 <= len(audio):
            byte_rate = struct.unpack("<I", audio[pos + 16:pos + 20])[0]
            if not 1000 <= byte_rate <= 384_000:  # past 48 kHz stereo 32-bit: not real
                return None
        elif chunk == b"data":
            if not byte_rate:
                return None
            return min(size, len(audio) - pos - 8) / byte_rate
        pos += 8 + size + (size & 1)
    return None


def audio_seconds_floor(audio: bytes, content_type: str) -> float:
    """Task 45: the least length to book for this clip, from the bytes alone: a WAV's own
    header, else bytes at the type's lowest plausible bitrate."""
    ext = STT_TYPES.get(content_type, "webm")
    if ext == "wav":
        exact = wav_seconds(audio)
        if exact is not None:
            return exact
    return len(audio) * 8 / MIN_BITRATE.get(ext, 6000)


class OpenAISTT:
    """POST /v1/audio/transcriptions (multipart: file, model, response_format=json) ->
    {"text", "usage"?}. gpt-4o-transcribe first (OPENAI_STT_MODEL); a model the key can't
    use (a 400/404 naming the model) is retried once on whisper-1. usage.seconds (whisper's
    `duration` usage) is passed on when the answer has it, for booking."""

    provider = "openai"

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None,
                 timeout: float = 30.0) -> None:
        if not api_key:
            raise NotConfigured("stt_unavailable: no OPENAI_API_KEY")
        self.model = os.environ.get("OPENAI_STT_MODEL", "gpt-4o-transcribe")
        # D41: always a language (auto-detect wrote Urdu, Amharic, Korean from room noise)
        self.language = os.environ.get("OPENAI_STT_LANGUAGE") or "en"
        self._http = httpx.Client(base_url=API, timeout=httpx.Timeout(timeout, connect=5.0),
                                  transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}"})

    def __repr__(self) -> str:  # never show the key
        return f"OpenAISTT(model={self.model!r})"

    def _post(self, model: str, audio: bytes, content_type: str) -> httpx.Response:
        ext = STT_TYPES.get(content_type, "webm")
        data = {"model": model, "response_format": "json"}
        if self.language:
            data["language"] = self.language
        try:
            return self._http.post("/audio/transcriptions", data=data,
                                   files={"file": (f"speech.{ext}", audio, content_type)})
        except httpx.TransportError as exc:
            raise Unavailable(f"openai unreachable: {exc}") from exc

    def transcribe(self, audio: bytes, content_type: str) -> Transcript:
        model = self.model
        response = self._post(model, audio, content_type)
        if (response.status_code in (400, 404) and model != STT_FALLBACK
                and "model" in response.text.lower()):
            model = STT_FALLBACK  # the key can't use the newer model: whisper-1 instead
            response = self._post(model, audio, content_type)
        if not response.is_success:
            raise _map_error(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise Rejected("openai answered a transcript that isn't JSON") from exc
        text = body.get("text") if isinstance(body, dict) else None
        if not isinstance(text, str):
            raise Rejected("openai returned no transcript text")
        usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
        seconds = usage.get("seconds") if usage.get("type") == "duration" else None
        return Transcript(text=text.strip(), model=model,
                          seconds=float(seconds) if isinstance(seconds, (int, float))
                          and not isinstance(seconds, bool) else None)
