# Backend lane report

## Outcome

The U-Tax gateway is done for the event. It is a FastAPI backend built to `contracts/api.md`
v1.7.4. An append-only event store is the only source of truth, and every action goes through
one place, `perform()`. That function applies the rules for the QA gate, trust
(ASK / AUTO / PAUSED), caps, masking, idempotency and fail-closed writes. Slow sponsor calls
(the cloud phone, scrapes, data purchases, the fee page, the CRM mirror) run outside the
gateway lock (§6f), so one slow sponsor never freezes the theater, the CEO stop switch or
inbound mail.

Clients mail in, or talk on `/talk`. The local stand-in crew, or the Naïve agents through
per-seat MCP, then take each case through these steps:

1. extract facts from verbatim quotes
2. compute the tax with the tested engine
3. QA
4. memo (with its `Reference: UTX-…` line), sent after CEO approval
5. phone check, then book the review call (an iCalendar invitation)

Each step leaves an event that the theater streams over SSE. There are 807 tests (7 skipped:
Postgres and the live sponsor smoke tests, which need keys). Every READY passed a `/review`
adversarial pass, and fixes were made before merge. A full local end-to-end run on main (all
5 fixtures, the stand-in crew, the CEO approving) ended with no failed events and no errors.

Unverified:
- **Every sponsor adapter against its real service.** AgentMail (mail, webhook, calendar),
  Naïve social, Mobilerun, Apify, HubSpot and Monid are built against their documented APIs
  and tested against in-process fakes. None has made a real call: there were no keys tonight.
- **Postgres:** the store is unit-tested in shape but has never run against a real database.
- **Public URL:** the cloudflared tunnel URL shape. My session's permission check blocked
  `npx cloudflared`, and the controller said it isn't needed tonight.
- **Naïve's MCP client:** never connected to `/mcp/<seat>`, because Naïve access arrives at
  the event.
- **Driver patterns:** the stand-in Intake patterns are tuned on hand-written sentences, not
  real founder mail. When a pattern is unsure it re-asks the client; it never guesses a fact.
- **Prices:** Mobilerun's per-step price ($0.01) is a placeholder.

## What merged (lane/backend → main)

| Merge | What |
|---|---|
| d9a5461 | FastAPI scaffold, `GET /api/health`, the contract error shape |
| d08ce40 | Naïve REST client (async httpx, SSE resume, idempotency) |
| 7af9034 | Task 4: franchise tax (both methods), Form 5472 flag, R&D flag, `parse_amount` |
| b5fbe5c | Task 5: event store (JSONL and Postgres), `perform()`, projections, ten ports with local stand-ins |
| d2033bd | Task 6: HTTP surface (§4/§5), SSE, local crew driver; the controller ran 5 fixtures end to end |
| cfad236 | Task 6b: contract v1.5, EIN redaction, 3 memos per address per day, honest stand-in HR |
| 4cc1f77 | Task 6c: contract v1.6 to v1.6.2: the agent copy of the mail (names for intake and qa only), the tax-word stop list, "Client replied" with memo v2, `greeting_name`, `hr.decision_mailed`, ingress rules (own addresses, auto-replies, HTML) |
| 23c26b1 | Task 7: per-seat MCP at `/mcp/<seat>`: each seat's token checked, only its §6 tools, payload and concurrency capped |
| abee8d7 | 6c LOW and D2: approved slot = booked slot, word-boundary cuts, plain-language re-asks, the driver reading "Delaware corp" / other states / foreign owners, two quadratic regexes made linear |
| 463482c | Task 8: AgentMail mail and the Svix-signed `/hooks/agentmail` webhook (both inboxes), Naïve social posts, the review-call calendar on AgentMail (design D27), contract v1.6.4 |
| 1d9bcd5 | Task 9: contract v1.7 / v1.7.1: slow ports off the lock (async phone check, pre-lock law search, the fee warmed at boot), the phone screenshot private, `memo_ref`, law query cleaning and cap, per-run spend; flagged cases hidden from the theater; Mobilerun and Apify wired |
| ee9b2f1 | E2e run fixes: a slot waiting on the CEO is never offered to another case; HTML-only `.eml` injects |
| e614001 | Task 10: HubSpot and Monid wired; `buy_tool` for seat mkt (contract v1.7.3) with fail-closed money handling and a boot replay; the CRM mirror off the request path, in stage order, honouring Retry-After; the first CEO digest waits a period after boot |

## What the owner must do (Saturday)

1. **Public URL.** Follow `backend/README.md` → "Public URL for Saturday", word for word.
   It has two variants:
   - **A:** `npx cloudflared tunnel --url http://localhost:8000` on the event laptop, with
     uvicorn bound to `127.0.0.1`.
   - **B:** Render. Postgres plus a web service, with `STORE=postgres`.

   Step 1 makes the seven seat tokens plus the admin and theater tokens. Never commit them.
2. **Give each Naïve agent its own token and URL:** `https://<public-host>/mcp/<seat>`
   with `Authorization: Bearer <UTAX_TOOL_TOKEN_SEAT>`. A seat with a missing, short or
   shared token is closed, and the boot log names it.
3. **Set the env the README lists:**
   - `THEATER_ORIGIN`, `PUBLIC_TALK_URL`, `CEO_CONSOLE_URL`, `PUBLIC_MODE=event`,
     `PUBLIC_ASSET_HOSTS`
   - `NAIVE_API_KEY`
   - `CEO_EMAIL`, `MAIL_INBOX_CLIENTS`, `MAIL_INBOX_HIRING`
   - Leave `UTAX_DRIVER` empty when the real crew runs.
4. **Turn on the sponsors one at a time** with the swap table below: paste the key and set
   the switch, restart, then run one fixture end to end (`/api/dev/inject-email`) and watch
   the theater. A switch whose key or setup is missing refuses to boot, with the missing
   variable named, so the demo never runs on a surprise stand-in. Start with
   `ADAPTER_MAIL=agentmail`: the calendar and the phone check need it.
5. **Create the AgentMail webhook** for `message.received`, scoped to the two inboxes, at
   `https://<public-host>/hooks/agentmail`, and put its `whsec_…` secret in
   `AGENTMAIL_WEBHOOK_SECRET`.
6. **If a Monid purchase is left "unsure"** (a timeout after sending, or a crash mid-buy,
   recorded at the next boot), check it in Monid's runs list and settle it once with
   `POST /api/dev/resolve-purchase` (admin token): `{"key": "<agent key, no buy: prefix>",
   "charged": false}` (not charged) or `{"key": "...", "charged": true, "cost_usd": "0.0400"}`
   (charged). A cost above the tool's `max_usd` also needs `"over_ceiling": true`; nothing
   over $1000 is accepted. A purchase still in flight can't be resolved; wait for it.
   The purchase intents live in `UTAX_DATA_DIR` (the Render disk at /var/data/utax): never
   point it at a disk that is wiped on deploy, or a deploy mid-purchase forgets a charge.
7. **Don't run two Render instances on one database.** Deploy stop-then-start.

## Saturday adapter swap

Each port is picked by `ADAPTER_<PORT>` (`backend/app/gateway/registry.py`). Every switch
below is built except `ADAPTER_PAY=stripe` (skipped: the owner's Stripe account is blocked
until January 2027) and `ADAPTER_LLM` (nothing calls it). Each adapter's contract suite runs
against an in-process fake of its API (`backend/tests/adapters/fakes.py` and the agents
lane's `contract_factory` functions); the live smoke tests skip without keys. All keys stay
in `backend/.env`, never in git; `backend/.env.example` documents every variable.

| Switch | Status | Env / setup it needs | Suite that proves it |
|---|---|---|---|
| `STORE=postgres` | built, never run against a real DB | `DATABASE_URL`; non-empty `ADMIN_TOKEN` and `THEATER_TOKEN` (refuses to boot otherwise); the table from `db/001_events.sql` | `tests/contract/test_event_store.py` with `UTAX_TEST_DATABASE_URL=<a throwaway database>` (5 of tonight's skips) |
| `ADAPTER_MAIL=agentmail` | built (task 8) | `AGENTMAIL_API_KEY`; `MAIL_INBOX_CLIENTS` (required) and `MAIL_INBOX_HIRING`, the inbox addresses; the webhook of owner step 5 with `AGENTMAIL_WEBHOOK_SECRET` (whsec_…, checked at boot; without it, `UTAX_HOOK_TOKEN` in an `X-UTax-Hook-Token` header) | the mail suite against the AgentMail fake, `tests/adapters/test_agentmail.py`, `test_task8_review.py`; live: `test_live_send_once` with `AGENTMAIL_TEST_TO` |
| `ADAPTER_CALENDAR=agentmail` (D27) | built (task 8) | the AgentMail mail env, and `ADAPTER_MAIL=agentmail` (refuses to boot otherwise); optional `UTAX_CALL_LOCATION` (the meeting link on the invitation); US region only | `test_calendar_book_is_idempotent` against the fake, the calendar tests in `test_agentmail.py` and `test_task8_review.py` |
| `ADAPTER_SOCIAL=naive` | built (task 8) | `NAIVE_API_KEY` (scope `sessions:write`); `NAIVE_SOCIAL_IDENTITY=utax-mkt`, or its `idn_…` id from `naive identity list` if a post answers 404 (reported as not configured); its social workspace activated and X/LinkedIn connected; `PUBLIC_TALK_URL`; `PUBLIC_ASSET_HOSTS` for images | `test_social_post_is_idempotent` against the Naïve fake, `tests/adapters/test_naive_social.py`; live: `test_live_post_once` with `NAIVE_SOCIAL_TEST_POST=yes` (it posts publicly) |
| `ADAPTER_DEVICE=mobilerun` | built (agents lane, wired in task 9) | `MOBILERUN_API_KEY`; `MOBILERUN_MEMO_SENDER` (required: the clients inbox address); `MAIL_QA_BCC` (the mailbox the cloud phone's mail app is signed in to; every memo is BCC'd there; keep it private); `ADAPTER_MAIL=agentmail`; optional `MOBILERUN_DEVICE_ID`, `MOBILERUN_MAIL_APP`, `MOBILERUN_MODEL`. About $0.25 a run (placeholder price) | `test_device_capture` against the Mobilerun fake, `tests/adapters/test_mobilerun.py`, the phone tests in `tests/gateway/test_contract_v17.py` |
| `ADAPTER_SCRAPE=apify` | built (agents lane, wired in task 9) | `APIFY_TOKEN`. Each run is capped at $0.10; law_search at 20 an hour; the fee page is fetched at boot and every 5 h, and a fee that differs from the snapshot keeps the snapshot and tells the CEO | `test_scrape_fee_page_and_search` against the Apify fake, `tests/adapters/test_apify.py`, the law and fee tests in `test_contract_v17.py` |
| `ADAPTER_CRM=hubspot` | built (agents lane, wired in task 10) | `HUBSPOT_TOKEN`: a **private app** token with `crm.objects.contacts.write` and `crm.objects.deals.write`. Setup: in Settings > Properties add a **unique** single-line text property `utax_key` on **Contacts** and on **Deals**. Optional `HUBSPOT_PIPELINE` (default `default`) and `HUBSPOT_STAGE_MAP` (JSON: `memo_sent` / `call_booked` to that pipeline's stage ids). Only masked labels (Team #N) are mirrored | `test_crm_upsert_is_idempotent_and_maps_errors` against the HubSpot fake, `tests/adapters/test_hubspot.py`, `tests/gateway/test_task10.py` |
| `ADAPTER_TOOLBUY=monid` | built, but **HOLD: keep `local` until task 17b merges** (controller 2026-11-07: unsure-purchase edge cases still open) | `MONID_API_KEY`; `MONID_TOOLS` (JSON allowlist; empty = nothing can be bought; each tool needs `max_usd`, e.g. `{"enrichment": {"provider": "exa", "endpoint": "search", "max_usd": "0.05", "fields": ["query"]}}`; pick provider and endpoint with Monid's `POST /v1/discover`); `MONID_DAILY_CAP_USD` (default 1.00). Setup: also set a **run cap control** in Monid's dashboard. Used by `buy_tool` (seat mkt) | `test_toolbuy_reports_decimal_cost` against the Monid fake, `tests/adapters/test_monid.py`, `tests/gateway/test_task10.py`, `test_task10_review.py` |
| `ADAPTER_VOICE=openai` | built | `OPENAI_API_KEY` (paid: owner's word first); optional `OPENAI_REALTIME_MODEL`, `OPENAI_TTS_MODEL`, `OPENAI_TTS_VOICE`. Without a key, `/api/voice/session` answers 503 | `test_voice_without_key_is_not_configured`, the voice tests in `tests/gateway/test_api.py`; live: open `/talk` and speak |
| `ADAPTER_PAY=stripe` | not built (skipped) | `STRIPE_SECRET_KEY` | `test_pay_has_no_adapter_yet` |
| `ADAPTER_LLM` | not built (nothing calls LLMPort) | `OPENROUTER_API_KEY` or `OPENAI_API_KEY` | `test_llm_reports_tokens_and_maps_errors` |

Each suite also checks the error mapping through `FaultPlan`: 429 becomes `RateLimited`
(retried after Retry-After), 5xx and timeouts become `Unavailable` (retried), 401 becomes
`NotConfigured` and 4xx `Rejected` (never retried). After a swap, run the whole backend suite
(`pytest` in `backend/`): the gateway tests use the local adapters, so they keep passing.

## Post-hackathon

Accepted for the hackathon by the controller, to fix afterwards:

- **Mail and calendar sends still run under the gateway lock** (§6f covers the slow ports;
  these are fast, with 3 s / 8 s timeouts). Fix: the same pre-lock / post-lock split as the
  phone check.
- **The inbound hook does work in the request** (task 8, mail review 8). `/hooks/agentmail`
  opens the case, sends the hiring questionnaire and delivers the wakes before it answers.
  Mitigation: short timeouts, and the message-id dedupe makes AgentMail's retries safe. Fix:
  write the inbound event, answer 200, and run the rest from a queue.
- **Inbound dedupe keys on the sender's Message-ID** (mail review 11), not scoped by inbox,
  so a resend that reuses an id is dropped as a duplicate. Fix: key on a hash of inbox_id +
  message_id (an event key format change: needs a migration note).
- **A booking that times out after AgentMail stored it** (calendar review 5). After 3
  timeouts the case is BLOCKED although the invitation went out; a different slot then sends
  a second invitation. Fix: on a timeout, look the event up by client_id first.
- **A 2xx can hide a refused invitation** (calendar review 10): `call.booked` is written but
  the client got nothing. Fix: watch message.sent / message.rejected for the invitation and
  flag the case.
- **An approved slot the real calendar keeps rejecting** holds the other slots until it
  passes (6c LOW item 7).
- **Pending phone checks live in memory** (task 9 review 5). A restart drops them; a started
  Mobilerun task keeps running (paid) and is never polled. The case never blocks, and the next
  phone_check starts a fresh attempt. Fix: a `phone.started` event and resumed polling at boot.
- **The QA mailbox can be spoofed** (task 9 review 9). The memo reference is public, so
  anyone who knows `MAIL_QA_BCC` can mail a "Reference: UTX-…" decoy that burns the 3
  attempts. Tonight: keep that address private and set `MOBILERUN_MEMO_SENDER`. Every memo is
  also BCC'd to a mailbox a third-party cloud phone reads. Fix: a per-memo secret in the BCC
  copy only, and SPF/DKIM checks on the phone side.
- **The Monid boot replay writes the adapter's private state** (task 10). If those
  attributes change, it logs an error and skips the replay (today's spend is then not
  replayed). Fix: a public `restore()` on `MonidToolBuy` (the agents lane's file).
- **buy_tool's input filter** refuses emails, phones, SSN/EIN and case ids, but not personal
  or company names (mkt has no case-reading tool, and spend is capped). Fix: check inputs
  against the known client names, as `post_social` does.
- **A crash-looping server never sends a CEO digest** (each restart waits a full
  `CEO_DIGEST_HOURS`). Decided for the demo; revisit for production.

## Lesson

Time hostile input on every regex that runs under a lock, including the ones that aren't
new. Every review round found one more quadratic regex (`html_to_text`, `tidy_lines`, the
driver patterns), and the worst was in code that had passed 400 tests for days:
`intake._NUMBER` took 8.7 s on 4,000 repeated "a" words, while holding the lock every gateway
call waits on. Unit tests on realistic text never show this. The fix that held up was to stop
asking "is this regex correct?" and run every pattern on 50 kB of repeated filler, spaces and
unclosed tags, with a time limit in the test. The same habit, applied to money ("what happens
if the write fails right after the charge?"), found the worst bug of task 10.
