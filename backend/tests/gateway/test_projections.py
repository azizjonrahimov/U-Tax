"""Restart -> projections rebuilt identically (Test Plan: event store edge test)."""

import json

from app.gateway.adapters.jsonl_store import JsonlEventStore
from app.gateway.projections import rebuild


def test_restart_rebuilds_identical_state(h):
    case_id = h.to_sent()
    h.ok("voice", text="good work", speaker="ceo")
    h.ok("book_cost", provider="openrouter", model="m", cost_usd="0.0034", agent="intake")
    before = h.gw.state.public()
    h.restart()
    assert h.gw.state.public() == before
    assert h.gw.state.cases[case_id].reply_to == "founder@team7.dev"
    # and the rebuilt gateway keeps the rules: the memo isn't sent again
    assert h.ok("send_memo", case_id=case_id)["duplicate"] is True


def test_export_then_replay_is_identical(h, tmp_path):
    h.to_sent()
    export = tmp_path / "replay.jsonl"
    count = h.store.export_jsonl(str(export))
    assert count == len(list(h.store.since(0)))
    replayed = JsonlEventStore(export)
    assert [e.public() for e in replayed.since(0)] == [e.public() for e in h.store.since(0)]
    assert rebuild(replayed.since(0)).public() == h.gw.state.public()


def test_timestamps_never_go_backwards(h):
    # regression: a frozen clock once stamped memo.sent before the trust.changed it followed
    h.to_sent()
    stamps = [e.ts for e in h.store.since(0)]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)


def test_zero_events_is_an_empty_state(h):
    state = h.gw.state.public()
    assert state["last_event_id"] == 0
    assert state["cases"] == [] and state["firm"]["clients"] == 0
    assert state["trust"] == {"email": "ASK", "social_post": "ASK", "calendar_invite": "ASK",
                              "voice_screen": "ASK"}
    assert state["money"] == {"tax_saved_cents": 0, "spent_usd": "0.00", "spent_by_provider": {}}
    json.dumps(state)  # serializable as-is
