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
