"""D42 M3: the autopilot safety net. A case nothing moved for 45 s gets its next deterministic
step from the tick, through the tool's own handler as that seat, marked via: "autopilot"."""

from __future__ import annotations

from .conftest import HAPPY_QUOTES

TICK = 46  # seconds between ticks in these tests (just over the 45 s rule)


def ready(h) -> str:
    case_id = h.new_case()
    h.ok("extract_facts", case_id=case_id, quotes=HAPPY_QUOTES, incorporated_delaware=True,
         foreign_owner_25=False)
    assert h.gw.state.cases[case_id].status == "READY"
    return case_id


def tick(h, seconds=TICK):
    h.clock.advance(seconds=seconds)
    return h.gw.drive_cases()


def test_1_ready_for_45_s_is_computed_by_the_autopilot(h):
    case_id = ready(h)
    assert tick(h, 30) == []  # not 45 s yet
    assert tick(h, 16) == [f"{case_id}:compute_tax"]
    [computed] = h.events("tax.computed")
    assert computed.actor == "tax" and computed.payload["via"] == "autopilot"
    assert "via" not in h.gw.state.cases[case_id].result  # the result QA re-checks is clean
    status = [e for e in h.events("case.status") if e.payload["to"] == "COMPUTED"][-1]
    assert status.payload["via"] == "autopilot"


def test_2_the_chain_reaches_the_memo_within_4_ticks(h):
    h.ok("set_trust", action_type="email", state="AUTO")
    case_id = ready(h)
    ran = [step for _ in range(4) for step in tick(h)]
    assert ran == [f"{case_id}:compute_tax", f"{case_id}:qa_check",
                   f"{case_id}:prepare_documents", f"{case_id}:send_memo"]
    [memo] = h.events("memo.sent")
    assert memo.actor == "acct" and memo.payload["via"] == "autopilot"
    assert h.gw.state.cases[case_id].status == "SENT"
    assert tick(h) == [] and tick(h) == []  # nothing left to do


def test_3_an_agent_that_already_computed_gets_no_duplicate(h):
    case_id = ready(h)
    h.clock.advance(seconds=40)
    h.gw.drive_cases()  # seen READY
    h.gw.perform("compute_tax", {"case_id": case_id}, actor="tax")  # the agent, meanwhile
    assert tick(h, 10) == []  # it moved: 45 s from the new status, and never compute again
    assert len(h.events("tax.computed")) == 1
    assert "via" not in h.events("tax.computed")[0].payload  # the agent's own step


def test_4_a_pending_approval_holds_the_case(h):
    case_id = h.to_qa_passed()  # documents prepared; email is ASK
    first = h.do("send_memo", case_id=case_id)
    assert first.status == "needs_approval"
    for _ in range(3):
        assert tick(h) == []
    assert h.events("memo.sent") == []


def test_4b_paused_email_or_a_flagged_case_holds_it(h):
    case_id = ready(h)
    h.ok("set_trust", action_type="email", state="PAUSED")
    assert tick(h, 120) == []
    h.ok("set_trust", action_type="email", state="RESUME")
    assert tick(h) == [f"{case_id}:compute_tax"]


def test_5_autopilot_0_does_nothing(make_harness):
    h = make_harness(autopilot=False)
    ready(h)
    assert tick(h, 600) == [] and h.events("tax.computed") == []


def test_the_setting_and_the_tick(monkeypatch):
    from app import main
    from app.gateway.settings import Settings

    monkeypatch.setenv("AUTOPILOT", "0")
    assert Settings.from_env().autopilot is False
    monkeypatch.delenv("AUTOPILOT")
    assert Settings.from_env().autopilot is True
    called = []

    class Gw:
        def __getattr__(self, name):
            return lambda *a, **k: called.append(name)

    main.tick(Gw())
    assert "drive_cases" in called
