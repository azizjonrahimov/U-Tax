# D38 RUNBOOK — U-Tax hires agents and pays them in tokens

**For:** the controller session (`naive-yc-hackathon-99`) and the `backend`, `agents` and `frontend` lanes.
**Written:** Sun Nov 8 2026, ~1:30 AM, by Claude (the owner's planning chat), against the code on disk at ~1:00 AM (`contracts/api.md` v1.17, decisions up to D37, `perform.py` with task 40c).
**Owner's words (verbatim):** "can we make something similar to our tax and accounting firm like our agents hire other agents and will pay with tokens to these hired agents instead of money" … "make very detailed plan and here also is the stripe api key … so agent can do the entire work without making mistakes and stops".
**Authority:** the owner's standing "auto-decide" rule (CREW.md). Every choice below is already made; don't ask, build. New scope, so this file **is** the rule-5 plan: the controller replies `APPROVED` to each lane brief in §9 without waiting for the owner.

---

## 0. Read this first: rules that keep this from going wrong

1. **Don't start D38 until the live firm is healthy.** Run your usual preflight. Gateway up, frontend deployed, `naive up` done. If anything is red, fix that first. D38 is a **cuttable layer**; the demo must work with D38 switched off (`MARKET_ENABLED=0`).
2. **Never stop on a failure. Fall back.** Every step has a fallback in §11. The biggest one: if the Tempo testnet or `pympp` fails at gate G1, set `ADAPTER_WALLET=local` and keep building. Everything else works the same, and the payment shows as a "ledger token" payment.
3. **Testnet only.** Chain 42431 (Tempo Moderato). `TempoWallet` refuses any other chain. Never put a mainnet key or a real wallet anywhere.
4. **Secrets:** never print, log, commit or paste a private key or the Stripe key. Keys live only in gitignored `.env` files. `repr()` of wallet objects never shows keys (the reference code already does this).
5. **Product law still holds** (CREW rule 12). Math and checks live in code. Only the **gateway** moves money (no agent ever holds a wallet key). Events are the only truth. Every animation comes from a real event. Masked by default.
6. **Honesty in the demo:** these are **test-network tokens with no real value**, and PixelAgent is run by us (an independent process with its own wallet, open to any agent via `/market/llms.txt`). Say both.
7. **Time box:** all D38 code merged by **06:30**, rehearsed by **08:30**, frozen with the rest at **10:00**. If a task runs 45 min over its box, cut to the next fallback in §11 and move on.

---

## 1. What was verified tonight (so nobody re-researches it)

| Fact | Value | How it was verified |
|---|---|---|
| Python payment SDK | **`pympp` 0.20.0**, `pip install "pympp[tempo,sqlite]==0.20.0"`, needs Python ≥ 3.11 (backend venv is **3.12.3** ✓) | installed and run |
| Network | Tempo **Moderato testnet**, chain **42431** (`0xa5bf`), RPC `https://rpc.moderato.tempo.xyz`, explorer `https://explore.tempo.xyz/tx/<hash>` | `eth_chainId` answered `0xa5bf` |
| Token | **pathUSD** `0x20c0000000000000000000000000000000000000`, 6 decimals | balances read on-chain |
| Faucet | JSON-RPC `tempo_fundAddress [address]` on the testnet RPC → 1,000,000 pathUSD (and others) | funded 3 fresh wallets |
| Fees | payer pays ~0.00018 pathUSD per payment, in pathUSD (no gas coin needed) | balance diff |
| **Proof 1** | firm wallet paid a 402 invoice 0.50 pathUSD | tx `0x865bc3b48829be3d39ceb7e40f07ffcc0cbb41541f66ad4aa7cb64ec9b19e445` |
| **Proof 2 (full loop)** | PixelAgent polled → claimed → rendered → submitted; `share_card.verify` passed; firm paid 0.50; tx mined ok; a 2nd invoice call returned the **same tx, no charge**; an **overcharge** (0.90 vs cap 0.50) and a **wrong recipient** were **refused** by the wallet | tx `0x3b60adac073316d2a4d9a0adceb4446afc2dee1fe1f4b5395476aacf0b7d2277` |
| Stripe test account | `acct_1URoJAFRfhAMzNKo`, US, test mode, crypto deposit addresses ✓, Connect list ✓, **Stripe business profile: NOT created** (needed only for the optional Stripe-recorded sell side, §7) | Stripe API |
| Stripe Tempo deposit address (test) | `cda_1UZRxEFRfhAMzNKoTqJKVcLw` → `0x5ff8d73e8bccd3701c9aef78389f3b9771172b5c` (created tonight) | `POST /v1/crypto/deposit_addresses network=tempo` |

### Gotchas already hit (don't hit them again)
- **G1** `tempo client.charge(max_amount=...)` **requires** `max_amount_decimals=6`, or it raises `ValueError`.
- **G2** The `@pay(...)` decorator calls your endpoint as `endpoint(request, credential, receipt)`, with **no path params**. Type the first param `request: Request` (FastAPI), name the others exactly `credential` and `receipt`, and read path values from `request.path_params[...]`. Otherwise FastAPI answers 422.
- **G3** The `Mpp` server needs `secret_key=` (or env `MPP_SECRET_KEY`) and a `realm=`.
- **G4** The default replay store is in-memory (a warning prints). One process is fine; for restarts use `mpp.stores.SQLiteStore` (`pympp[sqlite]`).
- **G5** `TempoWallet.pay_invoice` runs `asyncio.run`, so call it from sync code or a worker thread, **never** from inside a running event loop. In the gateway, call it from the background tick (§5.5), which runs sync jobs.
- **G6** Your post guard: `post_social` refuses "Team #…"/"case …" references and any amount outside `$85,165 / $400 / $450 / $50`. The share-card headline is fixed: **`Delaware franchise tax: $85,165 → $450`**.
- **G7** `_asset_url_ok` only accepts `https://<UTAX_PUBLIC_URL host>/assets/ad_<n>.png`. Save delivered cards as `ad_<n>.png` (the next free number), so no regex change is needed.

---

## 2. Secrets and env (step 0, the controller, about 5 minutes)

**The core feature needs no Stripe key.** Contractor payments use Tempo testnet wallets (2.3). The owner's Stripe **test** key is only for the optional Stripe-recorded sell side (§5 41d, §12), and the owner adds it to `.env` with a hidden prompt (the remote link can't write `.env` files, and the key must never go in chat or git):

**2.1 (owner, optional, 30 s) Add the Stripe test key without showing it.** PowerShell, in the folder of the `.env` the running gateway reads (main checkout `backend\.env`, or the worktree's if the gateway runs there):
```powershell
cd "D:\Claude Projects\Naive YC Hackathon\backend"
$k = Read-Host "Paste the sk_test_ key" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($k))
if ($plain -notlike "sk_test_*") { Write-Host "Not a test key; nothing written." } else {
  (Get-Content .env) | Where-Object { $_ -notmatch '^STRIPE_SECRET_KEY=' } | Set-Content .env
  Add-Content .env "STRIPE_SECRET_KEY=$plain"
  Add-Content .env "STRIPE_TEMPO_DEPOSIT_ADDRESS=0x5ff8d73e8bccd3701c9aef78389f3b9771172b5c"
  Write-Host "Saved to .env (not shown)." }
Remove-Variable plain, k
```
The controller **doesn't wait** for this: everything except the Stripe-recorded sell side runs without it.

**2.2 New env vars** (add to `backend\.env.example` with comments and no values; real values in `.env`):
```
# D38 agent market (all optional; MARKET_ENABLED=0 turns the whole feature off)
MARKET_ENABLED=1
ADAPTER_WALLET=local              # local | tempo   (tempo after gate G1)
TEMPO_CHAIN_ID=42431              # testnet only; anything else refuses to boot
FIRM_WALLET_KEY=                  # 0x...; written by `python -m app.devtools market-wallets`
MARKET_PRICE_USD=0.50             # default job price
MARKET_MAX_JOB_USD=1.00           # per-job cap; above it the CEO approves that payment
MARKET_MAX_DAY_USD=10.00          # per-day cap across all contractor payments
MARKET_JOB_TTL_MINUTES=10         # an unclaimed or unsubmitted job expires
MARKET_CONTRACTORS=               # name|bearer_token|wallet|invoice_base ; comma-separated
                                  # e.g. pixelagent|<random 32 hex>|0xABC...|http://127.0.0.1:8020
MPP_SECRET_KEY=                   # random 64 hex, for the sell-side 402 challenges
SELL_ENABLED=1                    # /agent/franchise-check paid by agents
SELL_PRICE_USD=0.50
STRIPE_PROFILE_ID=                # profile_test_...; empty = sell side pays the firm wallet directly
STRIPE_TEMPO_DEPOSIT_ADDRESS=0x5ff8d73e8bccd3701c9aef78389f3b9771172b5c   # test mode, created 2026-11-08 (not secret)
```

**2.3 Wallets** (backend lane builds the command in task 41a; the controller runs it once):
```powershell
cd "D:\Claude Projects\Naive YC Hackathon\backend"
.\.venv\Scripts\python -m app.devtools market-wallets
```
It generates **two fresh keys** (firm, pixelagent), funds both from the faucet, and appends `FIRM_WALLET_KEY=` to `backend\.env`, plus `PIXELAGENT_KEY=` and `MPP_SECRET_KEY=` to `contractors\pixelagent\.env`. It also writes a fresh `MARKET_CONTRACTORS=` line (with a random token) to `backend\.env` and `MARKET_TOKEN=` to the pixelagent `.env`. It prints **only addresses and balances**.

---

## 3. Decision record (controller appends to `docs/designs/u-tax.md`, decisions table)

`| D38 | Agents hire agents and pay in tokens (2026-11-08) | Owner: "can we make something similar … our agents hire other agents and will pay with tokens to these hired agents instead of money" and "make very detailed plan … so agent can do the entire work without making mistakes and stops". Auto-decided per this runbook: **(1)** an agent market in the gateway: Marketing calls `hire_contractor` (template `share_card`); outside contractor agents with their own wallets claim and submit through `/market/*`; **the gateway** checks the work in code and only then pays the contractor's MPP 402 invoice in **pathUSD on Tempo testnet** (pympp), within `MARKET_MAX_JOB_USD`/`MARKET_MAX_DAY_USD` and a new trust type `contractor_pay` (one spoken approval, then AUTO); **(2)** PixelAgent, our first independent contractor (its own process, wallet and cost model); **(3)** optional sell side: other agents pay U-Tax 0.50 pathUSD at `/agent/franchise-check` (recorded in Stripe when `STRIPE_PROFILE_ID` is set); **(4)** honesty: test tokens with no value, PixelAgent run by us, and checks are filters, not proofs. Off switch: `MARKET_ENABLED=0`. |`

---

## 4. Contract diff → v1.18 (controller edits `contracts/api.md`, then tells lanes to `git merge main`)

**4.1 Changelog line:**
`- v1.18 (2026-11-08, D38): agent market: MCP tools hire_contractor/get_job (mkt); /market/* contractor routes; /agent/franchise-check (MPP 402); events job.*, payment.sent, agent.sale; provider tempo; trust type contractor_pay.`

**4.2 §3 events (exact payload keys; all masked; no private keys ever):**
| type | actor | provider | payload keys | notes |
|---|---|---|---|---|
| `job.posted` | mkt | null | `job_id, template, price_usd, headline` | idem `job:{key}` |
| `job.claimed` | system | null | `job_id, contractor` | contractor = registry name |
| `job.submitted` | system | null | `job_id, contractor, sha256` | artifact stays private until paid |
| `job.verified` | system | null | `job_id, checks` | checks = `[{name, ok}]` |
| `job.failed` | system | null | `job_id, checks, reason` | reason: `checks_failed` / `expired` / `contractor_error` |
| `payment.sent` | system | `tempo` or `local` | `job_id, contractor, amount_usd, token, tx_hash, explorer_url` | `cost_usd` = amount; status `failed` + private `unsure` on an unsure send |
| `job.delivered` | system | null | `job_id, image_url` | image is now `/assets/ad_<n>.png` |
| `agent.sale` | system | `tempo` or `stripe` | `amount_usd, token, tx_hash, explorer_url, payer_masked` | sell side; payer = `0x12…ab` |
Also: `PROVIDERS += {"tempo"}`; the same payment also writes `cost.booked` (`provider tempo|local`, `model "contractor:share_card"`, `estimated false`), so the money panel's spend includes it (the gateway books money; no agent does).

**4.3 §6 MCP tools (Marketing only):**
- `hire_contractor {template: "share_card", headline: string, key: string}` → `{job_id, status: "open", price_usd}`. Headline must pass `post_problem()` and `_names_a_client()` (same as `make_ad`). The price comes from `MARKET_PRICE_USD`; agents can't set it.
- `get_job {job_id}` → `{job_id, status: open|claimed|submitted|verified|paid|delivered|failed|expired, contractor?, image_url?, tx_hash?, explorer_url?, checks?}`.
Marketing then posts with the existing `post_social {…, image_url}`.

**4.4 New §5-ter "Agent market" routes (contractors, Bearer = their `MARKET_CONTRACTORS` token):**
- `GET /market/llms.txt`: plain-text docs for agents (how to claim, submit and get paid). No auth.
- `GET /market/jobs?status=open` → `{jobs: [{job_id, template, price_usd, input: {headline}, expires_at}]}`
- `POST /market/jobs/{job_id}/claim {wallet, invoice_url}` → 200 job | 409 `taken` | 403 `wallet_mismatch` (wallet must equal the registry wallet; invoice_url must start with the registry `invoice_base`) | 410 `expired`
- `POST /market/jobs/{job_id}/submit` (multipart `file`, ≤ 2 MB) → 200 `{verified: bool, checks}` | 409 `not_claimed_by_you`
**§5-quater "Sell to agents":**
- `GET /agent/llms.txt`: what U-Tax sells to agents, the price and the input schema.
- `POST /agent/franchise-check {authorized_shares, issued_shares, gross_assets_cents, stated_par?}` → **402** MPP challenge (0.50 pathUSD) → paid: `{authorized_method_cents, assumed_par_method_cents, total_cents, due_date, note: "Educational estimate, not tax advice", receipt_tx}`. It uses `app/engine` only. No case, no email, no PII.

**4.5 Trust:** `TRUST_TYPES += ("contractor_pay",)`; `CAP_EVENTS["contractor_pay"] = ("payment.sent",)`. Starts `ASK`: the first payment needs the CEO's spoken approval (training touch), then AUTO within caps. A payment over `MARKET_MAX_JOB_USD`, or one that would pass `MARKET_MAX_DAY_USD`, always asks.

---

## 5. Backend lane: tasks 41a–41e (Python, `backend/` + new `contractors/`)

> Ownership: the controller assigns the new top-level `contractors/` folder to **backend** (CREW rule 3 amendment). The reference code is in `docs/designs/agent-market/` (tested tonight). Copy it, don't import from `docs/`.

### 41a: dependencies, ports, wallet adapters, devtools (≈ 45 min)
1. `backend\requirements.txt` add: `pympp[tempo,sqlite]==0.20.0`. Install and run the full suite:
   ```powershell
   cd "D:\Claude Projects\Naive YC Hackathon\.worktrees\backend\backend"
   .\.venv\Scripts\pip install "pympp[tempo,sqlite]==0.20.0"
   .\.venv\Scripts\python -m pytest -q
   ```
   (If the worktree has no `.venv`, use the main checkout's `backend\.venv\Scripts\python`.) If anything that was green turns red, `pympp`'s pins (`urllib3==2.8.0`, `yarl==1.24.5`) are the suspect. Run `/investigate`, and if it isn't fixed in 15 min, install pympp into a **separate venv used only by the contractor**, and have the gateway's wallet call go through a subprocess (`python -m app.wallet_cli pay ...`) running in that venv.
2. `app/gateway/ports/__init__.py`: add a `WalletPort` Protocol (`provider`, `address`, `balance_usd()`, `tx_ok(tx)`, `pay_invoice(url, *, max_usd, recipient) -> PaidInvoice`) plus the `PaidInvoice` dataclass. Add `wallet: WalletPort` to `Ports`.
3. `app/gateway/adapters/tempo_wallet.py`: copy `docs/designs/agent-market/tempo_wallet.py`, then map `WalletUnavailable→Unavailable`, `WalletRejected→Rejected`, and add `class Unsure(PortError): code="unsure"` (or reuse the toolbuy one) for `WalletUnsure`.
4. `app/gateway/adapters/local.py`: `LocalWallet` (copy from the reference; fake tx `local:<sha>`).
5. `registry.py`: `"wallet": ("tempo",)`, default `local`. `ADAPTER_WALLET=tempo` needs `FIRM_WALLET_KEY` and `TEMPO_CHAIN_ID=42431` (else `AdapterNotReady` with the exact env name).
6. `settings.py`: fields for every §2.2 variable. `market_contractors` parses `name|token|wallet|invoice_base`. Reject a malformed line at boot with a clear message, and **never** log the token.
7. `app/devtools.py`: commands
   - `market-wallets` (§2.3, idempotent: if `FIRM_WALLET_KEY` is set, only print the address and balance; never overwrite a key)
   - `market-balance` (firm + every contractor wallet)
   - `market-faucet <address>`
8. Tests `tests/adapters/test_wallet.py`: LocalWallet contract; TempoWallet with `httpx.MockTransport` for `balance_usd` and `tx_ok`; chain ≠ 42431 refused; `repr` has no key. **Network test** `tests/adapters/test_wallet_live.py` marked `@pytest.mark.live` (skipped by default): faucet + pay a local 402 server (copy `docs/designs/agent-market/e2e.py`'s server part).
**Gate G1 (by 41a's end):** `python -m app.devtools market-wallets` prints two funded addresses, **and** the live test passes once. Not passing in 30 min → `ADAPTER_WALLET=local`, tell the controller, and continue 41b with local.

### 41b: the market inside the gateway (≈ 90 min)
Follow the **money-safe pattern of `buy_tool`** exactly (intent before paying, `store.ensure_writable()`, the log as memory, unsure → resolve).
1. **State** (`projections.py`): `jobs: dict[job_id, Job]` rebuilt from `job.*`/`payment.sent`/`job.delivered`. `Job` fields: id, template, headline, price_usd, status, contractor, sha256, checks, tx_hash, explorer_url, image_url, posted_at, expires_at. Counters: `contractor_paid_usd` (Σ payment.sent ok), `agent_sales_usd` (Σ agent.sale). Add both to the money panel/state payload (`public_state`).
2. **Events** (`events.py`): the §4.2 types with exact keys; `PROVIDERS` add `"tempo"`.
3. **Trust** (`projections.py`): `TRUST_TYPES` + `"contractor_pay"`; `CAP_EVENTS["contractor_pay"] = ("payment.sent",)`.
4. **Tools** (`perform.py`): `TOOL_ACTORS["hire_contractor"]=("mkt",)`, `TOOL_ACTORS["get_job"]=("mkt",)`; register both in `_handlers`.
   - `_hire_contractor(args)`: `MARKET_ENABLED` else `Refused("market_off")`; template must be `share_card`; headline guards (as `make_ad`); `key` required; `job_id = f"job_{n}"`; `_done_from(f"job:{key}")` → duplicate returns the same job; emit `job.posted` (actor mkt), `expires_at = now + TTL`.
   - `_get_job(args)`: reads state; never returns the artifact bytes or private fields.
5. **Contractor routes**: a new router file `app/market_api.py`, included in `main.py` next to `build_mcp_router`. Each route calls a **control action** through `gw.perform(...)` with no actor (contractors are never MCP actors):
   - `market_claim {job_id, contractor, wallet, invoice_url}` → checks (open, not expired, wallet == registry, invoice_url startswith registry base) → `job.claimed`.
   - `market_submit {job_id, contractor, data(bytes)}` → size ≤ 2 MB → save private copy `data/market/{job_id}.png` → `job.submitted` → run `share_card.verify(data, job.headline)` (copy `docs/designs/agent-market/share_card.py` to `app/gateway/share_card.py`) → `job.verified` or `job.failed{reason:"checks_failed"}`. One resubmit allowed after a fail, then the job stays failed.
   - Auth: Bearer token → contractor name (constant-time compare). Rate limit: 30 req/min per token (reuse your per-IP limiter pattern).
6. **Payment** (gateway-owned, a new tick): `gw.advance_market_jobs()` added to the periodic list in `app/main.py` (line ~36, next to `advance_phone_jobs`). For each `verified` job, **outside the lock**:
   1. `_gate("contractor_pay", f"pay:{job_id}", f"pay {contractor} {price} pathUSD for {job_id}", None)` → `NeedsApproval` = wait (the CEO's console/voice approve flow already handles it; that's the training touch).
   2. Caps: price ≤ `MARKET_MAX_JOB_USD` and today's `payment.sent` sum + price ≤ `MARKET_MAX_DAY_USD`, else an approval for this payment.
   3. Intent: `_KeyedJson(data_dir/"market-pay-intents.json").put(f"pay:{job_id}", {...})`, after `store.ensure_writable()`. An intent already present means this job's payment is unsure (resolve, below).
   4. **Ask the contractor first**: `GET {invoice_base}/invoice/{job_id}/status`. If it says paid with a tx, and `wallet.tx_ok(tx)` is True, book that (no new payment).
   5. Else `wallet.pay_invoice(f"{invoice_base}/invoice/{job_id}", max_usd=price, recipient=registry_wallet)`.
   6. Success: emit `payment.sent` (provider tempo|local, `cost_usd=price`, idem `pay:{job_id}`) + `cost.booked` (idem `cost:pay:{job_id}`) → copy the private PNG to `data/assets/ad_<next>.png` → `job.delivered {image_url}` → clear the intent → **wake Marketing** (existing wake path), with a card note: "job_N delivered: post it with image_url".
   7. `Unsure`: emit `payment.sent status=failed` with private `{unsure: true}`; keep the intent; on the next tick, step 4 resolves it (status endpoint + `tx_ok`). **Never** call `pay_invoice` while an intent exists without step 4 first.
   8. `Rejected`/`Unavailable`: retry with your `SEND_ATTEMPTS`/backoff; after 3, `job.failed{reason:"contractor_error"}` and clear the intent (nothing was paid: the wallet refuses before signing).
7. **Expiry:** the tick also emits `job.failed{reason:"expired"}` for jobs open or claimed past `expires_at`.
8. **Tests** `tests/gateway/test_task41.py` (all with LocalWallet + a fake contractor via `httpx.MockTransport`):
   - `test_hire_requires_mkt_and_guards_headline` ("Team #7" refused; G6 headline accepted)
   - `test_claim_wallet_mismatch_403`, `test_claim_taken_409`, `test_claim_expired_410`
   - `test_submit_verify_pass_and_fail` (bad dimensions → failed; one resubmit)
   - `test_no_payment_without_verified`
   - `test_first_payment_asks_ceo_then_auto` (trust training touch)
   - `test_caps_per_job_and_per_day_ask`
   - `test_pay_is_idempotent` (two ticks → one `payment.sent`)
   - `test_unsure_resolves_from_contractor_status_without_second_payment`
   - `test_delivered_asset_passes_asset_url_ok_and_post_social` (G7)
   - `test_market_off_refuses_everything`
   - `test_money_panel_counts_contractor_paid`
   - `test_events_payload_keys_match_contract_v1_18`

### 41c: PixelAgent, the first contractor (≈ 40 min)
1. `contractors/pixelagent/`: copy `docs/designs/agent-market/pixelagent.py` and `share_card.py`, then add a `requirements.txt` (`fastapi uvicorn httpx pillow "pympp[tempo,sqlite]==0.20.0"`) and a `.env.example` (`PIXELAGENT_KEY, MARKET_URL, MARKET_TOKEN, MPP_SECRET_KEY, PIXELAGENT_PORT=8020, PIXELAGENT_MIN_USD=0.30, PIXELAGENT_POLL_SECONDS=3, PIXELAGENT_STATE=data/pixelagent-state.json`).
2. Swap its in-memory replay store for `SQLiteStore` (G4): `tempo.charge(..., store=await SQLiteStore.create("data/pixelagent-mpp.db"))` inside a FastAPI lifespan.
3. Optional "judgement" (only if time allows): before taking a job, one cheap LLM call (OpenAI key, D36) writes a one-line reason ("take: $0.50 ≥ my $0.30 cost"). Show it in its console log. Keep the numeric rule as the decider.
4. Run (its own terminal; venv per G-fallback in 41a.1 if needed):
   ```powershell
   cd "D:\Claude Projects\Naive YC Hackathon\contractors\pixelagent"
   python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
   .\.venv\Scripts\python -m uvicorn pixelagent:app --host 127.0.0.1 --port 8020
   ```
   `MARKET_URL` = the gateway URL (local `http://127.0.0.1:8000`, or the public URL if the gateway runs on Render). `invoice_base` in `MARKET_CONTRACTORS` must be an address **the gateway can reach**: localhost if both run on the laptop; if the gateway is on Render, expose PixelAgent with `npx cloudflared tunnel --url http://localhost:8020` and use that https URL.
5. Test `contractors/pixelagent/test_pixelagent.py`: `decide()` take/skip; a status of an unknown job → 404; an invoice for a job not submitted → 404; a paid job's second invoice → duplicate with the same tx.

### 41d: sell side, agents pay U-Tax (≈ 40 min, cut first if late)
1. `app/sell_api.py` router: `GET /agent/llms.txt`, `POST /agent/franchise-check`.
2. The 402 server uses pympp, with recipient selection:
   - **`STRIPE_PROFILE_ID` set** → Stripe-recorded: `from mpp.methods.stripe import create as stripe_mp`; `stripe_mp(network_id=STRIPE_PROFILE_ID, livemode=False, client=StripeClient(STRIPE_SECRET_KEY), store=SQLiteStore, deposit_addresses={"tempo": STRIPE_TEMPO_DEPOSIT_ADDRESS})`, then use its Tempo charge method. Payments show in the Stripe **test** Dashboard → Payments. *UNVERIFIED tonight (no profile yet); if it errors, fall back to the next bullet.*
   - **Else** → plain `tempo.charge(recipient=<firm wallet address>, chain_id=42431, currencies=[PATH_USD], store=SQLiteStore)`.
3. Handler (G2 signature) → validate input with the existing facts parser → `app/engine` franchise methods → `agent.sale` event (actor system, provider tempo|stripe) → JSON answer including `"Educational estimate, not tax advice"`.
4. Tests: unpaid → 402 with `WWW-Authenticate: Payment`; bad input → 422 **before** any challenge (never charge for bad input); a paid path via LocalWallet-style fake verification (pympp offers test hooks; else mark live-only).
5. Demo client (devtools): `python -m app.devtools sell-demo` pays from a **third** fresh "buyer agent" wallet and prints the answer + explorer link.

### 41e: E2E command and READY (≈ 20 min)
`python -m app.devtools market-e2e` (needs the gateway and PixelAgent running) does all of this:
- hires one job as `mkt` via `perform` with an actor
- waits ≤ 60 s for delivered
- prints the job timeline, `tx_hash` and explorer link, and the money panel numbers

Exit code 0 only if it ends `delivered` with `tx_ok` True (or `local:` with `ADAPTER_WALLET=local`). Then: `/review`, `pytest -q`, READY per CREW rule 7.

---

## 6. Agents lane: task A41 (≈ 25 min)
1. `naive/naive.config.ts`: add the MCP tools `hire_contractor`, `get_job` to **marketing** only.
2. `naive/prompts/marketing.md`, new section "Hiring outside agents (D38)":
   - When the CEO orders a share card, or after any memo saved a client over $80,000: call `hire_contractor {template:"share_card", headline:"Delaware franchise tax: $85,165 → $450", key:"card:<case or order id>"}`.
   - Then `get_job` every ~20 s, up to 3 min. On `delivered`: `post_social` to X and LinkedIn with its `image_url` (the existing trust rules apply). On `failed`/`expired`: use `make_ad` instead, and say so on the card.
   - **Never** discuss or set prices, wallets or keys; you don't pay. The firm pays after its checks.
3. `naive/prompts/chief-of-staff.md`: map CEO phrases ("hire a designer", "get an outside agent to make the card", "pay the contractor") to Marketing with that order.
4. `naive/prompts/ceo-assistant.md`: it may approve `contractor_pay` when the CEO says "approve the payment" (the existing approval tool).
5. `npx tsc --noEmit`, `naive up --dry-run`, then `naive up` once the backend 41b is MERGED and deployed.

---

## 7. Frontend lane: task F41 (≈ 60 min)
1. `src/theater/events.ts`: add the §4.2 types.
2. `src/theater/script.ts` + `components/theater/Panes.tsx`: a **Market** pane, its animation driven only by events:
   - `job.posted` → a Marketing cursor drops a job card ("share_card · 0.50 pathUSD")
   - `job.claimed` → a **grey, outlined contractor cursor** labeled "PixelAgent · contractor" picks it up
   - `job.submitted` → a thumbnail slides in (blurred until `job.delivered`)
   - `job.verified` → each check ticks; `job.failed` → the failing check is circled red
   - `approval.requested{action_type:contractor_pay}` → the white CEO cursor; `touch.training` → "approved"
   - `payment.sent` → a coin flies from the "U-Tax wallet" chip to the "PixelAgent wallet" chip; the tx hash is shortened (`0x3b60…2277`) and links to `explorer_url`; provider `local` shows "ledger token" instead
   - `job.delivered` → the card unblurs and moves to the Social pane (the existing post animation follows the real `post.published`)
3. Money rail (`theater/rail.ts`): add "Contractors paid: X pathUSD" and "Earned from agents: Y pathUSD" from `public_state`.
4. Office Marketing workspace (`src/office/workspace.ts`): the same job card timeline (D37 style).
5. Stage mode: the Market pane is readable at 1.5×.
6. `npm run lint`, `npm run build`, `vitest` (add `script.test.ts` cases: a payment animation only after `payment.sent`; nothing animates for unknown types), `/design-review`, `/qa`, then READY and redeploy per `crew/scripts/deploy-frontend.ps1`.

---

## 8. Demo (add after the client memo, ≈ 25 s)
1. CEO (voice): "Get an outside designer agent to make a share card for that result."
2. On screen: the Marketing card posts the job, the PixelAgent cursor claims it, the card arrives, and the checks tick.
3. First time only: the console asks "Pay PixelAgent 0.50 pathUSD?" and the CEO says "approve" (one training touch; later payments are automatic).
4. The coin flies, and the tx link opens on the explorer.
5. The card posts to X and LinkedIn.
6. Line: *"Marketing just hired an outside agent. Our code checked its work, then the firm paid it fifty cents in test-network digital dollars, on its own, inside a budget I set once. Here's the transaction. Other agents can also buy our franchise-tax check the same way."*
7. Optional, if 41d merged: run `python -m app.devtools sell-demo` and show "Earned from agents" tick up.
Record a backup take by 08:30.

---

## 9. Lane briefs (controller sends these; reply APPROVED immediately; plan = this file)
- **backend:** "D38 tasks 41a→41e per docs/designs/agent-market/RUNBOOK.md §5. Gate G1 at the end of 41a: report go (tempo) or no-go (local) and keep going. READY after 41b+41c (41d is separate, cut if past 05:30). Contract v1.18 lands before you start 41b."
- **agents:** "D38 task A41 per RUNBOOK §6, after backend 41b MERGED."
- **frontend:** "D38 task F41 per RUNBOOK §7. Start now with fixture events (events from RUNBOOK §4.2); switch to live events after backend 41b MERGED."

## 10. Timeline (Sunday)
| By | What | Gate |
|---|---|---|
| 01:45 | controller: D38 row, contract v1.18, briefs (§2.1 is the owner's, optional, never waited on) | — |
| 02:30 | backend 41a done | **G1: tempo or local** |
| 04:00 | backend 41b + 41c READY → MERGED | `market-e2e` passes locally |
| 04:30 | agents A41 MERGED, `naive up` | Marketing hires on a CEO order |
| 05:00 | frontend F41 READY → deployed | Market pane animates a real job |
| 05:30 | backend 41d (or cut) | `sell-demo` passes |
| 06:30 | all D38 merged | full rehearsal with D38 |
| 08:30 | backup recording | — |
| 10:00 | freeze (everything) | — |

## 11. Failure playbook (never stop: do the right column)
| Symptom | Do |
|---|---|
| `pip install pympp` breaks other tests | separate venv for pympp + `app.wallet_cli` subprocess (41a.1); if still broken in 15 min → `ADAPTER_WALLET=local` |
| faucet error / RPC down | retry in 2 min; then `ADAPTER_WALLET=local` ("ledger tokens" on screen, say so) |
| `ValueError … max_amount_decimals` | G1: pass `max_amount_decimals=6` |
| 422 from the invoice route | G2: endpoint signature `(request: Request, credential, receipt)` |
| `NoMethodFoundError` when paying | the contractor asked more than `max_usd`, or the wrong chain/currency: correct; the job fails as `contractor_error` |
| payment timed out | it's `Unsure`: never re-pay; the next tick checks `/invoice/{id}/status` and `tx_ok` |
| contractor not reachable from Render | run the gateway locally, or tunnel PixelAgent (41c.4) |
| `post_social` refuses the image | G6/G7: the headline must be the fixed one; the asset must be `ad_<n>.png` on `UTAX_PUBLIC_URL` |
| Stripe-recorded sell side errors | leave `STRIPE_PROFILE_ID` empty → plain Tempo to the firm wallet |
| anything else past its time box | `MARKET_ENABLED=0`; the demo runs without D38 |

## 12. The owner's only optional steps
1. (Optional) Run §2.1 to add the Stripe test key. Then (2 min), for sell-side payments to appear in **Stripe**: in the Stripe Dashboard (test mode), create a Stripe profile (docs.stripe.com/get-started/account/profile), copy its `profile_test_…` ID, and tell the controller to put it in `STRIPE_PROFILE_ID`. Skip this and everything else still works.
2. After the event: rotate the Stripe test key (it was pasted in chat) and delete the testnet wallets' keys.
