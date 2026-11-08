# naive/: the U-Tax crew on Naïve

Seven firm agents (Chief of Staff as coordinator, plus Intake, Tax, QA, Accounting, Marketing and HR)
on one shared board, plus the CEO's own assistant (`ceo-assistant`, v1.14: off the team and the board), declared in `naive.config.ts` and applied with `naive up`. Their only tools are
the U-Tax gateway's MCP tools (`contracts/api.md` §6, each agent on its own `/mcp/<actor>` seat) plus the board. Native email, social, phone,
paid and escalation tools are denied by name.

| Path | What |
|---|---|
| `naive.config.ts` | 7 firm agents + the CEO assistant, each with its own identity + vault + gateway token; roster + board; Sunday 8 AM brief; first-run briefing |
| `src/crew.ts` | the agent → gateway-tool map (one source of truth) |
| `src/policy.ts` | deny-by-default toolsets |
| `prompts/*.md` | one system prompt per agent; `_firm-rules.md` is appended to each |
| `prompts/voice-intake.md` | the `/talk` voice agent (OpenAI Realtime, run by the backend; not a Naïve agent, no tools) |
| `prompts/ceo-voice.md` | the `/ceo` voice agent for the CEO (OpenAI Realtime, run by the backend; not a Naïve agent; only the contract §5b CEO tools, every start action a proposal the CEO confirms) |
| `prompts/ceo-assistant.md` | the CEO's conversational assistant on Naïve (v1.14 §5a-ter, D34): the §5b CEO tools on `/mcp/ceo`, run directly with no confirm; exact ids for approve, deny and hire; no board |
| `prompts/talk-phrases.json` | the only lines the public `/talk` voice speaks (contract v1.10.2): a flat key → line map the gateway renders for `POST /api/tts` purpose `talk`, numbers only in `{placeholders}` |
| `templates/memo.md` | the client memo (contract §7 placeholders, plus D36's `payment_link`) |
| `templates/*.md` | gateway-sent texts (placeholders below), posts, demo script cards, backup slide |
| `scripts/check.mjs` | offline check: engine validation, exact tool policy, contract §6 names and Agent column, per-seat URLs and tokens, prompts (including the two voice prompts' rules and the §5b CEO tool names), the `/talk` phrase registry (the gateway loader's format, the page's questions word for word), the Chief of Staff's card rules and firm rules 9 and 11 kept word for word, the CEO assistant's seat (§5b tools, no team or board, $3/day) and safety rules, every template's rules, bad-env guard tests |

## Templates for the gateway (values come from code and projections, never from an agent)
Status: the gateway loads `memo.md` today. The others are the agreed wording with these exact
placeholders, for the backend to load the same strict way (unknown or missing placeholder → refuse,
built-in fallback); until then it sends its own hard-coded text. Every file is sent as the body; the
gateway sets the subject. Placeholders that may be empty (`form5472_note`, `rnd_note`, `problems_line`,
`approvals_line`, `blocked_line`, `hiring_line`) sit inline in a sentence, never alone on a line, so an
empty value leaves no blank gap; a client is greeted by `greeting_name`, never the masked name.

| File | Sent when | Placeholders |
|---|---|---|
| `memo.md` | `send_memo` | contract §7: `greeting_name` (the client's company name, else "there"), the amounts, `due_date`, `form5472_note`, `rnd_note`, `payment_link` (D36: the Stripe link for the $49 offer of a human-led 20-minute walkthrough of the client's own filing (U-Tax files nothing); its whole line is dropped when empty, by the gateway once its D36 change lands) (`masked_name` allowed, unused) |
| `ask-client.md` | `ask_client` | `greeting_name`, `missing_list` (joined fact labels, never `stated_par`), `problems_line` (may be empty) |
| `not-eligible-<reason>.md` | `mark_not_eligible`, one file per reason enum | `greeting_name` |
| `hr-questionnaire.md` | auto-reply to applicants (`hr.questionnaire_sent`) | none |
| `hr-hired.md` / `hr-declined.md` | the CEO's decision in `/ceo` | none |
| `hr-outreach-note.md` | `hr_outreach`, email 1 (task 32) | `greeting_name`, `slot_time`, `minutes`; carries the AI-screen line; no links |
| `hr-outreach-invite.md` | the candidate's reply, email 2 (task 32) | `greeting_name`, `slot_time`, `minutes`, `booking_url` (its own line, dropped when `HR_BOOKING_URL` is empty) |
| `follow-up-memo_check.md` | `follow_up` kind `memo_check` | `greeting_name` |
| `follow-up-missing_facts.md` | `follow_up` kind `missing_facts` | `greeting_name`, `missing_list` |
| `follow-up-call_reminder.md` | `follow_up` kind `call_reminder` | `greeting_name`, `call_time` |
| `payment-receipt.md` | `payment.received` (D36) | `greeting_name`, `booking_url` (required: a bare https link to the CEO's 20-minute booking page, on its own line right under the price line) |
| `signup-welcome.md` | `POST /api/signup` (v1.12) | `greeting_name` (the Spec also allows `masked_name`, never used in client mail) |
| `ceo-update-email.md` | approval needed, BLOCKED, flagged, shortlisted, paused | `headline`, `lead`, `summary`, `action_line` (per reason: "Approve or Deny it in the console", "Unblock or skip it", …), `console_url` |
| `ceo-digest-email.md` | every 3 hours | `period`, `clients_new`, `memos_sent`, `calls_booked`, `tax_saved`, `agent_spend`, `touches_training`, `touches_operating`, `touches_engineering`, `pending_approvals`, `blocked_lines`, `hiring_line`, `flags_lines`, `console_url` |
| `login-brief.md` | `/ceo` login brief (spoken + shown) | `greeting`, `clients_new`, `memos_sent`, `tax_saved`, `agent_spend`, `approvals_line`, `blocked_line`, `hiring_line` |

`sourcing-posts.md` and `job-post.md`: Marketing posts the X and LinkedIn sections (`check.mjs` runs
a port of the gateway's post guard over them: only $85,165 / $400 / $450 / $50, no links, @handles,
emails, phone numbers or case names; X posts ≤ 255 characters so the appended link fits). The
Discord/Slack sections are for the owner to post by hand.
`demo-script.md` and `backup-slide.md` are for the presenter; `demo-video.md` is the submission
video's shot list.

## Env vars (see `.env.example`)
`NAIVE_API_KEY`, `UTAX_MCP_URL` (required; public HTTPS base ending in `/mcp`; each agent uses `<base>/<actor>`), one token per agent
`UTAX_TOOL_TOKEN_COS|_INTAKE|_TAX|_QA|_ACCT|_MKT|_HR|_CEO` (required; the backend's values, 32+ characters,
all different), `UTAX_PHONE_E164` (optional, off).
The config reads `process.env`, so export them in your shell before `naive up`.

## Commands
```bash
cd naive
npm install
npx tsc --noEmit          # typecheck
npm run check             # offline validation, no key needed
naive up --dry-run        # plan against the real org (needs the env vars above)
naive up                  # apply
```

## Rotating a gateway token
`naive up` matches a vault credential by kind, key and URL and never compares the value, so a new
token value is ignored while the old credential exists. Rotate one agent's token like this
(`utax-intake-secrets` for intake; the vault is `utax-<actor>-secrets`):
```bash
naive vault credentials --vault utax-intake-secrets      # find the vcr_ id
naive vault rm --vault utax-intake-secrets --credential vcr_...
naive up
```
The same goes for a new tunnel URL: the credential is keyed by `UTAX_MCP_URL`, so `naive up` adds a
new one, and the old one can be removed the same way.

Every agent runs `anthropic/claude-haiku-5.5` (design D31; the live `GET /v1/models` lists it,
window `immediate`). Its per-token price on Naïve is still unverified: the daily caps are ceilings.
