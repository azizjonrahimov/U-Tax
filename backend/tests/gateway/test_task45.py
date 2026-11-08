"""Task 45: /api/stt books a length the server bounds from the bytes, never only the
caller's declared seconds."""

from __future__ import annotations

import struct

import httpx

from app.gateway.adapters.openai_voice import OpenAISTT, audio_seconds_floor, wav_seconds

from .test_task40a import FakeOpenAI, c, h, stt, stt_bookings  # noqa: F401 (fixtures)

WEBM = b"\x1aE\xdf\xa3"


def wav(seconds: float, rate: int = 16000) -> bytes:
    data = b"\x00\x00" * int(rate * seconds)  # 16-bit mono
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    return (b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE" + b"fmt "
            + struct.pack("<I", 16) + fmt + b"data" + struct.pack("<I", len(data)) + data)


def test_a_30_second_body_declared_as_1_second_books_the_byte_floor(h, c):
    body = WEBM + b"\x00" * (30 * 6000 // 8 - 4)  # 30 s of 6 kbps opus
    assert stt(c, seconds=1, body=body).status_code == 200
    [booked] = stt_bookings(h)
    assert booked.tokens_in == 30  # not 1
    assert h.gw.state.stt_talk_seconds_by_day["2026-11-07"] == 30


def test_a_lie_is_bounded_and_capped_at_the_clip_limit(h, c):
    body = WEBM + b"\x00" * 200_000  # bytes for 267 s: capped at 30
    stt(c, seconds=2, body=body)
    assert stt_bookings(h)[0].tokens_in == 30


def test_an_honest_clip_books_its_declared_seconds(h, c):
    body = WEBM + b"\x00" * (3 * 6000 // 8 - 4)  # at most 3 s
    stt(c, seconds=4.2, body=body)
    assert stt_bookings(h)[0].tokens_in == 5  # 4.2 rounded up


def test_a_wav_is_measured_exactly_from_its_header(h, c):
    assert abs(wav_seconds(wav(7.5)) - 7.5) < 0.01
    stt(c, seconds=1, body=wav(7.5), headers={"Content-Type": "audio/wav"})
    assert stt_bookings(h)[0].tokens_in == 8
    assert wav_seconds(b"RIFF\x00\x00\x00\x00WAVEjunk") is None


def test_a_forged_wav_byte_rate_falls_back_to_the_bitrate_floor():
    audio = bytearray(wav(10))
    audio[28:32] = struct.pack("<I", 50_000_000)  # an impossible byte rate
    assert wav_seconds(bytes(audio)) is None
    assert audio_seconds_floor(bytes(audio), "audio/wav") >= 9.9  # 64 kbps floor


def test_the_providers_own_duration_still_wins(h, c):
    h.fake.usage = {"type": "duration", "seconds": 12.2}
    stt(c, seconds=1, body=WEBM + b"\x00" * 596)
    assert stt_bookings(h)[0].tokens_in == 12


def test_floors_per_type():
    kb = b"\x00" * 1000
    assert audio_seconds_floor(kb, "audio/webm") == 8000 / 6000
    assert audio_seconds_floor(kb, "audio/mp4") == 1.0
    assert audio_seconds_floor(kb, "audio/flac") == 0.125
