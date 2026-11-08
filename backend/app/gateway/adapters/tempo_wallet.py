"""D38 (task 47a): WalletPort on Tempo, paying a contractor's MPP 402 invoice in pathUSD.

From docs/designs/agent-market/tempo_wallet.py (proven on Tempo Moderato, chain 42431, with
pympp 0.20.0 on 2026-11-08). Testnet only: any other chain is refused at construction.

- `pay_invoice(url, max_usd, recipient)`: POST the invoice; on its 402 pay AT MOST `max_usd`
  pathUSD to exactly `recipient` (pympp refuses an overcharge or another recipient BEFORE
  signing). A timeout after paying may have started is `Unsure`: never pay again without
  checking the contractor's status and `tx_ok` first.
- Sync API: each call runs its own event loop (asyncio.run), so it's called from the
  background tick or a worker thread, never from inside a running loop (gotcha G5).
- The key never leaves this object: `repr` shows the address only.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx

from app.gateway.ports import PaidInvoice, Rejected, Unavailable, Unsure

TESTNET_CHAIN_ID = 42431
RPC_URL = "https://rpc.moderato.tempo.xyz"
PATH_USD = "0x20c0000000000000000000000000000000000000"  # 6 decimals
EXPLORER = "https://explore.tempo.xyz/tx/"
DECIMALS = 6
TOKEN = "pathUSD"


def rpc(method: str, params: list, *, rpc_url: str = RPC_URL, timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None):
    try:
        with httpx.Client(timeout=timeout, transport=transport) as http:
            r = http.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method,
                                         "params": params})
    except httpx.TransportError as exc:
        raise Unavailable(f"tempo rpc unreachable: {type(exc).__name__}") from exc
    try:
        body = r.json()
    except ValueError as exc:
        raise Unavailable(f"tempo rpc answered {r.status_code}, not JSON") from exc
    if "error" in body:
        raise Rejected(f"tempo rpc {method}: {str(body['error'])[:200]}")
    return body.get("result")


def balance_of(address: str, *, rpc_url: str = RPC_URL,
               transport: httpx.BaseTransport | None = None) -> Decimal:
    """pathUSD held by `address` (an ERC-20 balanceOf call)."""
    addr = address[2:].lower().rjust(64, "0")
    raw = rpc("eth_call", [{"to": PATH_USD, "data": "0x70a08231" + addr}, "latest"],
              rpc_url=rpc_url, transport=transport)
    return Decimal(int(raw or "0x0", 16)) / Decimal(10 ** DECIMALS)


def faucet(address: str, *, rpc_url: str = RPC_URL,
           transport: httpx.BaseTransport | None = None):
    """Testnet only: mints test stablecoins (pathUSD and others) to `address`."""
    return rpc("tempo_fundAddress", [address], rpc_url=rpc_url, transport=transport)


def new_key() -> tuple[str, str]:
    """(private key 0x..., address): a fresh testnet wallet."""
    from eth_account import Account

    account = Account.create()
    return "0x" + account.key.hex().removeprefix("0x"), account.address


class TempoWallet:
    provider = "tempo"
    token = TOKEN

    def __init__(self, private_key: str, *, chain_id: int = TESTNET_CHAIN_ID,
                 rpc_url: str = RPC_URL, timeout: float = 60.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        if chain_id != TESTNET_CHAIN_ID:
            raise Rejected(f"testnet only: TEMPO_CHAIN_ID must be {TESTNET_CHAIN_ID} (Moderato)")
        from mpp.methods.tempo import TempoAccount

        key = private_key if private_key.startswith("0x") else "0x" + private_key
        self._account = TempoAccount.from_key(key)
        self.chain_id, self.rpc_url, self.timeout = chain_id, rpc_url, timeout
        self._transport = transport  # tests: the RPC reads

    def __repr__(self) -> str:  # never show the key
        return f"TempoWallet({self.address})"

    @property
    def address(self) -> str:
        return self._account.address

    def balance_usd(self, address: str | None = None) -> Decimal:
        return balance_of(address or self.address, rpc_url=self.rpc_url,
                          transport=self._transport)

    def tx_ok(self, tx_hash: str) -> bool | None:
        """True mined ok, False reverted, None not found (yet)."""
        receipt = rpc("eth_getTransactionReceipt", [tx_hash], rpc_url=self.rpc_url,
                      transport=self._transport)
        if not receipt:
            return None
        return receipt.get("status") == "0x1"

    def explorer_url(self, tx_hash: str) -> str:
        return EXPLORER + tx_hash

    def pay_invoice(self, url: str, *, max_usd: str, recipient: str) -> PaidInvoice:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._pay(url, max_usd=max_usd, recipient=recipient))
        raise RuntimeError("pay_invoice runs its own loop: call it from a thread (G5)")

    async def _pay(self, url: str, *, max_usd: str, recipient: str) -> PaidInvoice:
        from mpp.client import Mpp
        from mpp.methods.tempo import client as tempo

        method = tempo.charge(account=self._account, chain_id=self.chain_id,
                              currencies=[PATH_USD], max_amount=max_usd,
                              max_amount_decimals=DECIMALS,  # G1: both or neither
                              expected_recipients=[recipient],
                              allowed_chain_ids=[self.chain_id])
        try:
            async with Mpp(methods=[method]) as mpp:
                r = await asyncio.wait_for(mpp.fetch(url, method="POST"), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise Unsure(f"no answer within {self.timeout:.0f}s after paying may have "
                         "started") from exc
        except httpx.TransportError as exc:
            raise Unavailable(f"invoice unreachable: {type(exc).__name__}") from exc
        except Exception as exc:  # noqa: BLE001 - mpp PaymentError, NoMethodFoundError, policy
            raise Rejected(f"payment refused: {type(exc).__name__}: {exc}"[:300]) from exc
        if r.status_code != 200:
            raise Rejected(f"invoice answered {r.status_code}")
        try:
            body = r.json()
        except ValueError as exc:
            raise Unsure("the paid answer wasn't JSON") from exc
        tx = str(body.get("tx") or "")
        if not tx.startswith("0x"):
            raise Unsure("the paid answer carried no transaction hash")
        return PaidInvoice(tx_hash=tx, amount_usd=str(body.get("amount_usd") or max_usd),
                           explorer_url=EXPLORER + tx, paid=True)
