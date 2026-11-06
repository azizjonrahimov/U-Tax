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

Each port is picked by `ADAPTER_<PORT>` (`backend/app/gateway/registry.py`). Built: the local stand-ins, `STORE=postgres`, `ADAPTER_VOICE=openai`, and since task 8 `ADAPTER_MAIL=agentmail`, `ADAPTER_CALENDAR=agentmail` and `ADAPTER_SOCIAL=naive`. The agents lane builds Mobilerun, Apify, HubSpot and Monid; I wire them as they land.

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
| `ADAPTER_MAIL=agentmail` | **built** (task 8), never run against the real API | `AGENTMAIL_API_KEY`; `MAIL_INBOX_CLIENTS` (required) and `MAIL_INBOX_HIRING`, the inbox addresses; a `message.received` webhook scoped to those two inboxes, pointed at `https://<public-host>/hooks/agentmail`, with its secret in `AGENTMAIL_WEBHOOK_SECRET` (whsec_..., checked at boot) | the mail suite against the AgentMail fake (`tests/adapters/fakes.py`), plus `tests/adapters/test_agentmail.py` and `test_task8_review.py`; live: `test_live_send_once` with `AGENTMAIL_TEST_TO` set |
| `ADAPTER_LLM=openrouter` / `openai` | not built (no gateway step needs an LLM tonight) | `OPENROUTER_API_KEY` or `OPENAI_API_KEY` | `test_llm_reports_tokens_and_maps_errors` |
| `ADAPTER_CRM=hubspot` | not built | *proposed* `HUBSPOT_ACCESS_TOKEN` (private app token) | `test_crm_upsert_is_idempotent_and_maps_errors` |
| `ADAPTER_DEVICE=mobilerun` | not built | `MOBILERUN_API_KEY` | `test_device_capture` |
| `ADAPTER_SCRAPE=apify` | not built | `APIFY_TOKEN` | `test_scrape_fee_page_and_search` (fee page against `fixtures/snapshots/delaware-fees.html`) |
| `ADAPTER_TOOLBUY=monid` | not built | *proposed* `MONID_API_KEY` | `test_toolbuy_reports_decimal_cost` (cost must be a Decimal, booked as `cost.booked`) |
| `ADAPTER_CALENDAR=agentmail` (design D27; replaces google) | **built** (task 8), never run against the real API | the AgentMail mail env above, and `ADAPTER_MAIL=agentmail` too (refuses to boot otherwise); optional `UTAX_CALL_LOCATION` (the meeting link shown on the invitation); US region only | `test_calendar_book_is_idempotent` against the fake, plus the calendar tests in `test_agentmail.py` and `test_task8_review.py`. Deferred item 7 still stands: an approved slot the calendar keeps rejecting holds the other slots. |
| `ADAPTER_SOCIAL=naive` | **built** (task 8), never run against the real API | `NAIVE_API_KEY` (scope `sessions:write`); `NAIVE_SOCIAL_IDENTITY=utax-mkt`; if Naïve refuses the name in the path (a post answers 404, reported as not configured), the owner sets it to the `idn_...` id from `naive identity list`, with its social workspace activated and X/LinkedIn connected; `PUBLIC_TALK_URL`; `PUBLIC_ASSET_HOSTS` for images | `test_social_post_is_idempotent` against the Naïve fake, plus `tests/adapters/test_naive_social.py`; live: `test_live_post_once` with `NAIVE_SOCIAL_TEST_POST=yes` (it posts publicly) |
| `ADAPTER_PAY=stripe` | not built (no payment flow in v1.6) | `STRIPE_SECRET_KEY` (`sk_test_` first) | `test_pay_has_no_adapter_yet` must be replaced by a real pay suite |

Each suite also checks error mapping through `FaultPlan`: 429 becomes `RateLimited`
(retried), 503 becomes `Unavailable` and 401 becomes `NotConfigured` (never retried). For
an HTTP adapter, the factory drives these with an `httpx.MockTransport`. After a swap, run
the whole backend suite (`pytest` in `backend/`). The gateway tests use the local adapters,
so they keep passing. For the real service, run one fixture end to end with the
`/api/dev/inject-email` admin route, and check the theater's event stream.

## Post-hackathon

Accepted for the hackathon by the controller (task 8 review), to fix afterwards:

- **Inbound hook does work in the request** (mail review 8). `/hooks/agentmail` opens
  the case, sends the hiring questionnaire and delivers the wakes before it answers.
  Tonight's mitigation: 3 s / 8 s timeouts, and the message-id dedupe makes AgentMail's
  retries safe. Fix: write the inbound event, answer 200, and run the rest from a queue.
- **Inbound dedupe keys on the sender's Message-ID** (mail review 11). `email:{message_id}`
  uses an id the sender chooses, not scoped by inbox, so a resend that reuses an id is
  dropped as a duplicate. Fix: key on a hash of inbox_id + message_id (an event key
  format change, so it needs a migration note).
- **A booking that times out after AgentMail stored it** (calendar review 5). After 3
  timeouts the case is BLOCKED although the invitation went out. Re-booking the same slot
  replays safely (same client_id); a different slot sends a second invitation, and the
  first event is never cancelled. Fix: on a timeout, look the event up by client_id first.
- **A 2xx can hide a refused invitation** (calendar review 10). AgentMail stores the event
  even when outbound checks refuse the invitation mail, so `call.booked` is written but the
  client got nothing. Fix: subscribe to message.sent / message.rejected for the invitation
  and flag the case.
- **Deferred since 6c LOW, item 7**: an approved slot the real calendar keeps rejecting
  holds the other slots until it passes.

## Lesson

Time hostile input on every regex that runs under a lock, including the ones that aren't
new. Every review round tonight found one more quadratic regex (`html_to_text`,
`tidy_lines`, the driver patterns), and the worst was in code that had passed 400 tests
for days. `intake._NUMBER` took 8.7 s on 4,000 repeated "a" words, while holding the lock
every gateway call waits on. Unit tests on realistic text never show this. The fix that
held up was to stop asking "is this regex correct?" and run every pattern on 50 kB of
repeated filler, spaces and unclosed tags, with a time limit in the test.
