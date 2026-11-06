"""JSONL event store: one full event record per line (local dev; later a Render disk).

Single writer: a lock serializes appends; each batch is one write() + fsync, so a batch
lands whole. A torn final line from a crash is skipped on load and never reused.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Iterator
from pathlib import Path

from app.gateway.events import Event, NewEvent
from app.gateway.ports.store import Appended, EventWriteError

log = logging.getLogger(__name__)


class JsonlEventStore:
    name = "jsonl"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._events: list[Event] = []
        self._by_key: dict[str, Event] = {}
        self._needs_newline = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        self._needs_newline = bool(raw) and not raw.endswith(b"\n")
        for n, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                event = Event.from_record(json.loads(line))
            except (ValueError, KeyError, TypeError):
                log.warning("skipping unreadable event line %d in %s", n, self.path)
                continue
            if self._events and event.id <= self._events[-1].id:
                raise EventWriteError(f"{self.path}: ids not increasing at line {n}")
            self._events.append(event)
            if event.idempotency_key:
                self._by_key[event.idempotency_key] = event

    def ensure_writable(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab"):
                pass
        except OSError as exc:
            raise EventWriteError(f"event store not writable: {exc}") from exc

    def append_batch(self, events: list[NewEvent], *, ts: str) -> list[Appended]:
        with self._lock:
            results: list[Appended] = []
            fresh: list[Event] = []
            next_id = self.last_id() + 1
            batch_keys: dict[str, Event] = {}
            for new in events:
                key = new.idempotency_key
                existing = self._by_key.get(key) if key else None
                existing = existing or (batch_keys.get(key) if key else None)
                if existing is not None:
                    results.append(Appended(existing, duplicate=True))
                    continue
                event = Event.from_new(new, id=next_id, ts=ts)
                next_id += 1
                fresh.append(event)
                if key:
                    batch_keys[key] = event
                results.append(Appended(event))
            if fresh:
                self._write(fresh)
                self._events.extend(fresh)
                self._by_key.update(batch_keys)
            return results

    def _write(self, events: list[Event]) -> None:
        data = "".join(json.dumps(e.record(), ensure_ascii=False, separators=(",", ":")) + "\n"
                       for e in events)
        if self._needs_newline:
            data = "\n" + data
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise EventWriteError(f"event write failed: {exc}") from exc
        self._needs_newline = False

    def since(self, after_id: int = 0, limit: int | None = None) -> Iterator[Event]:
        with self._lock:
            snapshot = [e for e in self._events if e.id > after_id]
        return iter(snapshot[:limit] if limit is not None else snapshot)

    def find_by_key(self, idempotency_key: str) -> Event | None:
        return self._by_key.get(idempotency_key)

    def last_id(self) -> int:
        return self._events[-1].id if self._events else 0

    def export_jsonl(self, path: str) -> int:
        events = list(self.since(0))
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            for e in events:
                fh.write(json.dumps(e.record(), ensure_ascii=False, separators=(",", ":")) + "\n")
        return len(events)
