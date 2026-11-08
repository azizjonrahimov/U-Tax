"""D41 B51 (contract v1.20 §10): a CEO order of several tasks is captured whole, merged across
pauses, and run one task at a time across the firm; utax.task_done ends a task; STOP ends them
all; a restart picks up where the log left off. A single order keeps its rehearsed wake."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ceo_tools
from app.gateway.order_queue import TASK_SOFT_S

from .gateway.conftest import Harness


class Firm:
    """A harness with a fake waker and a frozen queue clock."""

    def __init__(self, tmp_path, **kw) -> None:
        self.h = Harness(tmp_path / "firm", **kw)
        self.now = 1_000.0
        self.wakes: list[str] = []
        self._wire()

    def _wire(self) -> None:
        self.h.gw.wall = lambda: self.now
        self.h.gw.on_wake(self.wakes.append)

    def restart(self) -> None:
        self.h.restart()
        self.wakes = []  # the new gateway's wakes only
        self._wire()

    @property
    def gw(self):
        return self.h.gw

    def order(self, text: str) -> str:
        """Propose by tool, confirm by voice: the order_<id>."""
        proposal = self.gw.perform("ceo_tool", {"name": "order", "args": {"text": text}})
        assert proposal.status == "done", proposal.as_dict()
        done = self.confirm(proposal.data["proposal_id"])
        assert done.status == "done", done.as_dict()
        return f"order_{self.h.events('voice.command')[-1].id}"

    def confirm(self, proposal_id: str):
        return self.gw.perform("ceo_confirm", {"proposal_id": proposal_id,
                                               "decision": "confirm", "via": "voice",
                                               "heard": "confirm"})

    def task_done(self, order_id, task_no, status="done", actor="cos", **extra):
        return self.gw.perform("task_done", {"order_id": order_id, "task_no": task_no,
                                             "status": status, **extra}, actor=actor)

    def acted(self, actor: str, order_id: str) -> None:
        """A seat's MCP call for this order (every call writes tool.called)."""
        self.gw.record_tool_call(actor, "make_ad", "done", order_id)

    def done(self, status=None):
        return [(e.payload["task_no"], e.payload["status"])
                for e in self.h.events("order.task_done")
                if status is None or e.payload["status"] == status]

    def started(self):
        return [(e.payload["order_id"], e.payload["task_no"])
                for e in self.h.events("order.task_started")]


@pytest.fixture
def firm(tmp_path) -> Firm:
    return Firm(tmp_path)


THREE = "Advertise the firm, hire 2 people and email our clients"


# ---------------------------------------------------------------- the runbook's 8 cases


def test_1_three_tasks_queue_and_task_1_wakes_the_chief_of_staff(firm):
    oid = firm.order(THREE)
    [queued] = firm.h.events("order.queued")
    assert queued.payload == {"order_id": oid, "of": 3, "tasks": [
        {"no": 1, "text": "Advertise the firm"}, {"no": 2, "text": "Hire 2 people"},
        {"no": 3, "text": "Email our clients"}]}
    assert firm.started() == [(oid, 1)]
    n = oid.removeprefix("order_")
    assert firm.wakes == [f"CEO order_{n} task 1/3: Advertise the firm"]
    command = firm.h.events("voice.command")[-1].payload
    assert command["tasks"] == ["Advertise the firm", "Hire 2 people", "Email our clients"]
    assert command["text"] == "1) Advertise the firm · 2) Hire 2 people · 3) Email our clients"


def test_2_task_done_starts_the_next_task_once(firm):
    oid = firm.order(THREE)
    firm.acted("mkt", oid)  # mkt worked task 1 (its event carries task_no 1)
    assert firm.h.events("tool.called")[-1].payload["task_no"] == 1
    out = firm.task_done(oid, 1, actor="mkt", summary="ad posted on X")
    assert out.status == "done" and out.data == {"ok": True, "next": "task 2/3 starts now"}
    [ended] = firm.h.events("order.task_done")
    assert ended.actor == "mkt" and ended.payload == {
        "order_id": oid, "task_no": 1, "of": 3, "status": "done", "summary": "ad posted on X",
        "by": "mkt"}
    assert firm.started() == [(oid, 1), (oid, 2)]
    assert firm.wakes[-1].endswith("task 2/3: Hire 2 people") and len(firm.wakes) == 2
    again = firm.task_done(oid, 1, actor="mkt")
    assert again.data == {"ok": True, "duplicate": True} and len(firm.done()) == 1


def test_3_an_approval_for_the_running_task_ends_it_waiting_on_you(firm):
    oid = firm.order(THREE)
    firm.task_done(oid, 1)
    out = firm.gw.perform("post_social", {"channel": "x", "text": "We're hiring reviewers.",
                                          "order_id": oid}, actor="mkt")
    assert out.status == "needs_approval"
    [asked] = firm.h.events("approval.requested")
    assert asked.payload["order_id"] == oid and asked.payload["task_no"] == 2
    assert firm.done() == [(1, "done"), (2, "waiting_on_you")]
    assert firm.started()[-1] == (oid, 3) and firm.wakes[-1].endswith("task 3/3: Email our clients")


def test_4_a_silent_task_times_out_then_the_order_is_done(firm):
    oid = firm.order("Advertise the firm")
    firm.now += TASK_SOFT_S - 1
    firm.gw.advance_orders()
    assert firm.done() == []  # not yet
    firm.now += 2
    firm.gw.advance_orders()
    assert firm.done() == [(1, "timeout")]
    [done] = firm.h.events("order.done")
    assert done.payload == {"order_id": oid, "of": 1, "done": 0, "waiting_on_you": 0,
                            "other": 1}
    firm.gw.advance_orders()
    assert len(firm.h.events("order.done")) == 1  # once


def test_5_a_single_order_keeps_the_legacy_wake_exactly(firm):
    oid = firm.order("open the firm")
    assert firm.wakes == [f"CEO {oid}: open the firm"]
    # the typed command path, and the old /api/voice CEO path, too
    proposal = firm.gw.perform("ceo_command", {"text": "advertise our firm"}).data
    assert proposal["readback"] == ('Send this order to the Chief of Staff: "advertise our '
                                    'firm". Say confirm.')
    firm.task_done(oid, 1)
    firm.confirm(proposal["proposal_id"])
    second = f"order_{firm.h.events('voice.command')[-1].id}"
    assert firm.wakes[-1] == f"CEO {second}: advertise our firm"
    firm.task_done(second, 1)
    firm.h.ok("voice", text="process the new case", speaker="ceo", channel="typed")
    third = f"order_{firm.h.events('voice.command')[-1].id}"
    assert firm.wakes[-1] == f"CEO {third}: process the new case"


def test_6_a_second_order_waits_for_the_first_orders_last_task(firm):
    first = firm.order("Advertise the firm, then hire 2 people")
    second = firm.order("email our clients")
    assert firm.started() == [(first, 1)] and len(firm.wakes) == 1
    firm.task_done(first, 1)
    assert firm.started()[-1] == (first, 2)
    firm.task_done(first, 2)
    assert firm.started()[-1] == (second, 1)
    assert firm.wakes[-1] == f"CEO {second}: email our clients"
    assert [e.payload["order_id"] for e in firm.h.events("order.done")] == [first]


def test_7_stop_everything_cancels_running_and_queued_tasks(firm):
    oid = firm.order(THREE)
    other = firm.order("email our clients")
    woke = len(firm.wakes)
    out = firm.gw.perform("ceo_tool", {"name": "pause", "args": {"action_type": "everything"},
                                       "channel": "click"})
    assert out.status == "done", out.as_dict()
    assert firm.done("cancelled") == [(1, "cancelled"), (2, "cancelled"), (3, "cancelled"),
                                      (1, "cancelled")]
    assert sorted(e.payload["order_id"] for e in firm.h.events("order.done")) == \
        sorted([oid, other])
    firm.now += 10_000
    firm.gw.advance_orders()
    assert len(firm.wakes) == woke and firm.gw._orders.active() is None


def test_8_a_restart_mid_order_resumes_without_a_second_start(firm):
    oid = firm.order(THREE)
    firm.task_done(oid, 1)
    started = firm.started()
    firm.restart()
    o, t = firm.gw._orders.active()
    assert (o.order_id, t.no) == (oid, 2)
    firm.gw.advance_orders()
    assert firm.started() == started and firm.wakes == []
    firm.task_done(oid, 2)
    assert firm.started()[-1] == (oid, 3)


# ---------------------------------------------------------------- §10.2: explicit task, own task


def test_an_explicit_earlier_task_never_ends_the_running_task(firm):
    oid = firm.order(THREE)
    firm.task_done(oid, 1)
    firm.task_done(oid, 2)  # task 3 runs
    out = firm.gw.perform("post_social", {"channel": "x", "text": "We're hiring reviewers.",
                                          "order_id": oid, "task_no": 2}, actor="mkt")
    assert out.status == "needs_approval"
    [asked] = firm.h.events("approval.requested")
    assert asked.payload["task_no"] == 2  # the card's own task wins
    o, t = firm.gw._orders.active()
    assert t.no == 3 and not t.waiting and firm.done() == [(1, "done"), (2, "done")]


def test_a_task_no_naming_no_task_of_the_order_is_restamped(firm):
    oid = firm.order("Advertise the firm, then hire 2 people")
    firm.gw.perform("post_social", {"channel": "x", "text": "We're hiring reviewers.",
                                    "order_id": oid, "task_no": 5}, actor="mkt")
    assert firm.h.events("approval.requested")[-1].payload["task_no"] == 1


def test_task_done_only_from_cos_or_a_seat_that_worked_the_task(firm):
    oid = firm.order(THREE)
    out = firm.task_done(oid, 1, actor="hr")
    assert out.status == "refused" and out.error["code"] == "not_yours"
    firm.acted("hr", oid)
    assert firm.task_done(oid, 1, actor="hr").status == "done"
    assert firm.task_done(oid, 2, actor="cos").status == "done"  # cos always may


def test_a_read_for_the_task_counts_as_working_it(firm):
    # agents' clarification: a reporter card that only reads, then reports, is not refused
    oid = firm.order(THREE)
    read = firm.gw.perform("get_case", {"case_id": "case_404", "order_id": oid, "task_no": 1},
                           actor="acct")
    assert read.status == "refused"  # no such case: the call still names the task
    assert firm.task_done(oid, 1, actor="acct").status == "done"
    # a call naming another task (or none running) never makes the seat an owner
    firm.gw.perform("get_case", {"case_id": "case_404", "order_id": oid, "task_no": 3},
                    actor="qa")
    assert firm.task_done(oid, 2, actor="qa").error["code"] == "not_yours"


def test_tool_called_keeps_the_cards_task(firm):
    oid = firm.order(THREE)
    firm.task_done(oid, 1)
    firm.gw.record_tool_call("mkt", "make_ad", "done", oid, 1)  # late, from task 1
    assert firm.h.events("tool.called")[-1].payload["task_no"] == 1
    firm.gw.record_tool_call("mkt", "make_ad", "done", oid, 9)  # no such task: the running one
    assert firm.h.events("tool.called")[-1].payload["task_no"] == 2


def test_task_done_refusals(firm):
    oid = firm.order(THREE)
    queued = firm.task_done(oid, 3)
    assert queued.status == "refused" and queued.error["code"] == "not_running"
    assert firm.task_done("order_999", 1).error["code"] == "not_found"
    assert firm.task_done(oid, 4).error["code"] == "not_found"
    assert firm.task_done(oid, 1, status="waiting_on_you").error["code"] == "bad_input"


def test_task_done_is_an_mcp_tool_for_every_seat():
    from app.gateway.perform import AGENTS, TOOL_ACTORS
    from app.mcp import TOOLS

    assert TOOL_ACTORS["task_done"] == AGENTS
    schema = TOOLS["task_done"]["inputSchema"]
    assert schema["required"] == ["order_id", "task_no", "status"]
    assert schema["properties"]["status"]["enum"] == ["done", "blocked", "failed"]
    assert schema["properties"]["order_id"]["pattern"] == "^order_[0-9]{1,12}$"
    assert TOOLS["make_ad"]["inputSchema"]["properties"]["task_no"]["maximum"] == 6


# ---------------------------------------------------------------- merge, read-back, confirm


def test_two_utterances_merge_into_one_proposal(firm):
    first = firm.gw.perform("ceo_command", {"text": "Advertise the firm."}).data
    firm.h.clock.advance(seconds=3)
    second = firm.gw.perform("ceo_command", {"text": "Hire two people."}).data
    assert second["readback"] == ("2 tasks for the Chief of Staff, one by one: 1) Advertise "
                                  "the firm; 2) Hire two people. Say confirm.")
    open_ = [p for p in firm.gw.state.proposals.values() if p["status"] == "open"]
    assert [p["proposal_id"] for p in open_] == [second["proposal_id"]]
    cancelled = firm.h.events("ceo.cancelled")[-1].payload
    assert cancelled["proposal_id"] == first["proposal_id"] and cancelled["reason"] == "merged"
    stale = firm.confirm(first["proposal_id"])
    assert stale.status == "refused" and stale.error["code"] == "superseded"
    public = firm.h.events("ceo.proposed")[-1]
    assert public.payload["args"]["tasks"] == 2
    assert public.payload["readback"] == "2 tasks for the Chief of Staff. Say confirm."
    assert firm.confirm(second["proposal_id"]).status == "done"
    assert firm.h.events("order.queued")[-1].payload["of"] == 2


def test_the_model_passing_tasks_in_one_call(firm):
    out = firm.gw.perform("ceo_tool", {"name": "order", "args": {
        "tasks": ["advertise the firm", "hire 2 people", "Hire 2 people"]}}).data
    assert out["readback"].startswith("2 tasks for the Chief of Staff, one by one: 1) ")


def test_an_approve_after_an_order_proposal_still_supersedes(firm):
    case_id = firm.h.to_qa_passed()
    firm.h.do("send_memo", case_id=case_id)  # an approval waits
    order = firm.gw.perform("ceo_tool", {"name": "order",
                                         "args": {"text": "advertise the firm"}}).data
    approve = firm.gw.perform("ceo_tool", {"name": "approve",
                                           "args": {"approval": "latest"}}).data
    assert approve["kind"] == "proposal"
    cancelled = firm.h.events("ceo.cancelled")[-1].payload
    assert cancelled["proposal_id"] == order["proposal_id"]
    assert cancelled["reason"] == "superseded"


def test_a_plain_confirm_confirms_the_numbered_read_back():
    readback = ("3 tasks for the Chief of Staff, one by one: 1) Advertise the firm; 2) Hire 2 "
                "people; 3) Email our clients. Say confirm.")
    assert ceo_tools.heard_confirms("confirm", "order", readback)
    assert ceo_tools.heard_confirms("yes, confirm", "order", readback)
    assert not ceo_tools.heard_confirms("hire 2 people", "order", readback)


def test_the_limits_were_raised():
    assert ceo_tools.FREE_ORDER_MAX == 1200 and ceo_tools.ORDER_MAX == 1500
    assert ceo_tools.free_order("advertise the firm " * 60) is not None  # 1140 chars


# ---------------------------------------------------------------- STT: English only


def test_stt_asks_for_english_and_drops_a_transcript_without_latin_letters(tmp_path,
                                                                            monkeypatch):
    from app.gateway.adapters.openai_voice import OpenAISTT
    from app.main import create_app

    from .gateway.test_task40a import ADMIN, AUDIO, WEBM, FakeOpenAI

    monkeypatch.setenv("OPENAI_STT_LANGUAGE", "")  # empty no longer means auto-detect
    h = Harness(tmp_path / "stt", admin_token=ADMIN, stt_public_seconds_per_day=60)
    fake = FakeOpenAI(text="شکریہ")
    h.ports.stt = OpenAISTT("sk-test", transport=httpx.MockTransport(fake))
    with TestClient(create_app(h.gw, start_background=False)) as c:
        out = c.post("/api/stt", params={"purpose": "talk", "seconds": 4}, content=AUDIO,
                     headers=WEBM)
        assert out.status_code == 200 and out.json() == {"text": ""}
        fake.text = "Hire two people."
        out = c.post("/api/stt", params={"purpose": "talk", "seconds": 4}, content=AUDIO,
                     headers=WEBM)
        assert out.json() == {"text": "Hire two people."}
    assert {call["language"] for call in fake.calls} == {"en"}
