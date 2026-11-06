"""EventStorePort contract: JSONL tonight; Postgres when UTAX_TEST_DATABASE_URL is set.

Point it at a throwaway database: the suite creates the schema (db/001_events.sql) and
truncates nothing, so each run uses fresh idempotency keys.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.gateway.adapters.jsonl_store import JsonlEventStore
from app.gateway.events import NewEvent
from app.gateway.ports.store import EventWriteError
from app.gateway.settings import REPO_ROOT

TS = "2026-11-07T21:14:00.000Z"


def _jsonl(tmp_path):
    return JsonlEventStore(tmp_path / "events.jsonl"), lambda: JsonlEventStore(tmp_path / "events.jsonl")


def _postgres(tmp_path):
    dsn = os.environ.get("UTAX_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("UTAX_TEST_DATABASE_URL not set")
    psycopg = pytest.importorskip("psycopg")
    from app.gateway.adapters.postgres_store import PostgresEventStore

    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute((REPO_ROOT / "db" / "001_events.sql").read_text(encoding="utf-8"))
    return PostgresEventStore(dsn), lambda: PostgresEventStore(dsn)


STORES = {"jsonl": _jsonl, "postgres": _postgres}


@pytest.fixture(params=list(STORES))
def store(request, tmp_path):
    return STORES[request.param](tmp_path)


def status_event(n: int, key: str | None = None) -> NewEvent:
    return NewEvent(type="case.status", actor="system", case_id=f"case_{n}",
                    payload={"from": "NEW", "to": "INTAKE_PENDING"}, idempotency_key=key,
                    private={"secret": n})


def test_append_then_since_in_order(store):
    s, _ = store
    start = s.last_id()
    appended = s.append_batch([status_event(1), status_event(2), status_event(3)], ts=TS)
    ids = [a.event.id for a in appended]
    assert ids == sorted(ids) and ids[0] > start
    got = [e.id for e in s.since(start)]
    assert got == ids
    assert [e.id for e in s.since(ids[0])] == ids[1:]
    assert [e.id for e in s.since(start, limit=2)] == ids[:2]


def test_duplicate_key_is_a_no_op(store):
    s, _ = store
    key = f"memo.sent:case_7:v1:{uuid.uuid4()}"
    first = s.append_batch([status_event(7, key)], ts=TS)[0]
    again = s.append_batch([status_event(7, key)], ts=TS)[0]
    assert not first.duplicate and again.duplicate
    assert again.event.id == first.event.id
    assert s.find_by_key(key).id == first.event.id


def test_private_and_key_survive_but_stay_out_of_public(store):
    s, reopen = store
    key = f"k:{uuid.uuid4()}"
    event = s.append_batch([status_event(9, key)], ts=TS)[0].event
    again = next(e for e in reopen().since(event.id - 1) if e.id == event.id)
    assert again.private == {"secret": 9}
    assert again.idempotency_key == key
    assert "private" not in again.public() and "idempotency_key" not in again.public()


def test_restart_reads_the_same_log(store):
    s, reopen = store
    s.append_batch([status_event(1), status_event(2)], ts=TS)
    before = [e.public() for e in s.since(0)]
    assert [e.public() for e in reopen().since(0)] == before


def test_export_round_trips(store, tmp_path):
    s, _ = store
    s.append_batch([status_event(1), status_event(2)], ts=TS)
    path = tmp_path / "export.jsonl"
    n = s.export_jsonl(str(path))
    replay = JsonlEventStore(path)
    assert n == len(list(replay.since(0)))
    assert [e.public() for e in replay.since(0)] == [e.public() for e in s.since(0)]


# ---------------------------------------------------------------- JSONL specifics


def test_jsonl_write_failure_raises_and_appends_nothing(tmp_path, monkeypatch):
    s = JsonlEventStore(tmp_path / "events.jsonl")
    s.append_batch([status_event(1)], ts=TS)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(type(s.path), "open", boom)
    with pytest.raises(EventWriteError):
        s.append_batch([status_event(2), status_event(3)], ts=TS)
    with pytest.raises(EventWriteError):
        s.ensure_writable()
    monkeypatch.undo()
    assert [e.case_id for e in s.since(0)] == ["case_1"]
    assert s.last_id() == 1


def test_jsonl_torn_last_line_is_skipped(tmp_path):
    path = tmp_path / "events.jsonl"
    s = JsonlEventStore(path)
    s.append_batch([status_event(1)], ts=TS)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": 2, "ts": "2026-11-07T21:1')  # crash mid-write
    reopened = JsonlEventStore(path)
    assert reopened.last_id() == 1
    reopened.append_batch([status_event(3)], ts=TS)
    assert [e.id for e in JsonlEventStore(path).since(0)] == [1, 2]
