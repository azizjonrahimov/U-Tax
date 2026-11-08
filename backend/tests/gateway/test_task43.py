"""Task 43 (the frontend's D37 asks): doc.step cell formats, the relay never re-logs a
replayed old step, and /api/events types= before the limit."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.gateway.documents import MONEY, cell_dict
from app.main import create_app
from app.naive import NaiveClient
from app.naive_activity import ActivityRelay, _Session

ADMIN = "admin-" + "a" * 30
THEATER = "theater-" + "b" * 30


# ---------------------------------------------------------------- 1. cell formats


def test_cells_carry_a_format_and_exact_numbers():
    wb = Workbook()
    ws = wb.active
    ws.append(["Tax by this method", 851.65])
    ws["B1"].number_format = MONEY
    ws.append(["Tax", 400.5])
    ws["B2"].number_format = MONEY
    ws.append(["Big", 85165.0])
    ws["B3"].number_format = MONEY
    ws.append(["4. Stated par", 0.00001])
    ws.append(["Stated par value per share", "0.00001"])
    ws.append(["Issued shares", 8_000_000])
    ws.append(["6. Assumed par value capital = par used × authorized shares", 1250000.125])
    ws.append(["Delaware corporation", "yes"])
    got = {c: cell_dict(ws, ws[c]) for c in ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8",
                                             "A1")}
    assert (got["B1"]["value"], got["B1"]["fmt"]) == (851.65, "usd")
    assert (got["B2"]["value"], got["B2"]["fmt"]) == (400.5, "usd")
    assert (got["B3"]["value"], got["B3"]["fmt"]) == (85165, "usd")
    assert (got["B4"]["value"], got["B4"]["fmt"]) == (0.00001, "par")
    assert (got["B5"]["value"], got["B5"]["fmt"]) == (0.00001, "par")  # a string par too
    assert (got["B6"]["value"], got["B6"]["fmt"]) == (8_000_000, "int")
    assert (got["B7"]["value"], got["B7"]["fmt"]) == (1250000.125, "usd")
    assert got["B8"]["fmt"] == "text" and got["A1"]["fmt"] == "text"
    assert json.loads(json.dumps(got))["B4"]["value"] == 0.00001  # survives JSON


def test_the_workpapers_steps_carry_formatted_cells(h):
    h.to_qa_passed()
    by_detail = {e.payload["detail"].split(":")[0]: h.gw.public_event(e, admin=True)
                 ["payload"].get("cells") for e in h.events("doc.step")}
    comparison = {c["cell"]: c for c in by_detail["Comparison"]}
    assert comparison["B4"]["fmt"] == "usd" and isinstance(comparison["B4"]["value"],
                                                           (int, float))
    apv = {c["cell"]: c for c in by_detail["Assumed Par Value Capital Method"]}
    assert apv["B6"]["fmt"] == "par" and apv["B6"]["value"] > 0  # the stated par row
    assert apv["B4"]["fmt"] == "int"  # issued shares


# ---------------------------------------------------------------- 2. no replayed old steps


def test_a_replayed_old_step_is_never_relogged_but_new_ones_are(h):
    seen = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)

    def at(seconds):
        return (seen + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")

    events = [
        {"seq": 1, "type": "message.completed", "created_at": at(-600),
         "data": {"role": "user", "content": "order order_7: post on LinkedIn"}},
        {"seq": 2, "type": "tool.started", "created_at": at(-590), "data": {"name": "old"}},
        {"seq": 3, "type": "tool.completed", "created_at": at(-580), "data": {"name": "old"}},
        {"seq": 4, "type": "tool.started", "created_at": at(-5), "data": {"name": "recent"}},
        {"seq": 5, "type": "tool.started", "created_at": at(3), "data": {"name": "live"}},
        {"seq": 6, "type": "session.idle", "created_at": at(4), "data": {}},
    ]
    body = "".join(f"id: {e['seq']}\ndata: {json.dumps(e)}\n\n" for e in events)

    async def handler(request):
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    relay = ActivityRelay(h.gw, NaiveClient("nk", transport=httpx.MockTransport(handler)),
                          now=lambda: seen)
    relay.sessions["ses_1"] = _Session("mkt", seen)
    asyncio.run(relay.follow("ses_1"))
    got = [e.payload for e in h.events("agent.activity")]
    assert [(a["kind"], a.get("tool")) for a in got] == [
        ("tool_started", "recent"), ("tool_started", "live"), ("idle", None)]
    assert all(a["order_id"] == "order_7" for a in got)  # the old opening message still read
    assert relay.sessions["ses_1"].last_seq == 6


# ---------------------------------------------------------------- 3. types= before the limit


def test_types_filter_applies_before_the_limit(make_harness):
    h = make_harness(admin_token=ADMIN, theater_token=THEATER, session_secret="s" * 48)
    h.to_sent()
    for i in range(30):
        h.do("agent_activity", agent="intake", session_id="ses_x", kind="thinking", seq=i)
    with TestClient(create_app(h.gw, start_background=False)) as c:
        def get(**params):
            return c.get("/api/events", params=params,
                         headers={"Authorization": f"Bearer {ADMIN}"}).json()

        page = get(types="memo.sent,doc.verified", limit=2)
        assert [e["type"] for e in page["data"]] == ["doc.verified", "doc.verified"]
        assert page["has_more"] is True
        rest = get(types="memo.sent,doc.verified", after_id=page["next_after_id"], limit=50)
        assert [e["type"] for e in rest["data"]] == ["doc.verified", "memo.sent"]
        assert rest["has_more"] is False and rest["next_after_id"] == h.store.last_id()
        # the theater: admin-only rows are dropped before the limit, never emptying a page
        theater = c.get("/api/events", params={"token": THEATER, "limit": 5,
                                               "after_id": h.events("agent.activity")[0].id - 1}
                        ).json()
        assert theater["data"] == [] and theater["has_more"] is False
        asked = c.get("/api/events", params={"token": THEATER, "types": "agent.activity"}).json()
        assert asked["data"] == []  # never, even when asked for by name
        assert len(get(types="agent.activity", limit=100)["data"]) == 30
