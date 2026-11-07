# Digest (Naive YC Hackathon · U-Tax) — 2026-11-07 08:05 local · SATURDAY LIVE SETUP

## Needs you

Captain (naive-yc-hackathon-99)
- A (now): the tunnel is DOWN (cloudflared not running). Restart it and paste the new URL: `& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --no-autoupdate --url http://localhost:8000`. I re-run naive up with it.
- B: decide D29 (Naïve data-leak confirms): A = the gateway resolves them with an allowlist (rec), B = click each in Naïve, C = ask the Naïve team
- C: sponsor keys via `crew/scripts/set-key.ps1 <NAME>`: AGENTMAIL_API_KEY first, then OPENAI_API_KEY
- D (later): change the Naïve password; delete the utax-up key after the event

## New

- backend · 0m · MERGED 17b (1106 tests); Monid hold lifted; lane free for the AgentMail swap
- captain · 10m · LIVE: naive up done (7 agents; engine bug worked around in 2 phases); the CoS ran through the tunnel; D29 found and proposed

- captain · 0m · Naïve: org U-Tax live, Pro plan active, both models exist; gateway key saved + verified; gateway running locally; tunnel = owner step (classifier)
- backend · 10m · MERGED task 18 CEO voice gateway (1077 tests); 18b started
- captain · 15m · main red then fixed: the frontend fixture copy (my miss)

- agents · 0m · MERGED task 5: the CEO voice agent prompt + check rule 12 + the 20-second demo voice beat
- backend · 10m · MERGED task 17 (964 tests, CI green); 17b open (8 items), Monid held local; task 18 (CEO voice) started
- backend · 0m · task 17 TIMEBOXED (auto): READY at its green head, open items → 17b; Monid stays local until 17b; task 18 (CEO voice) next
- captain · 5m · contract v1.8.1: /ceo echo guard for voice confirms; the action result goes back to the model (from agents review)
- captain · 0m · PLAN D28: the CEO runs the firm by voice (contract v1.8, faa2fad). Frontend (/ceo voice) and agents (task 5) started; backend task 18 queued after 17
- backend · 0m · task 17 review-3 fixes committed (a598c8c); fresh /review run next, READY ETA ~11:15
- captain · 5m · render.yaml: 1 GB disk for data_dir (8068646), CI green
- backend · 0m · MERGED Linux depth fix as 2bf3a38; CI green on GitHub (all 3 jobs). Task 17 buy_tool hardening started (auto-decided)
- captain · 25m · first CI run caught a Linux-only crash in Monid deep-input handling (backend 878/879)
- captain · 0m · local boot with backend/.env: health 200; /mcp/cos with its own token lists 3 tools; another seat's token and no token both get 401; no token in the log
- captain · 5m · backend/.env made (7 seat tokens, ADMIN, THEATER, PUBLIC_MODE=event; gitignored, never printed); backend/.venv installed in main
- agents · 0m · MERGED buy_tool for mkt; CI + render.yaml pushed with it
- agents · 10m · WOKEN: add buy_tool to mkt (contract v1.7.3 Saturday note), needs no Naïve access
- captain · 15m · GitHub repo created: private azizjonrahimov/U-Tax; history secret scan clean; main pushed

## In progress

| Thread | Owns | State | Last report |
|---|---|---|---|
| frontend | `frontend/` | building /ceo voice mode (D28) | 0m |
| backend | `backend/`, `fixtures/`, `db/` | building task 18: CEO voice gateway (D28) | 0m |
| agents | `naive/` | done (task 5 merged); resting until `naive up` (task 3) | 0m |
