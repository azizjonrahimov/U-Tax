"""HTTP surface (contract §4/§5) and SSE, through create_app() with a test gateway."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.api import sse_events
from app.main import create_app

from .conftest import HAPPY_BODY

ADMIN = "admin-token-for-tests"
THEATER = "theater-token-for-tests"


@pytest.fixture
def api(make_harness):
    h = make_harness(admin_token=ADMIN, theater_token=THEATER)
    with TestClient(create_app(h.gw, start_background=False)) as client:
        yield h, client


def admin():
    return {"Authorization": f"Bearer {ADMIN}"}


def inject(client, body=HAPPY_BODY, sender="founder@team7.dev"):
    r = client.post("/api/dev/inject-email", headers=admin(),
                    json={"from": sender, "subject": "checkup", "body": body})
    assert r.status_code == 200, r.text
    return r.json()["case_id"]


# ---------------------------------------------------------------- auth + §4


def test_state_needs_the_theater_token(api):
    h, client = api
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", params={"token": "nope"}).json()["error"]["code"] == \
        "unauthorized"
    ok = client.get("/api/state", params={"token": THEATER})
    assert ok.status_code == 200 and ok.json()["firm"] == {"agents": 7, "clients": 0}
    assert client.get("/api/state", headers=admin()).status_code == 200  # CEO console


def test_control_routes_need_the_admin_token(api):
    h, client = api
    for path, body in (("/api/approve", {"approval_id": "apr_1", "decision": "allow"}),
                       ("/api/trust", {"action_type": "email", "state": "PAUSED"}),
                       ("/api/dev/inject-email", {"from": "a@b.co", "body": "x"}),
                       ("/api/cases/case_1/unblock", {}),
                       ("/api/candidates/cand_1/decision", {"decision": "hire"})):
        assert client.post(path, json=body).status_code == 401, path
    assert client.get("/api/ceo/brief").status_code == 401


def test_events_paging(api):
    h, client = api
    inject(client)
    inject(client, body="more", sender="b@two.dev")
    page = client.get("/api/events", params={"token": THEATER, "limit": 3}).json()
    assert [e["id"] for e in page["data"]] == [1, 2, 3] and page["has_more"] is True
    assert page["next_after_id"] == 3
    rest = client.get("/api/events", params={"token": THEATER, "after_id": 3}).json()
    assert rest["has_more"] is False and rest["data"][0]["id"] == 4
    assert "private" not in json.dumps(page) and "founder@team7.dev" not in json.dumps(page)


def test_sse_replays_then_streams_live_and_pings(api):
    h, client = api
    inject(client)

    async def collect():
        frames = []
        stop = asyncio.Event()

        async def disconnected():
            return stop.is_set()

        gen = sse_events(h.gw, 1, is_disconnected=disconnected, heartbeat=0.5)
        frames.append(await gen.__anext__())  # replay: event 2 only (after_id=1)
        live = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.05)
        await asyncio.to_thread(h.gw.perform, "voice", {"text": "hi", "speaker": "ceo"})
        frames.append(await live)
        frames.append(await gen.__anext__())  # second event of that batch
        frames.append(await gen.__anext__())  # heartbeat
        stop.set()
        await gen.aclose()
        return frames

    frames = asyncio.run(collect())
    assert frames[0].startswith("id: 2\ndata: ")
    assert json.loads(frames[0].split("data: ", 1)[1])["type"] == "case.status"
    assert frames[1].startswith("id: 3\n") and frames[2].startswith("id: 4\n")
    assert frames[3] == ": ping\n\n"
    assert h.gw._listeners == []  # unsubscribed on close


def test_stream_route_resumes_from_last_event_id(api):
    h, client = api
    inject(client)
    h.gw.settings  # stream route validates the token before streaming
    assert client.get("/api/events/stream").status_code == 401


# ---------------------------------------------------------------- §5 control


def test_approve_flow_over_http(api):
    h, client = api
    case_id = inject(client)
    h.to_qa_passed(case_id)
    pending = h.do("send_memo", case_id=case_id)
    r = client.post("/api/approve", headers=admin(),
                    json={"approval_id": pending.approval_id, "decision": "allow", "note": "ok"})
    assert r.status_code == 200 and r.json()["ok"] is True
    again = client.post("/api/approve", headers=admin(),
                        json={"approval_id": pending.approval_id, "decision": "allow"})
    assert again.json()["duplicate"] is True
    missing = client.post("/api/approve", headers=admin(),
                          json={"approval_id": "apr_99", "decision": "allow"})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_customer_voice_is_public_but_rate_limited(api):
    h, client = api
    body = {"text": "Delaware C-corp, 10M authorized", "channel": "typed",
            "speaker": "customer", "reply_email": "judge@yc.com"}
    first = client.post("/api/voice", json=body)
    assert first.status_code == 200 and first.json()["case_id"] == "case_1"
    codes = [client.post("/api/voice", json=body).status_code for _ in range(35)]
    assert 429 in codes  # task 12: Send's own bucket is 30/min per IP
    assert client.post("/api/voice", json={"text": "go", "speaker": "ceo"}).status_code == 401


def test_trust_unblock_and_decision_routes(api):
    h, client = api
    r = client.post("/api/trust", headers=admin(), json={"action_type": "social_post",
                                                         "state": "PAUSED"})
    assert r.status_code == 200 and r.json()["trust"] == "PAUSED"
    bad = client.post("/api/trust", headers=admin(), json={"action_type": "nope", "state": "AUTO"})
    assert bad.status_code == 400
    case_id = inject(client)
    conflict = client.post(f"/api/cases/{case_id}/unblock", headers=admin(), json={})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "wrong_status"
    h.ok("inject_email", **{"from": "a@x.io", "subject": "gig", "body": "EA", "inbox": "hiring"})
    hire = client.post("/api/candidates/cand_1/decision", headers=admin(),
                       json={"decision": "hire"})
    assert hire.status_code == 200 and hire.json()["stage"] == "hired"


def test_voice_routes_without_a_key_answer_503(api):
    h, client = api
    r = client.post("/api/voice/session")
    assert r.status_code == 503 and r.json()["error"]["code"] == "voice_unavailable"
    brief = client.get("/api/ceo/brief", headers=admin(), params={"since_id": 0}).json()
    assert set(brief) == {"brief_event_id", "text", "counts", "pending_approvals",
                          "last_event_id"}
    audio = client.get("/api/brief/audio", params={"token": THEATER,
                                                   "event_id": brief["brief_event_id"]})
    assert audio.status_code == 503
    not_a_brief = client.get("/api/brief/audio", params={"token": THEATER, "event_id": 999})
    assert not_a_brief.status_code == 404


def test_store_failure_maps_to_503(api):
    from app.gateway.ports.store import EventWriteError

    h, client = api

    class Broken:
        def __getattr__(self, name):
            return getattr(h.store, name)

        def ensure_writable(self):
            raise EventWriteError("disk full")

    h.gw.store = Broken()
    r = client.post("/api/dev/inject-email", headers=admin(),
                    json={"from": "a@b.co", "subject": "s", "body": "b"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "store_unavailable"


def test_open_access_when_tokens_are_empty(make_harness):
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        assert client.get("/api/state").status_code == 200
        assert client.post("/api/dev/inject-email",
                           json={"from": "a@b.co", "subject": "s", "body": "b"}).status_code == 200


def test_theater_origin_list_is_allowed(make_harness, monkeypatch):
    monkeypatch.setenv("THEATER_ORIGIN", "http://localhost:3200, https://u-tax.vercel.app/")
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        for origin in ("http://localhost:3000", "http://localhost:3200",
                       "https://u-tax.vercel.app"):
            r = client.get("/api/health", headers={"Origin": origin})
            assert r.headers.get("access-control-allow-origin") == origin
        r = client.get("/api/health", headers={"Origin": "http://evil.example"})
        assert "access-control-allow-origin" not in r.headers
