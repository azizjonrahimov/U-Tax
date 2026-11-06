# Backend lane report

## Outcome

The U-Tax gateway is done for the event. It is a FastAPI backend built to `contracts/api.md`
v1.6.3. An append-only event store is the only source of truth, and every action goes
through one place, `perform()`. That function applies the rules for the QA gate, trust
(ASK / AUTO / PAUSED), caps, masking, idempotency and fail-closed writes.

Clients mail in. The local stand-in crew, or the Naïve agents through per-seat MCP, then
take each case through these steps:

1. extract facts from verbatim quotes
2. compute the tax with the tested engine
3. QA
4. memo, sent after CEO approval
5. book the call

Each step leaves an event that the theater streams over SSE. There are 461 tests (5 skipped
without a Postgres URL). Every READY passed a `/review` adversarial pass, and fixes were made
before merge.

Unverified:
- **Real adapters:** every sponsor adapter except OpenAI voice is unbuilt, so they have not
  been run against a real service (see the adapter swap section).
- **Postgres:** the store is unit-shaped but has never run against a real database.
- **Public URL:** the cloudflared tunnel URL shape. My session's permission check blocked
  `npx cloudflared`, and the controller said it isn't needed tonight.
- **Naïve's MCP client:** never connected to `/mcp/<seat>`, because Naïve access arrives at
  the event.
- **Driver patterns:** the D2 stand-in Intake patterns are tuned on hand-written sentences,
  not real founder mail. When a pattern is unsure it re-asks the client; it never guesses
  a fact.

## What merged (lane/backend → main)

| Merge | What |
|---|---|
| d9a5461 | FastAPI scaffold, `GET /api/health`, the contract error shape |
| d08ce40 | Naïve REST client (async httpx, SSE resume, idempotency) |
| 7af9034 | Task 4: franchise tax (both methods), Form 5472 flag, R&D flag, `parse_amount` |
| b5fbe5c | Task 5: event store (JSONL and Postgres), `perform()`, projections, ten ports with local stand-ins |
| d2033bd | Task 6: HTTP surface (§4/§5), SSE, local crew driver; the controller ran 5 fixtures end to end |
| cfad236 | Task 6b: contract v1.5, EIN redaction, 3 memos per address per day, honest stand-in HR |
| 4cc1f77 | Task 6c: contract v1.6 to v1.6.2. Covered: the agent copy of the mail (names for intake and qa only), the tax-word stop list in masking, "Client replied" with memo v2, `greeting_name`, `hr.decision_mailed`, ingress rules (own addresses, auto-replies, HTML) |
| 23c26b1 | Task 7: per-seat MCP at `/mcp/<seat>`. Each seat's token is checked, the seat sees only its §6 tools, and payload and concurrency are capped |
| abee8d7 | 6c LOW and D2. Covered: approved slot = booked slot, word-boundary cuts, newline collapse, plain-language re-asks, the driver reading "Delaware corp" / other states / foreign owners, and two quadratic regexes made linear |

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
   - `THEATER_ORIGIN`, `PUBLIC_TALK_URL`, `CEO_CONSOLE_URL`, `PUBLIC_MODE=event`
   - `NAIVE_API_KEY`
   - `CEO_EMAIL`, `MAIL_INBOX_CLIENTS`, `MAIL_INBOX_HIRING`
   - Leave `UTAX_DRIVER` empty when the real crew runs.
4. **Swap adapters only after their contract suite passes** (next section). Naming a
   sponsor adapter that isn't built fails at boot on purpose, so the demo never runs on a
   surprise stand-in.
5. **Don't run two Render instances on one database.** Deploy stop-then-start.

## Saturday adapter swap

Each port is picked by `ADAPTER_<PORT>` (`backend/app/gateway/registry.py`). Today only the
local stand-ins exist, plus `ADAPTER_VOICE=openai` and `STORE=postgres`.

A sponsor adapter is done when both of these hold:
- Its factory is added to that port's `FACTORIES` in `backend/tests/contract/test_ports.py`.
  The factory skips itself when the key is missing.
- The suite named below passes with the key set.

Then `build_port` returns the adapter instead of raising `AdapterNotReady`.

Key names marked *proposed* are not in the code yet. They come from the sponsor's docs
(`research/sponsors.md`). Add each to `.env.example` when its adapter lands, and keep
every key out of git.

| Switch | Status tonight | Env / keys it needs | Suite that proves it |
|---|---|---|---|
| `STORE=postgres` | **built**, never run against a real DB | `DATABASE_URL`; non-empty `ADMIN_TOKEN` and `THEATER_TOKEN` (refuses to boot otherwise); the table from `db/001_events.sql` | `tests/contract/test_event_store.py` with `UTAX_TEST_DATABASE_URL=<a throwaway database>`. Those are tonight's 5 skips. |
| `ADAPTER_VOICE=openai` | **built** | `OPENAI_API_KEY`, plus optional `OPENAI_REALTIME_MODEL`, `OPENAI_TTS_MODEL`, `OPENAI_TTS_VOICE`. Without a key it falls back to no voice (`/api/voice/session` answers 503). Paid: owner's word first. | `test_voice_without_key_is_not_configured`, plus the voice tests in `tests/gateway/test_api.py`. A live check means opening `/talk` and speaking. |
| `ADAPTER_MAIL=agentmail` | not built | `AGENTMAIL_API_KEY`; `MAIL_INBOX_CLIENTS` / `MAIL_INBOX_HIRING` set to the AgentMail addresses; a `message.received` webhook pointed at the gateway | mail suite: `test_mail_send_returns_a_message_id`, `test_mail_same_key_no_second_mail`, `test_mail_maps_rate_limit`, `test_mail_bad_recipient_is_rejected_not_retryable`, `test_mail_parses_fixture_inbox` (all 4 fixtures) |
| `ADAPTER_LLM=openrouter` / `openai` | not built (no gateway step needs an LLM tonight) | `OPENROUTER_API_KEY` or `OPENAI_API_KEY` | `test_llm_reports_tokens_and_maps_errors` |
| `ADAPTER_CRM=hubspot` | not built | *proposed* `HUBSPOT_ACCESS_TOKEN` (private app token) | `test_crm_upsert_is_idempotent_and_maps_errors` |
| `ADAPTER_DEVICE=mobilerun` | not built | `MOBILERUN_API_KEY` | `test_device_capture` |
| `ADAPTER_SCRAPE=apify` | not built | `APIFY_TOKEN` | `test_scrape_fee_page_and_search` (fee page against `fixtures/snapshots/delaware-fees.html`) |
| `ADAPTER_TOOLBUY=monid` | not built | *proposed* `MONID_API_KEY` | `test_toolbuy_reports_decimal_cost` (cost must be a Decimal, booked as `cost.booked`) |
| `ADAPTER_CALENDAR=google` | not built | *proposed* `GOOGLE_CALENDAR_ID` plus `GOOGLE_SERVICE_ACCOUNT_JSON` | `test_calendar_book_is_idempotent`. Before it goes live, also close deferred item 7: an approved slot the real calendar keeps rejecting must not hold the other slots. |
| `ADAPTER_SOCIAL=naive` | not built | `NAIVE_API_KEY`; `PUBLIC_TALK_URL` must be set (posts are refused without it) | `test_social_post_is_idempotent` |
| `ADAPTER_PAY=stripe` | not built (no payment flow in v1.6) | `STRIPE_SECRET_KEY` (`sk_test_` first) | `test_pay_has_no_adapter_yet` must be replaced by a real pay suite |

Each suite also checks error mapping through `FaultPlan`: 429 becomes `RateLimited`
(retried), 503 becomes `Unavailable` and 401 becomes `NotConfigured` (never retried). For
an HTTP adapter, the factory drives these with an `httpx.MockTransport`. After a swap, run
the whole backend suite (`pytest` in `backend/`). The gateway tests use the local adapters,
so they keep passing. For the real service, run one fixture end to end with the
`/api/dev/inject-email` admin route, and check the theater's event stream.

## Lesson

Time hostile input on every regex that runs under a lock, including the ones that aren't
new. Every review round tonight found one more quadratic regex (`html_to_text`,
`tidy_lines`, the driver patterns), and the worst was in code that had passed 400 tests
for days. `intake._NUMBER` took 8.7 s on 4,000 repeated "a" words, while holding the lock
every gateway call waits on. Unit tests on realistic text never show this. The fix that
held up was to stop asking "is this regex correct?" and run every pattern on 50 kB of
repeated filler, spaces and unclosed tags, with a time limit in the test.
