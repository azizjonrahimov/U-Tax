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

## Update 2026-11-06 (design D21): no Naïve voice
Don't buy a number and skip the voice spike. The CEO's orders reach the Chief of Staff as
`voice.command` text relayed by the gateway from the mic page. The morning brief is a
`firm_brief` the theater plays as audio. The HR screen is an email questionnaire, never a
live phone screen, still disclosed as AI-run, and the human makes the final call. Task 3
becomes: `naive up --dry-run` once the key exists, plus written Connect steps for the owner.

## Update 2026-11-06 (design D23-D26)
- No Naïve account until Saturday at the event (D26). Task 3 (`naive up --dry-run`, Connect
  steps) moves to Saturday. Make tasks 1-2 complete and ready to apply with one command then.
- The CEO never speaks. Customers talk to the firm's AI agent on `/talk`; its system
  prompt (the voice Intake agent: warm, brief, asks for the facts, reads numbers back,
  never gives tax advice, says it's an AI) belongs in `naive/prompts/voice-intake.md`, and
  the backend uses it for the OpenAI Realtime session.
- The Chief of Staff keeps the CEO informed by email (approval needed, BLOCKED, a digest
  every 3 hours) and by the login brief (`firm_brief`). Write those templates in
  `naive/templates/` (ceo-update-email.md, ceo-digest-email.md, login-brief.md).

## Contract v1.3 (2026-11-06): your review's 8 asks, decided
All taken as you recommended, except #3, which is stronger: **one tool token per agent**
(`UTAX_TOOL_TOKEN_<ACTOR>`, each in its own vault entry), and the gateway derives the actor
from the token. Keep `X-UTax-Actor` only as an optional matching header. `book_cost` is
removed from §6. Wake messages are exactly as in §6b, plus `New applicant cand_N`.

## Contract v1.4 (2026-11-06): your pass-3 asks, decided
1. `get_candidate` added (hr only). HR never declines; the CEO decides.
2. **Per-seat URLs now, not as a fallback**: `/mcp/<actor>`, each with its own token. Point
   each agent's MCP server at its own seat URL (`${UTAX_MCP_URL}/<actor>`).
3. The post guard also blocks URLs (except the firm's `PUBLIC_TALK_URL`) and @handles.
4. QA gets `flag_case {case_id, kind}`. CEO emails include flags and shortlists. Standing
   orders are gateway switches (`POST /api/trust`), so the CoS doesn't need to remember them.
5. `UTAX_PHONE_E164` stays unset.

## Contract v1.6 (2026-11-07, your QA pass)
- memo.md: greet with `{{greeting_name}}` ("Hi {{greeting_name}},"), never `{{masked_name}}`.
- CoS prompt: handle `Client replied case_N` (route to Intake: get_case, re-extract; a
  changed fact leads to a new memo through the normal QA path).
- get_case now returns the real wording (contact details and SSN/EIN redacted only); prompts
  must still never repeat contact details.

## Task 4: four sponsor adapters (2026-11-07, controller; temporary ownership)
While Naïve waits, you build four backend adapters, **in new files only**:
`backend/app/gateway/adapters/{mobilerun,apify,hubspot,monid}.py` and
`backend/tests/adapters/test_{mobilerun,apify,hubspot,monid}.py`. You own exactly those 8
files. The backend lane owns registry.py, test_ports.py FACTORIES and .env.example, and wires
your adapters in.
Order: Mobilerun (DevicePort: open the memo on a cloud phone and screenshot it; Best Mobile
Automation prize), Apify (ScrapePort: the Delaware fee page), HubSpot (CRMPort: contact + deal
stage, never cap-table numbers), Monid (ToolBuyPort). Read each sponsor's official API docs
(start from research/sponsors.md), implement the port interface in backend/app/gateway/ports/,
and test with httpx.MockTransport (auth header, success, 429 mapped, error mapped,
idempotency where the port has a key). Never call a live API, and mark unverified shapes
`# UNVERIFIED`. Make a venv in your worktree's backend/ for pytest. One READY per adapter or
per pair.

## Saturday note (contract v1.7.3)
Marketing gains `buy_tool {tool, input, key}` (Monid). Add it to mkt's allowed set and prompt:
use it only for enriching public startup lists, never with client data or emails, and only for
tools the owner has put in MONID_TOOLS.
