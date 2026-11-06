"""AgentMail mail adapter (ADAPTER_MAIL=agentmail).

Built against the documented REST API (docs.agentmail.to, read 2026-11-07):
- send: `POST /v0/inboxes/{inbox_id}/messages/send` with `Authorization: Bearer <key>`,
  JSON `{to: [..], subject, text}`, answer `{message_id, thread_id}`. The inbox id is the
  inbox's address. `Idempotency-Key` (1..256 of [A-Za-z0-9-._~], kept 24 h) makes a retry
  return the first message id and send no second mail; a local index keeps that promise
  past 24 h and across restarts.
- inbound: the `message.received` webhook, Svix-signed (`svix-id`, `svix-timestamp`,
  `svix-signature: v1,<base64>`; secret `whsec_<base64>`), deduped on `event_id`
  (delivery is at least once). Bodies over 64 KiB arrive without text/html and with a
  `body_url`; the adapter then fetches the message (`GET /v0/inboxes/{inbox}/messages/{id}`).
Real calls are UNVERIFIED until a key exists; the MockTransport tests pin the shapes.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from email.utils import parseaddr
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from app.gateway.adapters import _http
from app.gateway.adapters.local import LocalMail, _KeyedJson
from app.gateway.ports import InMail, NotConfigured, OutMail, Rejected, SentMail

BASE_URL = "https://api.agentmail.to"
RECEIVED = "message.received"
SVIX_TOLERANCE_SECONDS = 300


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
        sent_at = int(stamp)
        key = base64.b64decode(secret.removeprefix("whsec_"))
    except ValueError:
        return False
    if abs((now if now is not None else time.time()) - sent_at) > tolerance:
        return False  # a replay of an old delivery
    signed = f"{msg_id}.{stamp}.".encode() + body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in sigs.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


class AgentMailMail:
    provider = "agentmail"

    def __init__(self, api_key: str, *, inboxes: dict[str, str], data_dir: Path,
                 base_url: str = BASE_URL, transport: httpx.BaseTransport | None = None,
                 timeout: float = 10.0) -> None:
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
        known = self._index.get(key)
        if known:
            return SentMail(message_id=known)
        if "@" not in mail.to:
            raise Rejected(f"bad recipient {mail.to!r}")
        inbox = self._inboxes.get(mail.inbox) or self._inboxes.get("clients")
        if not inbox:
            raise NotConfigured("MAIL_INBOX_CLIENTS is not set (the sending inbox)")
        response = _http.call(self.provider, lambda: self._client.post(
            f"/v0/inboxes/{quote(inbox, safe='@')}/messages/send",
            json={"to": [mail.to], "subject": mail.subject, "text": mail.body},
            headers={"Idempotency-Key": _http.safe_key(key)}))
        message_id = (response.json() or {}).get("message_id")
        if not message_id:
            raise Rejected("agentmail answered without a message_id")
        self._index.put(key, message_id)
        return SentMail(message_id=message_id)

    # ------------------------------------------------------------------ inbound

    def inbox_of(self, payload: dict[str, Any]) -> str:
        """Which of our two inboxes a webhook's message landed in (hiring or clients)."""
        inbox_id = str(((payload.get("message") or {}).get("inbox_id")) or "").lower()
        hiring = self._inboxes.get("hiring", "").lower()
        return "hiring" if hiring and inbox_id == hiring else "clients"

    def parse_inbound(self, raw: bytes | str | dict[str, Any]) -> InMail | None:
        if not (isinstance(raw, dict) and "event_type" in raw):
            return self._eml.parse_inbound(raw)
        if raw.get("event_type") != RECEIVED:
            return None  # sent/delivered/bounced...: not a client's mail
        msg = raw.get("message") or {}
        sender = parseaddr(str(msg.get("from") or ""))[1]
        message_id = str(msg.get("message_id") or "")
        if not sender or not message_id:
            return None
        if msg.get("text") is None and msg.get("html") is None and msg.get("inbox_id"):
            msg = {**msg, **self._fetch(str(msg["inbox_id"]), message_id)}  # > 64 KiB body
        body = msg.get("text")
        if not body and msg.get("html"):
            from app.gateway.masking import html_to_text

            body = html_to_text(str(msg["html"]))
        headers = {str(k).lower(): str(v) for k, v in (msg.get("headers") or {}).items()}
        auto = (headers.get("auto-submitted", "no").strip().lower() not in ("", "no")
                or "x-autoreply" in headers or "x-autorespond" in headers
                or headers.get("precedence", "").strip().lower() in ("auto_reply", "bulk",
                                                                     "junk"))
        return InMail(message_id=message_id, sender=sender,
                      subject=str(msg.get("subject") or ""), body=str(body or "").strip(),
                      auto_reply=auto)

    def _fetch(self, inbox_id: str, message_id: str) -> dict[str, Any]:
        response = _http.call(self.provider, lambda: self._client.get(
            f"/v0/inboxes/{quote(inbox_id, safe='@')}/messages/{quote(message_id, safe='')}"))
        data = response.json() or {}
        return {k: data.get(k) for k in ("text", "html", "headers", "subject") if k in data}
