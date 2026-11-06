# naive/: the U-Tax crew on Naïve

Seven agents (Chief of Staff as coordinator, plus Intake, Tax, QA, Accounting, Marketing and HR) on
one shared board, declared in `naive.config.ts` and applied with `naive up`. Their only tools are
the U-Tax gateway's MCP tools (`contracts/api.md` §6) plus the board. Native email, social, phone,
paid and escalation tools are denied by name.

| Path | What |
|---|---|
| `naive.config.ts` | 7 agents, each with its own identity + vault + gateway token; roster + board; Sunday 8 AM brief; first-run briefing |
| `src/crew.ts` | the agent → gateway-tool map (one source of truth) |
| `src/policy.ts` | deny-by-default toolsets |
| `prompts/*.md` | one system prompt per agent; `_firm-rules.md` is appended to each |
| `templates/memo.md` | the client memo (contract §7 placeholders only) |
| `scripts/check.mjs` | offline check: engine validation, exact tool policy, contract §6 names, prompts, memo |

## Env vars (see `.env.example`)
`NAIVE_API_KEY`, `UTAX_MCP_URL` (required; public HTTPS ending in `/mcp`), one token per agent
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

Models are marked `// UNVERIFIED: check GET /v1/models` until checked against the live catalogue
(`naive models get z-ai/glm-5.3-flash`).
