# U-Tax Agent Market: agents hire agents and pay in tokens

Status: PROPOSED (Sun Nov 8, ~12:45 AM). A cuttable layer, so don't start it until the live firm is stable (gateway up, frontend redeployed).
Inspired by Piecework (Stripe track winner, YC "Make Something Agents Want", Oct 2026). **Piecework has no license, so don't copy its code.** Use the same public building blocks (MPP + Tempo) and write our own.

## The idea in one paragraph
U-Tax departments hire outside **contractor agents** for work they don't do themselves, and pay them in **pathUSD**, a test-network stablecoin on Tempo, through the Machine Payments Protocol (MPP). Payment happens **only after the work passes a deterministic check in our code**, the same "math in code, QA before anything leaves" rule U-Tax already follows. Spending stays under owner caps; above a cap, the CEO approves by voice, which counts as one touch. Optional second direction: other companies' agents **pay U-Tax** in pathUSD for a franchise-tax check through a paid endpoint, so agents become customers.

## Why it helps us win
- **Most autonomous:** money moves agent → agent with no human click, inside caps the owner set once.
- **Unicorn story:** U-Tax becomes a firm whose suppliers *and* customers can be agents.
- **Stripe/Tempo/MPP** is exactly what won the Stripe track last month; it's demonstrable with real on-chain test transactions (explorer links on screen).
- **The theater gets a new moment:** a grey "contractor" cursor delivers work, QA ticks it, coins fly from the firm wallet to the contractor wallet, and the tx hash appears.

## Be honest in the demo
- These are **testnet tokens with no real value**. Say "test-network digital dollars, settled on Tempo." Real-money mainnet needs compliance work (later, via Stripe).
- If we run the contractor agents ourselves, say so: "These two contractors are independent agents with their own wallets; any team's agent can sign up at `/market/llms.txt`."

## Jobs U-Tax hires out (pick 1 first)
| # | Buyer dept | Job | Contractor | Deterministic check before paying | Price |
|---|---|---|---|---|---|
| 1 (first) | Marketing | **Share card** PNG: "Team #7 saved $84,715 on Delaware franchise tax" | PixelAgent | PNG, 1200×630, OCR contains the exact amount string and "U-Tax", file < 2 MB | 0.50 pathUSD |
| 2 | Intake | **Cap-table extraction** from a client PDF → JSON {authorized, issued, par, assets} | ParseAgent | schema valid AND every number appears in the PDF text (quoted span), same rule as `parseAmount` | 0.50 pathUSD |
| 3 (stretch) | HR | Candidate shortlist from public portfolios | ScoutAgent | N items, each with a reachable URL and an email that matches the URL's domain/profile | 1.00 pathUSD |
Job 1 carries no client-private data (names masked), so it's the safest first one.

## How a hire works
```
Marketing agent ──tool: post_job(template="share_card", case_id, price=0.50)──► U-Tax gateway (market)
gateway ──event job.posted──► contractor polls GET /market/jobs (or webhook) ──claim──► job.claimed
contractor does the work ──POST /market/jobs/{id}/submit (artifact)──► gateway stores it (not yet usable)
gateway runs verify(template, artifact) IN CODE ──► job.verified  |  job.failed (no pay; contractor may retry once)
gateway: caps OK? (≤ 1.00/job, ≤ 10.00/day) ── else ──► CEO voice approval (1 touch)
gateway (as MPP CLIENT) ──POST {contractor}/invoice/{job_id}──► 402 Payment Required (exact price)
        pays from the FIRM wallet with pympp (Tempo) ──► contractor returns receipt ──► payment.sent {tx_hash}
gateway releases the artifact to Marketing ──► Marketing posts the share card (existing flow)
every step ──► events ──► SSE ──► theater "Market" pane + money panel
```
Why the gateway, not the agent, pays: **agents never hold wallet keys.** The firm wallet's private key lives only in the server env. Agents call tools, and `perform()` enforces verify → caps → pay. It's the same pattern as the QA gate.

## Optional second direction: agents pay U-Tax
`POST /agent/franchise-check` is protected by an MPP charge of 0.50 pathUSD to the firm wallet. The body takes the same 5 facts; the response is the memo JSON + receipt. Publish `/agent/llms.txt` describing it. One route with `pympp` on FastAPI (about 1 hour). Money panel line: "Earned from agents: X pathUSD."

## Pieces
| Piece | Choice | Notes |
|---|---|---|
| Payment protocol | **MPP** via **`pympp`** (Python, fits the FastAPI backend): `pip install "pympp[tempo]"` (Python ≥ 3.11) | server: `@pay(payment.charge(amount="0.50"))` on a FastAPI route; client: `Mpp(...)` + `payment.fetch(...)` with pinned `allowed_chain_ids`, `currency`, `max_amount`. Docs: https://mpp.dev/sdk/python |
| Network | **Tempo Moderato testnet**, chain ID **42431**, RPC `https://rpc.moderato.tempo.xyz`, explorer `https://explore.tempo.xyz` | mainnet is 4217. **Never use mainnet keys tonight** |
| Token | **pathUSD** `0x20c0000000000000000000000000000000000000` | faucet: `tempo_fundAddress` on the testnet RPC mints test stablecoins |
| Wallets | firm wallet (gateway env `FIRM_WALLET_KEY`), one wallet per contractor (contractor env) | generate fresh keys tonight; never reuse a real wallet |
| Contractors | 1 small FastAPI app per contractor: `/invoice/{job}` (402-protected), a worker loop that polls jobs, does the work, submits | PixelAgent: Pillow template render (no LLM needed) or Naïve `generate_image` + text overlay |
| Port | existing `PayPort` → adapters `mock` (ledger only) and `tempo` (pympp) | `ADAPTER_PAY=mock` until the first real tx succeeds |

## Events (add to actions/events types)
`job.posted {job_id, template, price, buyer_dept, case_id}` · `job.claimed {contractor}` · `job.submitted {sha256}` · `job.verified` / `job.failed {reason}` · `payment.approval_needed {amount}` · `payment.sent {amount, token:"pathUSD", tx_hash, explorer_url, contractor}` · `payment.received {amount, payer, tx_hash}` (sell side). All get an idempotency key: `pay:{job_id}` (never pay twice).

## Theater additions
- **Market pane:** job card → grey outlined contractor cursor ("PixelAgent · contractor") drops the artifact → QA cursor ticks the checks → a coin flies from the "U-Tax wallet" chip to the "PixelAgent wallet" chip → the tx hash appears (click → explorer).
- **Money panel:** "Contractors paid: 1.50 pathUSD · Earned from agents: 1.00 pathUSD" alongside saved vs spent.

## Error handling
| What fails | Then |
|---|---|
| Verify fails | no payment; `job.failed`; one retry, then Marketing uses the built-in fallback card |
| 402 payment fails / RPC down | retry with backoff ×2 → `payment.failed`; artifact stays locked; the theater shows a red coin; the case is never blocked (the share card is optional) |
| Over cap | `payment.approval_needed` → CEO says "approve" by phone (touch) or it expires in 30 min |
| Duplicate pay attempt | idempotency key `pay:{job_id}` → no-op |
| Contractor offline | job expires after 10 min → fallback card |
| Faucet empty | switch `ADAPTER_PAY=mock` (ledger tokens) and say so in the demo |

## Tests (must pass before demo)
1. Verify: a good card passes; a wrong amount fails; the wrong size fails.
2. No payment without `job.verified` (unit test on `perform()`).
3. Caps: 1.01 → approval needed; the daily cap holds across two quick jobs.
4. Idempotency: `pay_contractor` twice → one `payment.sent`.
5. E2E on testnet: post → claim → submit → verify → pay → tx hash visible on explorer.
6. Sell side: calling `/agent/franchise-check` without paying → 402; after paying → memo + receipt.

## Time box (about 3.5 h total; cut at any line)
1. **0:00–0:30** Make wallets, call the faucet, and send one manual pympp payment on Moderato. **Go/no-go:** if it fails by 0:30, use `ADAPTER_PAY=mock` (ledger tokens) and keep going.
2. **0:30–1:30** Market routes + `verify(share_card)` + PixelAgent contractor + `pay_contractor` in `perform()`, with tests 1–4.
3. **1:30–2:15** Theater Market pane + money panel line.
4. **2:15–3:00** E2E test 5, one rehearsal take, a backup recording.
5. **3:00–3:30 (optional)** Sell-side `/agent/franchise-check` + test 6.

## Demo line (≈20 s, after the client memo)
"Marketing just hired an outside designer agent. Our QA checked its work in code, then the firm paid it fifty cents in digital dollars, on its own, inside the budget I set once. Here's the transaction. No human clicked anything."
