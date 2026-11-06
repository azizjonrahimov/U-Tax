# naive/: the U-Tax crew on Naïve

Seven agents (Chief of Staff as coordinator, plus Intake, Tax, QA, Accounting, Marketing and HR) on
one shared board, declared in `naive.config.ts` and applied with `naive up`. Their only tools are
the U-Tax gateway's MCP tools (`contracts/api.md` §6). Native email, social, phone and paid tools
are denied by name.

| Path | What |
|---|---|
| `naive.config.ts` | identity, vault (gateway bearer), 7 agents, roster + board, Sunday 8 AM brief, first board card |
| `src/crew.ts` | the agent → gateway-tool map (one source of truth) |
| `src/policy.ts` | deny-by-default toolsets |
| `prompts/*.md` | one system prompt per agent; `_firm-rules.md` is appended to each |
| `templates/*.md` | memo (§7) and reply/post/demo texts |
| `scripts/check.mjs` | offline check: loads the config through the blueprint engine and checks tools, prompts, memo |

## Env vars (see `.env.example`)
`NAIVE_API_KEY`, `UTAX_MCP_URL` (required; public HTTPS), `UTAX_TOOL_SECRET` (required for `naive up`),
`UTAX_PHONE_E164` (optional, off).

## Commands
```bash
cd naive
npm install
npx tsc --noEmit          # typecheck
npm run check             # offline validation, no key needed
naive up --dry-run        # plan against the real org (needs NAIVE_API_KEY, UTAX_MCP_URL, UTAX_TOOL_SECRET)
naive up                  # apply
```

Models are marked `// UNVERIFIED: check GET /v1/models` until checked against the live catalogue
(`naive models get z-ai/glm-5.3-flash`).
