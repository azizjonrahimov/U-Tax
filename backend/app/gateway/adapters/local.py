"""Local stand-ins for every port. No network, no keys; artifacts go under data_dir.

Each one keeps the port's promises (idempotent keys, error classes) so the contract
suites pass against it, and accepts a FaultPlan so tests can make it fail on purpose.
"""

from __future__ import annotations

import email
import email.policy
import email.utils
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.gateway.events import iso_ts, parse_ts
from app.gateway.ports import (
    Booking,
    CrmContact,
    DeviceCheck,
    FaultPlan,
    InMail,
    LLMRequest,
    LLMResult,
    NotConfigured,
    OutMail,
    Page,
    PaymentLink,
    Post,
    Posted,
    Purchase,
    Rejected,
    SearchHit,
    SentMail,
    Slot,
    Unavailable,
    VoiceSession,
)

log = logging.getLogger("utax.local")

DELAWARE_FEE_URL = "https://corp.delaware.gov/paytaxes/"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _fsync_dir(path: Path) -> None:
    """Make a rename durable (POSIX); Windows can't open a directory, and needs none."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class _KeyedJson:
    """A small JSON file mapping idempotency keys to results (survives restarts)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()  # reads too: Windows refuses a replace mid-read

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            return json.loads(self.path.read_text(encoding="utf-8") or "{}")

    def get(self, key: str) -> Any:
        return self.load().get(key)

    def put(self, key: str, value: Any) -> None:
        """Store `value` under `key`; None removes the key (the file never grows with them)."""
        with self._lock:
            data = self.load()
            if value is None:
                data.pop(key, None)
            else:
                data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:  # durable before anything is paid
                fh.write(json.dumps(data, indent=1, sort_keys=True))
                fh.flush()
                os.fsync(fh.fileno())
            tmp.replace(self.path)
            _fsync_dir(self.path.parent)


# ------------------------------------------------------------------ mail


class LocalMail:
    """Inbox: fixtures/inbox/*.eml. Outbox: data/outbox/<n>.json plus a console line."""

    provider = "local"

    def __init__(self, data_dir: Path, faults: FaultPlan | None = None) -> None:
        self.outbox = data_dir / "outbox"
        self._index = _KeyedJson(self.outbox / "index.json")
        self.faults = faults or FaultPlan()
        self._lock = threading.Lock()

    def send(self, mail: OutMail, key: str) -> SentMail:
        known = self._index.get(key)
        if known:
            return SentMail(message_id=known)
        self.faults.check()
        if "@" not in mail.to:
            raise Rejected(f"bad recipient {mail.to!r}")
        with self._lock:
            self.outbox.mkdir(parents=True, exist_ok=True)
            n = len(list(self.outbox.glob("local-msg-*.json"))) + 1
            message_id = f"local-msg-{n}"
            (self.outbox / f"{message_id}.json").write_text(
                json.dumps({"message_id": message_id, "key": key, "to": mail.to,
                            "subject": mail.subject, "body": mail.body,
                            "in_reply_to": mail.in_reply_to, "bcc": mail.bcc,
                            "attachments": [{"filename": a.filename, "bytes": len(a.content)}
                                            for a in mail.attachments]}, indent=1,
                           ensure_ascii=False),
                encoding="utf-8",
            )
            self._index.put(key, message_id)
        log.info("OUTBOX %s -> %s: %s", message_id, mail.to, mail.subject)
        return SentMail(message_id=message_id)

    def parse_inbound(self, raw: bytes | str | dict[str, Any]) -> InMail | None:
        if isinstance(raw, dict):
            sender, subject, body = raw.get("from"), raw.get("subject", ""), raw.get("body")
            if not sender or body is None:
                return None
            mid = raw.get("message_id") or f"local-in-{_digest(f'{sender}|{subject}|{body}')}"
            return InMail(message_id=mid, sender=sender, subject=subject, body=body)
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        msg = email.message_from_bytes(data, policy=email.policy.default)
        sender = email.utils.parseaddr(str(msg.get("From", "")))[1]
        if not sender:
            return None
        part = msg.get_body(preferencelist=("plain", "html"))  # HTML-only mail too
        body = part.get_content() if part is not None else ""
        if part is not None and part.get_content_subtype() == "html":
            from app.gateway.masking import html_to_text

            body = html_to_text(body)
        subject = str(msg.get("Subject", ""))
        mid = str(msg.get("Message-ID", "")).strip("<> ") or \
            f"local-in-{_digest(f'{sender}|{subject}|{body}')}"
        auto = (str(msg.get("Auto-Submitted", "no")).strip().lower() not in ("", "no")
                or msg.get("X-Autoreply") is not None or msg.get("X-Autorespond") is not None
                or str(msg.get("Precedence", "")).strip().lower() in ("auto_reply", "bulk",
                                                                     "junk"))
        return InMail(message_id=mid, sender=sender, subject=subject, body=body.strip(),
                      auto_reply=auto)


# ------------------------------------------------------------------ llm


class LocalLLM:
    """Deterministic stand-in: no model call. Token counts are estimates (chars / 4)."""

    provider = "local"

    def __init__(self, faults: FaultPlan | None = None) -> None:
        self.faults = faults or FaultPlan()

    def complete(self, request: LLMRequest) -> LLMResult:
        self.faults.check()
        text = "[local stand-in] " + request.prompt.strip().splitlines()[0][:160] \
            if request.prompt.strip() else "[local stand-in]"
        return LLMResult(text=text, model=request.model or "local/stand-in",
                         tokens_in=max(1, (len(request.system) + len(request.prompt)) // 4),
                         tokens_out=max(1, len(text) // 4), cost_usd=None)


# ------------------------------------------------------------------ crm


class LocalCRM:
    provider = "local"

    def __init__(self, data_dir: Path, faults: FaultPlan | None = None) -> None:
        self._contacts = _KeyedJson(data_dir / "crm" / "contacts.json")
        self._stages = _KeyedJson(data_dir / "crm" / "stages.json")
        self.faults = faults or FaultPlan()

    def upsert_contact(self, contact: CrmContact) -> str:
        self.faults.check()
        known = self._contacts.get(contact.key)
        if known:
            return known["id"]
        contact_id = f"local-contact-{_digest(contact.key)}"
        self._contacts.put(contact.key, {"id": contact_id, "label": contact.label,
                                         "kind": contact.kind})
        return contact_id

    def set_stage(self, deal_key: str, stage: str) -> None:
        self.faults.check()
        self._stages.put(deal_key, stage)

    def stage_of(self, deal_key: str) -> str | None:
        return self._stages.get(deal_key)


# ------------------------------------------------------------------ device


class LocalDevice:
    """Writes the memo as a phone-width HTML page instead of a cloud-phone screenshot."""

    provider = "local"

    def __init__(self, data_dir: Path, faults: FaultPlan | None = None) -> None:
        self.screens = data_dir / "screens"
        self.faults = faults or FaultPlan()

    def open_and_capture(self, mail_id: str, body: str) -> DeviceCheck:
        self.faults.check()
        self.screens.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", mail_id)
        escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        (self.screens / f"{safe}.html").write_text(
            "<!doctype html><meta charset=utf-8><meta name=viewport content='width=390'>"
            f"<pre style='white-space:pre-wrap;font:16px system-ui;max-width:390px'>{escaped}</pre>",
            encoding="utf-8",
        )
        return DeviceCheck(screenshot_url=f"local://screens/{safe}.html",
                           rendered_ok=bool(body.strip()))


# ------------------------------------------------------------------ scrape


class LocalScrape:
    """Serves saved snapshots from fixtures/snapshots and canned law search results."""

    provider = "local"
    SNAPSHOTS = {DELAWARE_FEE_URL: "delaware-fees.html"}

    def __init__(self, fixtures_dir: Path, faults: FaultPlan | None = None) -> None:
        self.fixtures = fixtures_dir
        self.faults = faults or FaultPlan()

    def fetch_page(self, url: str) -> Page:
        self.faults.check()
        name = self.SNAPSHOTS.get(url)
        path = self.fixtures / "snapshots" / name if name else None
        if path is None or not path.exists():
            raise Unavailable(f"no saved snapshot for {url}")
        fetched = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return Page(url=url, html=path.read_text(encoding="utf-8"), fetched_at=iso_ts(fetched),
                    source="snapshot")

    def search(self, query: str, limit: int = 3) -> list[SearchHit]:
        self.faults.check()
        path = self.fixtures / "law" / "delaware.json"
        entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        words = set(re.findall(r"[a-z0-9]+", query.lower()))
        scored = []
        for entry in entries:
            score = len(words & set(entry.get("keywords", [])))
            if score:
                scored.append((score, entry))
        scored.sort(key=lambda pair: -pair[0])
        return [SearchHit(cite=e["cite"], snippet=e["snippet"]) for _, e in scored[:limit]]


# ------------------------------------------------------------------ toolbuy


class LocalToolBuy:
    """Log-only: records what an agent would have bought; spends nothing."""

    provider = "local"

    def __init__(self, faults: FaultPlan | None = None) -> None:
        self.faults = faults or FaultPlan()

    def buy(self, tool: str, input: dict[str, Any], key: str) -> Purchase:
        self.faults.check()
        log.info("WOULD BUY %s (%s)", tool, key)
        return Purchase(result={"would_buy": tool}, cost_usd="0.0000", bought=False)


# ------------------------------------------------------------------ voice


class NoVoice:
    """No OPENAI_API_KEY: the mic page falls back to typed text and browser speech."""

    provider = None

    def create_session(self, instructions: str = "", tools: list[dict] | None = None) -> VoiceSession:
        raise NotConfigured("voice_unavailable: no OPENAI_API_KEY")

    def speak(self, text: str) -> bytes:
        raise NotConfigured("voice_unavailable: no OPENAI_API_KEY")


# ------------------------------------------------------------------ calendar


class LocalCalendar:
    """Appends VEVENTs to data/calendar.ics."""

    provider = "local"

    def __init__(self, data_dir: Path, faults: FaultPlan | None = None) -> None:
        self.path = data_dir / "calendar.ics"
        self._index = _KeyedJson(data_dir / "calendar.index.json")
        self.faults = faults or FaultPlan()
        self._lock = threading.Lock()

    def book(self, slot: Slot, invitee: str, title: str, key: str) -> Booking:
        known = self._index.get(key)
        if known:
            return Booking(event_id=known)
        self.faults.check()
        start = parse_ts(slot.start)
        end = start + timedelta(minutes=slot.minutes)
        fmt = "%Y%m%dT%H%M%SZ"
        with self._lock:
            event_id = f"local-cal-{_digest(key)}"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\r\n") as fh:
                fh.write("BEGIN:VEVENT\n"
                         f"UID:{event_id}\nDTSTART:{start.astimezone(timezone.utc):{fmt}}\n"
                         f"DTEND:{end.astimezone(timezone.utc):{fmt}}\nSUMMARY:{title}\n"
                         f"ATTENDEE:mailto:{invitee}\nEND:VEVENT\n")
            self._index.put(key, event_id)
        return Booking(event_id=event_id)


# ------------------------------------------------------------------ social


class LocalSocial:
    """Console poster: data/social.jsonl, urls local://post/<n>."""

    provider = "local"

    def __init__(self, data_dir: Path, faults: FaultPlan | None = None) -> None:
        self.path = data_dir / "social.jsonl"
        self._index = _KeyedJson(data_dir / "social.index.json")
        self.faults = faults or FaultPlan()
        self._lock = threading.Lock()

    def post(self, post: Post, key: str) -> Posted:
        known = self._index.get(key)
        if known:
            return Posted(url=known)
        self.faults.check()
        with self._lock:
            count = len(self._index.load()) + 1
            url = f"local://post/{count}"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"url": url, "channel": post.channel, "text": post.text,
                                     "image_url": post.image_url}, ensure_ascii=False) + "\n")
            self._index.put(key, url)
        log.info("POST %s %s: %s", url, post.channel, post.text[:60])
        return Posted(url=url)


# ------------------------------------------------------------------ pay


class NoPay:
    provider = None

    def create_link(self, amount_cents: int, label: str, key: str) -> PaymentLink:
        raise NotConfigured("PayPort has no adapter (Stripe is a stretch goal)")
