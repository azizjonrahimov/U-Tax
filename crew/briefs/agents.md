# Brief: agents (P3: Naïve agents, prompts, voice)

```text
Lane: agents · Controller: naive-yc-hackathon-99 · Rules: crew/CREW.md · Base: main
Branch: lane/agents · Worktree: D:\Claude Projects\Naive YC Hackathon\.worktrees\agents
Owns: naive/, crew/reports/agents.md
Do not touch: backend/ fixtures/ db/ (backend), frontend/ (frontend), contracts/ docs/ research/ reference/ and root files (controller)
Plan: approved: docs/designs/u-tax.md (rule 13)
Spend cap: up to $5 of Naïve credit for spikes once the owner's key exists; more is an ask
```

Read, in order: `crew/CREW.md`, `U-TAX-CLAUDE.md`, `docs/designs/u-tax.md` (all of it),
`contracts/api.md` v1 (§6 MCP tools and §7 memo template are yours to use), then
`research/naive-sdk.md` (§3 agents, budgets, tools and permissions, scheduling,
multi-agent teams, identity; §4 Studio and blueprints; §5 custom tools via MCP; §7
gotchas). The Naïve CLI is installed globally: `naive` 1.0.0 (`@usenaive-sdk/cli`). The
docs are at https://usenaive.ai/docs (index: https://usenaive.ai/docs/llms.txt); read the
pages you need, never guess an API name.

There is **no Naïve API key yet**: the owner still has to subscribe. Build everything
that doesn't need one first. Send one READY per task.

## Task 1: `naive/` project (READY when done)
- `package.json` (`"type": "module"`), `tsconfig.json`, and `naive.config.ts` declaring the
  firm: Chief of Staff as coordinator with a roster of Intake, Tax, QA, Accounting,
  Marketing, HR and a shared board; conservative budgets in integer micro-USD; models
  chosen from the docs (mark each `// UNVERIFIED: check GET /v1/models` until checked).
- Tools: **only** the U-Tax MCP server (URL from env `UTAX_MCP_URL`, bearer
  `UTAX_TOOL_SECRET` stored in a Naïve vault). Native email and social tools are denied
  (decision E5). Policies set so autonomy doesn't stall on `ask` gates the gateway already
  enforces (trust lives in `perform()`).
- The 8 AM Sunday morning-brief schedule for the Chief of Staff.
- `npx tsc --noEmit` green.

## Task 2: prompts and templates (READY when done)
- `naive/prompts/<agent>.md` for all 7 agents. Each names exactly the MCP tools from
  contract §6 that agent may call, and the hard rules: the AI quotes spans and never
  computes a number; never ask for SSNs or EINs; "Educational estimate — not tax advice";
  HR discloses the AI screen and the human makes the final call; DMs are drafts only.
- `naive/templates/memo.md` with the §7 placeholders only, plus intake re-ask,
  not-eligible reply, job post (a paid 1-hour gig), client-sourcing posts (event
  Discord/Slack, X), the 3-minute demo script cards from the design doc, and a backup
  slide text.

## Task 3: voice spike and Connect checks (needs the owner's key)
When the controller says the key is in place: provision a Naïve phone number and test an
inbound and an outbound call, routed to the Chief of Staff (design doc E2, Open Question 1).
Report exactly what worked, with the commands, request ids and costs. If voice fails,
say so plainly; the decided fallback is the OpenAI Realtime mic page and "judge emails the
firm". Then `naive up --dry-run` against the real org. Google Calendar, X and LinkedIn
Connect need the owner's OAuth: write the exact steps for the owner, don't attempt them.
