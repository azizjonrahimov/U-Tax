"""Ports: the interfaces sponsors sit behind. Stand-ins today, sponsor adapters Saturday.

Every adapter maps its failures onto the errors below, so `perform()` handles AgentMail,
HubSpot or a local stand-in the same way. The contract suites in tests/contract/ run the
same checks against every adapter of a port.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol


class PortError(RuntimeError):
    """Base for port failures. `retryable` tells perform() whether backoff can help."""

    code = "port_error"
    retryable = False

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message or self.code)
        self.retry_after = retry_after


class RateLimited(PortError):
    code = "rate_limited"
    retryable = True


class Unavailable(PortError):
    code = "unavailable"
    retryable = True


class AuthExpired(PortError):
    code = "auth_expired"


class Rejected(PortError):
    """The provider refused this input (bad address, content policy...)."""

    code = "rejected"


class NotConfigured(PortError):
    code = "not_configured"


class FaultPlan:
    """Test hook: the next calls raise these errors in order (stand-ins only)."""

    def __init__(self, faults: Iterable[PortError] = ()) -> None:
        self._faults = list(faults)

    def add(self, *faults: PortError) -> None:
        self._faults.extend(faults)

    def check(self) -> None:
        if self._faults:
            raise self._faults.pop(0)


# ------------------------------------------------------------------ mail


@dataclass(frozen=True)
class OutMail:
    to: str
    subject: str
    body: str
    in_reply_to: str | None = None


@dataclass(frozen=True)
class InMail:
    message_id: str
    sender: str
    subject: str
    body: str
    auto_reply: bool = False  # Auto-Submitted / X-Autoreply / Precedence: auto_reply


@dataclass(frozen=True)
class SentMail:
    message_id: str


class MailPort(Protocol):
    provider: str

    def send(self, mail: OutMail, key: str) -> SentMail:
        """Send once per key: a repeated key returns the first message id, no second mail."""

    def parse_inbound(self, raw: bytes | str | dict[str, Any]) -> InMail | None:
        """Parse an inbound mail (a .eml file locally, a webhook body Saturday)."""


# ------------------------------------------------------------------ llm


@dataclass(frozen=True)
class LLMRequest:
    system: str
    prompt: str
    model: str | None = None
    max_tokens: int = 512


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: str | None = None  # decimal string when the provider reports it


class LLMPort(Protocol):
    provider: str

    def complete(self, request: LLMRequest) -> LLMResult: ...


# ------------------------------------------------------------------ crm


@dataclass(frozen=True)
class CrmContact:
    key: str  # our stable key, e.g. "case_7" or "cand_1"
    label: str  # masked label; never cap-table numbers
    kind: str = "client"  # client | candidate


class CRMPort(Protocol):
    provider: str

    def upsert_contact(self, contact: CrmContact) -> str:
        """Returns the CRM's contact id; the same key always maps to the same id."""

    def set_stage(self, deal_key: str, stage: str) -> None: ...


# ------------------------------------------------------------------ device


@dataclass(frozen=True)
class DeviceCheck:
    screenshot_url: str
    rendered_ok: bool


class DevicePort(Protocol):
    provider: str

    def open_and_capture(self, mail_id: str, body: str) -> DeviceCheck: ...


# ------------------------------------------------------------------ scrape


@dataclass(frozen=True)
class Page:
    url: str
    html: str
    fetched_at: str
    source: str  # "snapshot" (saved copy) | "apify" (fetched live)


@dataclass(frozen=True)
class SearchHit:
    cite: str
    snippet: str


class ScrapePort(Protocol):
    provider: str

    def fetch_page(self, url: str) -> Page: ...

    def search(self, query: str, limit: int = 3) -> list[SearchHit]: ...


# ------------------------------------------------------------------ toolbuy


@dataclass(frozen=True)
class Purchase:
    result: Any
    cost_usd: str  # decimal string
    bought: bool  # False for the log-only stand-in


class ToolBuyPort(Protocol):
    provider: str

    def buy(self, tool: str, input: dict[str, Any], key: str) -> Purchase: ...


# ------------------------------------------------------------------ voice


@dataclass(frozen=True)
class VoiceSession:
    client_secret: str
    model: str
    expires_at: str


class VoicePort(Protocol):
    provider: str

    def create_session(self, instructions: str) -> VoiceSession:
        """Ephemeral realtime session for /talk, with the instructions (and no tools) set
        server-side when minting. NotConfigured without a key."""

    def speak(self, text: str) -> bytes:
        """TTS audio (audio/mpeg) for the morning brief. NotConfigured without a key."""


# ------------------------------------------------------------------ calendar


@dataclass(frozen=True)
class Slot:
    start: str  # ISO 8601 UTC
    minutes: int = 15


@dataclass(frozen=True)
class Booking:
    event_id: str


class CalendarPort(Protocol):
    provider: str

    def book(self, slot: Slot, invitee: str, title: str, key: str) -> Booking: ...


# ------------------------------------------------------------------ social


@dataclass(frozen=True)
class Post:
    channel: str
    text: str
    image_url: str | None = None


@dataclass(frozen=True)
class Posted:
    url: str


class SocialPort(Protocol):
    provider: str

    def post(self, post: Post, key: str) -> Posted: ...


# ------------------------------------------------------------------ pay


@dataclass(frozen=True)
class PaymentLink:
    url: str


class PayPort(Protocol):
    provider: str | None

    def create_link(self, amount_cents: int, label: str, key: str) -> PaymentLink: ...


@dataclass
class Ports:
    """Everything perform() can act through. The store is separate (events are special)."""

    mail: MailPort
    llm: LLMPort
    crm: CRMPort
    device: DevicePort
    scrape: ScrapePort
    toolbuy: ToolBuyPort
    voice: VoicePort
    calendar: CalendarPort
    social: SocialPort
    pay: PayPort
    extra: dict[str, Any] = field(default_factory=dict)
