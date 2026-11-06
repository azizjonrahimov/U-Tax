# Digest (Naive YC Hackathon · U-Tax) — 2026-11-06 10:45 local

## Needs you

Captain (naive-yc-hackathon-99)
- A: Nothing on Naïve works until the org has a plan, so the agents lane can't run the voice spike. Steps: 1) open https://app.usenaive.ai/register, sign up, click the confirmation email; 2) subscribe to Standard ($20/mo) or the 7-day trial (card needed); 3) in the Studio, create an API key and copy it; 4) in your own PowerShell window (not this chat) run `Get-Clipboard | naive login`, then `naive whoami --human` and `naive plan show --human`. Reply "naive ready". A1 to take recommendation.
- B: The design review left two theater items undecided: the red BLOCKED case card (QA cursor circling the bad number) and "waiting" queue chips. We could: 1) build both behind a flag, on by default; 2) build both, off by default; 3) skip. Recommendation: 1, because a visible failure the firm catches makes the QA story believable. B1 to take recommendation.

## New

- agents · 0m · lane launched (naive/ config, prompts, templates, voice spike)
- frontend · 0m · sent Task 3: Agent Theater after scaffold READY
- backend · 0m · sent tasks 4-7: engine, perform/store/ports, HTTP+SSE, MCP
- controller · 5m · contract v1 + events.sample.jsonl; design doc D16-D20 (FastAPI + Next.js, MCP at /mcp, three lanes)
- backend · 25m · MERGED Naïve REST client as d08ce40
- controller · 30m · Naïve CLI 1.0.0 installed; owner's U-Tax package committed

## In progress

| Thread | Owns | State | Last report |
|---|---|---|---|
| backend | `backend/`, `fixtures/`, `db/` | building: task 4 engine | 25m |
| frontend | `frontend/` | building: scaffold, then theater | 2h (check its tab) |
| agents | `naive/` | building: task 1 naive.config.ts (gstack ok) | 0m |
