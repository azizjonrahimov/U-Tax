"""share_card: render (PixelAgent's side) and verify a 1200x630 PNG share card.

A copy of docs/designs/agent-market/share_card.py for the contractor: it renders the card U-Tax
asked for and checks it before submitting (U-Tax checks it again on its side, in
backend/app/gateway/share_card.py).

verify() is deterministic: format, size, bytes, not blank, and the exact headline carried in
the PNG text chunk `utax:headline`. It's a filter, not a proof (say so in the demo).
"""
from __future__ import annotations

import io
from PIL import Image, ImageDraw, ImageFont, ImageStat
from PIL.PngImagePlugin import PngInfo

WIDTH, HEIGHT = 1200, 630
MAX_BYTES = 2 * 1024 * 1024
TEXT_KEY = "utax:headline"


def _font(size: int):
    for name in ("arialbd.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def render(headline: str, out_path: str, sub: str = "Educational estimate, not tax advice") -> str:
    img = Image.new("RGB", (WIDTH, HEIGHT), (7, 7, 11))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, WIDTH, 14], fill=(255, 77, 61))
    d.text((60, 60), "U-Tax", font=_font(56), fill=(255, 77, 61))
    words, lines, line = headline.split(), [], ""
    for w in words:  # wrap at ~28 chars
        if len(line) + len(w) + 1 > 28:
            lines.append(line); line = w
        else:
            line = (line + " " + w).strip()
    lines.append(line)
    y = 190
    for ln in lines[:4]:
        d.text((60, y), ln, font=_font(64), fill=(236, 235, 245)); y += 82
    d.text((60, HEIGHT - 80), sub, font=_font(28), fill=(139, 138, 163))
    meta = PngInfo(); meta.add_text(TEXT_KEY, headline)
    img.save(out_path, "PNG", pnginfo=meta)
    return out_path


def verify(data: bytes, expected_headline: str) -> list[dict]:
    checks = []
    def add(name, ok): checks.append({"name": name, "ok": bool(ok)})
    add("size_under_2mb", 0 < len(data) <= MAX_BYTES)
    try:
        img = Image.open(io.BytesIO(data)); img.load()
    except Exception:
        add("is_png", False); return checks
    add("is_png", img.format == "PNG")
    add("dimensions_1200x630", img.size == (WIDTH, HEIGHT))
    stat = ImageStat.Stat(img.convert("L"))
    add("not_blank", stat.stddev[0] > 8)
    add("headline_exact", getattr(img, "text", {}).get(TEXT_KEY) == expected_headline)
    return checks


def passed(checks: list[dict]) -> bool:
    return bool(checks) and all(c["ok"] for c in checks)
