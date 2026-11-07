# naive/: the U-Tax crew on Naïve

Seven agents (Chief of Staff as coordinator, plus Intake, Tax, QA, Accounting, Marketing and HR) on
one shared board, declared in `naive.config.ts` and applied with `naive up`. Their only tools are
the U-Tax gateway's MCP tools (`contracts/api.md` §6, each agent on its own `/mcp/<actor>` seat) plus the board. Native email, social, phone,
paid and escalation tools are denied by name.

| Path | What |
|---|---|
| `naive.config.ts` | 7 agents, each with its own identity + vault + gateway token; roster + board; Sunday 8 AM brief; first-run briefing |
| `src/crew.ts` | the agent → gateway-tool map (one source of truth) |
| `src/policy.ts` | deny-by-default toolsets |
| `prompts/*.md` | one system prompt per agent; `_firm-rules.md` is appended to each |
| `prompts/voice-intake.md` | the `/talk` voice agent (OpenAI Realtime, run by the backend; not a Naïve agent, no tools) |
| `prompts/ceo-voice.md` | the `/ceo` voice agent for the CEO (OpenAI Realtime, run by the backend; not a Naïve agent; only the contract §5b CEO tools, every start action a proposal the CEO confirms) |
| `prompts/talk-phrases.json` | the only lines the public `/talk` voice speaks (contract v1.10.2): a flat key → line map the gateway renders for `POST /api/tts` purpose `talk`, numbers only in `{placeholders}` |
| `templates/memo.md` | the client memo (contract §7 placeholders only) |
| `templates/*.md` | gateway-sent texts (placeholders below), posts, demo script cards, backup slide |
| `scripts/check.mjs` | offline check: engine validation, exact tool policy, contract §6 names and Agent column, per-seat URLs and tokens, prompts (including the two voice prompts' rules and the §5b CEO tool names), the `/talk` phrase registry (the gateway loader's format, word-for-word questions), every template's rules, bad-env guard tests |

## Templates for the gateway (values come from code and projections, never from an agent)
Status: the gateway loads `memo.md` today. The others are the agreed wording with these exact
placeholders, for the backend to load the same strict way (unknown or missing placeholder → refuse,
built-in fallback); until then it sends its own hard-coded text. Every file is sent as the body; the
gateway sets the subject. Placeholders that may be empty (`form5472_note`, `rnd_note`, `problems_line`,
`approvals_line`, `blocked_line`, `hiring_line`) sit inline in a sentence, never alone on a line, so an
empty value leaves no blank gap; a client is greeted by `greeting_name`, never the masked name.

| File | Sent when | Placeholders |
|---|---|---|
| `memo.md` | `send_memo` | contract §7: `greeting_name` (the client's company name, else "there"), the amounts, `due_date`, `form5472_note`, `rnd_note` (`masked_name` allowed, unused) |
| `ask-client.md` | `ask_client` | `greeting_name`, `missing_list` (joined fact labels, never `stated_par`), `problems_line` (may be empty) |
| `not-eligible-<reason>.md` | `mark_not_eligible`, one file per reason enum | `greeting_name` |
| `hr-questionnaire.md` | auto-reply to applicants (`hr.questionnaire_sent`) | none |
| `hr-hired.md` / `hr-declined.md` | the CEO's decision in `/ceo` | none |
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
`UTAX_TOOL_TOKEN_COS|_INTAKE|_TAX|_QA|_ACCT|_MKT|_HR` (required; the backend's values, 32+ characters,
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
