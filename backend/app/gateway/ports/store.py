"""EventStorePort: append-only, monotonic ids, fail closed.

`append_batch` is atomic: all events land or none do. An event whose idempotency key
already exists is not written again; the stored event comes back with `duplicate=True`.
Any write failure raises EventWriteError, and the caller must not take the action.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from app.gateway.events import Event, NewEvent


class EventWriteError(RuntimeError):
    """The store could not write. The action must not be taken (fail closed)."""


@dataclass(frozen=True)
class Appended:
    event: Event
    duplicate: bool = False


class EventStorePort(Protocol):
    name: str

    def ensure_writable(self) -> None:
        """Raise EventWriteError if an append would fail now. Called before side effects."""

    def append_batch(self, events: list[NewEvent], *, ts: str) -> list[Appended]: ...

    def since(self, after_id: int = 0, limit: int | None = None) -> Iterator[Event]:
        """Events with id > after_id, ordered by id."""

    def find_by_key(self, idempotency_key: str) -> Event | None: ...

    def last_id(self) -> int: ...

    def export_jsonl(self, path: str, *, include_private: bool = False) -> int:
        """One JSON object per line: public envelopes, or full records for a private
        backup. Returns the count."""
