"""B52 (D41 §E, contract v1.20 §10.3): GET /api/payments, the firm's payment ledger."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.gateway.events import NewEvent
from app.main import create_app

THEATER = "theater-" + "t" * 30
ADMIN = "admin-" + "a" * 30
EXPLORER = "https://explore.tempo.xyz/tx/"
TXS = ["0x02f0" + "a" * 60, "0x7b31" + "b" * 60, "0x9c4e" + "c" * 60]


def emit(h, *events):
    with h.gw._lock:
        h.gw._emit(list(events))


def paid(job_id, tx, status="ok"):
    return NewEvent(type="payment.sent", actor="system", provider="tempo", status=status,
                    payload={"job_id": job_id, "contractor": "PixelAgent", "amount_usd": "0.50",
                             "token": "pathUSD", "tx_hash": tx,
                             "explorer_url": EXPLORER + tx if tx else ""})


def the_three(h):
    emit(h, *(paid(f"job_{i}", tx) for i, tx in enumerate(TXS, 1)))


def test_the_three_contractor_payments_come_out_with_explorer_links(make_harness):
    h = make_harness(theater_token=THEATER, admin_token=ADMIN)
    the_three(h)
    with TestClient(create_app(h.gw, start_background=False)) as c:
        assert c.get("/api/payments").status_code == 401
        assert c.get("/api/payments", params={"token": "nope"}).status_code == 401
        body = c.get("/api/payments", params={"token": THEATER}).json()
        assert c.get("/api/payments", headers={"Authorization": f"Bearer {ADMIN}"}
                     ).status_code == 200
    rows = body["data"]
    assert [r["job_id"] for r in rows] == ["job_3", "job_2", "job_1"]  # newest first
    for row, tx in zip(rows, reversed(TXS)):
        assert row["direction"] == "out" and row["kind"] == "contractor"
        assert row["counterparty"] == "PixelAgent" and row["amount"] == "0.50"
        assert row["token"] == "pathUSD" and row["rail"] == "tempo-testnet"
        assert row["status"] == "ok" and row["tx_hash"] == tx
        assert row["explorer_url"] == EXPLORER + tx
        assert "stripe" not in row
    assert body["totals"] == {"contractors_paid": {"amount": "1.50", "token": "pathUSD"},
                              "agent_sales": {"amount": "0.00", "token": "pathUSD"},
                              "clients_paid_usd": "0.00"}


def test_sales_clients_failures_and_the_stripe_join(h):
    the_three(h)
    case_id = h.new_case()
    emit(h, paid("job_4", "", status="failed"),
         NewEvent(type="agent.sale", actor="system", provider="tempo",
                  payload={"amount_usd": "0.50", "token": "pathUSD", "tx_hash": "0xsale",
                           "explorer_url": EXPLORER + "0xsale", "payer_masked": "0x12…ab"}),
         NewEvent(type="payment.received", actor="acct", case_id=case_id,
                  payload={"case_id": case_id, "amount_cents": 4900, "currency": "usd"}),
         NewEvent(type="payment.recorded", actor="system", provider="stripe",
                  payload={"job_id": "job_1", "tx_hash": TXS[0], "stripe_id": "pi_123",
                           "mode": "mirror",
                           "dashboard_url": "https://dashboard.stripe.com/test/payments/pi_123"}))
    out = h.gw.payments()
    kinds = [(r["kind"], r["status"]) for r in out["data"]]
    assert kinds == [("client", "ok"), ("agent_sale", "ok"), ("contractor", "failed"),
                     ("contractor", "ok"), ("contractor", "ok"), ("contractor", "ok")]
    client = out["data"][0]
    assert client == {**client, "direction": "in", "counterparty": "Team #1",
                      "amount": "49.00", "token": "USD", "rail": "stripe"}
    sale = out["data"][1]
    assert sale["direction"] == "in" and sale["counterparty"] == "0x12…ab"
    assert "tx_hash" not in out["data"][2]  # a failed payment that never got a tx
    [first] = [r for r in out["data"] if r.get("job_id") == "job_1"]
    assert first["stripe"] == {"id": "pi_123", "mode": "mirror",
                               "dashboard_url": "https://dashboard.stripe.com/test/payments/pi_123"}
    # testnet tokens never reach the USD total; a failed payment is never counted
    assert out["totals"] == {"contractors_paid": {"amount": "1.50", "token": "pathUSD"},
                             "agent_sales": {"amount": "0.50", "token": "pathUSD"},
                             "clients_paid_usd": "49.00"}


def test_the_ledger_keeps_100_rows_and_is_cached_5_seconds(h, monkeypatch):
    import app.gateway.perform as perform

    clock = [100.0]
    monkeypatch.setattr(perform.time, "monotonic", lambda: clock[0])
    emit(h, *(paid(f"job_{i}", f"0x{i:064x}") for i in range(105)))
    out = h.gw.payments()
    assert len(out["data"]) == 100 and out["data"][0]["job_id"] == "job_104"
    assert out["totals"]["contractors_paid"]["amount"] == "52.50"  # all 105
    emit(h, paid("job_new", "0x" + "f" * 64))
    clock[0] += 4
    assert h.gw.payments()["data"][0]["job_id"] == "job_104"  # cached
    clock[0] += 2
    assert h.gw.payments()["data"][0]["job_id"] == "job_new"


def test_order_links_ride_the_row(h):
    h.ok("voice", text="get a share card made", speaker="ceo", channel="typed")
    oid = f"order_{h.events('voice.command')[-1].id}"
    event = paid("job_1", TXS[0])
    emit(h, NewEvent(type=event.type, actor=event.actor, provider=event.provider,
                     payload={**event.payload, "order_id": oid}))
    [row] = h.gw.payments()["data"]
    assert row["order_id"] == oid and row["task_no"] == 1
