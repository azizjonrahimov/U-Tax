"""Contract v1.10.2 §5c: `/api/tts` purpose "talk" speaks only lines from a server registry.

`naive/prompts/talk-phrases.json` (agents lane) is a flat JSON object:

    {"ask_state": "Which state is your company incorporated in?",
     "readback_shares": "So that's {authorized} authorized shares and {issued} issued."}

- keys: 1-64 of a-z 0-9 _ . -
- values: a line of at most 600 characters; `{name}` placeholders only (no format spec,
  no conversion, no attribute or index access; `{{`/`}}` for a literal brace).

It is loaded strictly: anything else and the whole file is refused (the route then answers
503 voice_unavailable and the page uses the browser's voice). A call names a key and gives
exactly that line's args, each a number of at most 12 digits; anything else is bad_input.
An arg named `*_cents` is spoken as dollars: 8516500 -> "$85,165", 8516550 -> "$85,165.50".
"""

from __future__ import annotations

import json
import re
import string
from pathlib import Path

KEY = re.compile(r"[a-z0-9_.-]{1,64}")
ARG = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")
NUMBER = re.compile(r"[0-9]{1,12}")
MAX_LINE = 600


class PhraseError(ValueError):
    """A bad registry file, or a bad call (unknown key, wrong or non-numeric args)."""


def placeholders(line: str) -> set[str]:
    names: set[str] = set()
    try:
        parsed = list(string.Formatter().parse(line))
    except ValueError as err:  # an unmatched brace
        raise PhraseError(f"bad template: {err}") from err
    for _, field, spec, conversion in parsed:
        if field is None:
            continue
        if spec or conversion or not ARG.fullmatch(field):
            raise PhraseError(f"placeholder {{{field}}} must be a plain name")
        names.add(field)
    return names


def load(path: Path) -> dict[str, str]:
    """The registry, or PhraseError: never half a file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise PhraseError(f"{path.name} is missing") from err
    except (OSError, ValueError) as err:
        raise PhraseError(f"{path.name} is not readable JSON: {err}") from err
    if not isinstance(data, dict) or not data:
        raise PhraseError(f"{path.name} must be a non-empty JSON object")
    for key, line in data.items():
        if not KEY.fullmatch(key):
            raise PhraseError(f"bad phrase key {key!r}")
        if not isinstance(line, str) or not line.strip() or len(line) > MAX_LINE:
            raise PhraseError(f"phrase {key!r} must be a line of 1-{MAX_LINE} characters")
        placeholders(line)
    return dict(data)


def render(phrases: dict[str, str], key: object, args: object) -> str:
    """The line for `key` with its numeric args, or PhraseError."""
    if not isinstance(key, str) or key not in phrases:
        raise PhraseError("unknown phrase")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise PhraseError("args must be an object")
    line = phrases[key]
    wanted = placeholders(line)
    if set(args) != wanted:
        raise PhraseError(f"phrase {key!r} takes exactly {sorted(wanted)}")
    values: dict[str, str] = {}
    for name, value in args.items():
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise PhraseError(f"{name} must be a number")
        text = str(value)
        if not NUMBER.fullmatch(text):
            raise PhraseError(f"{name} must be a whole number of at most 12 digits")
        values[name] = dollars(int(text)) if name.endswith("_cents") else text
    return line.format(**values)


def dollars(cents: int) -> str:
    whole, part = divmod(cents, 100)
    return f"${whole:,}" if part == 0 else f"${whole:,}.{part:02d}"
