"""Contract v1.11 `make_ad`: the gateway renders the firm's ad, a 1200x628 PNG.

The text is the firm name, the agent's headline (already through the post_social content
guard), and for a client ad the real example "$85,165 -> $450". Nothing else: no client
data, no free-form image generation. Pillow's bundled font (no system fonts needed); the
arrow is drawn, so no glyph can be missing.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 628
INK = (17, 24, 39)
PAPER = (248, 250, 252)
ACCENT = (37, 99, 235)
SAVE = (22, 163, 74)
FIRM = "U-Tax"
TAGLINE = "Delaware franchise tax, checked by AI agents. Free."
EXAMPLE = ("$85,165", "$450")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def render(kind: str, headline: str, path: Path) -> Path:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAPER)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 14), fill=ACCENT)
    draw.text((64, 52), FIRM, font=_font(64), fill=ACCENT)
    y = 160
    for line in textwrap.wrap(headline, width=30)[:3]:
        draw.text((64, y), line, font=_font(58), fill=INK)
        y += 72
    if kind == "client":
        before, after = EXAMPLE
        big = _font(76)
        top = max(y + 24, 400)
        draw.text((64, top), before, font=big, fill=INK)
        width = draw.textlength(before, font=big)
        x0 = 64 + width + 32
        mid = top + 44
        draw.line((x0, mid, x0 + 90, mid), fill=SAVE, width=10)
        draw.polygon(((x0 + 90, mid - 22), (x0 + 124, mid), (x0 + 90, mid + 22)), fill=SAVE)
        draw.text((x0 + 150, top), after, font=big, fill=SAVE)
    else:
        draw.text((64, max(y + 24, 420)), "Paid 1-hour gig: review AI-written tax memos.",
                  font=_font(40), fill=INK)
    draw.text((64, HEIGHT - 70), TAGLINE, font=_font(30), fill=(71, 85, 105))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    image.save(tmp, format="PNG")
    tmp.replace(path)
    return path
