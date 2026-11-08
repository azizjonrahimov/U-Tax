"""Task 22 (contract §6b): the gateway wakes the real Chief of Staff in Naïve."""

from __future__ import annotations

import json
import logging
import threading

import httpx
import pytest

from app.naive_waker import NaiveWaker, WakeFailed

AGENTS = [{"id": "agt_cos", "name": "chief-of-staff", "metadata": {"utax_actor": "cos"}},
          {"id": "agt_tax", "name": "tax"}]


class FakeNaive:
    def __init__(self, gw=None, agents=None):
        self.gw = gw
        self.agents = AGENTS if agents is None else agents
        self.posts: list[dict] = []
        self.fail: list[int] = []  # statuses to answer the next POSTs with
        self.lock_free: list[bool] = []

    def _lock_free(self) -> bool:
        got: list[bool] = []

        def probe():
            ok = self.gw._lock.acquire(blocking=False)
            if ok:
                self.gw._lock.release()
            got.append(ok)

        t = threading.Thread(target=probe)
        t.start()
        t.join()
        return got[0]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/v1/agents":
            return httpx.Response(200, json={"data": self.agents, "has_more": False})
        if request.method == "POST" and request.url.path == "/v1/sessions":
            if self.gw is not None:
                self.lock_free.append(self._lock_free())
            self.posts.append({"key": request.headers.get("Idempotency-Key"),
                               "version": request.headers.get("Vetta-Version"),
                               "agent": request.headers.get("User-Agent"),
                               **json.loads(request.content)})
            if self.fail:
                status = self.fail.pop(0)
                return httpx.Response(status, json={"error": {"code": "x", "message": "x"}})
            return httpx.Response(201, json={"id": f"ses_{len(self.posts)}", "status": "running"})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "x"}})


def waker_for(fake) -> NaiveWaker:
    return NaiveWaker("nk_test", transport=httpx.MockTransport(fake))


def test_a_wake_starts_a_chief_of_staff_session_with_the_exact_message(caplog):
    fake = FakeNaive()
    waker = waker_for(fake)
    with caplog.at_level(logging.INFO, logger="utax"):
        waker("New case case_1")
    [post] = fake.posts
    assert post["agent_id"] == "agt_cos" and post["message"] == "New case case_1"
    assert post["key"].startswith("utax-wake:") and post["version"] and post["agent"]
    assert "identity" not in post  # the agent's attached identity (utax-cos) applies
    assert "metadata" not in post  # the wake text stays out of Naive's metadata
    assert any("WAKE delivered" in r.getMessage() for r in caplog.records)
    assert "nk_test" not in repr(waker)


def test_a_retry_reuses_its_key_and_a_new_wake_gets_a_new_one():
    fake = FakeNaive()
    fake.fail = [429]  # a 5xx would open the outage breaker; this is about the key
    waker = waker_for(fake)
    with pytest.raises(WakeFailed):
        waker("Client replied case_1")
    waker("Client replied case_1")  # the gateway's retry
    waker("Client replied case_1")  # a later, real second reply
    keys = [p["key"] for p in fake.posts]
    assert keys[0] == keys[1] != keys[2]


def test_a_recreated_agent_is_looked_up_again():
    fake = FakeNaive()
    waker = waker_for(fake)
    waker("New case case_1")
    fake.fail = [404]
    fake.agents = [{"id": "agt_cos2", "name": "chief-of-staff"}]
    with pytest.raises(WakeFailed):
        waker("New case case_2")
    waker("New case case_2")
    assert fake.posts[-1]["agent_id"] == "agt_cos2"


def test_no_chief_of_staff_or_another_seats_agent_is_a_failure_to_retry():
    for agents in ([], [{"id": "agt_x", "name": "chief-of-staff",
                         "metadata": {"utax_actor": "mkt"}}]):
        fake = FakeNaive(agents=agents)
        with pytest.raises(WakeFailed):
            waker_for(fake)("New case case_1")
        assert fake.posts == []


def test_a_new_email_wakes_the_naive_chief_of_staff_off_the_lock(h):
    fake = FakeNaive(gw=h.gw)
    h.gw.on_wake(waker_for(fake))
    h.ok("inject_email", **{"from": "founder@team9.dev", "subject": "Delaware tax",
                            "body": "We're a Delaware C-corp with 10,000,000 shares."})
    assert [p["message"] for p in fake.posts] == ["FAST PATH case_1"]  # D42 M2
    assert fake.lock_free == [True]


def test_a_failed_wake_goes_to_the_backoff_retry(h):
    fake = FakeNaive(gw=h.gw)
    fake.fail = [500]
    h.gw.on_wake(waker_for(fake))
    h.ok("inject_email", **{"from": "founder@team9.dev", "subject": "Delaware tax",
                            "body": "We're a Delaware C-corp with 10,000,000 shares."})
    assert h.gw.pending_wake_retries() == ["FAST PATH case_1"]  # D42 M2


def test_the_server_registers_the_waker_only_for_the_real_crew(make_harness, monkeypatch):
    from fastapi.testclient import TestClient

    import app.naive_confirm as nc
    from app.main import create_app
    from app.naive_waker import NaiveWaker as Waker

    async def idle(self):
        import asyncio

        await asyncio.sleep(3600)

    monkeypatch.setattr(nc.NaiveConfirmer, "run", idle)
    monkeypatch.setattr("app.gateway.perform.Gateway.boot_wakes", lambda self: [])
    monkeypatch.setattr("app.gateway.perform.Gateway.refresh_fee", lambda self, force=False: None)
    for key, driver, expected in (("", "", False), ("nk_live", "local", False),
                                  ("nk_live", "", True)):
        h = make_harness(naive_api_key=key, driver=driver)
        with TestClient(create_app(h.gw)):
            registered = any(isinstance(w, Waker) for w in h.gw._wakers)
        assert registered is expected, (key, driver)
        assert not any(isinstance(w, Waker) for w in h.gw._wakers)  # unregistered at shutdown


# ---------------------------------------------------------------- review 1


def test_a_restarts_boot_wake_reuses_the_first_key_so_naive_starts_no_second_session():
    fake = FakeNaive()
    waker_for(fake)("New case case_7")
    waker_for(fake)("New case case_7")  # a new process: the boot wake for the same open case
    assert fake.posts[0]["key"] == fake.posts[1]["key"]  # Naive dedupes it


def test_a_recreated_agent_gets_a_new_key_never_a_409_loop():
    fake = FakeNaive()
    waker = waker_for(fake)
    waker.resolve()
    fake.fail = [404]
    with pytest.raises(WakeFailed):
        waker("New case case_1")  # the old agent is gone
    fake.agents = [{"id": "agt_cos2", "name": "chief-of-staff",
                    "metadata": {"utax_actor": "cos"}}]
    waker("New case case_1")
    first, second = fake.posts
    assert second["agent_id"] == "agt_cos2" and second["key"] != first["key"]


def test_an_idempotency_conflict_rotates_the_key():
    fake = FakeNaive()
    fake.fail = [409]
    waker = waker_for(fake)
    with pytest.raises(WakeFailed):
        waker("CEO: post the hiring ad")
    waker("CEO: post the hiring ad")
    assert fake.posts[0]["key"] != fake.posts[1]["key"]


def test_after_an_outage_wakes_fail_fast_for_a_while():
    now = {"t": 0.0}
    calls = {"n": 0}

    def down(request):
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    waker = NaiveWaker("nk_test", transport=httpx.MockTransport(down), clock=lambda: now["t"])
    for _ in range(5):
        with pytest.raises(WakeFailed):
            waker("New case case_1")
    assert calls["n"] == 1  # one network try, then fast failures straight to the backoff
    now["t"] = 31.0
    with pytest.raises(WakeFailed):
        waker("New case case_1")
    assert calls["n"] == 2


def test_the_stamped_chief_of_staff_wins_and_an_archived_one_is_skipped():
    agents = [{"id": "agt_old", "name": "chief-of-staff"},
              {"id": "agt_arch", "name": "chief-of-staff", "archived": True,
               "metadata": {"utax_actor": "cos"}},
              {"id": "agt_ours", "name": "chief-of-staff", "metadata": {"utax_actor": "cos"}}]
    fake = FakeNaive(agents=agents)
    waker_for(fake)("New case case_1")
    assert fake.posts[0]["agent_id"] == "agt_ours"


# ---------------------------------------------------------------- 22b


def test_a_second_email_on_an_open_case_starts_a_new_session(h):
    # live: a reply on an open INTAKE_PENDING case woke "New case case_1" again; the stable
    # key made Naive replay the first session and nobody read the reply
    fake = FakeNaive(gw=h.gw)
    waker = NaiveWaker("nk_test", transport=httpx.MockTransport(fake),
                       trigger_of=h.gw.wake_trigger)
    h.gw.on_wake(waker)
    mail = {"from": "founder@team9.dev", "subject": "Delaware tax",
            "body": "We're a Delaware C-corp with 10,000,000 shares."}
    h.ok("inject_email", **mail)
    h.ok("inject_email", **{**mail, "body": "Also: 8,000,000 issued, $500k in assets."})
    new_case = [p for p in fake.posts if p["message"] == "FAST PATH case_1"]  # D42 M2
    assert len(new_case) >= 2 and len({p["key"] for p in new_case}) == len(new_case)


def test_a_restarts_boot_wake_still_dedupes_on_the_same_trigger(h):
    fake = FakeNaive()
    h.new_case()
    first = NaiveWaker("nk_test", transport=httpx.MockTransport(fake),
                       trigger_of=h.gw.wake_trigger)
    first("New case case_1")
    gw = h.restart()
    again = NaiveWaker("nk_test", transport=httpx.MockTransport(fake),
                       trigger_of=gw.wake_trigger)
    again("New case case_1")  # the boot wake: no new mail since
    assert fake.posts[0]["key"] == fake.posts[1]["key"]


def test_a_new_applicant_wake_follows_the_applicants_mail(h):
    fake = FakeNaive(gw=h.gw)
    triggers = {"New applicant cand_1": 5}
    waker = NaiveWaker("nk_test", transport=httpx.MockTransport(fake),
                       trigger_of=triggers.get)
    waker("New applicant cand_1")
    triggers["New applicant cand_1"] = 9  # a later applicant mail
    waker("New applicant cand_1")
    assert fake.posts[0]["key"] != fake.posts[1]["key"]


def test_the_gateway_names_each_wakes_trigger(h):
    case_id = h.new_case()
    case = h.gw.state.cases[case_id]
    assert h.gw.wake_trigger(f"New case {case_id}") == case.last_inbound_id > 0
    assert h.gw.wake_trigger(f"FAST PATH {case_id}") == case.last_inbound_id
    assert h.gw.wake_trigger("New case case_99") is None
    assert h.gw.wake_trigger("CEO order_3: hello") is None
