"""Task 40b (design D36): PayPort on Stripe, test mode unless STRIPE_LIVE=1.

- `create_link`: one Price for the offer (filing help: a worksheet + a 20-minute call,
  $49; U-Tax files nothing), made once per
  gateway and reused (or STRIPE_PRICE_ID), then a Payment Link per case:
  `POST /v1/payment_links` with `metadata[case_id]` and `payment_intent_data[metadata]`,
  sent with an Idempotency-Key so a retry never makes a second link. The URL carries
  `?client_reference_id=<case_id>`, which Stripe copies onto the Checkout Session.
- `verify_stripe`: the `Stripe-Signature` header (`t=<unix>,v1=<hex>`): HMAC-SHA256 of
  `"<t>.<raw body>"` keyed with the endpoint's whole `whsec_...` secret, within 5 minutes.
- No card data ever reaches the gateway: Stripe hosts the checkout page, and the webhook
  is read for ids, the amount and the currency only.

UNVERIFIED against a live account (no key tonight): the shapes follow Stripe's public API
reference (form-encoded bodies, Bearer secret key). Tested with MockTransport.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import quote

import httpx

from app.gateway.ports import (AuthExpired, NotConfigured, PaymentLink, PortError,
                               RateLimited, Rejected, Unavailable)

API = "https://api.stripe.com"
TOLERANCE_SECONDS = 300


def key_mode(key: str) -> str:
    """"test", "live" or "" (not a Stripe secret/restricted key)."""
    for prefix in ("sk_", "rk_"):
        if key.startswith(prefix + "test_"):
            return "test"
        if key.startswith(prefix + "live_"):
            return "live"
    return ""


def _map_error(response: httpx.Response) -> PortError:
    status = response.status_code
    try:
        detail = str((response.json().get("error") or {}).get("message") or "")[:200]
    except (ValueError, AttributeError):
        detail = ""
    if status in (401, 403):
        return AuthExpired(f"stripe refused the key ({status})")
    if status == 429:
        return RateLimited("stripe rate limited")
    if status >= 500:
        return Unavailable(f"stripe {status}")
    return Rejected(f"stripe {status}: {detail}")


class StripePay:
    provider = "stripe"

    def __init__(self, api_key: str, *, price_id: str = "", timeout: float = 10.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        if not api_key:
            raise NotConfigured("pay_unavailable: no STRIPE_SECRET_KEY")
        self.live = key_mode(api_key) == "live"
        # D41 B53: a mirror is a test-mode charge; with any other key it would be real money
        self.test_key = api_key.startswith("sk_test_")
        self._prices: dict[tuple[int, str], str] = {}
        self._price_id = price_id
        self._http = httpx.Client(base_url=API, timeout=httpx.Timeout(timeout, connect=5.0),
                                  transport=transport,
                                  headers={"Authorization": f"Bearer {api_key}",
                                           "User-Agent": "utax-gateway"})

    def __repr__(self) -> str:  # never show the key
        return f"StripePay(live={self.live})"

    def _post(self, path: str, data: dict, idempotency_key: str) -> dict:
        try:
            response = self._http.post(path, data=data,
                                       headers={"Idempotency-Key": idempotency_key[:255]})
        except httpx.TransportError as exc:
            raise Unavailable(f"stripe unreachable: {exc}") from exc
        if not response.is_success:
            raise _map_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise Rejected("stripe answered something that isn't JSON") from exc

    def _price(self, amount_cents: int, label: str) -> str:
        if self._price_id:
            return self._price_id
        cached = self._prices.get((amount_cents, label))
        if cached:
            return cached
        digest = hashlib.sha256(label.encode()).hexdigest()[:12]
        body = self._post("/v1/prices", {"currency": "usd", "unit_amount": str(amount_cents),
                                         "product_data[name]": label},
                          f"utax-price-{amount_cents}-{digest}")
        price = body.get("id")
        if not isinstance(price, str) or not price.startswith("price_"):
            raise Rejected("stripe returned no price id")
        self._prices[(amount_cents, label)] = price
        return price

    def create_link(self, amount_cents: int, label: str, key: str,
                    case_id: str = "") -> PaymentLink:
        price = self._price(amount_cents, label)
        data = {"line_items[0][price]": price, "line_items[0][quantity]": "1"}
        if case_id:
            data["metadata[case_id]"] = case_id
            data["payment_intent_data[metadata][case_id]"] = case_id
        body = self._post("/v1/payment_links", data, f"utax-{key}")
        url, link_id = body.get("url"), body.get("id")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise Rejected("stripe returned no https link")
        if case_id:
            url += ("&" if "?" in url else "?") + "client_reference_id=" + quote(case_id)
        return PaymentLink(url=url, link_id=str(link_id or ""))

    MIRROR_KIND = "contractor_payment_mirror"  # /hooks/stripe never books it as revenue

    def mirror_contractor_payment(self, *, tx_hash: str, amount_usd: str, token: str,
                                  job_id: str, contractor: str, explorer_url: str,
                                  order_id: str | None) -> str:
        """D41 B53: one labelled test-mode PaymentIntent per Tempo contractor payment, so the
        Stripe Dashboard (test mode) shows it. The payment itself is on Tempo testnet: this
        only records it. sk_test_ keys only; the key is idempotent per transaction."""
        if not self.test_key:
            raise NotConfigured("stripe mirror: test mode only (an sk_test_ key)")
        from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

        try:
            usd = Decimal(str(amount_usd))
        except (InvalidOperation, ValueError) as exc:
            raise Rejected(f"stripe mirror: amount {amount_usd!r} is not a number") from exc
        cents = max(50, int((usd * 100).to_integral_value(ROUND_HALF_UP)))  # card minimum $0.50
        body = self._post("/v1/payment_intents", {
            "amount": str(cents), "currency": "usd",
            # B53b: the account's API version refuses payment_method_types; automatic
            # methods without redirects confirm the test card server-side
            "payment_method": "pm_card_visa", "confirm": "true",
            "automatic_payment_methods[enabled]": "true",
            "automatic_payment_methods[allow_redirects]": "never",
            "description": f"U-Tax paid {contractor} for {job_id}: MIRROR of Tempo testnet "
                           f"tx {tx_hash[:10]}…",
            "metadata[utax_kind]": self.MIRROR_KIND, "metadata[job_id]": job_id,
            "metadata[contractor]": contractor, "metadata[amount_token]": f"{amount_usd} {token}",
            "metadata[tempo_tx]": tx_hash, "metadata[explorer_url]": explorer_url,
            **({"metadata[order_id]": order_id} if order_id else {}),
        }, f"utax-mirror-{tx_hash}")
        pi = body.get("id")
        if not isinstance(pi, str) or not pi.startswith("pi_") or body.get("status") != "succeeded":
            raise Rejected("stripe did not record the mirror")
        return pi

    def list_webhooks(self) -> list[dict]:  # devtools stripe-webhook
        try:
            response = self._http.get("/v1/webhook_endpoints", params={"limit": 100})
        except httpx.TransportError as exc:
            raise Unavailable(f"stripe unreachable: {exc}") from exc
        if not response.is_success:
            raise _map_error(response)
        return [w for w in response.json().get("data") or [] if isinstance(w, dict)]


def verify_stripe(secret: str, header: str | None, raw: bytes, *,
                  now: float | None = None, tolerance: int = TOLERANCE_SECONDS) -> bool:
    """True when one v1 signature in `Stripe-Signature` matches and the timestamp is
    within `tolerance` seconds (a replayed old delivery is refused)."""
    if not secret or not header:
        return False
    stamp, signatures = None, []
    for part in header.split(","):
        name, _, value = part.strip().partition("=")
        if name == "t":
            stamp = value
        elif name == "v1":
            signatures.append(value)
    if not stamp or not stamp.isdigit() or not signatures:
        return False
    if abs((time.time() if now is None else now) - int(stamp)) > tolerance:
        return False
    expected = hmac.new(secret.encode(), stamp.encode() + b"." + raw,
                        hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def sign(secret: str, raw: bytes, stamp: int) -> str:
    """The header Stripe would send (tests, and a local replay with devtools)."""
    sig = hmac.new(secret.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={stamp},v1={sig}"
