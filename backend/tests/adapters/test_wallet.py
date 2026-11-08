"""D38 (task 47a): the wallet port: LocalWallet, TempoWallet's reads (MockTransport RPC),
testnet only, no key ever shown, the registry switch and the devtools wallets command."""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest

from app.gateway.adapters.local import LocalWallet
from app.gateway.adapters.tempo_wallet import PATH_USD, TempoWallet, new_key
from app.gateway.ports import PaidInvoice, Rejected
from app.gateway.registry import AdapterNotReady, build_ports
from app.gateway.settings import Settings, parse_contractors

WALLET = "0x" + "ab" * 20


def rpc_world(calls):
    def respond(request):
        body = json.loads(request.content)
        calls.append(body)
        if body["method"] == "eth_call":
            assert body["params"][0]["to"] == PATH_USD
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                             "result": hex(12_500_000)})  # 12.50 pathUSD
        if body["method"] == "eth_getTransactionReceipt":
            tx = body["params"][0]
            result = {"0xok": {"status": "0x1"}, "0xbad": {"status": "0x0"}}.get(tx)
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": result})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "error": {"code": -32601, "message": "no"}})
    return respond


def test_local_wallet_contract():
    w = LocalWallet()
    paid = w.pay_invoice("http://c.test/invoice/job_1", max_usd="0.50", recipient=WALLET)
    assert isinstance(paid, PaidInvoice) and paid.tx_hash.startswith("local:")
    assert paid.amount_usd == "0.50" and paid.paid is False and paid.explorer_url == ""
    assert w.tx_ok(paid.tx_hash) is True and w.tx_ok("0xabc") is None
    again = w.pay_invoice("http://c.test/invoice/job_1", max_usd="0.50", recipient=WALLET)
    assert again.tx_hash == paid.tx_hash  # deterministic
    assert w.provider == "local" and w.balance_usd() == Decimal("1000")


def test_tempo_wallet_reads_balance_and_receipts_and_hides_its_key():
    calls = []
    key, address = new_key()
    w = TempoWallet(key, transport=httpx.MockTransport(rpc_world(calls)))
    assert w.balance_usd() == Decimal("12.5")
    assert calls[0]["params"][0]["data"].endswith(address[2:].lower())
    assert w.tx_ok("0xok") is True and w.tx_ok("0xbad") is False and w.tx_ok("0xnone") is None
    assert key not in repr(w) and key[2:] not in repr(w) and address in repr(w)
    assert w.explorer_url("0xok") == "https://explore.tempo.xyz/tx/0xok"


def test_only_the_testnet_chain():
    key, _ = new_key()
    with pytest.raises(Rejected, match="42431"):
        TempoWallet(key, chain_id=4217)


def test_pay_invoice_refuses_inside_a_running_loop():
    import asyncio

    key, _ = new_key()
    w = TempoWallet(key)

    async def inside():
        w.pay_invoice("http://c.test/x", max_usd="0.50", recipient=WALLET)

    with pytest.raises(RuntimeError, match="thread"):
        asyncio.run(inside())


def test_registry_wallet_switch(tmp_path):
    def ports(**kw):
        return build_ports(Settings(data_dir=tmp_path, **kw))

    assert ports().wallet is None  # MARKET_ENABLED=0: no wallet at all
    assert isinstance(ports(market_enabled=True).wallet, LocalWallet)
    with pytest.raises(AdapterNotReady, match="FIRM_WALLET_KEY"):
        ports(market_enabled=True, adapters={"wallet": "tempo"})
    key, _ = new_key()
    with pytest.raises(AdapterNotReady, match="TEMPO_CHAIN_ID"):
        ports(market_enabled=True, adapters={"wallet": "tempo"}, firm_wallet_key=key,
              tempo_chain_id=4217)
    with pytest.raises(AdapterNotReady, match="valid key") as err:
        ports(market_enabled=True, adapters={"wallet": "tempo"}, firm_wallet_key="0xnotakey")
    assert "notakey" not in str(err.value)
    assert isinstance(ports(market_enabled=True, adapters={"wallet": "tempo"},
                            firm_wallet_key=key).wallet, TempoWallet)


def test_contractor_registry_parses_and_never_shows_the_token(tmp_path):
    token = "t" * 32
    [c] = parse_contractors(f"pixelagent|{token}|{WALLET}|http://127.0.0.1:8020/")
    assert (c.name, c.wallet, c.invoice_base) == ("pixelagent", WALLET, "http://127.0.0.1:8020")
    assert token not in repr(c)
    for bad, why in ((f"pixelagent|{token}|{WALLET}", "name|token"),
                     (f"Pixel Agent|{token}|{WALLET}|http://x", "name must"),
                     (f"pixelagent|short|{WALLET}|http://x", "16 characters"),
                     (f"pixelagent|{token}|0x12|http://x", "0x address"),
                     (f"pixelagent|{token}|{WALLET}|ftp://x", "an http")):
        with pytest.raises(ValueError, match=why) as err:
            parse_contractors(bad)
        assert token not in str(err.value)
    with pytest.raises(AdapterNotReady, match="entry 1"):
        build_ports(Settings(data_dir=tmp_path, market_enabled=True,
                             market_contractors_raw="x|y"))
    build_ports(Settings(data_dir=tmp_path, market_contractors_raw="x|y"))  # off: ignored


def test_market_wallets_is_idempotent_and_prints_no_secret(tmp_path):
    from app import devtools

    backend_env, contractor_env = tmp_path / ".env", tmp_path / "pa" / ".env"
    backend_env.write_text("ADMIN_TOKEN=x\n", encoding="utf-8")
    keys = iter([new_key(), new_key()])
    funded, lines = [], []
    calls = []
    out = devtools.market_wallets(backend_env, contractor_env, out=lines.append,
                                  transport=httpx.MockTransport(rpc_world(calls)),
                                  new_key=lambda: next(keys), fund=funded.append)
    assert funded == [out["firm"], out["pixelagent"]]
    backend = devtools.env_file_values(backend_env)
    contractor = devtools.env_file_values(contractor_env)
    assert backend["FIRM_WALLET_KEY"].startswith("0x") and backend["ADMIN_TOKEN"] == "x"
    [c] = parse_contractors(backend["MARKET_CONTRACTORS"])
    assert c.wallet == out["pixelagent"] and c.token == contractor["MARKET_TOKEN"]
    assert contractor["PIXELAGENT_KEY"].startswith("0x") and len(contractor["MPP_SECRET_KEY"]) == 64
    printed = "\n".join(lines)
    for secret in (backend["FIRM_WALLET_KEY"], contractor["PIXELAGENT_KEY"],
                   contractor["MARKET_TOKEN"], contractor["MPP_SECRET_KEY"]):
        assert secret not in printed
    assert "12.50 pathUSD" in printed
    # a second run keeps every key and funds nobody
    before = (backend_env.read_text(encoding="utf-8"), contractor_env.read_text(encoding="utf-8"))
    again = devtools.market_wallets(backend_env, contractor_env, out=lines.append,
                                    transport=httpx.MockTransport(rpc_world(calls)),
                                    new_key=lambda: pytest.fail("no new key"),
                                    fund=lambda a: pytest.fail("no faucet"))
    assert again == out
    assert (backend_env.read_text(encoding="utf-8"),
            contractor_env.read_text(encoding="utf-8")) == before
