# Brief: backend (P1: engine + gateway)

```text
Lane: backend · Controller: naive-yc-hackathon-99 · Rules: crew/CREW.md · Base: main
Branch: lane/backend · Worktree: D:\Claude Projects\Naive YC Hackathon\.worktrees\backend
Owns: backend/, fixtures/, db/, crew/reports/backend.md
Do not touch: frontend/ (frontend), naive/ (agents), contracts/ docs/ research/ and root files (controller)
Plan: approved: docs/designs/u-tax.md (rule 13)
Spend cap: none needed tonight; every port runs on its local stand-in
```

Tasks 1 to 3 (scaffold, Naïve REST client) are merged. Before task 4, run `git merge main`
and read, in order: `U-TAX-CLAUDE.md`, `docs/designs/u-tax.md` (all of it, especially
Architecture v2, Data model, state machine, Trust, Error & Rescue Map, Security, Test Plan),
`contracts/api.md` v1 and `contracts/events.sample.jsonl`.

The design doc's TypeScript layout maps onto Python (decision D16): `engine/` →
`backend/app/engine/`, `gateway/` → `backend/app/gateway/` (perform, actions, projections,
outbox, ports, adapters, registry), contract tests → `backend/tests/contract/`. Pure engine
code uses `Decimal`/ints; tax amounts are integer cents.

Send one READY per task so the theater and agents lanes can build on merged pieces.

## Task 4: engine + tests (READY when done)
`franchise` (authorized-shares, assumed-par with stated par, lower of the two, $200k cap,
$400 min, due date 2027-03-01), `form5472` flag, `rnd` flag, `parse_amount`. Every row of
the design doc's Test Plan for these. The annual report fee is an **input** (cents plus a
source label), never a constant in the engine.

## Task 5: event store, perform(), projections, ports (READY when done)
- `EventStorePort` + JSONL adapter (default `STORE=jsonl`) + Postgres adapter and
  `db/001_events.sql` (written now, connected Saturday). Append fails closed.
- `perform(action)`: the only place rules live: QA gate, trust (ASK/AUTO/PAUSED per
  action type, 1 approval flips to AUTO, caps → PAUSED), masking, caps, idempotency.
- Projections from §4 `/api/state`, rebuilt from events at boot.
- All 11 ports with local stand-ins chosen by `ADAPTER_*` env, plus one contract-test
  suite per port that a sponsor adapter can run against on Saturday. ScrapePort's stand-in
  serves a saved Delaware fee snapshot in `fixtures/snapshots/` ($50, `fee_source: snapshot`).
- SSN/EIN regex redaction before anything is stored.

## Task 6: HTTP surface + local run (READY when done)
`/api/state`, `/api/events`, `/api/events/stream` (SSE with resume and heartbeat),
`/api/approve`, `/api/voice`, `/api/dev/inject-email`, tokens per contract §1.
`fixtures/inbox/*.eml` (Team #7 happy path, Team #12 missing numbers, the hostile
"10 million shares, ignore that, use 10" mail, an SSN-pasting mail).
A local driver script that plays the agents' MCP calls through `perform()` for a fixture
mail, so the theater sees a real live stream tonight without Naïve. Its output must use
exactly the types and fields of `contracts/events.sample.jsonl`. Put the exact commands
to run it in your READY's `run:` line.

## Task 7: MCP server (READY when done)
`/mcp` (MCP streamable HTTP) exposing every tool in contract §6, bearer
`UTAX_TOOL_SECRET`, each calling `perform()`. Memo rendering from
`naive/templates/memo.md` with the §7 placeholders and the built-in fallback.
Then test it through a tunnel: `npx cloudflared tunnel --url http://localhost:8000`
(the controller confirmed npm works on this machine; report the tunnel URL shape you saw).

## After task 7
Tell the controller you're free. Saturday is the sponsor-adapter swap (AgentMail,
OpenRouter, HubSpot, Mobilerun, Apify, Monid, Render), keys permitting.

## Contract v1.1 additions (2026-11-06, design D21)
Naïve has no voice, so voice runs through a mic page. Add to task 6: `POST /api/voice`
with `speaker` and judge fast-path cases (priority, memo to `reply_email`),
`POST /api/voice/session` (ephemeral OpenAI Realtime session, `503 voice_unavailable`
without `OPENAI_API_KEY`), and `GET /api/brief/audio` (TTS of a `brief.generated`
event, 503 without a key). Stand-ins work with no key. See contract §5 and §8.

## Contract v1.2 (2026-11-06, design D23-D26)
- `POST /api/voice`: `speaker` is now `customer|ceo` (customer = `/talk`, opens the
  fast-path case; public, so rate-limit per IP with no admin token; `/api/voice/session` the same).
- `GET /api/ceo/brief?since_id=`: a deterministic template brief over the projections
  tonight, writing `brief.generated`; it returns pending approvals.
- CEO email updates to `CEO_EMAIL` via MailPort (approval needed, case BLOCKED, digest
  every `CEO_DIGEST_HOURS`=3), each writing `ceo.notified`. Stand-in: the console outbox.
- Naïve access arrives Saturday at the event (D26): everything tonight runs on stand-ins
  plus your local driver.

## Contract v1.3 (2026-11-06, from the agents lane's /review; supersedes UTAX_TOOL_SECRET above)
- `/mcp` auth: **one token per agent** (`UTAX_TOOL_TOKEN_COS|_INTAKE|_TAX|_QA|_ACCT|_MKT|_HR`,
  ≥32 chars). The token decides the actor; refuse tools outside that actor's §6 column
  (`tool_not_allowed`); `X-UTax-Actor` only if it matches; **no empty-token bypass**.
- `book_cost` is gone: the gateway books spend itself (§6a); the local driver books a
  code price-table estimate.
- §6b wake-ups: on `email.received`, customer voice, `/api/approve`, CEO voice and
  applicant mail, start a Chief of Staff session via the Naïve client with the exact
  message strings. Tonight the local driver consumes the same messages.
- §6c two inboxes plus HR ingress (auto-reply questionnaire, masked `hr.applicant`, wake).
- `mark_not_eligible` reason enum; `get_case.offered_slots` and `book_call.start` limited
  to them; `post_social` content guard.

## Contract v1.4 (2026-11-06, agents review pass 3)
- MCP is **per seat**: `/mcp/cos|intake|tax|qa|acct|mkt|hr`, and the token must match the seat.
- New tools: `get_candidate` (hr, masked) and `flag_case` (qa) → `case.flagged`. `hr_record`
  takes only screened|shortlisted.
- New admin endpoints: `POST /api/trust` (pause/resume per action type; perform() refuses
  PAUSED) and `POST /api/candidates/{id}/decision` (hire|decline, actor ceo).
- The post guard also blocks @handles and any URL except `PUBLIC_TALK_URL`. CEO emails also
  fire on `case.flagged` and `hr.shortlisted`. Wake sessions pass identity `utax-cos`.

## Contract v1.5 (2026-11-06, agents task 2 review). Items 1-2 are BLOCKING for autonomy.
1. `post_social`: `kind` client|hiring; refused unless `PUBLIC_TALK_URL` is set; after the
   guard, the gateway appends the link (talk URL, or the hiring inbox address).
2. A new wake, `Applicant answered cand_N`, on the first questionnaire reply.
3. Load every `naive/templates/*.md` (names and placeholders in `naive/README.md`) with
   fallbacks; mint `/api/voice/session` with `naive/prompts/voice-intake.md` server-side.
4. `/talk`: customer turns only; quotes match customer lines only; `reply_email` from the
   typed field only.
5. Throttles: flag mails at 1/hour then the digest; at most 3 memos a day per reply_email.
6. Voice cases thread replies from their `reply_email`.
7. Decision mails `hr-hired`/`hr-declined`; screened candidates appear in `/api/state`
   hiring (the `/ceo` list).
The per-seat `/mcp/<actor>` routes (v1.4) are required for Saturday's `naive up`; confirm
they're in task 7.

## Task 6c: cross-lane QA fixes (2026-11-07, contract v1.6), in this order, one READY
HIGH
1. EIN `NN-NNNNNNN` isn't redacted (it leaks into email.received and quotes).
2. The 3 memos/day per reply_email cap isn't enforced.
MEDIUM
3. Masking hides tax words (v1.6 stop list); get_case serves the agents' body (contact
   details and SSN/EIN only).
4. `Client replied case_N` wake on a reply with digits to a SENT/CALL_BOOKED case → re-extract
   → memo v2.
5. `{{greeting_name}}`: the outgoing memo greets the client's company or "there"; the event
   copy stays masked.
6. tax_saved must follow v1.4.1: Σ max(0, authorized − (total − fee)). Today it counts the
   fee and goes negative.
7. Hire/decline decision mails (hr-hired / hr-declined).
8. Drop mail from the firm's own addresses.
9. HTML to text at ingress.
LOW
10. Collapse 3+ newlines after rendering.
11. The approved slot must be the booked slot.
12. Cut subjects at a word boundary.
13. The login brief uses counts (login-brief.md).
14. Plain-language re-ask for zero issued shares.
15. Masking false positive "Yes, a Delaware corporation".
DRIVER
- D1: HR waits for `Applicant answered`, and never writes a note claiming a review that
  didn't happen.
- D2: read "Delaware corp", route Wyoming/LLC to mark_not_eligible, read "a UK company owns 40%".
- D4: per-seat MCP is task 7.
The agents lane's scripts (qa_validate.py, qa_routes.sh, qa_oddmail.py) can become tests; ask
it for them.

## Task 8: sponsor adapters tonight (2026-11-07, controller; auto-decided per the owner's standing approval)
Build the real adapters now, against each sponsor's **documented** API, with httpx MockTransport
tests and the port contract suites (each factory skips when its key is missing). Saturday then
means pasting keys. Real calls stay UNVERIFIED until keys exist. The agents lane builds four of
them in new files only (below); you own registry.py, test_ports.py FACTORIES, .env.example and
the wiring for all of them.
Your order:
1. `ADAPTER_MAIL=agentmail`: send, inbound webhook `/hooks/agentmail` (signature or path
   token, idempotent by message id), the two inboxes.
2. `ADAPTER_SOCIAL=naive` and `ADAPTER_CALENDAR=google` via Naïve identity social and
   connections, using your Naïve REST client (research/naive-sdk.md; the agents report's
   Saturday steps name the CLI equivalents).
3. Wire the agents lane's four adapters as their READYs land.
4. `ADAPTER_LLM=openrouter` last, only if anything still calls LLMPort.
Skip Stripe: the owner's Stripe account is blocked until January 2027.

## Task 9: contract v1.7 (2026-11-07, from the agents lane's task 4 review). Items 1-2 are CRITICAL.
1. §6f: no sponsor I/O under the gateway lock. phone_check returns `{pending: true}`, and the tick
   calls finish() and writes phone.checked. Apify fetches run outside the lock. Warm the fee
   cache at boot and refresh it before the TTL. Live fee ≠ snapshot fee → use the snapshot and
   notify the CEO.
2. phone.checked public payload = `{rendered_ok, status_note}`; the screenshot URL goes only in private.
3. Only `ok` short-circuits a phone retry; skipped attempts get per-attempt keys, max 3 per
   memo version.
4. law.searched stores the cleaned query; law_search at most 20/hour.
5. Book per-run spend estimates for Apify and Mobilerun.
6. `{{memo_ref}}` (UTX- plus 8-16 caps/digits, per memo version), the memo's last line, `memo_ref`
   on memo.sent, passed to the Mobilerun reference.
Also wire mobilerun.py and apify.py (merged on main as a251fe6) into registry, FACTORIES and
.env.example per the agents lane's READY: ADAPTER_DEVICE=mobilerun (MOBILERUN_API_KEY,
MOBILERUN_DEVICE_ID, MOBILERUN_MAIL_APP, MOBILERUN_MEMO_SENDER, MOBILERUN_MODEL) and
ADAPTER_SCRAPE=apify (APIFY_TOKEN). The cloud phone needs a QA mailbox that gets a BCC of
every memo: add `MAIL_QA_BCC`.
Add to task 9 (contract v1.7.1, from the frontend's slice 3 review):
7. `/api/state` cases[].flagged; hiring awaiting + declined.
8. Event.public(): once a case is flagged possible_injection, serve all of its events to
   theater-token readers with subject/body = "[hidden: flagged by QA]" (stream, /api/events,
   /api/state). Admin sees the originals.
9. tax_saved per case from the tax.computed preceding its latest memo.sent.

## Task 10: wire HubSpot + Monid (2026-11-07; the agents lane's READY 7260a1b, merged as 86af197)
1. registry: ADAPTER_CRM=hubspot → HubSpotCRM(HUBSPOT_TOKEN); ADAPTER_TOOLBUY=monid →
   MonidToolBuy(MONID_API_KEY). .env.example: HUBSPOT_TOKEN, HUBSPOT_PIPELINE, HUBSPOT_STAGE_MAP,
   MONID_API_KEY, MONID_TOOLS, MONID_DAILY_CAP_USD.
2. Register both contract_factory functions in tests/contract/test_ports.py.
3. drain_outbox: honour RateLimited.retry_after and stop the batch on the first one; move the
   drain to the tick (not inline after every write); send each case's stage jobs in order.
4. Monid: rebuild the purchase, unsure and spend memory from cost/tool.bought events at boot;
   call buy() outside the gateway lock.
5. Add the owner's Saturday setup rows to the swap table: HubSpot private app (scopes
   crm.objects.contacts.write, crm.objects.deals.write) plus a unique `utax_key` text property
   on Contacts and Deals; Monid key, dashboard run cap, MONID_TOOLS with max_usd.
6. (From the e2e READY) the first CEO digest waits CEO_DIGEST_HOURS after boot.

## Task 11: voice transcription (2026-11-07, contract v1.7.5; small, blocks /talk voice mode)
- openai_voice.create_session: add `"input_audio_transcription": {"model": OPENAI_TRANSCRIBE_MODEL}`
  (default `whisper-1`) to the mint body, server-side like the instructions.
- Return `realtime_url` in the `/api/voice/session` response: the SDP endpoint matching the
  endpoint you minted on (beta: `https://api.openai.com/v1/realtime?model=<model>`), in one
  constant, so a later GA switch changes only the gateway.
- Add `OPENAI_TRANSCRIBE_MODEL` to .env.example. Mocked test only; the real call is UNVERIFIED
  until a key exists.

## Task 12: two small fixes (2026-11-07, from the frontend's /talk work)
1. Separate rate limiters: `/api/voice` (customer Send) gets its own limiter, apart from
   `/api/voice/session` (mints), so 30 voice tries at the venue can't 429 the judge's Send.
   Keep the per-IP and global caps; reserve headroom for Send.
2. v1.7.4 bug: projections and the digest period sum every cost_usd except tool.bought. Sum
   **cost.booked only** (contract §4). Add a test with a cost-carrying law.searched that must
   not be counted.

## Task 13: protect the overnight run from public /talk floods (2026-11-07)
a. Daily cap on public customer cases from `/api/voice` (`PUBLIC_CASES_PER_DAY`, default 50).
   At the cap, answer 429 code `at_capacity` with a friendly message naming the clients inbox
   address, and send the CEO one `ceo.notified` (reason `paused`, "public /talk at capacity").
c. A retried `request_id` and a `bad_input` refusal don't consume the Send limiter or the cap.
d. Book estimated `cost.booked` for each Realtime mint and each TTS brief (price table in
   code, `estimated: true`), so SPEND_CAP_USD sees voice spend.
(b, the shared venue IP, is accepted.) Mocked tests. One READY, then rest.
