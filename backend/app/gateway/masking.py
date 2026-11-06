"""Redaction and masking. Runs before anything is stored (Security & Threat Model).

- SSNs/EINs are replaced with "[redacted]" in every stored text, private or not.
- Emails shown on screen become "f•••@t•••.dev"; the company becomes "Team #N".
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"
DOT = "•"

# 123-45-6789, 123 45 6789 (SSN) and 12-3456789 (EIN): always redacted.
_FORMATTED_IDS = re.compile(r"(?<!\d)(?:\d{3}[- ]\d{2}[- ]\d{4}|\d{2}-\d{7})(?!\d)")
# A bare 9-digit run is only an id when a keyword says so: "100000000" alone is a
# share count and must survive.
_KEYWORD_ID = re.compile(
    r"(?i)\b(ssn|social security(?: number| no\.?)?|ein|tax id|tin|employer identification"
    r"(?: number)?|itin)\b([^\d\n]{0,20})(\d{9})(?!\d)"
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_FREE_MAIL = {"gmail", "googlemail", "outlook", "hotmail", "yahoo", "icloud", "proton",
              "protonmail", "aol", "live", "me", "mail", "gmx", "fastmail"}


def redact_ids(text: str) -> tuple[str, int]:
    """Replace SSN/EIN patterns. Returns the text and how many were removed."""
    count = 0

    def formatted(_: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return REDACTED

    def keyword(m: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{m.group(1)}{m.group(2)}{REDACTED}"

    text = _FORMATTED_IDS.sub(formatted, text)
    text = _KEYWORD_ID.sub(keyword, text)
    return text, count


def mask_email(address: str) -> str:
    """founder@team7.dev -> f•••@t•••.dev"""
    local, _, domain = address.strip().partition("@")
    if not domain:
        return f"{address[:1]}{DOT * 3}" if address else ""
    labels = domain.split(".")
    tld = labels[-1] if len(labels) > 1 else ""
    name = labels[0]
    masked_domain = f"{name[:1]}{DOT * 3}" + (f".{tld}" if tld else "")
    return f"{local[:1]}{DOT * 3}@{masked_domain}"


def company_label(address: str) -> str | None:
    """The sender's company word from their domain (team7.dev -> team7); None for webmail."""
    domain = address.partition("@")[2].lower()
    if not domain:
        return None
    name = domain.split(".")[0]
    if name in _FREE_MAIL or len(name) < 3:
        return None
    return name


_PHONE = re.compile(r"(?<![\w$])\+?\d{1,2}?[\s.-]?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)")


def has_email(text: str) -> bool:
    return bool(_EMAIL.search(text))


def has_phone(text: str) -> bool:
    return bool(_PHONE.search(text))


def strip_contacts(text: str) -> str:
    """Remove email addresses and phone numbers entirely (HR: no contact details reach agents)."""
    text, _ = redact_ids(text)
    text = _EMAIL.sub("[email removed]", text)
    return _PHONE.sub("[phone removed]", text)


def mask_text(text: str, *, masked_name: str, company: str | None) -> str:
    """Redact ids, mask email addresses, and replace the company word with the mask."""
    text, _ = redact_ids(text)
    text = _EMAIL.sub(lambda m: mask_email(m.group(0)), text)
    if company:
        text = re.sub(rf"(?i)\b{re.escape(company)}\b", masked_name, text)
    return text
