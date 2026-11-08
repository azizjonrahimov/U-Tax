# API contract (v1.19): U-Tax gateway ↔ Agent Theater ↔ Naïve agents

Owned by the controller. Lanes propose changes by message to the controller; never
edit this file on a lane branch. Every change bumps the version and is announced to
all lanes, who then `git merge main`. Product rules come from `docs/designs/u-tax.md`
(the source of truth) and `U-TAX-CLAUDE.md`; this file only fixes the shapes.

Who builds what:
- **backend** (`backend/`): the gateway. Everything below is served by it.
- **frontend** (`frontend/`): the Agent Theater. Reads `/api/state` and `/api/events/stream` only.
- **agents** (`naive/`): Naïve agents. Call the MCP tools in §6 only.

## 1. Basics

- Gateway base URL (local): `http://localhost:8000`. Public URL for Naïve (tonight): a
  `cloudflared` tunnel to port 8000. Production (Saturday): Render.
- JSON in and out. Errors: non-2xx with `{"error": {"code": "<snake_case>", "message": "..."}}`.
- CORS: `http://localhost:3000` plus the deployed theater origin from env `THEATER_ORIGIN`.
- Auth tokens (all from env, never committed). Theater and admin tokens: an empty value
  disables the check for local dev. **Tool tokens are always required** (a tunnel request
  arrives from localhost too, so there is no localhost bypass):

| Token | Env var | Who sends it | Where |
|---|---|---|---|
| Tool tokens, one per agent | `UTAX_TOOL_TOKEN_COS`, `_INTAKE`, `_TAX`, `_QA`, `_ACCT`, `_MKT`, `_HR` (each ≥ 32 chars) | each Naïve agent, from its own vault entry | `Authorization: Bearer <token>` on `/mcp/<actor>`; **the token must match the seat** |
| Theater token | `THEATER_TOKEN` | Theater, read-only | `?token=<t>` on `/api/state`, `/api/events*` |
| Admin token | `ADMIN_TOKEN` | The CEO's mic/approve page, dev tools | `Authorization: Bearer <t>` on `/api/approve`, `/api/dev/*` |

- Frontend env: `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`), `NEXT_PUBLIC_THEATER_TOKEN`.
- Money: tax amounts are **integer cents** (`*_cents`). Agent cost is a **fixed-point decimal
  string** of USD (`"0.0123"`, never exponent form like `"1E-7"`; in Python `format(d, 'f')`),
  because it is fractional. Shares are integers. Stated par is a
  decimal string (`"0.00001"`). Never floats on the wire for money.
- Times: ISO 8601 UTC strings (`2026-11-07T21:14:03.120Z`).

## 2. Event envelope

Events are append-only and are the only truth. Ids are strictly increasing integers starting
at 1. **Ids may have gaps** (a Postgres sequence skips a number on rollback): consumers
treat a gap as information, never as an error or a reason to drop events.
Payloads are **already masked** when written (company → `masked_name` like `Team #7`,
emails hidden) unless the client opted in (`show_on_screen: true` on the case).

```json
{
  "id": 42,
  "ts": "2026-11-07T21:14:03.120Z",
  "type": "tax.computed",
  "actor": "tax",
  "case_id": "case_7",
  "provider": "local",
  "status": "ok",
  "payload": { "...": "per type, §3" },
  "tokens_in": null,
  "tokens_out": null,
  "cost_usd": null
}
```

- `actor`: `cos | intake | tax | qa | acct | mkt | hr | ceo | system`
- `provider`: `local | naive | agentmail | openrouter | openai | hubspot | mobilerun | apify | monid | google | stripe` or `null`
- `status`: `ok | failed | skipped`
- Consumers **skip unknown types** (log them, never invent animations).

## 3. Event types and payloads

| type | actor | payload |
|---|---|---|
| `email.received` | intake | `{masked_name, from_masked, subject, body}` (body masked; SSNs/EINs redacted) |
| `facts.extracted` | intake | `{facts: {incorporated_delaware, authorized_shares, issued_shares, stated_par, gross_assets_cents, foreign_owner_25, gross_receipts_cents?, years_with_receipts?}, quotes: {<fact>: "<verbatim span>"}, assumed: [<fact>...]}` |
| `facts.missing` | intake | `{missing: [<fact>...], asks_so_far}` |
| `client.asked` | intake | `{missing: [<fact>...], body}` |
| `case.not_eligible` | intake | `{reason}` |
| `case.status` | system | `{from, to}` (state machine in the design doc) |
| `case.blocked` | system | `{reason, needs: "human"}` |
| `law.searched` | tax | `{query, results: [{cite, snippet}]}` |
| `tax.computed` | tax | `{authorized_method_cents, assumed_par_method_cents, chosen: "assumed_par"\|"authorized", annual_report_fee_cents, fee_source, total_cents, due_date: "2027-03-01", form5472_flag, rnd_flag, engine_version}` |
| `qa.passed` / `qa.failed` | qa | `{checks: [{field, ok, note}]}`; `qa.failed` carries at least one check with `ok: false`, and `qa.passed` has none |
| `memo.sent` | acct | `{to_masked, subject, body, message_id, seconds_since_last_fact, memo_ref}` (`memo_ref` like `UTX-7F3K9Q2M`, unique per memo version) |
| `call.booked` | intake | `{start, minutes, invitee_masked}` |
| `phone.checked` | qa | `{rendered_ok, status_note}` (the screenshot URL is **private**, never in the public payload: it shows the real outgoing mail) |
| `crm.sync_requested` / `crm.synced` | system | `{object: "contact"\|"deal", stage}` |
| `post.published` | mkt | `{channel, text, url, image_url}` |
| `dm.drafted` / `dm.sent_by_human` | mkt / ceo | `{recipient_label, text}` |
| `hr.applicant` / `hr.screened` / `hr.shortlisted` / `hr.hired` / `hr.declined` | hr (hired: ceo) | `{candidate_id, name_masked, note}` |
| `hr.questionnaire_sent` | system | `{candidate_id, name_masked, note}` (the §6c auto-reply; counts against the `email` cap) |
| `hr.decision_mailed` | system | `{candidate_id, name_masked, note}` (hire/decline mail sent; counts against the `email` cap) |
| `screen.call` | hr | `{candidate_id, transcript_excerpt, seconds}` |
| `voice.command` | ceo / intake | `{text, channel: "voice"\|"typed", speaker: "customer"\|"ceo", case_id, masked_name}` (`masked_name` set for customer cases) (actor `intake` and `case_id` set for customer conversations) |
| `ceo.notified` | cos | `{channel: "email"\|"login", subject, summary, reason: "approval"\|"blocked"\|"flagged"\|"shortlisted"\|"paused"\|"digest"\|"login"}` |
| `approval.requested` | system | `{approval_id, action_type, summary}` |
| `touch.training` / `touch.operating` / `touch.engineering` | ceo | `{note, approval_id, channel: "click"\|"voice"\|"typed"\|"email"}` (`channel` v1.8; absent on older events = `click`) |
| `ceo.proposed` | ceo | `{proposal_id, tool, args, readback, expires_at, source: "realtime"\|"command"}` (§5b) |
| `ceo.confirmed` / `ceo.cancelled` | ceo | `{proposal_id, via: "voice"\|"click", heard}` (`heard` = the CEO's transcribed words, ≤ 200 chars; cancelled also has `reason: "spoken"\|"clicked"\|"expired"\|"superseded"`) |
| `hr.outreach_declined` | hr | `{candidate_id, name_masked, note}`: HR read a reply to the outreach as a no (v1.15.1). The held slot is freed, `outreach_declined` is set (a later CEO hire or decline sends no mail), and the stage is unchanged |
| `payment.link_created` | acct | `{link_id, amount_cents, currency}` (v1.17, D36): the case's $49 filing-help Stripe Payment Link, made once per case when the memo is sent. The URL is private and never in a payload. |
| `payment.received` | acct | `{case_id, amount_cents, currency}` (v1.17), provider stripe: a paid Stripe Checkout Session for a case, once per session. It adds a revenue row to U-Tax_Books.xlsx and `money.revenue_cents`, then a `ceo.notified` (reason `paid`) and one `client.followed_up` (kind `payment_receipt`). |
| `agent.activity` | the agent's seat (`ceo_assistant` for the assistant) | `{agent, session_id, kind: "thinking"\|"tool_started"\|"tool_done"\|"writing"\|"idle"}` plus optional `tool` (a name, never arguments), `text` (writing only: masked, the latest ≤ 600 chars, ≤ 1 event/s per session) and `order_id` (v1.18, D37). Provider naive. **Admin and session readers only**: theater readers never receive it, and it doesn't change /api/state. |
| `trust.changed` | system | `{action_type, from: "ASK"\|"AUTO"\|"PAUSED", to, reason}` |
| `cost.booked` | system | `{provider, model, estimated}` (amount in envelope `cost_usd`) |
| `tool.bought` | any agent | `{tool, provider}` (amount in envelope `cost_usd`) |
| `brief.generated` | cos | `{text, counts}` |
| `case.flagged` | qa | `{kind: "possible_injection"\|"unrealistic_inputs"}` |

Trust `action_type` values: `email`, `social_post`, `calendar_invite`, `voice_screen`.

Theater pane per type (design doc "Event → theater animation"): mail ← `email.received`,
`client.asked`, `memo.sent`; sheet ← `facts.extracted`, `tax.computed`, `qa.*`; search ←
`law.searched`; calendar ← `call.booked`; social ← `post.published`, `dm.*`; phone ←
`phone.checked`, `screen.call`; rail ← `touch.*`, `trust.changed`, `cost.booked`,
`tool.bought`, `hr.*`, `case.*`.

A full happy-path sample lives in `contracts/events.sample.jsonl`. The theater must
render it, and the gateway's own fixture run must produce the same types and fields.

## 4. Theater endpoints (read-only)

### `GET /api/health` → `200 {"status": "ok", "version": "<semver>"}` (unchanged from v0)

### `GET /api/state?token=` → `200`
Projections rebuilt from events (never stored by hand):
```json
{
  "last_event_id": 57,
  "firm": {"agents": 7, "clients": 23},
  "cases": [{"case_id": "case_7", "masked_name": "Team #7", "status": "CALL_BOOKED",
             "result": {"total_cents": 45000, "authorized_method_cents": 8516500}}],
  "touches": {"training": 4, "operating": 2, "engineering": 1,
              "log": [{"event_id": 12, "ts": "...", "kind": "training", "note": "email → AUTO"}]},
  "trust": {"email": "AUTO", "social_post": "ASK", "calendar_invite": "AUTO", "voice_screen": "ASK"},
  "money": {"tax_saved_cents": 195767500, "spent_usd": "11.38", "spent_by_provider": {"openrouter": "9.10"}},
  "hiring": {"applicants": 14, "screened": 2, "shortlisted": 1, "hired": 0},
  "queue": {"waiting": 0, "blocked": 0}
}
```
`Each `cases[]` entry carries `flagged: true|false` (any `case.flagged`). `hiring` carries
`applicants, screened, shortlisted, awaiting, hired, declined` (`awaiting` = shortlisted with no
hire/decline yet). Under the theater token, `hiring` carries counts only; the per-candidate list (with stages)
is admin-only, for `/ceo`.

Per case, the saving comes from the `tax.computed` that preceded that case's **latest**
`memo.sent`; the theater's replay projection follows the same rule and keeps the last valid
value (never drops a case's saving on a malformed later event).
`spent_usd` = Σ `cost_usd` over **`cost.booked` events only**. Other events (`tool.bought`,
`law.searched`, …) may carry `cost_usd` for display but are never summed, so nothing is counted
twice; the theater's money rail follows the same rule.

`tax_saved_cents` = Σ over sent cases of max(0, `authorized_method_cents` − (`total_cents` −
`annual_report_fee_cents`)): franchise tax saved only. The fee is paid under either method,
so it is never counted as a saving, and the sum never goes negative.

### `GET /api/events?after_id=0&limit=500&token=` → `200 {"data": [Event...], "next_after_id": 57, "has_more": false}`
For Replay mode and catch-up. Ordered by `id`.

### `GET /api/events/stream?after_id=0&token=` → `text/event-stream`
- Each event: `id: <event id>` then `data: <Event JSON>` then a blank line.
- Resume with `Last-Event-ID` header or `after_id`; the server replays everything after it, then streams live.
- Heartbeat comment `: ping` every 15 s. Clients reconnect automatically.

## 5. Control endpoints (admin token)

- `POST /api/approve` `{approval_id, decision: "allow"|"deny", note}` → `200 {"ok": true}`.
  A new approval request for the same case and kind **supersedes** older pending ones (they answer
  `409 superseded` `{approval_id, superseded_by}`, newest pending id or null), so the CEO can never
  approve a figure they didn't see. A case reopen (CEO unblock, client mail on an open case, new
  `facts.extracted`) also supersedes that case's pending approvals; a reply with no changed facts
  doesn't. No event is written for a supersede tonight: on `409 superseded` the console drops the
  entry and refetches `/api/state`. (Post-hackathon: an `approval.superseded` event.)
  Writes `touch.training` (first approval of an action type) or `touch.operating`, plus
  `trust.changed` when the type flips. Idempotent by `approval_id`. Any approval of an ASK type
  flips it to AUTO, including after a manual `trust` ASK (reason `approved again`; v1.8.3 states the code's rule).
- `POST /api/voice` `{text, channel: "voice"|"typed", speaker: "customer"|"ceo", reply_email}` →
  writes `voice.command`. `speaker: "customer"` (the `/talk` page, design D23): opens a
  **fast-path case** from the transcript (priority, no queue), memo goes to `reply_email`;
  returns `{case_id}` so the page can follow it.
  Refusals: `429 rate_limited` (per-IP or global window; retry later), `429 at_capacity`
  (`PUBLIC_CASES_PER_DAY` reached, default 50, or 200 with `PUBLIC_MODE=event`; the message names
  the clients inbox to email instead; the page must show that message, not a generic retry),
  `413 too_large` (body over 1 MB), `400 too_long` (customer text over 4000 chars). A retried
  `request_id` returns the same case and never counts against the cap. `speaker: "ceo"`: an optional typed order
  from `/ceo`, forwarded to the Chief of Staff once Naïve is wired (stand-in: log only).
- `POST /api/trust` `{action_type, state: "PAUSED"|"AUTO"|"ASK"|"RESUME"}` (admin; `RESUME` returns
  the type to the state it had before it was paused, and approvals allowed while it was paused are
  then carried out) → the CEO's standing
  order as gateway state ("stop posting" = `social_post` PAUSED); writes `trust.changed`
  and `touch.operating`. `perform()` refuses PAUSED types until resumed.
- `POST /api/cases/{case_id}/unblock` `{to: "INTAKE_PENDING"|"READY"|"NOT_ELIGIBLE", note}` (admin)
  → `case.status` out of BLOCKED (or SENT/CALL_BOOKED → INTAKE_PENDING to reopen when the
  client sends new numbers), a `touch.operating`, and a §6b wake (`New case case_N`).
  `409 wrong_status` otherwise.
- A cap hit (10/hr per action type or `SPEND_CAP_USD`) flips that type to PAUSED, writes
  `trust.changed`, and mails the CEO; further calls get `refused` with code `paused` until
  `POST /api/trust {state: "AUTO"}` (design: PAUSED → human operating touch → AUTO).
- `POST /api/dev/resolve-purchase` `{key, charged: true|false, cost_usd, over_ceiling?}` (admin) → clears an
  "unsure" Monid purchase as not charged, or books it as charged (`cost.booked`). v1.8.2 (task 17):
  - `key` is the agent's key exactly, in the agent key charset, else 400. A not_found
    for a `buy:`-prefixed key carries a hint.
  - `charged` is required (400).
  - A cost above the tool's `max_usd` needs `over_ceiling: true`.
  - A cost over $1000, or a charged cost that rounds to $0.0000, is 400.
  - 409 `wrong_status` while the buy is in flight, when that episode is already
    resolved, or while the process still holds the purchase (the agent's retry books it).
  - Unsure and resolved records are per episode: idempotency keys carry `#n` after the
    first episode, and private gains `intent_ts` and `provider`.
  - Records found unsure at boot have actor `system`.
  - `buy_tool`: a store outage before paying is `error` `store_unavailable`. A purchase
    that may already be charged is refused until the CEO resolves it.
  - v1.9.1 (task 17b): the body takes `episode?` (int ≥ 0, default 0; required for a later
    episode), and the reply carries `episode`. A resolve for any episode other than the open
    one answers 409 `wrong_status` ("check it in Monid's runs, then resend with episode: N").
    A console must never auto-retry with the N from that refusal. A resolve of a key that's
    already bought answers `not_found` "already bought". A charge under $0.0001 is booked as
    $0.0001.
- `POST /api/candidates/{candidate_id}/decision` `{decision: "hire"|"decline"}` (admin) →
  `hr.hired` / `hr.declined` with actor `ceo`, a `touch.operating`, and the `hr-hired` /
  `hr-declined` mail to the candidate (trust type `email`). `/ceo` lists **every screened
  candidate**, not only shortlisted ones, so no AI screen becomes a silent rejection.
- `POST /api/voice/session` → `{client_secret, model, expires_at}`: an ephemeral OpenAI
  Realtime session minted with the gateway's `OPENAI_API_KEY` (the key never reaches the
  browser). `503 voice_unavailable` when no key is set; the mic page then falls back to
  typed text plus browser speech.
- `GET /api/brief/audio?event_id=<brief.generated id>&token=` → `audio/mpeg` (OpenAI TTS of the
  brief text). Accepts the **theater token or the admin token** (read-only audio the theater
  plays). `503 voice_unavailable` without a key; the page falls back to browser
  `speechSynthesis`.
- `GET /api/ceo/brief?since_id=<last event id the CEO saw>` → `{brief_event_id, text,
  counts, pending_approvals: [{approval_id, action_type, summary}], last_event_id}`. Writes a
  `brief.generated` (stand-in tonight: a deterministic template over the projections;
  Saturday: the Chief of Staff's `firm_brief`). The `/ceo` console calls it on login and
  stores `last_event_id` locally.
- CEO email updates (design D24): the gateway sends to `CEO_EMAIL` through MailPort when
  an approval is requested, a case is BLOCKED or flagged, or a candidate is shortlisted, and a digest every `CEO_DIGEST_HOURS`
  (default 3). Each send writes `ceo.notified`. Stand-in: the console outbox.
- `POST /api/dev/inject-email` `{from, subject, body}` → local MailPort only; produces
  `email.received` exactly as a real inbound mail would. Refused unless `ADAPTER_MAIL=local`.
- `POST /hooks/agentmail` (Saturday): AgentMail webhook; idempotency key = message id.

## 5-ter. Admin artifact views (v1.18, D37)

- For admin and session readers only:
  - `memo.sent`, `client.asked`, `client.followed_up` and `hr.outreach` add `payload.sent: {subject, body,
    to_masked}`: the artifact as it actually left (the real greeting, the Stripe link).
  - `doc.step` adds `payload.cells: [{sheet, cell, value}]`: what that step wrote.
- Theater readers get neither; their payloads are unchanged.
- `/hooks/stripe` sends the payment receipt only for a paid usd payment of exactly FILING_PRICE_CENTS. Anything
  else tells only the CEO ("Payment of $X received: check it").

v1.18.2 (task 43):
- `GET /api/events?types=a,b` (optional comma list). The type filter and the reader's visibility (theater readers never
  get admin-only types) apply BEFORE `limit`. `next_after_id` is the id of the last row READ (pass it back as
  `after_id`). `has_more` means rows are left to read (at most 50,000 are read per request).
- `doc.step` `payload.cells: [{sheet, cell, value, fmt}]` (admin only). `fmt` is one of:
  - `"usd"`: dollars as a JSON number to the cent; the assumed par value capital at full precision;
  - `"par"`: a per-share par value at full precision;
  - `"int"`;
  - `"text"`.
  Numbers are JSON numbers, never formatted strings.
- `agent.activity`: a stream's replay of steps created more than 15 s before the gateway first saw the session
  is not logged.

## 5-bis. Payments (v1.17, D36)

- `POST /hooks/stripe`: verifies Stripe-Signature (`t=,v1=`, HMAC-SHA256 of `t.body` with STRIPE_WEBHOOK_SECRET,
  5 min tolerance). With no secret it answers 503 `hook_not_configured`; a bad or old signature answers 401.
  - `checkout.session.completed` and `checkout.session.async_payment_succeeded` with
    `payment_status: "paid"` → `payment.received`. The case comes from metadata.case_id, then
    client_reference_id, then the payment link. It's idempotent by the session.
  - It answers 503 when the books are locked or the receipt failed: Stripe retries, and only what's
    missing is redone.
  - Everything else is acknowledged with 200 `{ignored}`, including live events while STRIPE_LIVE is off.
  - No card data or customer details are read.
- `ADAPTER_PAY=stripe`: STRIPE_SECRET_KEY (test mode; a live key needs STRIPE_LIVE=1),
  STRIPE_WEBHOOK_SECRET, optional STRIPE_PRICE_ID, FILING_PRICE_CENTS (4900).
  `python -m app.devtools stripe-webhook https://<host>` points the endpoint and writes the secret.
- `/api/state` `money.revenue_cents` = Σ `payment.received` (usd).
- The offer is a human-led 20-minute walkthrough of the client's own filing; U-Tax files nothing. The gateway,
  never an agent, books payments and sends the fulfilment mails.

## 5a. Product shell routes (v1.12, design D32)

- `POST /api/signup` `{name, company, email, request_id?}`. It's public, with the /talk per-IP
  limits, plus at most 3 sign-ups per email per day, and the public daily case cap.
  - It opens a case exactly like an inbound email (`email.received`, with `source: "signup"` in
    the payload and the sender masked), and wakes the crew ("New case case_N").
  - It sends one welcome email (template `signup-welcome`, trust type `email`) asking for the
    numbers. The reply threads onto the case.
  - Always returns `200 {"ok": true}`, for a new or an existing address alike, so the response never
    reveals who is a client (v1.12.2). The welcome email is the client's receipt.
  - Refusals: `400 bad_input` (invalid email or empty fields), `429 rate_limited`,
    `429 at_capacity`.
- Event `tool.called` (actor = the seat): `{tool, status: "done"|"needs_approval"|"refused"|"error",
  order_id?}`, written by the gateway for **every** MCP tool call, reads included. No args or data,
  so nothing private. It's what the department workspaces animate.

## 5a-bis. CEO sign-in (v1.13, design D33)

- `POST /api/auth/login` `{email, password}`. `email` must equal `CEO_EMAIL` (case-insensitive) and
  `password` must match `CEO_PASSWORD_HASH` (scrypt, `scrypt$<salt_b64>$<hash_b64>`). It returns
  `{session, expires_at}`: an HMAC-signed token (`UTAX_SESSION_SECRET`, generated at first boot
  if unset) that lasts 12 h.
  - Every admin route accepts `Authorization: Bearer <session>` just as it accepts
    `ADMIN_TOKEN`. The admin token stays, for scripts only.
  - Rate limit: 10 attempts per IP per 15 min. A failure answers `401 bad_login` (same body
    for a wrong email or a wrong password).
  - `POST /api/auth/logout` revokes the session (a denylist kept in memory until it expires).
- `/api/ceo/command` (v1.13):
  - While a proposal is open (unexpired, unconfirmed), any text that isn't a confirm or cancel
    answers `{kind: "say", say: "Say confirm or cancel."}`, and no new order is created.
    v1.13.1: stop-kind commands (`pause`: "stop posting", "pause emails", "stop everything", fillers
    included) are exempt. They run at once, and the pause cancels the open card (§5b: a pause from any
    path cancels open proposals), so nothing proposed before a STOP can run after it.
  - Greetings and mic checks never become orders. That means utterances made only of
    hey/hi/hello/test/testing/okay, or containing "can you hear me", "are you there" or
    "is this working". They answer `{kind: "say", say: "I'm listening. Tell me an order."}`.

## 5a-ter. CEO conversation (v1.14, design D34)

- `POST /api/ceo/talk` `{text, conversation_id?}` (admin or session).
  - It sends `text` into the CEO assistant's Naïve session for this sign-in: it creates one on the
    first turn, then posts `POST /v1/sessions/{id}/messages`.
  - It waits until the session idles, for at most 25 s, and returns `{conversation_id, say, actions:
    [{tool, summary, undo_id?}]}`. On a timeout it answers `{say: "Still working on it…", pending: true}`, and
    the page polls `GET /api/ceo/talk/{conversation_id}`.
  - Stop phrases never wait on the LLM: the gateway's grammar catches them first and runs `pause` at
    once (v1.13.1).
- MCP seat `/mcp/ceo` (token `UTAX_TOOL_TOKEN_CEO`, the agent `ceo-assistant`). Its tools are the §5b
  registry, but start tools **run directly**, with no proposal (owner's D34). Read tools mask as before.
  Each action writes its normal events plus `touch.operating` with `channel: "voice"`.
  - Undo: `pause` ↔ `set_trust RESUME`; `set_trust` ↔ the previous state; `order` (a CoS card) →
    `cancel_order {order_id}` closes the order's open cards. `approve`, `deny` and `decide_candidate`
    have no undo; the reply says so.
- v1.14.1, pinned shapes:
  - **The stop note.** A stop the grammar catches inside `/api/ceo/talk` runs at once. The gateway
    then appends this exact message to the assistant's session, as a message of its own: `System:
    the CEO said stop; the gateway paused <comma-separated types>. Undo id <undo_id>.` The gateway
    strips any leading `System:` (case-insensitive, after trimming) from the CEO's own text before
    relaying it, so only the gateway can write that note.
  - **Leaving PAUSED after a stop.** On the ceo seat, a `set_trust RESUME`, or any other way out of
    PAUSED (`set_trust AUTO`/`ASK` on a paused type, or `undo` of a pause), is refused (`code: "stopped"`)
    when the turn that issued it began before the latest stop. The CEO's own `POST /api/ceo/undo` is
    unaffected. The ceo seat also has `cancel_order {order_id}` and `undo {undo_id}`.
  - **Ambiguous references.** On the ceo seat, an approval or candidate reference of "latest", "it",
    a pronoun, or one matching more than one item answers
    `{status: "refused", code: "ambiguous", candidates: [{id, summary}]}`.
  - The assistant's events carry the actor `ceo_assistant`; the MCP seat path stays `/mcp/ceo`.
- v1.18.1 (task 41):
  - `POST /api/ceo/talk`: a stop the grammar catches runs FIRST, before the conversation is looked up. An
    unknown or forgotten `conversation_id` starts a new conversation, never a 404. A pending earlier turn's reply is
    collected before the next turn is relayed.
  - `ceo.replied.actions` is per turn: what the assistant did during that turn, plus any earlier, unreported turn.
    Each action is reported once.
  - The stop rule now covers EVERY start tool. On the ceo seat, `approve`, `deny`, `decide_candidate`, `order`,
    `set_trust` (any state), `unblock_case`, and an `undo` that would leave a pause, from a turn that began
    before the latest stop, are refused `{code: "stopped"}`. Reads, `pause` and `cancel_order` stay allowed.
  - `GET /api/ceo/talk/{id}` reads the turn's state under the same lock.
- `POST /api/ceo/undo` `{undo_id}` (admin or session).
- Events `ceo.said` `{conversation_id, text_len}` (the text is private) and `ceo.replied`
  `{conversation_id, actions}`.

## 5a-quater. Session transport (v1.16, backend 36/39 + frontend D32)

- The CEO session is accepted ONLY as `Authorization: Bearer <session>`, never in a URL. `/api/state` and
  `/api/events` with `?token=<session>` answer 401; `?token=<THEATER_TOKEN>` stays for the public theater.
- `POST /api/stream-ticket` (Bearer admin or session) → `{ticket, expires_at}`: HMAC-signed, 60 s, single use,
  valid only as `GET /api/events/stream?ticket=<t>&after_id=<n>`. A used, expired or forged ticket answers 401.
  On a stream error the console closes it, takes a fresh ticket and reopens after the last id seen.
- A ticket is never a bearer, and a session is never a ticket. `/api/brief/audio` and `/api/docs/*` are
  fetched with Bearer and played or saved as blobs.

## 5b. CEO voice console (v1.8, design D28)

All admin token. One registry of CEO tools (design table "CEO tool set") serves both paths below;
the tool names, kinds and args are fixed here:

| Tool | Kind | Args |
|---|---|---|
| `get_brief` | read | `{since_id?}` |
| `list_approvals` | read | `{}` |
| `get_case` | read | `{case}`: `"case_7"`, `"7"` or `"Team 7"` |
| `get_money` | read | `{}` |
| `get_trust` | read | `{}` |
| `get_hiring` | read | `{}` |
| `recent_activity` | read | `{minutes?}` (default 60, max 720) |
| `pause` | stop | `{action_type}`: an action type or `"everything"` |
| `approve` / `deny` | start | `{approval}`: an `approval_id`, `"latest"`, or a case reference |
| `set_trust` | start | `{action_type, state: "AUTO"\|"ASK"\|"RESUME"}` |
| `unblock_case` | start | `{case, to: "INTAKE_PENDING"\|"READY"\|"NOT_ELIGIBLE"}` |
| `decide_candidate` | start | `{candidate, decision: "hire"\|"decline"}` (`candidate`: `cand_N` or `"Candidate B"`) |
| `order` | start | `{text}` (≤ 500 chars) |

- `POST /api/voice/session` `{mode: "customer"|"ceo"}` (absent = `customer`). `mode: "ceo"` needs
  the admin token (else `401`), uses `naive/prompts/ceo-voice.md` (missing/empty → `503
  voice_unavailable`), carries the read/stop/start tool schemas from the registry, has input
  transcription on, and its own mint cap (10 an hour). Same response shape as today.
- `POST /api/ceo/tool` `{name, args, call_id}` →
  - read: `200 {kind: "read", data}`. `data` is masked like theater-token reads: labels only;
    no mail bodies, addresses or quotes; flagged cases read as `"flagged by QA, content hidden"`.
  - stop: `200 {kind: "done", summary}`, carried out at once and idempotent. Writes
    `trust.changed` plus `touch.operating` (`channel: "voice"`).
  - start: `200 {kind: "proposal", proposal_id, readback, expires_at}`, nothing carried out.
    Writes `ceo.proposed`. `readback` is one spoken sentence built by code from the pinned
    target (e.g. `"Approve the memo for Team #7: $450 to their inbox. Say confirm."`).
  - Refusals: `400 unknown_tool`, `400 bad_args`, `404 not_found` (e.g. no such case or
    approval; the message names what is open), `409 nothing_pending` (`"latest"` with no
    pending approval).
- `POST /api/ceo/confirm` `{proposal_id, decision: "confirm"|"cancel", via: "voice"|"click", heard}`
  - Confirm carries out the pinned action through the same code as the §5 routes, and
    returns `200 {kind: "done", summary}`. The action's own events carry `channel: "voice"` or
    `"click"` on the touch, plus `ceo.confirmed`.
  - Single use: a second confirm answers `409 used`. After `expires_at` (60 s) it answers
    `409 expired`. A superseded approval answers `409 superseded` (§5 body).
  - With `via: "voice"`, the gateway re-checks `heard` against the confirm grammar
    (`confirm`, `yes`, `do it`, `approve it`, `go ahead`, case-insensitive, whole words). A
    miss answers `400 not_a_confirm`. A cancel word (`no`, `cancel`, `stop`, `wait`) in `heard`
    always cancels.
- `POST /api/ceo/command` `{text}`: the fallback, for browser speech or typing. v1.12.3: text the grammar can't parse (3–300 chars, not a confirm, cancel or question word)
  becomes an `order` proposal ("Send this order to the Chief of Staff: \"<text>\". Say confirm."). The
  gateway's deterministic grammar maps the text to one tool call and answers exactly like
  `/api/ceo/tool`. Text it can't parse answers `200 {kind: "unknown", say}`, where `say`
  lists example commands. Confirm and cancel words are matched against the newest open
  proposal (the console then calls `/api/ceo/confirm`).
- `POST /api/trust` `{action_type: "*", state: "PAUSED"}` pauses every action type
  (`"everything"`). Only `PAUSED` is accepted with `"*"`.
- Never in the CEO tool set: `resolve-purchase`, keys, deploys, `/api/dev/*`.
- v1.8.4 (backend task 18, as built), on top of the rules above:
  - **Spoken confirm grammar** (server and console, identical):
    - The whole utterance must *be* a confirm phrase: `confirm`, `yes`, `do it`, `go ahead`,
      `confirm it`, `yes please`. Fillers are allowed: ok, okay, alright, so, um, uh, yeah, please.
    - `approve it` confirms an approve proposal only.
    - Any of `no`, `nope`, `cancel`, `stop`, `wait`, `hold on`, `never mind`, `don't`, `do not`,
      `not`, `never` cancels.
    - Never a confirm: a question (`?`), the readback's own words, or `say confirm`, `shall I`,
      `should I`, `do you want`, `would you like`, `want me to`. These answer `400 not_a_confirm`.
  - **409 `flagged`:** by voice, approving or denying a flagged case is refused ("decide in
    the console"). So is an approve or AUTO change that would also run a flagged case's waiting
    approval.
  - **409 `superseded`** also covers a pin that moved and a proposal cancelled by a pause. It has
    no §5 body unless another approval superseded this one.
  - **`/api/ceo/command`:**
    - With an approve proposal open, `approve it` answers
      `{kind: "confirm", proposal_id, readback}`; otherwise it proposes "approve the latest".
    - A stop sent through it writes channel `typed`.
  - **`/api/ceo/tool`** takes an optional `channel: "voice"|"click"` (default `voice`), so the
    STOP button logs `click` (backend 18b). Any other value answers `400 bad_input`.
  - v1.8.5: an utterance with any letter outside a-z, or a question mark in any script, is
    `not_a_confirm` (fails closed; the CEO clicks). A pause on an already-PAUSED type still
    cancels open proposals.
  - v1.8.5 (18c): the readback of a voice `approve` for a `social_post` reads the **full post
    text** (≤ 255 chars), not the summary. `heard` and order text move to the event's private
    part; the public `ceo.*` payload carries `heard_len` only. v1.8.6 (as built): a post readback is the text, the
    appended link and the channel ("the <channel> post, word for word: …"), so it can run
    past 255 chars. It lives in the private part, and the public `ceo.proposed` keeps the
    summary. An order's public readback is `"An order for the Chief of Staff. Say confirm."`
    with args `{text_len}`. A confirmed order is still public as `voice.command` (that's
    the show). A post approval from before v1.8.5 (no full text) answers `409
    console_only` by voice. Keep `PUBLIC_TALK_URL` fixed once set: a post's approval key
    digests the text before the link.
  - **Proposals:**
    - `ceo.proposed` carries `case_id`.
    - The order text and `heard` are masked like a CEO order.
    - Proposal ids are random (`prop_<hex>`).
    - Pinned args may carry extra keys.
    - A pause from any path cancels open proposals.
  - **`get_brief`** returns `new_events` when `since_id` is given.
- Open proposals are gateway state rebuilt from `ceo.proposed|confirmed|cancelled` events.
  After a restart, an expired one is cancelled with `reason: "expired"`.

## 5c. Speech output (v1.10, design D30)

- `ADAPTER_VOICE=elevenlabs` takes `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` (default
  `EXAVITQu4vr4xnSDxMaL`) and `ELEVENLABS_MODEL` (default `eleven_flash_v2_5`).
  `ADAPTER_VOICE=openai` still works if a key ever shows up.
- `POST /api/tts` `{text, purpose: "ceo"|"talk", request_id?}` → `audio/mpeg`.
  - `purpose: "ceo"` needs the admin token, with text ≤ 1500 chars.
  - `purpose: "talk"` is public: text ≤ 600 chars, the per-IP limits of `/talk` with
    `PUBLIC_MODE` doubling, and its own daily character budget (`TTS_PUBLIC_CHARS_PER_DAY`,
    default 200000).
  - Text is spoken as given. The gateway never adds to it, and masks contacts and ids
    first, like the theater.
  - Each call books an estimated `cost.booked`, provider `elevenlabs`.
  - Without a key: `503 voice_unavailable`, and the page falls back to `speechSynthesis`.
- `GET /api/brief/audio` uses the same adapter.
- v1.10.1 (the console's needs):
  - **CORS:** allow `POST` with `Authorization` and `Content-Type` from `THEATER_ORIGIN`.
  - **Success:** 200 `audio/mpeg`. The first byte should arrive within about 1–2 s, so stream
    the ElevenLabs response through rather than buffering it. The console gives up at 10 s.
  - **Budget:** a spent `talk` budget answers `429 rate_limited`, and a missing key `503
    voice_unavailable`. On either, the page falls back to the browser's voice without a sound
    of its own (on a 503 it stops using the gateway voice for 60 s).
  - **Text length:** `/talk` sends pieces of ≤ 600 chars, split at sentence ends.
- v1.10.2:
  - **`purpose: "talk"` takes no free text.** The body is `{purpose: "talk", phrase:
    "<key>", args: {...}}`. The gateway renders the line from a server-side registry: the
    `/talk` script's questions, the number read-back template (numbers only, ≤ 12 digits each),
    the "your memo is on its way" line, and the at-capacity line. An unknown key, extra args or
    non-numeric args answer `400 bad_input`. That keeps the firm's voice from being an open relay,
    and caps how fast the daily budget can drain. The frontend sends phrase keys only; the
    registry lives in `naive/prompts/talk-phrases.json` (agents lane) and the gateway loads it
    strictly.
  - **`purpose: "ceo"` is spoken unmasked** (admin token only). The CEO hears exactly what the
    card shows.
- `/api/voice/session` is unchanged: with no Realtime provider it answers 503, and the
  pages use browser `SpeechRecognition` for listening (§8 fallback, now the main path).
  The confirm grammar and the echo guard (v1.8.1/v1.8.4) apply to that path too: the mic is
  off while TTS audio plays, plus an 800 ms tail.

- v1.16.1 (task 40a, D36): `POST /api/stt?purpose=ceo|talk&seconds=<0-30>`.
  - The body is the recorded audio itself: `Content-Type` audio/webm|ogg|wav|mp4|mpeg|flac, ≤ 1 MB,
    one utterance ≤ 30 s. → `200 {"text": "..."}`.
  - `purpose: "ceo"` needs the admin bearer. `purpose: "talk"` is public: /talk's Send per-IP limits in
    their own bucket, plus `STT_PUBLIC_SECONDS_PER_DAY` (default 7200) across all visitors.
  - `seconds` is the recording's length. The gateway holds and books max(`seconds`, its own floor from the audio
    bytes), at most 30 s; a missing `seconds` holds 30 (v1.18.4, task 45).
    - The floor is a WAV's header length, else the bytes at the type's lowest bitrate: 6 kbps for webm/ogg,
      8 kbps for mp4/m4a/mpeg, 64 kbps for flac.
    - A duration the provider measures wins when the provider reports one.
    - Record at about 8 kbps opus (MediaRecorder `audioBitsPerSecond: 8000`) so honest clips book close to their
      length.
  - Errors: `400 bad_input`, `401`, `413 too_large`, `415 bad_input` (not audio), `429 rate_limited`, and
    `503 voice_unavailable` (no STT, no key, the spend cap, or an outage: use the browser recognizer).
  - Booked as `cost.booked`, model `stt:<purpose>:<model>`, seconds in `tokens_in`, estimated.
- Voice switches (v1.16.1):
  - `ADAPTER_TTS` = elevenlabs|openai|none (default `ADAPTER_VOICE`) speaks /api/tts and the brief.
  - `ADAPTER_REALTIME` = openai|none (default openai only when `ADAPTER_VOICE=openai`) mints
    /api/voice/session.
  - `ADAPTER_STT` = openai|none (default none) serves /api/stt.

## 6. MCP tools for Naïve agents

Served at **`POST /mcp/<actor>`** (one MCP endpoint per agent seat: `/mcp/cos`, `/mcp/intake`,
`/mcp/tax`, `/mcp/qa`, `/mcp/acct`, `/mcp/mkt`, `/mcp/hr`; MCP streamable HTTP),
`Authorization: Bearer <that agent's tool token>`. The token must belong to the seat in the
path, else `401`. Separate URLs mean Naïve can never hand one agent's credential to another.
The gateway maps the token to the actor, writes that actor on every event, and **refuses
any tool outside the actor's column below** (`refused`, code `tool_not_allowed`). An
optional `X-UTax-Actor` header is accepted only when it matches the token's actor.
Naïve custom client tools are "coming soon", so MCP over public HTTPS is how agents reach
the gateway (`research/naive-sdk.md` §5). Every tool goes through `perform(action)`.

Every tool returns:
```json
{"ok": true, "status": "done" | "needs_approval" | "refused" | "error",
 "data": {}, "approval_id": null, "error": null}
```
`needs_approval` means the action type is still ASK: the agent tells the Chief of Staff,
which asks the CEO; the agent retries the same call after approval (idempotent).

| Tool | Agent | Input | Effect |
|---|---|---|---|
| `list_open_cases` | cos | `{}` | open cases with status, masked name, waiting reason |
| `get_case` | any | `{case_id}` | **intake and qa** get the email body with only contact details and SSN/EIN redacted (names left as written, so facts like "a Delaware corporation" survive); **cos, mkt and hr** get the masked copy. Plus facts, quotes, status, result, and `offered_slots: [ISO...]` (the next free 15-minute slots the gateway can book) |
| `extract_facts` | intake | `{case_id, quotes: {<fact>: "<verbatim span from the email>"}, incorporated_delaware, foreign_owner_25}` | code parses each quote (`parse_amount`), checks it appears verbatim, conflicting spans → re-ask; `facts.extracted` or `facts.missing` |
| `ask_client` | intake | `{case_id, missing: [<fact>...]}` | templated re-ask email (trust type `email`, max 2); `client.asked` |
| `mark_not_eligible` | intake | `{case_id, reason: "not_incorporated"\|"outside_delaware"\|"not_a_corporation"}` | kind templated reply chosen by the enum (no free text reaches the client); anything else → `refused`; `case.not_eligible` |
| `law_search` | tax | `{case_id, query}` | `law.searched` with the **cleaned** query (operators, emails, money and ID-like numbers stripped); at most 20 per hour firm-wide (stand-in: canned Delaware results) |
| `compute_tax` | tax | `{case_id}` | engine on the extracted facts only; `tax.computed` |
| `qa_check` | qa | `{case_id}` | re-runs the engine, re-matches quotes; `qa.passed` / `qa.failed` |
| `send_memo` | acct | `{case_id}` | refuses unless `QA_PASSED`; memo from template + engine output only; `memo.sent` |
| `book_call` | intake | `{case_id, start}` | `start` must be one of the case's `offered_slots`, else `refused`; trust type `calendar_invite`; `call.booked` |
| `phone_check` | qa | `{case_id}` | starts a DevicePort run for the case's latest `memo_ref` and returns `status: "done", data: {pending: true}`; the gateway tick finishes it and writes `phone.checked` (never blocks the case). Only an `ok` result short-circuits a retry; a skipped attempt gets a fresh per-attempt key, at most 3 attempts per memo version |
| `post_social` | mkt | `{channel, text, image_url, kind: "client"\|"hiring"}` (kind defaults to client; text ≤ 255 chars) | refused unless `PUBLIC_TALK_URL` is set; `image_url` must be null or an https URL on a host in `PUBLIC_ASSET_HOSTS` (comma-separated), else refused; content guard first: refused when the text holds a case id, a `Team #N` name, an email address, a phone number, an @handle, any URL other than the firm's own `PUBLIC_TALK_URL`, or any dollar amount other than `$85,165`, `$400`, `$450`, `$50`; then trust type `social_post`; **after** the guard the gateway appends the link itself (`PUBLIC_TALK_URL` for client posts, the `MAIL_INBOX_HIRING` address for hiring posts); `post.published` |
| `draft_dm` | mkt | `{recipient_label, text}` | `dm.drafted` (the human sends it) |
| `get_candidate` | hr | `{candidate_id}` | the masked candidate: stated credentials, questionnaire answers, stage (no contact details) |
| `hr_record` | hr | `{stage: "screened"\|"shortlisted"\|"outreach_declined", candidate_id, note}` (outreach_declined v1.15.1: refused `wrong_status` for a candidate never sent the outreach; a repeat answers `duplicate: true`) | `hr.<stage>`; `hr.applicant` comes from the gateway (§6c); declining or hiring is the CEO's (`/ceo`), never an agent's |
| `flag_case` | qa | `{case_id, kind: "possible_injection"\|"unrealistic_inputs"}` | `case.flagged`; goes into the next CEO update email |
| `firm_brief` | cos | `{}` | counts, blocked cards, touches; `brief.generated` |
| `buy_tool` | mkt | `{tool, input, key}` | ToolBuyPort (Monid): refused unless `tool` is in `MONID_TOOLS`; the per-tool `max_usd` ceiling and `MONID_DAILY_CAP_USD` apply; the purchase runs outside the gateway lock; `tool.bought` with `cost_usd` in the envelope plus `cost.booked`; key, input fingerprint and unsure flag are private. Never input containing an email or a client's details (design: public startup lists only) |

**v1.11 (design D31, the live CEO orders):**

| Tool | Agent | Input | Effect |
|---|---|---|---|
| `make_ad` | mkt | `{kind: "client"\|"hiring", headline}` (headline ≤ 80 chars, passes the `post_social` content guard) | The gateway renders a branded 1200×628 PNG: firm name, headline, and for `client` the real example "$85,165 → $450". It's served at `<public-host>/assets/ad_<n>.png` (the gateway's own host is always in `PUBLIC_ASSET_HOSTS`). Returns `{image_url}`; event `ad.created`. No trust gate (nothing leaves the firm until `post_social`). |
| `source_candidates` | hr | `{count}` (1–5) | ToolBuyPort (Monid) public search for the gig's role. Returns masked public leads `{lead_id, label, headline, public_url}`, never an email or phone; event `hr.sourced`. With Monid off it returns `[]` and says so. |
| `hr_outreach` | hr | `{candidate_id, slot}` | Emails one candidate whose address the firm already holds with consent: an applicant from the hiring inbox, or an entry in `HR_CANDIDATES` (the CEO's own list: `name <email>`, comma-separated). The email carries the gig details (template `hr-outreach`) plus a calendar invite for `slot`, which must be one of `get_candidate.offered_slots`. Trust type `email`; event `hr.outreach` + `call.booked` (kind `candidate`). A lead from `source_candidates` has no address and is refused (`no_consented_address`). |
| `prepare_documents` | acct | `{case_id, order_id?}` | Refused unless `QA_PASSED`. The gateway builds the workpaper .xlsx, the memo .docx and a books entry from the engine's result and the extracted facts only. It verifies each file (it re-opens it and checks every figure against the engine) and stores them under `UTAX_DATA_DIR/docs/<case_id>/`. Events: one `doc.step` per build step `{doc: "workpaper"\|"memo"\|"books", step, detail}` (for example "Authorized Shares Method: 10,000,000 shares → $85,165"); then `doc.created {doc, file_name, sha256, pages_or_sheets}`, and `doc.verified {doc, ok}`. It's idempotent per case and memo version. |
| `list_candidates` | hr | `{}` | v1.11.2: masked rows `{candidate_id, label, stage, has_address, contacted, offered_slots}`, covering the applicants and the `HR_CANDIDATES` entries (stable ids). No name, email or phone. `has_address` is true only for a consented address. `offered_slots` is per row. |
| `follow_up` | intake | `{case_id, kind: "memo_check"\|"missing_facts"\|"call_reminder"}` | A templated follow-up (template `follow-up-<kind>`) in the client's own thread. Trust type `email`; event `client.followed_up`. At most 1 a day per case. |

`get_candidate` adds `offered_slots`, and `/api/state` hiring counts `sourced` and `contacted`.

v1.11.3: `HR_BOOKING_URL` (optional, https). When it's set, `hr-outreach` adds a line with the CEO's own
booking page (`{{booking_url}}`) beside the calendar invite. It's only ever sent to consented candidates.

v1.11.1, event payloads (the frontend's proposal, adopted):
- `ad.created` (mkt): `{kind: "client"|"hiring", headline, image_url}`. `image_url` is https on the gateway's own host.
- `hr.sourced` (hr): `{count, source: "monid"|"none"}`. Counts only: no lead labels or URLs on the public log.
- `hr.outreach` (hr): `{candidate_id, label, slot}`. `label` is the gateway's letter, "Candidate A"; never a name or email.
- `client.followed_up` (intake): `{kind}`, with `case_id` in the envelope.
- `call.booked` gains an optional `kind: "client"|"candidate"` (absent = client). A candidate call has
  `case_id` null and `invitee_masked` set to the label.
- **Order linking:**
  - The CEO-order wake becomes `CEO order_<id>: <text>`, where `<id>` is the order's
    `voice.command` event id.
  - The Chief of Staff copies `order_<id>` into each card's body, and agents pass
    `order_id: "order_<id>"` on the tools they call for that card.
  - Every §6 tool accepts an optional `order_id`, matching `^order_[0-9]+$` and referring to an
    existing CEO `voice.command`, else it's ignored. The gateway stamps it into the event payload.
  - The theater groups steps by `order_id`, and falls back to matching by department when it's
    absent.

**v1.15 (design D35, documents):**

`prepare_documents` (acct): see its §6 row above. Behaviour: refused unless `QA_PASSED`; the gateway builds the workpaper .xlsx, the memo .docx and a books entry from the engine's result and the extracted facts only; it verifies each file, stores them under `UTAX_DATA_DIR/docs/<case_id>/`, and emits `doc.step`, `doc.created` and `doc.verified`; it's idempotent per case and memo version.

v1.18.3 (task 44, security): every workbook text cell is stored as literal text, never a formula. A leading = + - @ tab or CR is
stored as text with Excel's quote prefix. The memo .docx has no field codes.

`send_memo` (v1.15) attaches the case's verified workpaper and memo (AgentMail attachments), and is refused
(`docs_missing`) when `prepare_documents` hasn't run for the current memo version. The theater gets
`doc.*` payloads with no client names (masked labels only). The files are never served publicly;
`GET /api/docs/<case_id>/<file_name>` is admin or session only (the CEO can open them in `/office`).

Fact names: `incorporated_delaware`, `authorized_shares`, `issued_shares`, `stated_par`,
`gross_assets`, `foreign_owner_25`. Optional (never required to finish a case, asked once
at most): `gross_receipts` (cents) and `years_with_receipts` (int), used only for the R&D
payroll-credit flag. Unknown → `rnd_flag: false` and the memo's `{{rnd_note}}` carries a
conditional "you may qualify if…" line instead of a claim.

## 6f. Slow ports and the gateway lock

Sponsor I/O (DevicePort, ScrapePort, and any adapter call that can take seconds) never runs
while the gateway lock is held: fetch outside the lock, then take the lock only to validate
and append events. The Delaware fee is fetched at boot and refreshed before its 6 h TTL; a
live fee that differs from the snapshot fee uses the snapshot and writes `case.flagged`-style
CEO notice (`ceo.notified` reason `flagged`, summary "fee page differs from snapshot").

## 6a. Who books money (no agent does)

Agents never report money. The gateway books agent spend itself as `cost.booked`: from
Naïve's `session.usage` events (`consumed_micro_usd`, integer micro-USD, converted in
code) or `GET /v1/credits/spend` per agent, and from sponsor responses (OpenRouter cost,
Monid charges) when those adapters run. Stand-in tonight: the local driver books a fixed
price-table estimate in code (`estimated: true`). Apify and Mobilerun runs book an estimate
per run (Apify ≤ its `maxTotalChargeUsd`; Mobilerun from steps × the per-step price in code).

## 6b. Waking the crew (gateway → Naïve)

Nothing in Naïve wakes an agent when work arrives, so the gateway starts a **Chief of
Staff session** through its Naïve REST client (`POST /v1/sessions`, agent `chief-of-staff`,
identity `utax-cos`)
with exactly one of these opening messages (the CoS prompt parses them):

| Trigger | Message |
|---|---|
| `email.received` (client inbox) | `New case case_N` |
| `POST /api/voice` with `speaker: "customer"` | `FAST PATH case_N` |
| `POST /api/approve` | `Approval apr_N allowed for case_N` / `Approval apr_N denied for case_N` (`for firm` when there is no case) |
| `POST /api/voice` with `speaker: "ceo"` | `CEO: <text>` |
| inbound applicant mail (hiring inbox) | `New applicant cand_N` (the CoS does nothing yet) |
| the applicant's first questionnaire reply | `Applicant answered cand_N` (the CoS routes it to an `hr` card) |
| a client reply with digits on a SENT or CALL_BOOKED case | `Client replied case_N` (Intake re-extracts; a changed fact makes memo v2; a reply with no digits, like "thanks!", wakes nobody). Digits are looked for in the **new text only** (quoted lines and signatures stripped, after redaction). At most 3 such wakes per case per day and 2 memo revisions per case; beyond that the case is BLOCKED and flagged for the CEO. Revision memos use their own send bucket, separate from the firm-wide email cap. After a revision is sent, a case that had a call booked returns to CALL_BOOKED. |

Each wake writes nothing extra; the session's own tool calls produce the events.
Stand-in tonight (no Naïve until Saturday, design D26): the **local driver** receives the
same messages and plays the agents' tool calls through `/mcp/<actor>` with their tokens.

## 6b-bis. Naïve confirms (v1.9, design D29, approved by the owner)

- The gateway watches the U-Tax agents' sessions that are `idle` with
  `stop_reason: "awaiting_approval"`: those it started itself, plus a poll of
  `GET /v1/sessions` every 5 s, filtered to the 7 U-Tax agent ids.
- For each `pending_actions[]` entry of `kind: "tool"`, it calls
  `POST /v1/sessions/{id}/tool_confirmations` `{tool_call_id, decision, reason}`:
  - `allow` (one call, no session scope) when `name` is `board_read`, `board_write`, or
    `utax.<tool>` with `<tool>` in that agent's own §6 column;
  - v1.10.3 (owner: "allow handoffs"): for the **Chief of Staff only**, also `send_to_agent`,
    `wait_for_agents` and `list_agents`, Naïve's coordinator tools, which hand work to our own
    agents and reach no outside party. Any other agent asking for them is denied.
  - `deny` for anything else, with a CEO notice (`ceo.notified` reason `flagged`, summary
    "Naïve asked to run <name>; denied").
- `kind: "question"` (`ask_operator`) is never answered by the gateway. It goes to the CEO
  as a notice.
- Event `naive.confirm` (actor `system`):
  `{session_id, agent, tool, decision: "allow"|"deny", read_people}`. Idempotency key:
  `tool_call_id`.
- Needs `NAIVE_API_KEY` with `sessions:read` and `sessions:write`.
- v1.12.1 (task 27): a U-Tax agent session that went `idle` with `stop_reason: "error"`, created in the last
  2 h, gets at most 2 recovery messages (`POST /v1/sessions/{id}/messages`, the first ≥ 30 s after
  first sight, the second ≥ 2 min after that). Then one `ceo.notified` (reason `blocked`).
  Event `naive.recovered` (actor `system`): `{session_id, agent, attempt}`; idempotency key
  `naive.recovered:<session_id>:<attempt>`.

## 6c. Two inboxes

`MAIL_INBOX_CLIENTS` (checkups) and `MAIL_INBOX_HIRING` (applicants), two AgentMail inboxes
on Saturday, two fixture folders tonight. Applicant mail: the gateway auto-replies with the
questionnaire template (trust type `email`), writes `hr.applicant` with the masked name and
stated credentials only (no contact details reach any agent), then wakes the crew with
`New applicant cand_N`. Questionnaire answers arrive as more mail on the same thread and are
added to the candidate, masked the same way.

## 6c-bis. Ingress rules (all inboxes)

- Mail from the firm's own addresses (`CEO_EMAIL`, `MAIL_INBOX_CLIENTS`, `MAIL_INBOX_HIRING`) is
  dropped and logged, never a case (mail-loop guard).
- `text/html` bodies become plain text at ingress; no markup is ever stored.
- Auto-replies are dropped at ingress (`Auto-Submitted` other than `no`, `X-Autoreply`,
  `Precedence: bulk|auto_reply|list`).
- Fact conflicts are judged **within one message**; a later message supersedes an earlier one.
- Quotes are matched against the raw text but **masked before** they are written to
  `facts.extracted`.
- Masking never hides a word from the tax vocabulary (delaware, corporation, corp, inc, llc,
  shares, authorized, issued, assets, foreign, owner, par, s-corp, c-corp, yes, no). SSN and EIN
  forms are always redacted, including `NN-NNNNNNN`.

## 6d. Templates and the voice prompt

The gateway loads **every** `naive/templates/*.md` with the strict memo renderer (unknown or
missing placeholder → built-in fallback, logged), not only `memo.md`. The names and
placeholders are listed in `naive/README.md`: `memo`, `ask-client`,
`not-eligible-<reason>`, `hr-questionnaire`, `hr-hired`, `hr-declined`, `ceo-update-email`
(with an `action_line` per reason), `ceo-digest-email`, `login-brief`. Template files carry no
Subject line; the gateway sets subjects. `hr-questionnaire` must contain the exact line
`This screen is run by an AI agent. A human makes the final hiring decision.`
`POST /api/voice/session` uses `naive/prompts/voice-intake.md` as the Realtime instructions
and sets instructions, tools **and `input_audio_transcription`** (model from
`OPENAI_TRANSCRIBE_MODEL`, default `whisper-1`) **server-side when minting**, so the customer's
words arrive as text the page can post. The page may repeat a `session.update` that sets only
`input_audio_transcription`, never instructions or tools. The mint response also carries
`realtime_url` (the SDP exchange endpoint the page must use), so the page never hard-codes it; so a client `session.update`
cannot replace them.

## 6e. Trust boundaries for `/talk` and mail volume

- `/talk` posts speaker-labelled turns (`{turns: [{speaker: "customer"|"agent", text}]}`).
  Agent turns are kept as **labelled, non-quotable context** (so a bare "No." keeps its
  question). Fact quotes must match customer lines only, never the AI's read-back.
- Known risk, accepted for the hackathon: an ephemeral Realtime session may let the client
  replace server-set instructions. The session has no tools, a per-response cap, and facts
  come only from customer turns checked in code, so the worst case is odd speech to that
  caller. Post-hackathon fix: a server-side relay. `reply_email` comes from the typed field, never from the transcript.
- Memos per `reply_email`: at most 3 a day, on top of the per-IP limit.
- A reply from a voice case's `reply_email` threads onto that case (the voice case's sender
  key is the reply address), never opening a new case.
- Flag mails to the CEO: the first `case.flagged` in an hour mails right away; the rest go
  into the 3-hour digest with a count.

## 7. Memo template

v1.17: optional `{{payment_link}}`, the case's Stripe link, filled in the client's copy only. A line holding it
disappears whole when there's no link (no Stripe, or Stripe down) and in the masked `memo.sent` body. There's a new
template `payment-receipt` `{{greeting_name}} {{booking_url}}` (booking_url required, plus the disclaimer). It's sent once
after a payment, to the memo thread, and only when HR_BOOKING_URL is set.

`naive/templates/memo.md` (agents lane writes the words), rendered by the gateway with
only these placeholders: `{{memo_ref}}`, `{{greeting_name}}` (the outgoing mail only: derived from the **sender's email domain label**,
letters/digits/spaces only, at most 40 chars, never from body text; `/talk` cases and free-mail
domains get `there`; the `memo.sent` event copy uses the masked name),
`{{masked_name}}`, `{{authorized_method}}`, `{{assumed_par_method}}`,
`{{annual_report_fee}}`, `{{total}}`, `{{due_date}}`, `{{form5472_note}}`, `{{rnd_note}}`.
The memo's **last line** is `Reference: {{memo_ref}}` (`UTX-` plus 8 to 16 capitals/digits, made by
the gateway per memo version); the phone check finds the memo only by it. Amounts arrive formatted (`$85,165`). The template must contain the line
`Educational estimate — not tax advice.` The gateway refuses to send if it doesn't, and
falls back to a built-in template if the file is missing.

## 8. Theater flags and the two voice/console pages

- Once a case has a `case.flagged` (`possible_injection`) event, the gateway serves **every**
  event of that case (earlier ones included) to theater-token readers with `subject`, `body`,
  `voice.command.text` and `facts.extracted.quotes` values replaced by `"[hidden: flagged by QA]"` (`/api/events`, the stream, `/api/state`); the admin
  token still sees them. The theater never shows the mail body of a flagged case either; it shows "hidden: flagged by QA" instead. It renders images only from hosts in
  `NEXT_PUBLIC_ASSET_HOSTS`.
- Theater env: `NEXT_PUBLIC_SHOW_BLOCKED` and `NEXT_PUBLIC_SHOW_QUEUE`, both default `1`
  (design D22): red BLOCKED card from `case.blocked` only (a `qa.failed` the firm resolves by
  re-asking never shows it), waiting chips from
  `/api/state` `queue.waiting`.
- **`/talk`: "Talk to U-Tax"** (design D23), public-facing, no admin token. A customer or
  the finale judge talks with the firm's AI agent (OpenAI Realtime session from
  `POST /api/voice/session`; fallback: typed chat plus browser speech). The agent speaks
  for the firm, asks for the facts, reads the numbers back, takes a reply email, posts the
  transcript to `POST /api/voice` (`speaker: "customer"`), then shows the case moving live.
  Because it's public, `POST /api/voice` with `speaker: "customer"` and `/api/voice/session`
  are rate-limited per IP and need no admin token.
- **`/ceo`: the CEO console** (design D24/D25), admin token. On login it plays and shows
  the brief from `GET /api/ceo/brief`, lists pending approvals with Approve/Deny
  (`POST /api/approve`), and has an optional typed order box (`POST /api/voice`,
  `speaker: "ceo"`, `channel: "typed"`).
- **`/ceo` voice mode (v1.8, design D28):**
  - Inputs: a mic button (push-to-talk), a hands-free toggle, and a STOP EVERYTHING
    button (also `Esc`), which sends `pause everything` with no confirm.
  - Realtime session from `POST /api/voice/session {mode: "ceo"}`. Function calls go to
    `POST /api/ceo/tool`, and their results go back to the model.
  - A proposal shows a confirm card with the `readback`. Confirmation comes from the CEO's
    own `input_audio_transcription.completed` text, matched by the same grammar in the
    console, then `POST /api/ceo/confirm` with `via: "voice"` and `heard`. A click works
    too. Model output never confirms.
  - Echo guard (v1.8.1): the mic is opened with `echoCancellation: true`. A transcript
    counts as a confirm only if its speech started after the agent's audio for the
    proposal finished (`output_audio_buffer.stopped`, or `response.done` when no audio
    event comes), and only if it doesn't contain the readback's own words ("say
    confirm"). Any earlier transcript is ignored for confirming, but can still cancel.
  - Result back to the model (v1.8.1): after `/api/ceo/confirm` (or a cancel or expiry),
    the console sends `conversation.item.create` with a system message `Action result:
    <summary>` and then `response.create`, so the agent says what actually happened.
    The model never receives a proposal_id it could act on.
  - Hands-free speaks each new `approval.requested` and `case.blocked` from the stream.
  - Fallback without a session: browser `SpeechRecognition` (or typing) →
    `POST /api/ceo/command`, with answers spoken by `speechSynthesis`.
  - The theater's touches rail shows a mic icon for `channel: "voice"`.

## 9. Agent market (v1.19, design D38)

Source: `docs/designs/agent-market/RUNBOOK.md` §4 (the runbook calls this "v1.18"; v1.18–v1.18.4 were
already taken). **Landing rule:** the §3 event rows and the §6 tool rows below are copied into §3 and §6 in the
SAME merge as the code that emits or serves them (backend D38-b; agents A41 for the Naïve tool list), so CI stays
green. Until then this section is the shape every lane builds to. Off switch: `MARKET_ENABLED=0`, which refuses
everything below.

**9.1 Events** (all masked; no private key, ever):

| type | actor | provider | payload keys | notes |
|---|---|---|---|---|
| `job.posted` | mkt | null | `job_id, template, price_usd, headline` | idem `job:{key}` |
| `job.claimed` | system | null | `job_id, contractor` | contractor = the registry name |
| `job.submitted` | system | null | `job_id, contractor, sha256` | the artifact stays private until paid |
| `job.verified` | system | null | `job_id, checks` | checks = `[{name, ok}]` |
| `job.failed` | system | null | `job_id, checks, reason` | reason: `checks_failed` / `expired` / `contractor_error` |
| `payment.sent` | system | `tempo` or `local` | `job_id, contractor, amount_usd, token, tx_hash, explorer_url` | `cost_usd` = amount; status `failed` + private `unsure` on an unsure send |
| `job.delivered` | system | null | `job_id, image_url` | `image_url` is absolute: `https://<UTAX_PUBLIC_URL host>/assets/ad_<n>.png` (what `post_social` accepts) |
| `agent.sale` | system | `tempo` or `stripe` | `amount_usd, token, tx_hash, explorer_url, payer_masked` | sell side; payer = `0x12…ab` |

- `PROVIDERS += {"tempo"}`.
- The same payment also writes `cost.booked` (`provider tempo|local`, `model "contractor:share_card"`,
  `estimated false`), so the money panel's spend includes it. The gateway books money; no agent does.
- `public_state` adds `contractor_paid_usd` (Σ `payment.sent` ok) and `agent_sales_usd` (Σ `agent.sale`).

**9.2 MCP tools (Marketing, `mkt`, only):**
- `hire_contractor {template: "share_card", headline: string, key: string}` → `{job_id, status: "open", price_usd}`.
  - The headline must pass `post_problem()` and `_names_a_client()`, the same as `make_ad`.
  - The price comes from `MARKET_PRICE_USD`; agents can't set it.
- `get_job {job_id}` → `{job_id, status, contractor?, image_url?, tx_hash?, explorer_url?, checks?, approval_id?}`.
  - While `status` isn't final (`delivered`, `failed`, `expired`), the gateway waits up to ~20 s for a change
    before it answers (outside the gateway lock); agents have no clock (agents A41).
  - `approval_id` is present while the payment waits on a `contractor_pay` approval.
  - `status` is one of `open`, `claimed`, `submitted`, `verified`, `paid`, `delivered`, `failed`, `expired`.
- Marketing then posts with the existing `post_social {…, image_url}`.

**9.3 Contractor routes** (Bearer = the contractor's `MARKET_CONTRACTORS` token; 30 req/min per token):
- `GET /market/llms.txt`: plain-text docs for agents (how to claim, submit and get paid). No auth.
- `GET /market/jobs?status=open` → `{jobs: [{job_id, template, price_usd, input: {headline}, expires_at}]}`.
- `POST /market/jobs/{job_id}/claim {wallet, invoice_url}` → 200 job | 409 `taken` | 403 `wallet_mismatch` | 410 `expired`.
  - `wallet` must equal the registry wallet.
  - `invoice_url` must start with the registry `invoice_base`.
- `POST /market/jobs/{job_id}/submit` (multipart `file`, ≤ 2 MB) → 200 `{verified: bool, checks}` | 409 `not_claimed_by_you`.
  - One resubmit is allowed after a fail.

**9.4 Sell to agents:**
- `GET /agent/llms.txt`: what U-Tax sells to agents, the price and the input schema.
- `POST /agent/franchise-check {authorized_shares, issued_shares, gross_assets_cents, stated_par?}` → **402**
  MPP challenge (0.50 pathUSD).
  - Paid: `{authorized_method_cents, assumed_par_method_cents, total_cents, due_date, note: "Educational
    estimate, not tax advice", receipt_tx}`.
  - It uses `app/engine` only: no case, no email, no PII.
  - Bad input → 422 before any challenge (never charge for bad input).

**9.5 Trust and money:**
- `TRUST_TYPES += ("contractor_pay",)`; `CAP_EVENTS["contractor_pay"] = ("payment.sent",)`.
- It starts `ASK`: the first payment needs the CEO's spoken approval (a training touch), then AUTO within caps.
- A payment over `MARKET_MAX_JOB_USD`, or one that would pass `MARKET_MAX_DAY_USD`, always asks.
- Only the gateway pays, from its own tick, after the code checks pass; no agent ever holds a wallet key.
- Testnet only: Tempo Moderato, chain 42431, token pathUSD. The tokens have no real value; say so on screen.

**9.6 Wakes, expiry and posting a contractor's image** (agents A41 review):
- Wakes to the chief of staff:
  - On `job.delivered`: `Job job_N delivered (order_<id>)`.
  - On `job.failed`: `Job job_N failed: <reason> (order_<id>)`.
  - A `contractor_pay` approval wake names its type: `… (contractor_pay job_N)`.
- `MARKET_JOB_TTL_MINUTES` defaults to **3**, matching Marketing's checks (about 9 × 20 s). A job past its TTL
  expires and can no longer be claimed, delivered or paid.
- **A contractor's image never posts unseen.** `post_social` with an `image_url` from a market job is ASK until
  the CEO has approved one post with an image from that contractor. A third party's pixels are a new kind of
  content: the format, size and headline checks are filters, not a review.

## Changelog

- v1.19 (2026-11-08, D38): agent market (§9): MCP tools hire_contractor/get_job (mkt); /market/* contractor routes; /agent/franchise-check (MPP 402); events job.*, payment.sent, agent.sale; provider tempo; trust type contractor_pay. §3/§6 rows land with their code.

- v1.18.4 (2026-11-08, task 45): /api/stt books max(declared, a byte floor).

- v1.18.3 (2026-11-08, task 44): xlsx cells literal (no formula injection).

- v1.18.2 (2026-11-08, task 43): events types= filter, cells fmt, relay replay skip.

- v1.18.1 (2026-11-08, task 41): stop-first talk, per-turn actions, STOP fences every assistant start tool.

- v1.18 (2026-11-08, task 42, D37): agent.activity; admin artifact content (sent, cells); the receipt guard.

- v1.17 (2026-11-08, task 40b, D36): Stripe filing-help link in the memo, /hooks/stripe, payment.link_created / payment.received, revenue_cents, the payment receipt and the CEO's "paid" notice.

- v1.16.1 (2026-11-08, task 40a, D36): voice ports split (ADAPTER_TTS/REALTIME/STT); POST /api/stt (raw audio body) on OpenAI gpt-4o-transcribe.

- v1.16 (2026-11-08, backend 36/39 + frontend D32/D33): session transport (Bearer only, stream tickets); the product shell routes.

- v1.15.1 (2026-11-07, backend 38b): hr_record stage outreach_declined; event hr.outreach_declined.

- v1.15 (2026-11-07, design D35): prepare_documents (xlsx workpaper, docx memo, books entry), doc.step/created/verified events, memo attachments, /api/docs.

- v1.14.1 (2026-11-07, agents t7b review): pinned the stop note text, the System: strip, the RESUME-after-stop refusal, the ambiguous refusal shape, the actor ceo_assistant.

- v1.14 (2026-11-07, design D34): /api/ceo/talk conversation, MCP seat ceo (direct actions, no confirm), undo.

- v1.13.1 (2026-11-07, frontend review): stop-kind commands bypass the open-proposal guard.

- v1.13 (2026-11-07, design D33): CEO password login + sessions; command loop guard; greetings never orders.

- v1.12.3 (2026-11-07, owner UX): unparsed CEO command text becomes an order proposal.

- v1.12.2 (2026-11-07, backend 28 review): /api/signup always answers {"ok": true}.

- v1.12.1 (2026-11-07, backend task 27): naive.recovered; session-error recovery.

- v1.12 (2026-11-07, design D32): §5a `POST /api/signup`, event `tool.called`; template `signup-welcome`.

- v1.11.3 (2026-11-07, owner's calendar link): HR_BOOKING_URL in hr-outreach.

- v1.11.2 (2026-11-07, D31 agents + backend): `list_candidates` (hr); refusal codes slot_bound, no_public_url, too_soon, no_consented_address; every MCP schema offers order_id.

- v1.11.1 (2026-11-07, frontend D31 proposal): payloads for ad.created, hr.sourced, hr.outreach, client.followed_up; call.booked kind; order_id linking (wake `CEO order_<id>: <text>`).

- v1.11 (2026-11-07, design D31): tools make_ad, source_candidates, hr_outreach, follow_up; HR_CANDIDATES; events ad.created, hr.sourced, hr.outreach, client.followed_up.

- v1.10.3 (2026-11-07, owner "allow handoffs"): the confirm resolver also allows the CoS's coordinator tools.

- v1.10.2 (2026-11-07, frontend review): /api/tts talk speaks phrase keys only (registry naive/prompts/talk-phrases.json); ceo is unmasked.

- v1.10.1 (2026-11-07, frontend D30 build): /api/tts CORS, streaming, 429 on a spent talk budget.

- v1.10 (2026-11-07, design D30): §5c speech output on ElevenLabs (`POST /api/tts`, `ADAPTER_VOICE=elevenlabs`); browser STT is the main listening path.

- v1.9.1 (2026-11-07, backend 17b): resolve-purchase `episode`; Monid hold lifted.

- v1.9 (2026-11-07, design D29): §6b-bis, the gateway resolves Naïve data-leak confirms (allowlist: board + the seat's own utax tools; otherwise deny); event `naive.confirm`.

- v1.8.6 (2026-11-07, backend 18c as built): post readback text+link+channel (private); `409 console_only`; order public readback literal.

- v1.8.5 (2026-11-07, backend 18b/18c): `channel` on /api/ceo/tool; confirm fails closed on non a-z or any `?`; full-text social readback; `heard` private.

- v1.8.4 (2026-11-07, backend task 18): strict spoken-confirm grammar; 409 flagged; ceo/command confirm shape; optional channel on /api/ceo/tool.

- v1.8.3 (2026-11-07, agents task 5 question): an approval flips an ASK type to AUTO even after a manual ASK.

- v1.8.2 (2026-11-07, backend task 17): resolve-purchase is per episode, with stricter args and 409s; buy_tool store_unavailable; Monid held local until 17b.

- v1.8.1 (2026-11-07, agents task 5 review): `/ceo` echo guard for voice confirms; the action result goes back to the model as a system item.

- v1.8 (2026-11-07, design D28): the CEO runs the firm by voice. §5b CEO tool registry; `/api/ceo/tool`,
  `/api/ceo/confirm`, `/api/ceo/command`; `/api/voice/session` `mode: "ceo"`; `trust` `"*"` pause;
  events `ceo.proposed|confirmed|cancelled`; touch `channel`; `/ceo` voice mode (§8).

- v1.7.8 (2026-11-07, backend task 14): supersede triggers and `superseded_by`; console refetches on 409.

- v1.7.7 (2026-11-07, frontend /ceo review): superseded approvals; `/api/trust` ASK and RESUME;
  allowed-while-paused approvals carried out on resume; event mode refuses empty admin/theater tokens.

- v1.7.6 (2026-11-07, backend task 13): `/api/voice` refusals `429 at_capacity`, `413 too_large`,
  `400 too_long`; public daily case cap; voice spend booked as estimates with a hold.

- v1.7.5 (2026-11-07, frontend /talk): the voice session is minted with input transcription
  (`OPENAI_TRANSCRIBE_MODEL`, default whisper-1); the mint response carries `realtime_url`.

- v1.7.4 (2026-11-07, backend task 10): spend counts `cost.booked` only (theater rail too);
  `POST /api/dev/resolve-purchase`.

- v1.7.3 (2026-11-07, backend task 10): `buy_tool` for mkt (Monid; allowlist, per-tool ceiling, daily
  cap, outside the lock, rebuilt from events at boot). Nothing is bought until the owner names tools.

- v1.7.2 (2026-11-07, backend task 9): flagged hiding also covers voice text and fact quotes.

- v1.7.1 (2026-11-07, frontend slice 3 review): `cases[].flagged`; hiring `awaiting` and `declined`;
  gateway hides flagged cases' subject/body from theater-token readers, retroactively; tax-saved parity rule.

- v1.7 (2026-11-07, agents task 4 review): `{{memo_ref}}` last line and `memo_ref` on memo.sent;
  phone.checked public payload without the screenshot URL; async phone check with per-attempt keys;
  cleaned law queries with an hourly cap; slow ports outside the lock (§6f); per-run spend estimates.

- v1.6.4 (2026-11-07, frontend slice 2): `masked_name` on customer `voice.command`; `image_url` host
  allowlist (`PUBLIC_ASSET_HOSTS` / `NEXT_PUBLIC_ASSET_HOSTS`); flagged cases' mail bodies hidden on the theater.

- v1.6.3 (2026-11-07, backend task 7): `get_case` names-kept copy for intake and qa only; other
  seats get the masked copy.

- v1.6.2 (2026-11-07, agents v1.6 review): greeting_name from the sender domain only; Client
  replied caps, new-text-only digits, revision send bucket, CALL_BOOKED restore; auto-reply drop;
  per-message conflicts; quotes masked before events.

- v1.6.1 (2026-11-07, backend 6b): AI turns kept as non-quotable context; Realtime override
  risk accepted and documented; `hr.decision_mailed`; candidate list admin-only.

- v1.6 (2026-11-07, agents cross-lane QA): `get_case` body keeps names and facts (contact
  details and SSN/EIN redacted only); `Client replied case_N` wake and memo v2; ingress rules
  (own-address drop, HTML to text, tax-word stop list, EIN form); `{{greeting_name}}`.

- v1.5 (2026-11-06, agents task 2 review): post links appended by the gateway (`kind`
  client|hiring, `PUBLIC_TALK_URL` required); `Applicant answered` wake; all templates loaded,
  voice-intake prompt minted server-side; `/talk` customer-turns-only, 3 memos/day per address,
  voice re-ask threading, flag mail throttle; every screened candidate visible to the CEO,
  decision mails.

- v1.4.3 (2026-11-06, frontend slice 2): `/api/brief/audio` accepts the theater token.

- v1.4.2 (2026-11-06, backend task 6): `POST /api/cases/{id}/unblock`; cap-hit PAUSED flow;
  `ceo.notified.reason` adds flagged, shortlisted, paused.

- v1.4.1 (2026-11-06, backend task 5): `tax_saved_cents` excludes the annual fee and never goes
  negative; `hr.questionnaire_sent`; fixture post text says `$85,165` (the guard refuses `$85k`).

- v1.4 (2026-11-06, agents review pass 3): per-seat MCP URLs `/mcp/<actor>`; HR `get_candidate`,
  HR never declines (CEO decides via `/api/candidates/{id}/decision`); QA `flag_case` and
  `case.flagged`; post guard also blocks @handles and foreign URLs; CEO emails include flags
  and shortlists; `POST /api/trust` for standing orders; wake sessions use identity `utax-cos`.

- v1.3.2 (2026-11-06, frontend review): ids increase but may have gaps; `qa.failed` names a
  failing check; the BLOCKED card comes only from `case.blocked`.

- v1.3.1 (2026-11-06): cost strings are fixed-point (no exponent form); fixture `voice.command`
  channel is `voice`.

- v1.3 (2026-11-06, from the agents lane's review): one tool token per agent, and the token
  decides the actor and the allowed tools; no empty-secret bypass; `book_cost` removed
  (the gateway books spend, §6a); wake-up messages (§6b); two inboxes and HR ingress
  (§6c); `mark_not_eligible` reason enum; `offered_slots` on `get_case` and `book_call`
  limited to them; `post_social` content guard.

- v1.2 (2026-11-06): the CEO never speaks (design D23-D25). `/mic` becomes `/talk` (customers
  talk with the AI agent) and `/ceo` (login brief, click approvals, typed orders);
  `speaker: "customer"|"ceo"`; `GET /api/ceo/brief`; CEO email updates and `ceo.notified`.

- v1.1 (2026-11-06): voice moved to a mic page (design D21): `POST /api/voice` gains
  `speaker`/`reply_email` and judge fast-path cases; `POST /api/voice/session`;
  `GET /api/brief/audio`; `voice.command` payload; theater flags (D22); optional R&D
  facts `gross_receipts` and `years_with_receipts`.

- v1 (2026-11-06): U-Tax gateway: event envelope and types, `/api/state`, `/api/events`,
  SSE stream, approve/voice/dev endpoints, MCP tools, memo template placeholders.
- v0 (2026-11-06): basics + `GET /api/health`.
