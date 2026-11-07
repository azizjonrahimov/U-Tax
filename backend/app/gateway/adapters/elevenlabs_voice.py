"""Contract v1.10 §5c (design D30): speech output on ElevenLabs.

- `POST /v1/text-to-speech/{voice_id}/stream` with `xi-api-key`, body `{text, model_id}`,
  answering `audio/mpeg` chunks. `open_stream` opens the response and checks its status
  BEFORE the first byte goes to the browser, so an upstream error still maps to a clean
  503/429, and the audio is then streamed through (first byte in about 1-2 s).
- No Realtime here: `create_session` is NotConfigured, so `/api/voice/session` answers 503 and
  the pages listen with the browser's SpeechRecognition (§8 fallback, now the main path).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from urllib.parse import quote

import httpx

from app.gateway.ports import (AuthExpired, NotConfigured, PortError, RateLimited, Rejected,
                               Unavailable, VoiceSession)

API = "https://api.elevenlabs.io"
DEFAULT_VOICE = "EXAVITQu4vr4xnSDxMaL"  # Sarah
DEFAULT_MODEL = "eleven_flash_v2_5"
OUTPUT_FORMAT = "mp3_44100_128"
CHUNK = 4096


def _map_error(response: httpx.Response) -> PortError:
    status = response.status_code
    try:
        body = response.json()
        detail = str(body.get("detail") or "" if isinstance(body, dict) else body)[:200]
    except (ValueError, httpx.HTTPError):
        detail = ""
    if status in (401, 403):
        return AuthExpired(f"elevenlabs refused the key ({status}): {detail}")
    if status == 429:
        retry = response.headers.get("Retry-After")
        return RateLimited(f"elevenlabs rate limit: {detail}",
                           retry_after=float(retry) if retry and retry.isdigit() else None)
    if status >= 500:
        return Unavailable(f"elevenlabs {status}: {detail}")
    return Rejected(f"elevenlabs {status}: {detail}")


class ElevenLabsVoice:
    provider = "elevenlabs"

    def __init__(self, api_key: str, *, voice_id: str = DEFAULT_VOICE,
                 model: str = DEFAULT_MODEL, timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.voice_id = voice_id or DEFAULT_VOICE
        self.tts_model = model or DEFAULT_MODEL
        # connect fast or fail: the console gives up at 10 s (v1.10.1)
        self._http = httpx.Client(base_url=API, timeout=httpx.Timeout(timeout, connect=3.0),
                                  transport=transport,
                                  headers={"xi-api-key": api_key, "Accept": "audio/mpeg",
                                           "User-Agent": "utax-gateway"})

    def __repr__(self) -> str:  # never show the key
        return f"ElevenLabsVoice(voice_id={self.voice_id!r}, model={self.tts_model!r})"

    def create_session(self, instructions: str = "", tools: list[dict] | None = None) -> VoiceSession:
        raise NotConfigured("voice_unavailable: no Realtime provider (browser listening)")

    def open_stream(self, text: str) -> tuple[Iterator[bytes], Callable[[], None]]:
        """(chunks, close): the upstream answered 200 when this returns. Raises PortError."""
        request = self._http.build_request(
            "POST", f"/v1/text-to-speech/{quote(self.voice_id, safe='')}/stream",
            params={"output_format": OUTPUT_FORMAT},
            json={"text": text, "model_id": self.tts_model})
        try:
            response = self._http.send(request, stream=True)
        except httpx.TransportError as exc:
            raise Unavailable(f"elevenlabs unreachable: {exc}") from exc
        self._last = response  # speak() reads it whole, without the stream's swallow
        if not response.is_success:
            try:
                response.read()
            except httpx.HTTPError:  # the error body never came: map on the status alone
                pass
            finally:
                response.close()
            raise _map_error(response)

        def chunks() -> Iterator[bytes]:
            try:
                yield from response.iter_bytes(CHUNK)
            except httpx.HTTPError:  # mid-stream: the browser has a partial clip, nothing else
                return
            finally:
                response.close()

        return chunks(), response.close

    def speak(self, text: str) -> bytes:
        """The whole clip (GET /api/brief/audio caches it): a cut or empty one is an error,
        never cached as the brief."""
        _, close = self.open_stream(text)
        response = self._last
        try:
            audio = b"".join(response.iter_bytes(CHUNK))
        except httpx.HTTPError as exc:
            raise Unavailable(f"elevenlabs stream cut: {exc}") from exc
        finally:
            close()
        if not audio:
            raise Unavailable("elevenlabs answered an empty clip")
        return audio
