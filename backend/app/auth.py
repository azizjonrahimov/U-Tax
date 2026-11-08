"""Contract v1.13 §5a-bis (design D33): the CEO signs in with an email and a password.

- `CEO_PASSWORD_HASH` is `scrypt$<salt_b64>$<hash_b64>` (hashlib.scrypt, n=2**14, r=8, p=1,
  32 bytes); `python -m app.devtools set-ceo-password` writes it. Compared in constant time.
- A session is `<payload_b64>.<hmac_b64>`: payload `{"sid", "exp"}`, signed HMAC-SHA256 with
  `UTAX_SESSION_SECRET`, 12 h. Logout puts the sid on an in-memory denylist until it expires.
- Every admin route accepts a session just as it accepts `ADMIN_TOKEN` (scripts keep that).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from typing import Callable

SESSION_SECONDS = 12 * 3600
TICKET_SECONDS = 60  # task 36: a stream ticket opens /api/events/stream once, within 60 s
_N, _R, _P, _LEN = 2 ** 14, 8, 1, 32
# a wrong email still pays a full scrypt check: the reply time never tells which field was wrong
_DUMMY = "scrypt$" + base64.b64encode(b"utax-dummy-salt!").decode() + "$" + base64.b64encode(
    b"\0" * _LEN).decode()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_LEN)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant time; a malformed or empty stored hash never matches (and still costs a
    scrypt run, like a real one)."""
    try:
        scheme, salt_b64, hash_b64 = (stored or _DUMMY).split("$")
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        if scheme != "scrypt" or not salt or len(expected) != _LEN:
            raise ValueError
        ok = stored != ""
    except ValueError:
        scheme, salt, expected, ok = "scrypt", b"utax-dummy-salt!", b"\0" * _LEN, False
    digest = hashlib.scrypt((password or "").encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_LEN)
    return hmac.compare_digest(digest, expected) and ok


class Sessions:
    """Signed, expiring CEO sessions with an in-memory logout denylist."""

    def __init__(self, secret: str, *, clock: Callable[[], float] | None = None) -> None:
        if len(secret) < 32:
            raise ValueError("UTAX_SESSION_SECRET must be at least 32 characters")
        self._key = secret.encode()
        self._clock = clock or (lambda: time.time())  # read at each call (patchable)
        self._revoked: dict[str, float] = {}
        self._used: dict[str, float] = {}  # task 36: redeemed stream tickets, until expiry
        self._lock = threading.Lock()

    def _sign(self, payload: str) -> str:
        return _b64(hmac.new(self._key, payload.encode(), hashlib.sha256).digest())

    def issue(self) -> tuple[str, float]:
        exp = self._clock() + SESSION_SECONDS
        payload = _b64(json.dumps({"sid": secrets.token_urlsafe(12), "exp": int(exp)},
                                  separators=(",", ":")).encode())
        return f"{payload}.{self._sign(payload)}", exp

    def _claims(self, token: str | None) -> dict | None:
        if not token or token.count(".") != 1 or len(token) > 512:
            return None
        payload, signature = token.split(".")
        if not hmac.compare_digest(signature.encode(), self._sign(payload).encode()):
            return None
        try:
            claims = json.loads(_unb64(payload))
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int):
            return None
        if "use" in claims or not claims.get("sid"):
            return None  # task 36: a stream ticket is never a session
        if claims["exp"] <= self._clock():
            return None
        with self._lock:
            if claims.get("sid") in self._revoked:
                return None
        return claims

    def valid(self, token: str | None) -> bool:
        return self._claims(token) is not None

    def sid(self, token: str | None) -> str | None:
        """v1.14: the sign-in a valid session belongs to (one CEO conversation each)."""
        claims = self._claims(token)
        return str(claims["sid"]) if claims else None

    def issue_ticket(self) -> tuple[str, float]:
        """Task 36: a single-use, 60 s ticket that only opens the event stream (EventSource
        can't send headers; a 12 h session in a URL lands in access logs)."""
        exp = self._clock() + TICKET_SECONDS
        payload = _b64(json.dumps({"use": "stream", "tk": secrets.token_urlsafe(12),
                                   "exp": int(exp) + 1}, separators=(",", ":")).encode())
        return f"{payload}.{self._sign(payload)}", exp

    def redeem_ticket(self, ticket: str | None) -> bool:
        """True once per valid, unexpired stream ticket; a session never passes."""
        if not ticket or ticket.count(".") != 1 or len(ticket) > 512:
            return False
        payload, signature = ticket.split(".")
        if not hmac.compare_digest(signature.encode(), self._sign(payload).encode()):
            return False
        try:
            claims = json.loads(_unb64(payload))
        except (ValueError, UnicodeDecodeError):
            return False
        if not isinstance(claims, dict) or claims.get("use") != "stream"                 or not isinstance(claims.get("exp"), int) or claims["exp"] <= self._clock():
            return False
        now = self._clock()
        with self._lock:
            self._used = {t: e for t, e in self._used.items() if e > now}
            if claims.get("tk") in self._used:
                return False  # single use: a ticket replayed from a log opens nothing
            self._used[str(claims.get("tk"))] = float(claims["exp"])
        return True

    def revoke(self, token: str | None) -> bool:
        claims = self._claims(token)
        if claims is None:
            return False
        now = self._clock()
        with self._lock:
            self._revoked = {s: e for s, e in self._revoked.items() if e > now}  # prune
            self._revoked[str(claims["sid"])] = float(claims["exp"])
        return True
