"""AgentMail mail adapter (ADAPTER_MAIL=agentmail).

Built against the documented REST API (docs.agentmail.to, read 2026-11-07):
- send: `POST /v0/inboxes/{inbox_id}/messages/send` with `Authorization: Bearer <key>`,
  JSON `{to: [..], subject, text}`, answer `{message_id, thread_id}`. The inbox id is the
  inbox's address. `Idempotency-Key` (1..256 of [A-Za-z0-9-._~], kept 24 h) makes a retry
  return the first message id and send no second mail; a local index keeps that promise
  past 24 h and across restarts. Both are keyed on the gateway key AND a fingerprint of
  recipient, subject and body, because gateway keys restart with a fresh event log
  ("memo:case_1:v1r0" again after a reset): a different mail under a reused key is a new
  send, never a 409 and never a silent "already sent".
- inbound: the `message.received` webhook, Svix-signed (`svix-id`, `svix-timestamp`,
  `svix-signature: v1,<base64>`; secret `whsec_<base64>`). Delivery is at least once; the
  gateway dedupes on the message id. Only our own two inboxes are accepted. Bodies over
  64 KiB arrive without text/html (with a `body_url`); the adapter then fetches the
  message from the API, for our inboxes only (`GET /v0/inboxes/{inbox}/messages/{id}`).
Real calls are UNVERIFIED until a key exists; the MockTransport tests pin the shapes.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.gateway.adapters import _http
from app.gateway.adapters.local import LocalMail, _KeyedJson
from app.gateway.ports import (
    Booking,
    InMail,
    NotConfigured,
    OutMail,
    Rejected,
    SentMail,
    Slot,
    Unavailable,
)

log = logging.getLogger("utax.agentmail")

BASE_URL = "https://api.agentmail.to"
RECEIVED = "message.received"
SVIX_TOLERANCE_SECONDS = 300
MIN_SECRET_BYTES = 24
SUBJECT_MAX = 200
MESSAGE_ID_MAX = 200


def secret_key(secret: str) -> bytes:
    """The HMAC key in a `whsec_<base64>` secret. Strict: bad base64 or a key shorter than
    MIN_SECRET_BYTES is refused (a lenient decode of 'whsec_' gives b'', which signs
    anything)."""
    try:
        key = base64.b64decode(secret.removeprefix("whsec_"), validate=True)
    except (binascii.Error, ValueError) as err:
        raise ValueError("AGENTMAIL_WEBHOOK_SECRET is not whsec_<base64>") from err
    if len(key) < MIN_SECRET_BYTES:
        raise ValueError(f"AGENTMAIL_WEBHOOK_SECRET decodes to under {MIN_SECRET_BYTES} bytes")
    return key


def verify_svix(secret: str, headers: dict[str, str], body: bytes, *,
                now: float | None = None, tolerance: int = SVIX_TOLERANCE_SECONDS) -> bool:
    """Svix webhook signature: HMAC-SHA256 over `{id}.{timestamp}.{body}` with the base64
    secret after `whsec_`; any `v1,<sig>` in the space-separated header may match."""
    lower = {k.lower(): v for k, v in headers.items()}
    msg_id, stamp, sigs = (lower.get("svix-id"), lower.get("svix-timestamp"),
                           lower.get("svix-signature"))
    if not (secret and msg_id and stamp and sigs):
        return False
    try:
        key = secret_key(secret)
        sent_at = int(stamp)
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent_at) > tolerance:
        return False  # a replay of an old delivery
    signed = f"{msg_id}.{stamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest())
    for part in sigs.split():
        version, _, sig = part.partition(",")
        try:
            given = sig.encode("ascii")
        except UnicodeEncodeError:
            continue  # junk, not a signature (and never a 500)
        if version == "v1" and hmac.compare_digest(given, expected):
            return True
    return False


def _one_line(text: str, limit: int) -> str:
    """A subject is one line: no CR/LF or control characters, at most `limit` chars."""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", text).strip()[:limit]


def _headers_of(msg: dict[str, Any]) -> dict[str, str]:
    raw = msg.get("headers")
    if isinstance(raw, dict):
        return {str(k).lower(): str(v) for k, v in raw.items()}
    if isinstance(raw, list):  # [{name, value}] form
        return {str(h.get("name", "")).lower(): str(h.get("value", ""))
                for h in raw if isinstance(h, dict)}
    return {}


class AgentMailMail:
    provider = "agentmail"

    def __init__(self, api_key: str, *, inboxes: dict[str, str], data_dir: Path,
                 base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None,
                 timeout: httpx.Timeout | float = _http.TIMEOUT) -> None:
        if not api_key:
            raise NotConfigured("AGENTMAIL_API_KEY is not set")
        self._inboxes = {name: addr.strip() for name, addr in inboxes.items() if addr.strip()}
        self._client = httpx.Client(base_url=base_url.rstrip("/"), transport=transport,
                                    timeout=timeout,
                                    headers={"Authorization": f"Bearer {api_key}",
                                             "User-Agent": "u-tax-gateway"})
        self._index = _KeyedJson(data_dir / "agentmail" / "sent.json")
        self._eml = LocalMail(data_dir)  # .eml files and dev injects parse as before

    # ------------------------------------------------------------------ send

    def send(self, mail: OutMail, key: str) -> SentMail:
        if "@" not in mail.to:
            raise Rejected("bad recipient (not an email address)")
        inbox = self._inboxes.get(mail.inbox) or self._inboxes.get("clients")
        if not inbox:
            raise NotConfigured("MAIL_INBOX_CLIENTS is not set (the sending inbox)")
        subject = _one_line(mail.subject, SUBJECT_MAX)
        # the same gateway key with a different mail (a reset event log) is a new send
        send_key = f"{key}|{_http.fingerprint(inbox, mail.to, subject, mail.body, mail.bcc)}"
        known = self._index.get(send_key)
        if known:
            return SentMail(message_id=known)
        response = _http.call(self.provider, lambda: self._client.post(
            f"/v0/inboxes/{quote(inbox, safe='@')}/messages/send",
            json={"to": [mail.to], "subject": subject, "text": mail.body,
                  **({"bcc": [mail.bcc]} if mail.bcc else {})},
            headers={"Idempotency-Key": _http.safe_key(send_key)}))
        message_id = (response.json() or {}).get("message_id")
        if not message_id:
            raise Rejected("agentmail answered without a message_id")
        self._index.put(send_key, message_id)
        return SentMail(message_id=message_id)

    # ------------------------------------------------------------------ inbound

    def inbox_of(self, payload: dict[str, Any]) -> str | None:
        """Which of our inboxes a webhook's message landed in: "clients", "hiring", or
        None for any other inbox in the organization (never ours to answer)."""
        msg = payload.get("message")
        inbox_id = str((msg.get("inbox_id") if isinstance(msg, dict) else "") or "").lower()
        for name in ("hiring", "clients"):
            if inbox_id and inbox_id == self._inboxes.get(name, "").lower():
                return name
        return None

    def parse_inbound(self, raw: bytes | str | dict[str, Any]) -> InMail | None:
        if not (isinstance(raw, dict) and "event_type" in raw):
            return self._eml.parse_inbound(raw)
        if raw.get("event_type") != RECEIVED:
            return None  # sent/delivered/bounced...: not a client's mail
        msg = raw.get("message")
        if not isinstance(msg, dict):
            return None
        sender = parseaddr(str(msg.get("from") or ""))[1]
        message_id = str(msg.get("message_id") or "")
        if not sender or "@" not in sender or not message_id:
            return None
        if len(message_id) > MESSAGE_ID_MAX:  # sender-chosen; keep store keys short
            message_id = "am-" + hashlib.sha256(message_id.encode()).hexdigest()[:40]
        if msg.get("text") is None and msg.get("html") is None:
            msg = {**msg, **self._fetch(msg, str(msg.get("message_id") or ""))}
        body = msg.get("text")
        if not body and msg.get("html"):
            from app.gateway.masking import html_to_text

            body = html_to_text(str(msg["html"]))
        if not body and msg.get("extracted_text"):
            body = msg.get("extracted_text")
        headers = _headers_of(msg)
        auto = (headers.get("auto-submitted", "no").strip().lower() not in ("", "no")
                or "x-autoreply" in headers or "x-autorespond" in headers
                or headers.get("precedence", "").strip().lower() in ("auto_reply", "bulk",
                                                                     "junk"))
        return InMail(message_id=message_id, sender=sender,
                      subject=str(msg.get("subject") or ""), body=str(body or "").strip(),
                      auto_reply=auto)

    def _fetch(self, msg: dict[str, Any], message_id: str) -> dict[str, Any]:
        """A body over 64 KiB: GET it, only from one of our own inboxes. A permanent 4xx
        (deleted, gone) opens the case without a body (it is asked for), never a 27-hour
        retry; a transient failure raises Unavailable so the hook answers 503."""
        inbox_id = str(msg.get("inbox_id") or "")
        if self.inbox_of({"message": msg}) is None:
            return {}
        try:
            response = _http.call(self.provider, lambda: self._client.get(
                f"/v0/inboxes/{quote(inbox_id, safe='@')}/messages/{quote(message_id, safe='')}"))
        except Rejected:
            log.warning("agentmail: body of a received message could not be fetched (4xx)")
            return {}
        data = response.json() or {}
        if not isinstance(data, dict):
            return {}
        return {k: data.get(k) for k in ("text", "html", "extracted_text", "headers", "subject")
                if k in data}


class AgentMailCalendar:
    """ADAPTER_CALENDAR=agentmail (design D27). The review call is an event on the clients
    inbox's own calendar, and AgentMail emails the client a standard iCalendar invitation
    (`send_invites: true`; Google, Outlook and Apple show it as a meeting). Documented
    route: `POST /v0/inboxes/{inbox_id}/calendar/events` with `{title, start, end,
    timezone, attendees, send_invites, client_id}`; a retry with the same `client_id` and
    body returns the original event (200, no second invitation, not charged again).
    409 `race_condition` is retryable; 409 `idempotency_conflict` is not. US region only.
    The CEO's approval gate stays in perform() (calendar_invite trust)."""

    provider = "agentmail"

    def __init__(self, api_key: str, *, inbox: str, data_dir: Path, base_url: str = BASE_URL,
                 location: str = "",
                 transport: httpx.BaseTransport | None = None,
                 timeout: httpx.Timeout | float = _http.TIMEOUT) -> None:
        if not api_key:
            raise NotConfigured("AGENTMAIL_API_KEY is not set")
        if not inbox:
            raise NotConfigured("MAIL_INBOX_CLIENTS is not set (the calendar's inbox)")
        self.inbox = inbox.strip()
        self.location = location.strip()
        self._client = httpx.Client(base_url=base_url.rstrip("/"), transport=transport,
                                    timeout=timeout,
                                    headers={"Authorization": f"Bearer {api_key}",
                                             "User-Agent": "u-tax-gateway"})
        self._index = _KeyedJson(data_dir / "agentmail" / "calendar.json")

    def book(self, slot: Slot, invitee: str, title: str, key: str) -> Booking:
        if not isinstance(invitee, str) or "@" not in invitee:
            raise Rejected("bad invitee (not an email address)")
        title = _one_line(title, SUBJECT_MAX)
        # a reused gateway key for another booking (a reset event log) is a new event
        book_key = f"{key}|{_http.fingerprint(self.inbox, invitee, slot.start, slot.minutes, title)}"
        known = self._index.get(book_key)
        if known:
            return Booking(event_id=known)
        start = datetime.fromisoformat(slot.start.replace("Z", "+00:00")).astimezone(timezone.utc)
        end = start + timedelta(minutes=slot.minutes)
        fmt = "%Y-%m-%dT%H:%M:%S"
        body = {"title": title, "start": start.strftime(fmt), "end": end.strftime(fmt),
                "timezone": "UTC", "attendees": [{"email": invitee, "role": "required"}],
                "send_invites": True, "client_id": _http.safe_key(book_key),
                "description": f"Your {slot.minutes}-minute U-Tax review of your Delaware "
                               "franchise tax estimate. Educational estimate, not tax advice."}
        if self.location:
            body["location"] = self.location
        try:
            response = _http.call(self.provider, lambda: self._client.post(
                f"/v0/inboxes/{quote(self.inbox, safe='@')}/calendar/events", json=body))
        except Rejected as err:
            code = getattr(err, "provider_code", "")
            if code == "race_condition":  # a concurrent change: the docs say retry
                raise Unavailable(str(err)) from err
            if getattr(err, "status", 0) == 403 and "region" in str(err).lower():
                raise NotConfigured("AgentMail calendar is US-region only "
                                    "(AGENTMAIL_BASE_URL must be api.agentmail.to)") from err
            raise
        data = _http.json_dict(response)
        event = data.get("event") if isinstance(data.get("event"), dict) else data
        # any 2xx is a stored event (and a sent invitation): never BLOCK a booked case
        event_id = str(event.get("event_id") or f"agentmail-{body['client_id']}")
        self._index.put(book_key, event_id)
        return Booking(event_id=event_id)
