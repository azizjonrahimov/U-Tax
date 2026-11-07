"""Task 12: /talk Send has its own rate limiter (apart from the voice-session mint), and spend
counts cost.booked only (contract §4)."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient

from app.api import MINT_LIMITS, SEND_LIMITS
from app.gateway.events import NewEvent
from app.main import create_app

SEND = {"text": "Delaware C-corp, 10M authorized", "channel": "typed", "speaker": "customer",
        "reply_email": "judge@yc.com"}


def test_mint_tries_never_use_up_the_send_budget(make_harness):
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        mints = [client.post("/api/voice/session").status_code for _ in range(30)]
        assert 429 in mints  # the mint bucket is spent ...
        assert client.post("/api/voice", json=SEND).status_code == 200  # ... Send still works


def test_send_tries_never_use_up_the_mint_budget(make_harness):
    h = make_harness()
    with TestClient(create_app(h.gw, start_background=False)) as client:
        sends = [client.post("/api/voice", json=SEND).status_code for _ in range(35)]
        assert 429 in sends
        assert client.post("/api/voice/session").status_code != 429


def test_send_has_headroom_over_the_mint_in_both_modes():
    for event in (False, True):
        (mint_ip, mint_all), (send_ip, send_all) = MINT_LIMITS[event], SEND_LIMITS[event]
        assert send_ip > mint_ip and send_all > mint_all
        assert mint_all == 6 * mint_ip and send_all == 6 * send_ip  # global keeps 6x headroom


def test_event_mode_lets_the_venue_ip_send_more(make_harness):
    h = make_harness(public_mode="event")
    with TestClient(create_app(h.gw, start_background=False)) as client:
        codes = [client.post("/api/voice", json=SEND).status_code for _ in range(60)]
    assert 429 not in codes


def test_spend_counts_cost_booked_only(make_harness):
    # v1.7.4 bug: every cost_usd except tool.bought was summed; a law.searched (or any other
    # event) carrying a display price must not count
    h = make_harness(ceo_email="ceo@u-tax.dev")
    h.gw._emit([
        NewEvent(type="law.searched", actor="tax", provider="apify",
                 payload={"query": "franchise tax", "results": 3}, cost_usd="5.0000"),
        NewEvent(type="cost.booked", actor="system", provider="apify",
                 payload={"provider": "apify", "model": "apify-law", "estimated": False},
                 cost_usd="0.0050"),
    ])
    assert h.gw.state.spent_by_provider == {"apify": Decimal("0.0050")}
    period = h.gw._period(0)
    assert Decimal(period["agent_spend"].lstrip("$")) == Decimal("0.0050")
