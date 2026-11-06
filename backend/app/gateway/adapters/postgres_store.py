"""Postgres event store (Render Postgres, connected Saturday). Schema: db/001_events.sql.

Uses psycopg 3 (imported lazily so local dev needs no driver). A batch is one
transaction; `ON CONFLICT (idempotency_key) DO NOTHING` makes a repeated key a no-op.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.gateway.events import Event, NewEvent, iso_ts
from app.gateway.ports.store import Appended, EventWriteError

_COLUMNS = ("id, ts, type, actor, case_id, provider, status, payload, tokens_in, tokens_out, "
            "cost_usd, idempotency_key, private")


def _row_to_event(row: tuple[Any, ...]) -> Event:
    (id_, ts, type_, actor, case_id, provider, status, payload, tin, tout, cost, key,
     private) = row
    return Event(
        id=int(id_), ts=iso_ts(ts) if isinstance(ts, datetime) else str(ts), type=type_,
        actor=actor, case_id=case_id, provider=provider, status=status,
        payload=payload if isinstance(payload, dict) else json.loads(payload),
        tokens_in=tin, tokens_out=tout,
        cost_usd=None if cost is None else f"{Decimal(cost):.4f}",
        idempotency_key=key,
        private=private if isinstance(private, (dict, type(None))) else json.loads(private),
    )


class PostgresEventStore:
    name = "postgres"

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise EventWriteError("STORE=postgres needs `pip install psycopg[binary]`") from exc
        self._dsn = dsn
        self._lock = threading.Lock()
        self._conn = None
        self._connect()

    WRITER_LOCK = 0x7574_6178  # "utax": one writing gateway per database

    def _connect(self):
        import psycopg

        try:
            conn = psycopg.connect(self._dsn, autocommit=False)
            # Single writer (design): projections live in this process's memory, so a
            # second instance (overlapping deploy, --workers 2) would fork case numbers.
            with conn.cursor() as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (self.WRITER_LOCK,))
                (got,) = cur.fetchone()
            conn.commit()
        except psycopg.Error as exc:
            raise EventWriteError(f"postgres unavailable: {exc}") from exc
        if not got:
            conn.close()
            raise EventWriteError("another U-Tax gateway holds the writer lock on this database")
        self._conn = conn
        return self._conn

    def _drop(self) -> None:
        """Close a broken connection (don't leak it); the next call reconnects."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - it is already broken
                pass
        self._conn = None

    def _cursor(self):
        if self._conn is None or self._conn.closed:
            self._connect()
        return self._conn.cursor()

    def ensure_writable(self) -> None:
        import psycopg

        with self._lock:
            try:
                with self._cursor() as cur:
                    cur.execute("SELECT 1")
                self._conn.rollback()
            except psycopg.Error as exc:
                self._drop()
                raise EventWriteError(f"postgres unavailable: {exc}") from exc

    def append_batch(self, events: list[NewEvent], *, ts: str) -> list[Appended]:
        import psycopg
        from psycopg.types.json import Jsonb

        with self._lock:
            results: list[Appended] = []
            try:
                with self._cursor() as cur:
                    for new in events:
                        cur.execute(
                            f"INSERT INTO events (ts, type, actor, case_id, provider, status, "
                            f"payload, tokens_in, tokens_out, cost_usd, idempotency_key, private) "
                            f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                            f"ON CONFLICT (idempotency_key) DO NOTHING RETURNING {_COLUMNS}",
                            (new.ts or ts, new.type, new.actor, new.case_id, new.provider,
                             new.status, Jsonb(new.payload), new.tokens_in, new.tokens_out,
                             new.cost_usd, new.idempotency_key,
                             None if new.private is None else Jsonb(new.private)),
                        )
                        row = cur.fetchone()
                        if row is not None:
                            results.append(Appended(_row_to_event(row)))
                            continue
                        cur.execute(f"SELECT {_COLUMNS} FROM events WHERE idempotency_key = %s",
                                    (new.idempotency_key,))
                        results.append(Appended(_row_to_event(cur.fetchone()), duplicate=True))
                self._conn.commit()
            except psycopg.Error as exc:
                try:
                    self._conn.rollback()
                except Exception:  # noqa: BLE001 - connection may already be gone
                    self._drop()
                raise EventWriteError(f"event write failed: {exc}") from exc
            return results

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[Event]:
        import psycopg

        with self._lock:
            try:
                with self._cursor() as cur:
                    cur.execute(sql, params)
                    rows = cur.fetchall()
                self._conn.rollback()
            except psycopg.Error as exc:
                self._drop()
                raise EventWriteError(f"postgres read failed: {exc}") from exc
        return [_row_to_event(r) for r in rows]

    def since(self, after_id: int = 0, limit: int | None = None) -> Iterator[Event]:
        sql = f"SELECT {_COLUMNS} FROM events WHERE id > %s ORDER BY id"
        params: tuple[Any, ...] = (after_id,)
        if limit is not None:
            sql += " LIMIT %s"
            params = (after_id, limit)
        return iter(self._query(sql, params))

    def find_by_key(self, idempotency_key: str) -> Event | None:
        rows = self._query(f"SELECT {_COLUMNS} FROM events WHERE idempotency_key = %s",
                           (idempotency_key,))
        return rows[0] if rows else None

    def last_id(self) -> int:
        import psycopg

        with self._lock:
            try:
                with self._cursor() as cur:
                    cur.execute("SELECT COALESCE(MAX(id), 0) FROM events")
                    (value,) = cur.fetchone()
                self._conn.rollback()
            except psycopg.Error as exc:
                self._drop()
                raise EventWriteError(f"postgres read failed: {exc}") from exc
        return int(value)

    def export_jsonl(self, path: str, *, include_private: bool = False) -> int:
        events = list(self.since(0))
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            for e in events:
                rec = e.record() if include_private else e.public()
                fh.write(json.dumps(rec, ensure_ascii=False, separators=(",", ":")) + "\n")
        return len(events)
