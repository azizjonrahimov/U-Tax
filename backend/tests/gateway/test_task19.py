"""Task 19 (contract v1.9 §6b-bis, design D29): the gateway answers Naïve's data-leak confirms."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.naive import NaiveClient
from app.naive_confirm import AGENT_NAMES, NaiveConfirmer, naive_decision

AGENTS = [{"id": f"agt_{name}", "name": name} for name in AGENT_NAMES] + [
    {"id": "agt_other", "name": "someone-elses-agent"}]


class FakeNaive:
    """GET /v1/agents, GET /v1/sessions, GET /v1/sessions/{id}, POST .../tool_confirmations."""

    def __init__(self, gw, sessions: dict[str, dict]) -> None:
        self.gw = gw
        self.sessions = sessions  # id -> {agent_id, status, stop_reason, pending_actions}
        self.confirms: list[dict] = []
        self.answers: list[str] = []
        self.lock_free_on_post: list[bool] = []

    def _lock_free(self) -> bool:
        """From another thread: the lock is an RLock, so this thread could always take it."""
        import threading

        got: list[bool] = []

        def probe() -> None:
            ok = self.gw._lock.acquire(blocking=False)
            if ok:
                self.gw._lock.release()
            got.append(ok)

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        return got[0]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        if request.method == "GET" and path == "/agents":
            return httpx.Response(200, json={"data": AGENTS, "has_more": False})
        if request.method == "GET" and path == "/sessions":
            agent = request.url.params.get("agent_id")
            rows = [{"id": sid, **{k: v for k, v in s.items() if k != "pending_actions"}}
                    for sid, s in self.sessions.items() if not agent or s["agent_id"] == agent]
            return httpx.Response(200, json={"data": rows, "has_more": False})
        if request.method == "GET" and path.startswith("/sessions/"):
            sid = path.split("/")[2]
            return httpx.Response(200, json={"id": sid, **self.sessions[sid]})
        if request.method == "POST" and path.endswith("/tool_confirmations"):
            self.lock_free_on_post.append(self._lock_free())  # never held over HTTP
            body = json.loads(request.content)
            sid = path.split("/")[2]
            self.confirms.append({"session": sid, **body})
            session = self.sessions[sid]
            session["pending_actions"] = [a for a in session["pending_actions"]
                                          if a["tool_call_id"] != body["tool_call_id"]]
            if not session["pending_actions"]:
                session.update(status="running", stop_reason=None)
            return httpx.Response(200, json={"status": "running"})
        if request.method == "POST" and path.endswith("/answers"):
            self.answers.append(path)
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})


@pytest.fixture
def h(make_harness):
    return make_harness(ceo_email="ceo@u-tax.test")  # notices are logged only when mailed


def waiting(agent: str, *actions: dict, read_people: bool = True) -> dict:
    return {"agent_id": f"agt_{agent}", "status": "idle", "stop_reason": "awaiting_approval",
            "read_people": read_people, "pending_actions": list(actions)}


def tool_call(name: str, call_id: str) -> dict:
    return {"kind": "tool", "name": name, "tool_call_id": call_id}


def run(gw, fake: FakeNaive, polls: int = 1) -> NaiveConfirmer:
    async def go() -> NaiveConfirmer:
        client = NaiveClient("nk_test", transport=httpx.MockTransport(fake))
        confirmer = NaiveConfirmer(gw, client)
        try:
            await confirmer.boot()
            for _ in range(polls):
                await confirmer.poll_once()
        finally:
            await client.aclose()
        return confirmer

    return asyncio.run(go())


@pytest.mark.parametrize("actor,name,decision", [
    ("cos", "board_read", "allow"), ("cos", "board_write", "allow"),
    ("cos", "utax.list_open_cases", "allow"), ("intake", "utax.get_case", "allow"),
    ("acct", "utax.send_memo", "allow"), ("mkt", "utax.post_social", "allow"),
    ("mkt", "utax.send_memo", "deny"),  # another seat's tool
    ("cos", "utax.resolve_purchase", "deny"),  # a control action, no seat has it
    ("cos", "utax.ceo_confirm", "deny"), ("cos", "web_fetch", "deny"), ("cos", "bash", "deny"),
    ("mkt", "social_post", "deny"), ("cos", "utax.", "deny"), ("cos", "UTAX.get_case", "deny"),
    ("cos", "board_write ", "deny"), (None, "board_read", "deny"),
    # v1.10.3: handoffs, the Chief of Staff only
    ("cos", "send_to_agent", "allow"), ("cos", "wait_for_agents", "allow"),
    ("cos", "list_agents", "allow"), ("tax", "send_to_agent", "deny"),
    ("mkt", "list_agents", "deny"), ("hr", "wait_for_agents", "deny"),
    ("cos", "send_to_agent ", "deny"), ("cos", " send_to_agent", "deny"),
    ("cos", "Send_To_Agent", "deny"), ("cos", "SEND_TO_AGENT", "deny"),
    ("cos", "send-to-agent", "deny"), ("cos", "utax.send_to_agent", "deny"),
    ("cos", "send_to_agents", "deny"), (None, "send_to_agent", "deny"),
])
def test_only_the_board_and_the_seats_own_tools_are_allowed(actor, name, decision):
    assert naive_decision(actor, name) == decision


def test_a_waiting_session_is_answered_and_every_decision_is_an_event(h):
    fake = FakeNaive(h.gw, {
        "ses_1": waiting("chief-of-staff", tool_call("board_write", "tc_1")),
        "ses_2": waiting("marketing", tool_call("utax.send_memo", "tc_2"),
                         tool_call("web_fetch", "tc_3")),
    })
    run(h.gw, fake)
    assert sorted((c["tool_call_id"], c["decision"]) for c in fake.confirms) == [
        ("tc_1", "allow"), ("tc_2", "deny"), ("tc_3", "deny")]
    assert all(c["scope"] == "call" for c in fake.confirms)  # one call, never the session
    assert all(fake.lock_free_on_post)
    events = {e.payload["tool"]: e.payload for e in h.events("naive.confirm")}
    assert events["board_write"] == {"session_id": "ses_1", "agent": "cos",
                                     "tool": "board_write", "decision": "allow",
                                     "read_people": True}
    assert events["web_fetch"]["agent"] == "mkt" and events["web_fetch"]["decision"] == "deny"
    notices = [e.payload for e in h.events("ceo.notified")]
    assert {n["summary"] for n in notices} >= {"Naïve asked to run web_fetch; denied",
                                               "Naïve asked to run utax.send_memo; denied"}
    assert {n["reason"] for n in notices} == {"flagged"}


def test_a_decision_is_written_once_per_tool_call(h):
    fake = FakeNaive(h.gw, {"ses_1": waiting("marketing", tool_call("bash", "tc_9"))})
    run(h.gw, fake)
    fake.sessions["ses_1"].update(waiting("marketing", tool_call("bash", "tc_9")))  # again
    run(h.gw, fake)
    assert len(h.events("naive.confirm")) == 1
    assert len([e for e in h.events("ceo.notified") if "bash" in e.payload["summary"]]) == 1


def test_a_question_is_never_answered_and_goes_to_the_ceo(h):
    question = {"kind": "question", "name": "ask_operator", "tool_call_id": "tc_q"}
    session = waiting("intake", question)
    session["stop_reason"] = "awaiting_answer"  # what ask_operator idles with (naive-sdk.md)
    fake = FakeNaive(h.gw, {"ses_1": session})
    run(h.gw, fake, polls=2)
    assert fake.confirms == [] and fake.answers == []
    assert not h.events("naive.confirm")
    [notice] = [e.payload for e in h.events("ceo.notified")]
    assert "question" in notice["summary"].lower()


def test_other_agents_and_running_sessions_are_left_alone(h):
    other = waiting("intake", tool_call("board_write", "tc_x"))
    other["agent_id"] = "agt_other"
    running = waiting("tax", tool_call("board_write", "tc_y"))
    running.update(status="running", stop_reason=None)
    fake = FakeNaive(h.gw, {"ses_o": other, "ses_r": running})
    run(h.gw, fake)
    assert fake.confirms == [] and not h.events("naive.confirm")


def test_a_failed_answer_is_retried_on_the_next_poll(h):
    fake = FakeNaive(h.gw, {"ses_1": waiting("qa", tool_call("utax.qa_check", "tc_1"))})
    real = fake.__call__
    state = {"fail": 1}

    def flaky(request):
        if request.method == "POST" and state["fail"]:
            state["fail"] -= 1
            return httpx.Response(503, json={"error": {"code": "unavailable", "message": "x"}})
        return real(request)

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(flaky), max_retries=0)
        confirmer = NaiveConfirmer(h.gw, client)
        try:
            await confirmer.boot()
            await confirmer.poll_once()  # the answer fails: nothing is logged
            assert not h.events("naive.confirm")
            await confirmer.poll_once()
        finally:
            await client.aclose()

    asyncio.run(go())
    assert [(c["tool_call_id"], c["decision"]) for c in fake.confirms] == [("tc_1", "allow")]
    assert len(h.events("naive.confirm")) == 1


def test_denied_notices_are_capped_per_agent_and_tool(h):
    fake = FakeNaive(h.gw, {f"ses_{i}": waiting("marketing", tool_call("web_fetch", f"tc_{i}"))
                            for i in range(5)})
    run(h.gw, fake)
    assert len(h.events("naive.confirm")) == 5  # every decision is on the log
    assert len(h.events("ceo.notified")) == 1  # the CEO hears it once per 10 minutes


def test_a_boot_without_any_utax_agent_polls_nothing(h):
    class Empty(FakeNaive):
        def __call__(self, request):
            if request.url.path.endswith("/agents"):
                return httpx.Response(200, json={"data": [], "has_more": False})
            raise AssertionError(f"unexpected call {request.url}")

    run(h.gw, Empty(h.gw, {}))


def test_the_server_starts_the_confirmer_only_with_a_naive_key(make_harness, monkeypatch):
    from fastapi.testclient import TestClient

    import app.naive_confirm as nc
    from app.main import create_app

    started = []

    async def fake_run(self):
        started.append(self.gw)
        await asyncio.sleep(3600)

    monkeypatch.setattr(nc.NaiveConfirmer, "run", fake_run)
    monkeypatch.setattr("app.gateway.perform.Gateway.boot_wakes", lambda self: [])
    monkeypatch.setattr("app.gateway.perform.Gateway.refresh_fee", lambda self, force=False: None)
    for key, switch, expected in (("", True, 0), ("nk_live", False, 0), ("nk_live", True, 1)):
        h = make_harness(naive_api_key=key, naive_confirm=switch)
        started.clear()
        with TestClient(create_app(h.gw)):
            pass
        assert len(started) == expected, (key, switch)


def test_one_pass_fetches_at_most_25_sessions(h):
    import app.naive_confirm as nc

    fake = FakeNaive(h.gw, {f"ses_{i:02d}": waiting("marketing", tool_call("board_read", f"t{i}"))
                            for i in range(40)})
    for s in fake.sessions.values():
        s["stop_reason"] = None  # a list row that doesn't say why: each needs a GET
    gets = []
    real = fake.__call__

    def counting(request):
        if request.method == "GET" and request.url.path.count("/") == 3:
            gets.append(request.url.path)
        return real(request)

    fake.__call__ = counting

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(counting))
        confirmer = NaiveConfirmer(h.gw, client)
        try:
            await confirmer.boot()
            await confirmer.poll_once()
        finally:
            await client.aclose()

    asyncio.run(go())
    assert len(gets) == nc.DETAILS_PER_POLL


# ---------------------------------------------------------------- review 1


def question_session(agent, call_id):
    session = waiting(agent, {"kind": "question", "name": "ask_operator", "tool_call_id": call_id})
    session["stop_reason"] = "awaiting_answer"
    return session


def test_unanswered_questions_never_starve_another_agents_confirm(h):
    sessions = {f"ses_q{i:02d}": question_session("chief-of-staff", f"q{i}") for i in range(30)}
    sessions["ses_tax"] = waiting("tax", tool_call("utax.compute_tax", "tc_tax"))
    fake = FakeNaive(h.gw, sessions)
    run(h.gw, fake, polls=3)
    assert ("tc_tax", "allow") in [(c["tool_call_id"], c["decision"]) for c in fake.confirms]


def test_a_session_of_another_agent_is_never_answered(h):
    session = waiting("chief-of-staff", tool_call("board_write", "tc_1"))
    fake = FakeNaive(h.gw, {"ses_1": session})
    real = fake.__call__

    def lying(request):  # the list says cos, the session itself is someone else's
        response = real(request)
        if request.method == "GET" and request.url.path.endswith("/ses_1"):
            body = response.json()
            body["agent_id"] = "agt_other"
            return httpx.Response(200, json=body)
        return response

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(lying))
        confirmer = NaiveConfirmer(h.gw, client)
        try:
            await confirmer.boot()
            await confirmer.poll_once()
        finally:
            await client.aclose()

    asyncio.run(go())
    assert fake.confirms == []


def test_an_agent_stamped_for_another_seat_is_never_adopted(h, monkeypatch):
    stamped = [dict(a, metadata={"utax_actor": "mkt"}) if a["name"] == "tax" else a
               for a in AGENTS]
    monkeypatch.setattr(__import__(__name__), "AGENTS", stamped, raising=False)
    fake = FakeNaive(h.gw, {})
    real = fake.__call__

    def agents(request):
        if request.url.path.endswith("/agents"):
            return httpx.Response(200, json={"data": stamped, "has_more": False})
        return real(request)

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(agents))
        try:
            return await NaiveConfirmer(h.gw, client).boot()
        finally:
            await client.aclose()

    found = asyncio.run(go())
    assert "agt_tax" not in found and found["agt_marketing"] == "mkt"


def test_an_unknown_kind_is_left_alone_without_mail(h):
    fake = FakeNaive(h.gw, {"ses_1": waiting("qa", {"kind": "tool_use", "name": "bash",
                                                    "tool_call_id": "tc_1"})})
    run(h.gw, fake, polls=2)
    assert fake.confirms == [] and not h.events("ceo.notified")


def test_every_answer_is_for_one_call_only(h):
    fake = FakeNaive(h.gw, {"ses_1": waiting("qa", tool_call("utax.qa_check", "tc_1"))})
    run(h.gw, fake)
    assert [c["scope"] for c in fake.confirms] == ["call"]


def test_a_decision_whose_event_failed_is_written_on_the_next_pass(h, monkeypatch):
    from app.gateway.perform import ToolResult

    fake = FakeNaive(h.gw, {"ses_1": waiting("qa", tool_call("utax.qa_check", "tc_1"))})
    real = h.gw.perform
    state = {"fail": 1}

    def flaky(name, args=None, **kw):
        if name == "naive_confirm" and state["fail"]:
            state["fail"] -= 1
            return ToolResult(status="error", error={"code": "store_unavailable",
                                                     "message": "disk"})
        return real(name, args, **kw)

    monkeypatch.setattr(h.gw, "perform", flaky)
    run(h.gw, fake, polls=2)
    assert len(fake.confirms) == 1 and len(h.events("naive.confirm")) == 1


def test_one_agents_list_error_never_skips_the_others(h):
    fake = FakeNaive(h.gw, {"ses_1": waiting("hr", tool_call("utax.hr_record", "tc_1"))})
    real = fake.__call__

    def broken(request):
        if request.url.path.endswith("/sessions") and \
                request.url.params.get("agent_id") == "agt_chief-of-staff":
            return httpx.Response(500, json={"error": {"code": "internal", "message": "x"}})
        return real(request)

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(broken), max_retries=0)
        confirmer = NaiveConfirmer(h.gw, client)
        try:
            await confirmer.boot()
            for _ in range(len(AGENT_NAMES)):
                await confirmer.poll_once()
        finally:
            await client.aclose()

    asyncio.run(go())
    assert [c["tool_call_id"] for c in fake.confirms] == ["tc_1"]


def test_a_refused_key_tells_the_ceo_once(h, monkeypatch):
    import app.naive_confirm as nc

    def refused(request):
        return httpx.Response(403, json={"error": {"code": "forbidden", "message": "scope"}})

    class Stop(Exception):
        pass

    sleeps = {"n": 0}

    async def sleep(_):
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise Stop

    monkeypatch.setattr(nc.asyncio, "sleep", sleep)

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(refused))
        try:
            await NaiveConfirmer(h.gw, client).run()
        except Stop:
            pass
        finally:
            await client.aclose()

    asyncio.run(go())
    [notice] = [e.payload for e in h.events("ceo.notified")]
    assert "agents:read" in notice["summary"]


def test_the_agent_ids_are_looked_up_again_later(h):
    import app.naive_confirm as nc

    fake = FakeNaive(h.gw, {})
    now = {"t": 0.0}

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(fake))
        confirmer = NaiveConfirmer(h.gw, client, clock=lambda: now["t"])
        try:
            await confirmer.boot()
            confirmer.agents.clear()  # say an agent was re-created since
            now["t"] = nc.REBOOT_SECONDS
            await confirmer.poll_once()
            return confirmer.agents
        finally:
            await client.aclose()

    assert len(asyncio.run(go())) == len(AGENT_NAMES)


def test_a_server_that_ignores_the_agent_filter_answers_nothing_extra(h):
    other = waiting("intake", tool_call("board_write", "tc_o"))
    other["agent_id"] = "agt_other"
    fake = FakeNaive(h.gw, {"ses_o": other})
    real = fake.__call__

    def unfiltered(request):  # every listing returns every session, whoever's it is
        if request.method == "GET" and request.url.path.endswith("/sessions"):
            rows = [{"id": "ses_o", "status": "idle", "stop_reason": "awaiting_approval"}]
            return httpx.Response(200, json={"data": rows, "has_more": False})
        return real(request)

    async def go():
        client = NaiveClient("nk_test", transport=httpx.MockTransport(unfiltered))
        confirmer = NaiveConfirmer(h.gw, client)
        try:
            await confirmer.boot()
            await confirmer.poll_once()
        finally:
            await client.aclose()

    asyncio.run(go())
    assert fake.confirms == [] and not h.events("naive.confirm")
