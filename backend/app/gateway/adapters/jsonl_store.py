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
        lines = raw.decode("utf-8", errors="replace").splitlines()
        last_real = max((i for i, line in enumerate(lines) if line.strip()), default=-1)
        for n, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = Event.from_record(json.loads(line))
            except (ValueError, KeyError, TypeError) as exc:
                if n - 1 == last_real:  # a torn final line from a crash mid-write
                    log.warning("cutting torn last line %d from %s", n, self.path)
                    self._cut_torn_tail(raw)
                    continue
                # corruption in the middle would silently change every projection
                raise EventWriteError(f"{self.path}: unreadable event at line {n}") from exc
            if self._events and event.id <= self._events[-1].id:
                raise EventWriteError(f"{self.path}: ids not increasing at line {n}")
            self._events.append(event)
            if event.idempotency_key:
                self._by_key[event.idempotency_key] = event

    def _cut_torn_tail(self, raw: bytes) -> None:
        """Truncate the file to the end of the last complete line, so the torn fragment
        never ends up in the middle of the log once new events are appended."""
        body = raw.rstrip()  # also drops whitespace-only trailing lines
        keep = body.rfind(b"\n") + 1  # 0 when the torn line is the only line
        try:
            with self.path.open("r+b") as fh:
                fh.truncate(keep)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise EventWriteError(f"cannot repair torn tail of {self.path}: {exc}") from exc
        self._needs_newline = False

    def close(self) -> None:
        """Nothing to release: each append opens and closes the file."""

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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        size = self.path.stat().st_size if self.path.exists() else 0
        try:
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            # All or nothing: cut back to the old end so a torn or unsynced batch can't
            # merge with the next one or come back to life with reused ids.
            try:
                with self.path.open("r+b") as fh:
                    fh.truncate(size)
                    fh.flush()
                    os.fsync(fh.fileno())
            except OSError:
                log.error("could not roll back %s after a failed write", self.path)
                self._needs_newline = True  # never glue the next batch onto a torn line
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

    def export_jsonl(self, path: str, *, include_private: bool = False) -> int:
        """Replay export. Public envelopes by default (safe for the repo's replay/ folder);
        `include_private=True` only for a full backup that stays off git."""
        events = list(self.since(0))
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="\n") as fh:
            for e in events:
                rec = e.record() if include_private else e.public()
                fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        return len(events)
