# API contract (v1.1): U-Tax gateway ↔ Agent Theater ↔ Naïve agents

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
- Auth tokens (all from env, never committed; empty value = check disabled for local dev):

| Token | Env var | Who sends it | Where |
|---|---|---|---|
| Tool secret | `UTAX_TOOL_SECRET` | Naïve agents (stored in a Naïve vault) | `Authorization: Bearer <secret>` on `/mcp` |
| Theater token | `THEATER_TOKEN` | Theater, read-only | `?token=<t>` on `/api/state`, `/api/events*` |
| Admin token | `ADMIN_TOKEN` | The CEO's mic/approve page, dev tools | `Authorization: Bearer <t>` on `/api/approve`, `/api/dev/*` |

- Frontend env: `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`), `NEXT_PUBLIC_THEATER_TOKEN`.
- Money: tax amounts are **integer cents** (`*_cents`). Agent cost is a **decimal string**
  of USD (`"0.0123"`), because it is fractional. Shares are integers. Stated par is a
  decimal string (`"0.00001"`). Never floats on the wire for money.
- Times: ISO 8601 UTC strings (`2026-11-07T21:14:03.120Z`).

## 2. Event envelope

Events are append-only and are the only truth. Ids are monotonic integers starting at 1.
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
| `qa.passed` / `qa.failed` | qa | `{checks: [{field, ok, note}]}` |
| `memo.sent` | acct | `{to_masked, subject, body, message_id, seconds_since_last_fact}` |
| `call.booked` | intake | `{start, minutes, invitee_masked}` |
| `phone.checked` | qa | `{screenshot_url, rendered_ok}` |
| `crm.sync_requested` / `crm.synced` | system | `{object: "contact"\|"deal", stage}` |
| `post.published` | mkt | `{channel, text, url, image_url}` |
| `dm.drafted` / `dm.sent_by_human` | mkt / ceo | `{recipient_label, text}` |
| `hr.applicant` / `hr.screened` / `hr.shortlisted` / `hr.hired` / `hr.declined` | hr (hired: ceo) | `{candidate_id, name_masked, note}` |
| `screen.call` | hr | `{candidate_id, transcript_excerpt, seconds}` |
| `voice.command` | ceo | `{text, channel: "mic"\|"typed", speaker: "ceo"\|"judge", case_id}` (`case_id` set for judge cases) |
| `approval.requested` | system | `{approval_id, action_type, summary}` |
| `touch.training` / `touch.operating` / `touch.engineering` | ceo | `{note, approval_id}` |
| `trust.changed` | system | `{action_type, from: "ASK"\|"AUTO"\|"PAUSED", to, reason}` |
| `cost.booked` | any agent | `{provider, model, estimated}` (amount in envelope `cost_usd`) |
| `tool.bought` | any agent | `{tool, provider}` (amount in envelope `cost_usd`) |
| `brief.generated` | cos | `{text, counts}` |

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
`tax_saved_cents` = Σ over sent cases of (`authorized_method_cents` − `total_cents`).

### `GET /api/events?after_id=0&limit=500&token=` → `200 {"data": [Event...], "next_after_id": 57, "has_more": false}`
For Replay mode and catch-up. Ordered by `id`.

### `GET /api/events/stream?after_id=0&token=` → `text/event-stream`
- Each event: `id: <event id>` then `data: <Event JSON>` then a blank line.
- Resume with `Last-Event-ID` header or `after_id`; the server replays everything after it, then streams live.
- Heartbeat comment `: ping` every 15 s. Clients reconnect automatically.

## 5. Control endpoints (admin token)

- `POST /api/approve` `{approval_id, decision: "allow"|"deny", note}` → `200 {"ok": true}`.
  Writes `touch.training` (first approval of an action type) or `touch.operating`, plus
  `trust.changed` when the type flips. Idempotent by `approval_id`.
- `POST /api/voice` `{text, channel: "mic"|"typed", speaker: "ceo"|"judge", reply_email}` →
  writes `voice.command`. `speaker: "ceo"`: forwarded to the Chief of Staff via the Naïve
  REST client once wired (stand-in: log only). `speaker: "judge"`: opens a **fast-path
  case** from the transcript (priority, no queue), memo goes to `reply_email`; returns
  `{case_id}` so the mic page can follow it.
- `POST /api/voice/session` → `{client_secret, model, expires_at}`: an ephemeral OpenAI
  Realtime session minted with the gateway's `OPENAI_API_KEY` (the key never reaches the
  browser). `503 voice_unavailable` when no key is set; the mic page then falls back to
  typed text plus browser speech.
- `GET /api/brief/audio?event_id=<brief.generated id>` → `audio/mpeg` (OpenAI TTS of the
  brief text). `503 voice_unavailable` without a key; the theater falls back to browser
  `speechSynthesis`.
- `POST /api/dev/inject-email` `{from, subject, body}` → local MailPort only; produces
  `email.received` exactly as a real inbound mail would. Refused unless `ADAPTER_MAIL=local`.
- `POST /hooks/agentmail` (Saturday): AgentMail webhook; idempotency key = message id.

## 6. MCP tools for Naïve agents

Served at **`POST /mcp`** (MCP streamable HTTP), `Authorization: Bearer $UTAX_TOOL_SECRET`.
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
| `get_case` | any | `{case_id}` | masked case: email body, facts, quotes, status, result |
| `extract_facts` | intake | `{case_id, quotes: {<fact>: "<verbatim span from the email>"}, incorporated_delaware, foreign_owner_25}` | code parses each quote (`parse_amount`), checks it appears verbatim, conflicting spans → re-ask; `facts.extracted` or `facts.missing` |
| `ask_client` | intake | `{case_id, missing: [<fact>...]}` | templated re-ask email (trust type `email`, max 2); `client.asked` |
| `mark_not_eligible` | intake | `{case_id, reason}` | kind templated reply; `case.not_eligible` |
| `law_search` | tax | `{case_id, query}` | `law.searched` (stand-in: canned Delaware results) |
| `compute_tax` | tax | `{case_id}` | engine on the extracted facts only; `tax.computed` |
| `qa_check` | qa | `{case_id}` | re-runs the engine, re-matches quotes; `qa.passed` / `qa.failed` |
| `send_memo` | acct | `{case_id}` | refuses unless `QA_PASSED`; memo from template + engine output only; `memo.sent` |
| `book_call` | intake | `{case_id, start}` | trust type `calendar_invite`; `call.booked` |
| `phone_check` | qa | `{case_id}` | DevicePort; `phone.checked` (never blocks the case) |
| `post_social` | mkt | `{channel, text, image_url}` | trust type `social_post`; `post.published` |
| `draft_dm` | mkt | `{recipient_label, text}` | `dm.drafted` (the human sends it) |
| `hr_record` | hr | `{stage: "applicant"\|"screened"\|"shortlisted"\|"declined", candidate_id, name_masked, note}` | `hr.<stage>` |
| `book_cost` | any | `{provider, model, tokens_in, tokens_out, cost_usd}` | `cost.booked` (estimated when cost_usd is absent) |
| `firm_brief` | cos | `{}` | counts, blocked cards, touches; `brief.generated` |

Fact names: `incorporated_delaware`, `authorized_shares`, `issued_shares`, `stated_par`,
`gross_assets`, `foreign_owner_25`. Optional (never required to finish a case, asked once
at most): `gross_receipts` (cents) and `years_with_receipts` (int), used only for the R&D
payroll-credit flag. Unknown → `rnd_flag: false` and the memo's `{{rnd_note}}` carries a
conditional "you may qualify if…" line instead of a claim.

## 7. Memo template

`naive/templates/memo.md` (agents lane writes the words), rendered by the gateway with
only these placeholders: `{{masked_name}}`, `{{authorized_method}}`, `{{assumed_par_method}}`,
`{{annual_report_fee}}`, `{{total}}`, `{{due_date}}`, `{{form5472_note}}`, `{{rnd_note}}`.
Amounts arrive formatted (`$85,165`). The template must contain the line
`Educational estimate — not tax advice.` The gateway refuses to send if it doesn't, and
falls back to a built-in template if the file is missing.

## 8. Theater flags and the mic page

- Theater env: `NEXT_PUBLIC_SHOW_BLOCKED` and `NEXT_PUBLIC_SHOW_QUEUE`, both default `1`
  (design D22): red BLOCKED card from `case.blocked` / `qa.failed`, waiting chips from
  `/api/state` `queue.waiting`.
- Mic page: `/mic` in the frontend app, using the admin token (it is the CEO's console).
  Speaker toggle CEO / Judge, a reply-email field for judges, live transcript, read-back
  of the numbers heard, then follows the judge's `case_id` on the stream.

## Changelog

- v1.1 (2026-11-06): voice moved to a mic page (design D21): `POST /api/voice` gains
  `speaker`/`reply_email` and judge fast-path cases; `POST /api/voice/session`;
  `GET /api/brief/audio`; `voice.command` payload; theater flags (D22); optional R&D
  facts `gross_receipts` and `years_with_receipts`.

- v1 (2026-11-06): U-Tax gateway: event envelope and types, `/api/state`, `/api/events`,
  SSE stream, approve/voice/dev endpoints, MCP tools, memo template placeholders.
- v0 (2026-11-06): basics + `GET /api/health`.
