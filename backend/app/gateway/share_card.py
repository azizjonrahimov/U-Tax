"""D38: verify (U-Tax side) a contractor's 1200x630 PNG share card. From the runbook's
reference (docs/designs/agent-market/share_card.py); the renderer lives with the
contractor (contractors/pixelagent/).

verify() is deterministic: the format, the size, the bytes, not blank, and the exact
headline carried in the PNG text chunk `utax:headline`. It's a filter, not a proof (say so
in the demo). Untrusted input: the dimensions are read from the header BEFORE any pixel is
decoded, so a decompression bomb is refused without decoding it.
"""

from __future__ import annotations

import io

WIDTH, HEIGHT = 1200, 630
MAX_BYTES = 2 * 1024 * 1024
TEXT_KEY = "utax:headline"


def verify(data: bytes, expected_headline: str) -> list[dict]:
    from PIL import Image, ImageStat

    checks: list[dict] = []

    def add(name: str, ok: object) -> None:
        checks.append({"name": name, "ok": bool(ok)})

    add("size_under_2mb", 0 < len(data) <= MAX_BYTES)
    if not checks[-1]["ok"]:
        return checks
    try:
        img = Image.open(io.BytesIO(data))  # the header only
    except Exception:  # noqa: BLE001 - anything unreadable is simply not a PNG
        add("is_png", False)
        return checks
    add("is_png", img.format == "PNG")
    add("dimensions_1200x630", img.size == (WIDTH, HEIGHT))
    if not (checks[-1]["ok"] and checks[-2]["ok"]):
        return checks  # never decode a wrong-sized (or non-PNG) image
    try:
        img.load()
    except Exception:  # noqa: BLE001
        add("not_blank", False)
        return checks
    add("not_blank", ImageStat.Stat(img.convert("L")).stddev[0] > 8)
    add("headline_exact", getattr(img, "text", {}).get(TEXT_KEY) == expected_headline)
    return checks


def passed(checks: list[dict]) -> bool:
    return bool(checks) and all(c["ok"] for c in checks)
