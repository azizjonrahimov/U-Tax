from app.tax_returns.engine import compute_returns, missing_facts

BASE = {"entity_type": "C-corp", "gross_receipts": 1_200_000, "cost_of_goods": 200_000,
        "total_deductions": 700_000}


def run(**kw):
    return compute_returns({**BASE, **kw})


def amounts(r):
    return {x.jurisdiction: (x.amount, x.status) for x in r.results}


def test_federal_lines():
    r = run(state="CA")
    fed = r.results[0]
    assert fed.lines["1c"] == 1_200_000 and fed.lines["3"] == 1_000_000
    assert fed.lines["30"] == 300_000 and fed.amount == 63_000 and fed.lines["31"] == 63_000


def test_states():
    assert amounts(run(state="CA"))["California"] == (26_520, "ok")        # 8.84% x 300k
    assert amounts(run(state="FL"))["Florida"] == (13_750, "ok")           # 5.5% x 250k
    assert amounts(run(state="IL"))["Illinois"] == (28_500, "ok")          # 9.5% x 300k
    assert amounts(run(state="DE"))["Delaware"] == (26_100, "ok")          # 8.7% x 300k
    assert amounts(run(state="NY"))["New York"] == (19_500, "ok")          # 6.5% x 300k > FDM 1,500
    assert amounts(run(state="TX"))["Texas"] == (0, "no_tax_due")          # 1.2M <= 2.65M
    assert amounts(run(state="WY"))["Wyoming"] == (0, "no_tax_due")
    assert amounts(run(state="WA"))["Washington"][1] == "human_review"
    assert amounts(run(state="ZZ"))["ZZ"][1] == "human_review"


def test_minimums_and_losses():
    loss = dict(gross_receipts=100_000, cost_of_goods=0, total_deductions=400_000)
    assert amounts(run(state="CA", **loss)) == {"Federal": (0, "no_tax_due"), "California": (800, "ok")}
    assert amounts(run(state="CA", first_year_in_state=True, **loss))["California"] == (0, "no_tax_due")
    assert amounts(run(state="NY", **loss))["New York"] == (25, "ok")      # FDM for <= $100k receipts


def test_ny_top_rate_and_tx_margin():
    big = dict(gross_receipts=20_000_000, cost_of_goods=8_000_000, total_deductions=6_000_000)
    assert amounts(run(state="NY", **big))["New York"] == (435_000, "ok")  # 7.25% x 6M
    # TX: margin = min(14M, 12M, 20M, 19M) = 12M; 0.75% = 90,000; EZ 0.331% x 20M = 66,200 -> EZ
    assert amounts(run(state="TX", **big))["Texas"] == (66_200, "ok")


def test_missing_and_other_entities():
    assert missing_facts({"state": "CA"}) == ["entity_type", "gross_receipts", "cost_of_goods", "total_deductions"]
    assert run(state="CA", entity_type="LLC").results[0].status == "human_review"
