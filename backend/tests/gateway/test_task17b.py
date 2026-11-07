"""Task 17b: the buy_tool money items left open when task 17 was timeboxed."""

from __future__ import annotations

from decimal import Decimal

from app.gateway.ports.store import EventWriteError

from .test_task10 import monid
from .test_task10_review import buy
from .test_task17 import crash_left_intent, lost, unsure_events


def resolve(h, **args):
    return h.gw.perform("resolve_purchase", {"key": "k1", **args})


def test_a_replayed_resolve_never_settles_a_newer_unchecked_episode(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)  # episode 0: unsure
    assert resolve(h, charged=False).status == "done"  # the CEO checked it: not charged
    buy(h.gw)  # episode 1: unsure again, nobody has checked it yet
    replay = resolve(h, charged=False)  # the first request again (a client retry)
    assert replay.status == "refused" and replay.error["code"] == "wrong_status"
    assert "episode: 1" in replay.error["message"]
    assert len(unsure_events(h, "k1")) == 2 and "buy:k1" in h.gw.ports.toolbuy._unsure
    checked = resolve(h, charged=True, cost_usd="0.0300", episode=1)  # checked, named
    assert checked.status == "done" and checked.data["episode"] == 1
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0300")}


def test_a_leftover_recorded_by_a_resolve_is_checked_before_it_is_settled(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    assert resolve(h, charged=False).status == "done"
    crash_left_intent(h, key="k1")  # a crash left a second, unrecorded attempt
    replay = resolve(h, charged=False)
    assert replay.status == "refused" and "episode: 1" in replay.error["message"]
    assert len(unsure_events(h, "k1")) == 2  # recorded for the CEO, not settled
    assert resolve(h, charged=False, episode=1).status == "done"


def test_an_episode_that_is_not_a_whole_number_is_bad_input(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    for bad in (-1, "1", True, 1.5):
        assert resolve(h, charged=False, episode=bad).error["code"] == "bad_input", bad


def test_a_charge_whose_commit_ack_was_lost_still_reaches_the_spend(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    real = h.gw.store.append_batch

    def committed_then_lost(events, **kw):
        out = real(events, **kw)
        if any(e.type == "cost.booked" for e in events):
            raise EventWriteError("commit reply lost")
        return out

    h.gw.store.append_batch = committed_then_lost
    assert resolve(h, charged=True, cost_usd="0.0400").status == "error"
    h.gw.store.append_batch = real
    h.new_case()  # the firm moves on: newer events land before the retry
    again = resolve(h, charged=True, cost_usd="0.0400")
    assert again.error["code"] == "wrong_status"  # once only
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0400")}  # booked, once
    resolve(h, charged=True, cost_usd="0.0400")
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0400")}


def commit_then_lose(h, event_type):
    """The store keeps the next batch holding `event_type`, and the reply is lost."""
    real = h.gw.store.append_batch

    def append(events, **kw):
        out = real(events, **kw)
        if any(e.type == event_type for e in events):
            h.gw.store.append_batch = real
            raise EventWriteError("commit reply lost")
        return out

    h.gw.store.append_batch = append


def test_a_lost_not_charged_answer_never_leaves_a_phantom_hold(h):
    import httpx

    from ..adapters.test_monid import completed

    calls = {"n": 0}

    def once_lost(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("gone", request=request)
        return httpx.Response(200, json=completed())

    h.gw.ports.toolbuy = monid(once_lost)
    buy(h.gw)  # unsure
    commit_then_lose(h, "tool.bought")
    assert resolve(h, charged=False).status == "error"  # logged, the reply lost
    assert "buy:k1" in h.gw.ports.toolbuy._unsure  # the adapter's hold stayed
    retry = buy(h.gw)  # the agent retries before anyone resends the resolve
    assert retry.status == "done" and retry.data["bought"] is True
    assert len(unsure_events(h, "k1")) == 1


def test_a_charge_under_a_hundredth_of_a_cent_is_booked_as_one(h):
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    r = resolve(h, charged=True, cost_usd="0.00004")
    assert r.status == "done" and r.data["charged"] is True
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0001")}


def test_a_leftover_intent_of_a_logged_purchase_is_not_held_by_the_process(h):
    h.gw.ports.toolbuy = monid()
    assert buy(h.gw).status == "done"  # bought and logged
    crash_left_intent(h, key="k1")  # its intent survived (the clear failed)
    r = resolve(h, charged=False)
    assert r.error["code"] == "not_found" and "already bought" in r.error["message"]
    assert h.gw._buy_intents.get("buy:k1") is None and not unsure_events(h, "k1")


def test_an_intents_file_that_is_not_an_object_never_stops_the_boot(make_harness):
    h = make_harness()
    h.gw.settings.data_dir.mkdir(parents=True, exist_ok=True)
    (h.gw.settings.data_dir / "toolbuy-intents.json").write_text("[1, 2]", encoding="utf-8")
    h.restart()  # used to crash: list has no .items()
    assert h.gw.state.last_event_id >= 0


def test_the_owner_resolves_a_newer_episode_over_http(make_harness):
    from fastapi.testclient import TestClient

    from app.main import create_app

    h = make_harness(admin_token="a" * 32)
    h.gw.ports.toolbuy = monid(lost)
    buy(h.gw)
    resolve(h, charged=False)
    buy(h.gw)  # episode 1
    auth = {"Authorization": "Bearer " + "a" * 32}
    with TestClient(create_app(h.gw, start_background=False)) as client:
        def post(**body):
            return client.post("/api/dev/resolve-purchase", headers=auth,
                               json={"key": "k1", "charged": False, **body})

        assert post().status_code == 409  # the replay: refused
        r = post(episode=1)
        assert r.status_code == 200 and r.json()["episode"] == 1


def test_a_buy_whose_commit_ack_was_lost_still_reaches_the_spend(h):
    h.gw.ports.toolbuy = monid()
    commit_then_lose(h, "cost.booked")
    assert buy(h.gw).status == "error"  # charged and logged, the reply lost
    h.new_case()  # newer events land before the agent retries
    assert buy(h.gw).data["duplicate"] is True
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0013")}
    assert resolve(h, charged=False).error["code"] == "not_found"
    assert h.gw.state.spent_by_provider == {"monid": Decimal("0.0013")}  # still once
