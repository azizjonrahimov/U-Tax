"""NaiveClient against httpx.MockTransport. No live calls, no key needed."""

import asyncio
import json
from collections.abc import AsyncIterator, Callable

import httpx
import pytest

from app.naive import NaiveClient, NaiveConfigError, NaiveError, Page

KEY = "sk_live_test_not_real"


def run(coro):
    return asyncio.run(coro)


class Recorder:
    """Records requests; answers each with the next handler result."""

    def __init__(self, *responses: Callable[[httpx.Request], httpx.Response] | httpx.Response):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        nxt = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return nxt(request) if callable(nxt) else nxt


def make_client(recorder: Recorder, **kwargs) -> tuple[NaiveClient, list[float]]:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client = NaiveClient(
        api_key=KEY,
        transport=httpx.MockTransport(recorder),
        sleep=fake_sleep,
        **kwargs,
    )
    return client, slept


def ok(body, status=200, headers=None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers)


def naive_error(status: int, code: str, message: str = "nope", headers=None) -> httpx.Response:
    body = {"error": {"type": "invalid_request", "code": code, "message": message,
                      "request_id": "req_123", "param": "budget"}}
    return httpx.Response(status, json=body, headers=headers)


# ---------------------------------------------------------------- config / auth


def test_auth_and_version_headers_on_every_request():
    rec = Recorder(ok({"id": "agt_1"}))
    client, _ = make_client(rec)
    assert run(client.get_agent("agt_1")) == {"id": "agt_1"}
    req = rec.requests[0]
    assert str(req.url) == "https://api.vetta.sh/v1/agents/agt_1"
    assert req.headers["authorization"] == f"Bearer {KEY}"
    assert req.headers["vetta-version"] == "2026-09-30"
    assert req.headers["accept"] == "application/json"
    assert "idempotency-key" not in req.headers  # GETs don't get one


@pytest.mark.parametrize(
    "base", ["https://staging.example", "https://staging.example/", "https://staging.example/v1"]
)
def test_api_base_from_env_gets_v1_appended(monkeypatch, base):
    monkeypatch.setenv("NAIVE_API_KEY", KEY)
    monkeypatch.setenv("NAIVE_API_BASE", base)
    client = NaiveClient()
    assert client.base_url == "https://staging.example/v1"


def test_default_base_and_key_from_env(monkeypatch):
    monkeypatch.setenv("NAIVE_API_KEY", KEY)
    monkeypatch.delenv("NAIVE_API_BASE", raising=False)
    client = NaiveClient()
    assert client.base_url == "https://api.vetta.sh/v1"
    assert KEY not in repr(client)


def test_missing_key_is_a_config_error(monkeypatch):
    monkeypatch.delenv("NAIVE_API_KEY", raising=False)
    with pytest.raises(NaiveConfigError):
        NaiveClient()


# ---------------------------------------------------------------- idempotency


def test_posts_carry_an_idempotency_key():
    rec = Recorder(ok({"id": "ses_1"}, status=201))
    client, _ = make_client(rec)
    run(client.create_session(agent_id="agt_1", message="hi", budget_micro_usd=2_000_000))
    req = rec.requests[0]
    assert req.method == "POST"
    assert len(req.headers["idempotency-key"]) >= 32
    assert json.loads(req.content) == {
        "agent_id": "agt_1", "message": "hi", "budget_micro_usd": 2_000_000,
    }


def test_caller_supplied_idempotency_key_is_used():
    rec = Recorder(ok({"id": "agt_1"}, status=201))
    client, _ = make_client(rec)
    budget = {"cap_micro_usd": 50_000_000, "max_task_micro_usd": 5_000_000, "period": "month"}
    run(client.create_agent(name="triage", model="z-ai/glm-5.3-flash", budget=budget,
                            idempotency_key="idem-abc", system="Be terse."))
    req = rec.requests[0]
    assert req.headers["idempotency-key"] == "idem-abc"
    assert json.loads(req.content) == {
        "name": "triage", "model": "z-ai/glm-5.3-flash", "budget": budget, "system": "Be terse.",
    }


# ---------------------------------------------------------------- errors / 429


def test_naive_error_carries_code_and_fields():
    rec = Recorder(naive_error(402, "subscription_required", "Subscribe first"))
    client, _ = make_client(rec)
    with pytest.raises(NaiveError) as info:
        run(client.digest())
    err = info.value
    assert err.status_code == 402
    assert err.code == "subscription_required"
    assert err.message == "Subscribe first"
    assert err.request_id == "req_123"
    assert err.type == "invalid_request"
    assert err.param == "budget"


def test_non_envelope_error_still_has_a_code():
    rec = Recorder(httpx.Response(502, text="<html>Bad gateway</html>"))
    client, _ = make_client(rec)
    with pytest.raises(NaiveError) as info:
        run(client.get_session("ses_1"))
    assert info.value.status_code == 502
    assert info.value.code == "unexpected_response"


def test_429_waits_for_retry_after_then_retries_with_same_idempotency_key():
    rec = Recorder(
        naive_error(429, "rate_limited", headers={"Retry-After": "3"}),
        ok({"session_id": "ses_1", "status": "running", "accepted_seq": 7}, status=202),
    )
    client, slept = make_client(rec)
    result = run(client.send_message("ses_1", message="next step", queue=True))
    assert result["accepted_seq"] == 7
    assert slept == [3.0]
    assert len(rec.requests) == 2
    first, second = rec.requests
    assert first.headers["idempotency-key"] == second.headers["idempotency-key"]
    assert json.loads(second.content) == {"message": "next step", "queue": True}


def test_429_gives_up_after_max_retries():
    rec = Recorder(naive_error(429, "rate_limited", headers={"Retry-After": "1"}))
    client, slept = make_client(rec, max_retries=2)
    with pytest.raises(NaiveError) as info:
        run(client.list_agents())
    assert info.value.code == "rate_limited"
    assert info.value.retry_after == 1.0
    assert len(rec.requests) == 3
    assert slept == [1.0, 1.0]


def test_retry_after_wait_is_capped():
    rec = Recorder(naive_error(429, "rate_limited", headers={"Retry-After": "3600"}), ok({}))
    client, slept = make_client(rec, max_retry_wait=10)
    run(client.digest())
    assert slept == [10]


# ---------------------------------------------------------------- money


def test_float_money_is_rejected_before_any_request():
    rec = Recorder(ok({}))
    client, _ = make_client(rec)
    with pytest.raises(TypeError):
        run(client.create_session(agent_id="agt_1", message="hi", budget_micro_usd=2.5))
    with pytest.raises(TypeError):
        run(client.create_agent(name="a", model="m",
                                budget={"cap_micro_usd": 50.0, "period": "month"}))
    assert rec.requests == []


def test_set_session_budget_body():
    rec = Recorder(ok({"id": "ses_1"}))
    client, _ = make_client(rec)
    run(client.set_session_budget("ses_1", 5_000_000))
    req = rec.requests[0]
    assert req.method == "PATCH"
    assert req.url.path == "/v1/sessions/ses_1/budget"
    assert json.loads(req.content) == {"cap_micro_usd": 5_000_000}


# ---------------------------------------------------------------- pagination


def test_list_returns_page_shape():
    rec = Recorder(ok({"data": [{"id": "agt_1"}], "has_more": True, "next_cursor": "cur_2"}))
    client, _ = make_client(rec)
    page = run(client.list_agents(limit=1))
    assert isinstance(page, Page)
    assert page.data == [{"id": "agt_1"}]
    assert page.has_more is True
    assert page.next_cursor == "cur_2"
    assert rec.requests[0].url.params["limit"] == "1"
    assert "after" not in rec.requests[0].url.params


def test_paginate_follows_next_cursor():
    rec = Recorder(
        ok({"data": [{"id": "d1"}, {"id": "d2"}], "has_more": True, "next_cursor": "c2"}),
        ok({"data": [{"id": "d3"}], "has_more": False, "next_cursor": None}),
    )
    client, _ = make_client(rec)

    async def collect():
        return [item["id"] async for item in client.paginate("/deployments", {"limit": 2})]

    assert run(collect()) == ["d1", "d2", "d3"]
    assert rec.requests[1].url.params["after"] == "c2"
    assert rec.requests[1].url.params["limit"] == "2"


def test_session_events_sorted_by_seq_with_include_accepted():
    events = [{"seq": 3, "type": "session.idle"}, {"seq": 1, "type": "session.running"},
              {"seq": 2, "type": "message.completed"}]
    rec = Recorder(ok({"data": events, "has_more": False, "next_cursor": None}))
    client, _ = make_client(rec)
    page = run(client.session_events("ses_1", after_seq=0, include_accepted=True))
    assert [e["seq"] for e in page.data] == [1, 2, 3]
    params = rec.requests[0].url.params
    assert params["after_seq"] == "0"
    assert params["include_accepted"] == "true"


# ---------------------------------------------------------------- SSE


SSE_BODY = (
    ": keep-alive\n\n"
    'id: 4\ndata: {"seq":4,"type":"tool.started","data":{"name":"write"}}\n\n'
    "id: 5\r\n"
    'data: {"seq":5,"type":"message.completed",\r\n'
    'data: "data":{"role":"assistant","content":"Done"}}\r\n\r\n'
    'id: 6\ndata: {"seq":6,"type":"session.idle","data":{"stop_reason":"end_turn"}}\n'
)


def test_stream_parses_sse_events():
    rec = Recorder(httpx.Response(200, text=SSE_BODY,
                                  headers={"content-type": "text/event-stream"}))
    client, _ = make_client(rec)

    async def collect():
        return [e async for e in client.stream("ses_1", after_seq=3)]

    events = run(collect())
    assert [e["seq"] for e in events] == [4, 5, 6]
    assert events[1]["data"] == {"role": "assistant", "content": "Done"}  # multi-line data joined
    assert events[2]["data"]["stop_reason"] == "end_turn"
    req = rec.requests[0]
    assert req.url.path == "/v1/sessions/ses_1/stream"
    assert req.url.params["after_seq"] == "3"
    assert req.headers["accept"] == "text/event-stream"
    assert req.headers["authorization"] == f"Bearer {KEY}"


class BrokenStream(httpx.AsyncByteStream):
    """Sends some bytes, then drops the connection."""

    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        raise httpx.ReadError("connection reset")


def test_stream_resumes_after_disconnect_without_duplicates():
    first = httpx.Response(200, stream=BrokenStream([
        b'id: 1\ndata: {"seq":1,"type":"session.running"}\n\n',
        b'id: 2\ndata: {"seq":2,"type":"message.delta"}\n\n',
    ]))
    second = httpx.Response(200, text=(
        'id: 2\ndata: {"seq":2,"type":"message.delta"}\n\n'  # server replays seq 2
        'id: 3\ndata: {"seq":3,"type":"session.idle"}\n\n'
    ))
    rec = Recorder(first, second)
    client, slept = make_client(rec)

    async def collect():
        return [e["seq"] async for e in client.stream("ses_1")]

    assert run(collect()) == [1, 2, 3]
    assert [r.url.params["after_seq"] for r in rec.requests] == ["0", "2"]
    assert slept == [1.0]


def test_stream_error_status_raises_naive_error():
    rec = Recorder(naive_error(404, "not_found", "No such session"))
    client, _ = make_client(rec)

    async def collect():
        return [e async for e in client.stream("ses_missing")]

    with pytest.raises(NaiveError) as info:
        run(collect())
    assert info.value.code == "not_found"


# ---------------------------------------------------------------- route bodies


def test_confirm_tool_and_answer_bodies():
    rec = Recorder(ok({"ok": True}))
    client, _ = make_client(rec)
    run(client.confirm_tool("ses_1", tool_call_id="tc_1", decision="deny", reason="out of policy"))
    run(client.answer("ses_1", tool_call_id="tc_2", answers={"q1": "yes", "q2": ["a", "b"]}))
    confirm, answer = rec.requests
    assert confirm.url.path == "/v1/sessions/ses_1/tool_confirmations"
    assert json.loads(confirm.content) == {
        "tool_call_id": "tc_1", "decision": "deny", "reason": "out of policy",
    }
    assert answer.url.path == "/v1/sessions/ses_1/answers"
    assert json.loads(answer.content) == {
        "tool_call_id": "tc_2", "answers": {"q1": "yes", "q2": ["a", "b"]},
    }
    with pytest.raises(ValueError):
        run(client.confirm_tool("ses_1", tool_call_id="tc_1", decision="maybe"))


def test_update_agent_is_patch_with_expected_version():
    rec = Recorder(ok({"id": "agt_1", "current_version": 3}))
    client, _ = make_client(rec)
    run(client.update_agent("agt_1", expected_version=2, system="New prompt"))
    req = rec.requests[0]
    assert req.method == "PATCH"
    assert json.loads(req.content) == {"expected_version": 2, "system": "New prompt"}


def test_version_conflict_surfaces_as_code():
    rec = Recorder(naive_error(409, "version_conflict"))
    client, _ = make_client(rec)
    with pytest.raises(NaiveError) as info:
        run(client.update_agent("agt_1", expected_version=1, system="x"))
    assert info.value.code == "version_conflict"


def test_create_deployment_uses_input_template():
    rec = Recorder(ok({"id": "dep_1"}, status=201))
    client, _ = make_client(rec)
    run(client.create_deployment(agent_id="agt_1", cron="0 9 * * *",
                                 input_template="Summarize overnight changes.",
                                 budget_micro_usd=5_000_000, window="loose"))
    req = rec.requests[0]
    assert req.url.path == "/v1/deployments"
    assert "idempotency-key" in req.headers
    assert json.loads(req.content) == {
        "agent_id": "agt_1", "cron": "0 9 * * *", "input_template": "Summarize overnight changes.",
        "budget_micro_usd": 5_000_000, "window": "loose",
    }


def test_dashboard_reads_hit_documented_routes():
    rec = Recorder(ok({"data": [], "has_more": False, "next_cursor": None}))
    client, _ = make_client(rec)
    run(client.digest())
    run(client.credits_spend(agent_id="agt_1", from_="2026-11-01"))
    run(client.agent_board("agt_1"))
    run(client.list_board_cards("brd_1", limit=10))
    run(client.list_deployments())
    paths = [r.url.path for r in rec.requests]
    assert paths == ["/v1/digest", "/v1/credits/spend", "/v1/agents/agt_1/board",
                     "/v1/boards/brd_1/cards", "/v1/deployments"]
    spend_params = rec.requests[1].url.params
    assert spend_params["agent_id"] == "agt_1"
    assert spend_params["from"] == "2026-11-01"
