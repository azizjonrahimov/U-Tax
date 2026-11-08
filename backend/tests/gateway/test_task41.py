"""Task 41 (the frontend's D34 review): stop first, per-turn actions, STOP wins over every
start tool of a running turn, and check() reads pending under the lock."""

from __future__ import annotations

import asyncio

import pytest

from app.ceo_talk import STILL_WORKING

from .test_task33 import FakeAssistant, talker


def direct(h, name, **args):
    return h.gw.perform("ceo_direct", {"name": name, "args": args})


# ---------------------------------------------------------------- 1. stop first


def test_a_stop_with_an_unknown_or_forgotten_conversation_still_stops(h):
    fake = FakeAssistant(h.gw, [])
    talk = talker(h, fake)
    out = asyncio.run(talk.talk("sid_1", "stop posting", "conv_gone_after_restart"))
    assert h.gw.state.trust["social_post"] == "PAUSED"
    assert out["actions"][0]["tool"] == "pause" and fake.messages == []


def test_an_unknown_conversation_id_starts_a_new_conversation_not_a_404(h):
    fake = FakeAssistant(h.gw, ["Hello."])
    talk = talker(h, fake)
    out = asyncio.run(talk.talk("sid_1", "hi", "conv_someone_elses"))
    assert out["say"] == "Hello." and out["conversation_id"] != "conv_someone_elses"


# ---------------------------------------------------------------- 2. per-turn actions


def test_a_second_turn_never_wipes_the_first_turns_actions(h):
    h.gw.perform("ceo_turn", {"conversation_id": "conv_a", "text": "pause posting"})
    first = direct(h, "pause", action_type="social_post")
    h.gw.perform("ceo_turn", {"conversation_id": "conv_a", "text": "and email"})
    reply = h.gw.perform("ceo_reply", {"conversation_id": "conv_a", "turn_id": "turn_1"})
    [action] = reply.data["actions"]
    assert action["tool"] == "pause" and action["undo_id"] == first.data["undo_id"]
    later = h.gw.perform("ceo_reply", {"conversation_id": "conv_a", "turn_id": "turn_2"})
    assert later.data["actions"] == []  # turn 1's action is reported once


def test_a_slow_turns_actions_reach_the_ceo_when_the_next_turn_starts(h):
    def assistant(text):
        if "auto" in text:
            direct(h, "set_trust", action_type="email", state="AUTO")

    fake = FakeAssistant(h.gw, ["On auto.", "Sure."], on_message=assistant, running_polls=10)
    talk = talker(h, fake, wait=0.0)
    first = asyncio.run(talk.talk("sid_1", "emails on auto"))
    assert first["pending"] is True and first["say"] == STILL_WORKING
    fake.running_polls = 0
    asyncio.run(talk.talk("sid_1", "thanks", first["conversation_id"]))
    replied = h.events("ceo.replied")
    assert [a["tool"] for a in replied[0].payload["actions"]] == ["set_trust"]
    assert all(a["tool"] != "set_trust" for e in replied[1:] for a in e.payload["actions"])


# ---------------------------------------------------------------- 3. STOP wins


@pytest.mark.parametrize("name, args", [
    ("approve", {"approval": "apr_1"}),
    ("deny", {"approval": "apr_1"}),
    ("decide_candidate", {"candidate": "cand_1", "decision": "hire"}),
    ("order", {"text": "advertise the firm"}),
    ("set_trust", {"action_type": "email", "state": "AUTO"}),
    ("set_trust", {"action_type": "calendar_invite", "state": "ASK"}),
    ("unblock_case", {"case": "case_1", "to": "READY"}),
])
def test_every_start_tool_from_a_turn_before_the_stop_is_refused(h, name, args):
    h.gw.perform("ceo_turn", {"conversation_id": "conv_a", "text": "do things"})
    h.gw.perform("ceo_direct", {"name": "pause", "args": {"action_type": "social_post"},
                                "as_ceo": True})  # the CEO said stop meanwhile
    out = direct(h, name, **args)
    assert out.status == "refused" and out.error["code"] == "stopped", out.as_dict()


def test_reads_pause_and_cancel_still_work_after_the_stop(h):
    h.gw.perform("ceo_turn", {"conversation_id": "conv_a", "text": "do things"})
    sent = direct(h, "order", text="advertise the firm")  # before the stop: allowed
    h.gw.perform("ceo_direct", {"name": "pause", "args": {"action_type": "email"},
                                "as_ceo": True})
    assert direct(h, "pause", action_type="social_post").status == "done"
    assert direct(h, "cancel_order", order_id=sent.data["order_id"]).status == "done"
    from app.ceo_tools import REGISTRY

    read = next(n for n, t in REGISTRY.items() if t.kind == "read" and not t.required)
    assert direct(h, read).status == "done"


def test_a_turn_begun_after_the_stop_may_start_things_again(h):
    h.gw.perform("ceo_direct", {"name": "pause", "args": {"action_type": "email"},
                                "as_ceo": True})
    h.gw.perform("ceo_turn", {"conversation_id": "conv_a", "text": "resume email"})
    assert direct(h, "set_trust", action_type="email", state="RESUME").status == "done"


def test_an_older_open_turn_still_wins_over_a_newer_one(h):
    """Naive runs the session's queued messages in order: while turn 1 is open, the
    assistant's calls are turn 1's, so a newer turn never unlocks them."""
    h.gw.perform("ceo_turn", {"conversation_id": "conv_a", "text": "turn 1"})
    h.gw.perform("ceo_direct", {"name": "pause", "args": {"action_type": "email"},
                                "as_ceo": True})
    h.gw.perform("ceo_turn", {"conversation_id": "conv_a", "text": "turn 2"})
    out = direct(h, "set_trust", action_type="email", state="RESUME")
    assert out.status == "refused" and out.error["code"] == "stopped"


# ---------------------------------------------------------------- 4. check() and the lock


def test_check_reads_pending_under_the_lock(h):
    fake = FakeAssistant(h.gw, ["Brief."], running_polls=10)
    talk = talker(h, fake, wait=0.0)

    async def go():
        first = await talk.talk("sid_1", "brief me")
        await talk._lock.acquire()  # a turn is landing
        task = asyncio.create_task(talk.check("sid_1", first["conversation_id"]))
        await asyncio.sleep(0)
        assert not task.done()  # it waits for the lock before reading pending
        fake.running_polls = 0
        talk._lock.release()
        return await task

    assert asyncio.run(go())["say"] == "Brief."
