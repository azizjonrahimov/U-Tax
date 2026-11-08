"""WalletPort adapters: pay a contractor's MPP 402 invoice in pathUSD on Tempo.

Proven on Tempo Moderato (chain 42431) with pympp 0.20.0 on 2026-11-08.
`TempoWallet` pays for real (testnet by default); `LocalWallet` is the stand-in (no network).
Sync API (perform() is sync): each call runs its own event loop, so never call these from
inside a running event loop (FastAPI sync endpoints and worker threads are fine).
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from decimal import Decimal

import httpx

TESTNET_CHAIN_ID = 42431
MAINNET_CHAIN_ID = 4217
RPC_URLS = {TESTNET_CHAIN_ID: "https://rpc.moderato.tempo.xyz", MAINNET_CHAIN_ID: "https://rpc.tempo.xyz"}
PATH_USD = "0x20c0000000000000000000000000000000000000"  # same address on testnet and mainnet
EXPLORER = "https://explore.tempo.xyz/tx/"
DECIMALS = 6


class WalletError(RuntimeError):
    code = "wallet_error"
    retryable = False


class WalletUnavailable(WalletError):
    code = "unavailable"
    retryable = True


class WalletRejected(WalletError):
    code = "rejected"


class WalletUnsure(WalletError):
    """The payment may have gone out (timeout after signing): check status before any retry."""
    code = "unsure"


@dataclass(frozen=True)
class PaidInvoice:
    tx_hash: str
    amount_usd: str  # decimal string, the price paid (fee not included)
    explorer_url: str
    paid: bool  # False for the stand-in


def _rpc(rpc_url: str, method: str, params: list, timeout: float = 20.0):
    try:
        r = httpx.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                       timeout=timeout)
    except httpx.TransportError as exc:
        raise WalletUnavailable(f"tempo rpc unreachable: {exc}") from exc
    body = r.json()
    if "error" in body:
        raise WalletRejected(f"tempo rpc {method}: {body['error']}")
    return body["result"]


class TempoWallet:
    provider = "tempo"

    def __init__(self, private_key: str, *, chain_id: int = TESTNET_CHAIN_ID, rpc_url: str | None = None,
                 timeout: float = 60.0) -> None:
        if chain_id != TESTNET_CHAIN_ID:
            raise WalletRejected("mainnet is off tonight: chain_id must be 42431 (Moderato)")
        from mpp.methods.tempo import TempoAccount
        key = private_key if private_key.startswith("0x") else "0x" + private_key
        self._account = TempoAccount.from_key(key)
        self.chain_id = chain_id
        self.rpc_url = rpc_url or RPC_URLS[chain_id]
        self.timeout = timeout

    def __repr__(self) -> str:  # never show the key
        return f"TempoWallet({self.address})"

    @property
    def address(self) -> str:
        return self._account.address

    def balance_usd(self, address: str | None = None, token: str = PATH_USD) -> Decimal:
        addr = (address or self.address)[2:].lower().rjust(64, "0")
        raw = _rpc(self.rpc_url, "eth_call", [{"to": token, "data": "0x70a08231" + addr}, "latest"])
        return Decimal(int(raw, 16)) / Decimal(10 ** DECIMALS)

    def tx_ok(self, tx_hash: str) -> bool | None:
        """True mined ok, False reverted, None not found (yet)."""
        receipt = _rpc(self.rpc_url, "eth_getTransactionReceipt", [tx_hash])
        if not receipt:
            return None
        return receipt.get("status") == "0x1"

    def pay_invoice(self, url: str, *, max_usd: str, recipient: str) -> PaidInvoice:
        """POST url; on 402 pay at most max_usd pathUSD to exactly `recipient`; return the receipt."""
        return asyncio.run(self._pay(url, max_usd=max_usd, recipient=recipient))

    async def _pay(self, url: str, *, max_usd: str, recipient: str) -> PaidInvoice:
        from mpp.client import Mpp
        from mpp.methods.tempo import client as tempo
        method = tempo.charge(account=self._account, chain_id=self.chain_id, currencies=[PATH_USD],
                              max_amount=max_usd, max_amount_decimals=DECIMALS,  # G1: both or neither
                              expected_recipients=[recipient], allowed_chain_ids=[self.chain_id])
        try:
            async with Mpp(methods=[method]) as mpp:
                r = await asyncio.wait_for(mpp.fetch(url, method="POST"), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise WalletUnsure(f"no answer from {url} within {self.timeout}s after paying may have started") from exc
        except httpx.TransportError as exc:
            raise WalletUnavailable(f"invoice unreachable: {exc}") from exc
        except Exception as exc:  # mpp PaymentError, NoMethodFoundError, policy refusals
            raise WalletRejected(f"payment refused: {type(exc).__name__}: {exc}"[:300]) from exc
        if r.status_code != 200:
            raise WalletRejected(f"invoice answered {r.status_code}: {r.text[:200]}")
        body = r.json()
        tx = str(body.get("tx") or "")
        if not tx.startswith("0x"):
            raise WalletUnsure("paid answer carried no transaction hash")
        return PaidInvoice(tx_hash=tx, amount_usd=str(body.get("amount_usd") or max_usd),
                           explorer_url=EXPLORER + tx, paid=True)


class LocalWallet:
    """Stand-in: records a fake payment, touches no network (ADAPTER_WALLET=local)."""
    provider = "local"
    address = "0x000000000000000000000000000000000000dEaD"

    def balance_usd(self, address: str | None = None, token: str = PATH_USD) -> Decimal:
        return Decimal("1000")

    def tx_ok(self, tx_hash: str) -> bool | None:
        return True if tx_hash.startswith("local:") else None

    def pay_invoice(self, url: str, *, max_usd: str, recipient: str) -> PaidInvoice:
        tx = "local:" + hashlib.sha256(f"{url}|{recipient}|{max_usd}".encode()).hexdigest()[:24]
        return PaidInvoice(tx_hash=tx, amount_usd=max_usd, explorer_url="", paid=False)


def faucet(address: str, rpc_url: str = RPC_URLS[TESTNET_CHAIN_ID]) -> list[str]:
    """Testnet only: mints test stablecoins (pathUSD and others) to `address`."""
    return _rpc(rpc_url, "tempo_fundAddress", [address])
