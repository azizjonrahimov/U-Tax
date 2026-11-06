"""Redaction and masking. Runs before anything is stored (Security & Threat Model).

- SSNs/EINs are replaced with "[redacted]" in every stored text, private or not.
- Emails shown on screen become "f•••@t•••.dev"; the company becomes "Team #N".
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"
DOT = "•"

# 123-45-6789, 123 45 6789 (SSN) and 12-3456789 (EIN): always redacted.
_FORMATTED_IDS = re.compile(
    r"(?<![\d.,])(?:\d{3}([- .–])\d{2}\1\d{4}|\d{2}[-– ]\d{7})(?![\d,]|\.\d)"
)
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


_SECOND_LEVEL = {"co", "com", "org", "net", "ac", "gov", "edu"}  # acme.co.uk -> acme


def company_label(address: str) -> str | None:
    """The sender's company word from their domain: team7.dev -> team7,
    eng.acme.com -> acme, acme.co.uk -> acme; None for webmail."""
    domain = address.partition("@")[2].lower().strip(".")
    labels = [p for p in domain.split(".") if p]
    if len(labels) < 2:
        return None
    labels = labels[:-1]  # drop the TLD
    if len(labels) >= 2 and labels[-1] in _SECOND_LEVEL:
        labels = labels[:-1]
    name = labels[-1]
    if name in _FREE_MAIL or len(name) < 3:
        return None
    return name


_GENERIC_LOCAL = {"founder", "founders", "hello", "info", "team", "admin", "contact", "ceo",
                  "cfo", "cto", "finance", "accounts", "billing", "support", "mail", "office"}


def sender_words(address: str) -> list[str]:
    """Name-like words from the local part (jane.doe@ -> ["jane", "doe"]) to hide."""
    local = address.partition("@")[0].lower()
    return [w for w in re.split(r"[._+-]+", local)
            if len(w) >= 3 and w.isalpha() and w not in _GENERIC_LOCAL]


_ENTITY_WORDS = re.compile(r"(?i)\b(delaware|[cs][ -]?corp(?:oration)?|our|my|a|an|the)\b")
_NAMED_ENTITY = re.compile(
    r"\b((?!(?:We|Our|My|I|It|This|Hi|Hey|Hello)\b)[A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*){0,3})"
    r"((?i:,?\s+(?:is\s+|are\s+)?an?\s+delaware\s+(?:[cs][ -]?corp|corporation|company|inc))\w*)"
)
# "Acme Robotics Inc", "Acme Labs": capitalised words before a company suffix.
_COMPANY_PHRASE = re.compile(
    r"\b(?:[A-Z][\w&'-]*\s+){0,3}[A-Z][\w&'-]*,?\s+"
    r"(?:Inc\.?|LLC|L\.L\.C\.|Corp\.?|Corporation|Co\.|Ltd\.?|Labs|Technologies|PBC)(?!\w)"
)


# optional country code, then 3-3-4: (415) 555-0100, 415.555.0100, +1 415 555 0100
_PHONE = re.compile(r"(?<![\w$])(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?!\d)")


_SPOKEN_EMAIL = re.compile(
    r"(?i)\b[a-z0-9][\w.+-]*\s+(?:at|@)\s+[a-z0-9-]+(?:\s*(?:dot|\.)\s*[a-z]{2,})+\b"
    r"|\b[a-z0-9][\w.+-]*\s+at\s+(?:gmail|googlemail|yahoo|outlook|hotmail|icloud|"
    r"proton(?:mail)?|aol|live|me)\b")


def mask_spoken_emails(text: str) -> str:
    """/talk transcripts: "jane at example dot com", "jane at gmail" -> hidden (v1.5)."""
    return _SPOKEN_EMAIL.sub("[email hidden]", text)


def has_email(text: str) -> bool:
    return bool(_EMAIL.search(text))


def has_phone(text: str) -> bool:
    return bool(_PHONE.search(text))


def strip_contacts(text: str) -> str:
    """Remove email addresses and phone numbers entirely (HR: no contact details reach agents)."""
    text, _ = redact_ids(text)
    text = _EMAIL.sub("[email removed]", text)
    return _PHONE.sub("[phone removed]", text)


def mask_text(text: str, *, masked_name: str, company: str | None,
              hide_words: list[str] | None = None) -> str:
    """Redact ids, mask emails and phone numbers, and replace company and sender names.

    Best effort against the design's #1 threat: numbers stay, names go. Company phrases
    with a suffix ("Acme Robotics Inc") and the sender's own name words are replaced too.
    """
    text, _ = redact_ids(text)
    text = _EMAIL.sub(lambda m: mask_email(m.group(0)), text)
    text = _PHONE.sub("[phone hidden]", text)
    replacement = masked_name or "[company]"
    # "a Delaware C Corp", "our Delaware Corporation" describe the entity type, not a
    # name: never mask them (intake quotes them for incorporated_delaware)
    text = _COMPANY_PHRASE.sub(
        lambda m: m.group(0) if _ENTITY_WORDS.search(m.group(0)) else replacement, text)
    # "Zebra Robotics, a Delaware corporation": the capitalised words naming the entity go
    text = _NAMED_ENTITY.sub(lambda m: replacement + m.group(2), text)
    if company:
        text = re.sub(rf"(?i)\b{re.escape(company)}\b", replacement, text)
    for word in hide_words or []:
        text = re.sub(rf"(?i)\b{re.escape(word)}\b", "[name]", text)
    return text
