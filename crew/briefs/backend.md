# Brief: backend

```text
Lane: backend · Controller: naive-yc-hackathon-99 · Rules: crew/CREW.md · Base: main
Branch: lane/backend · Worktree: D:\Claude Projects\Naive YC Hackathon\.worktrees\backend
Owns: backend/, crew/reports/backend.md
Do not touch: frontend/ (lane frontend), contracts/ and root files (controller)
Plan: exempt: scaffolding (the product brief comes later and needs a plan, rule 5)
Spend cap: none; no paid APIs without the owner's word
```

## Task 1: scaffold the API

1. Python 3.12 FastAPI app in `backend/`:
   - `backend/requirements.txt` (fastapi, uvicorn[standard], pydantic, pytest, httpx)
   - `backend/app/main.py` with the FastAPI app, CORS for `http://localhost:3000`,
     and `GET /api/health` exactly as `contracts/api.md` v0 says.
   - The error shape from the contract as a reusable exception handler.
   - `backend/tests/test_health.py` covering the health response and the CORS header.
   - `backend/README.md`: create the venv (`python -m venv .venv`), install, run
     (`uvicorn app.main:app --reload --port 8000`), test (`pytest`).
2. Run `pytest` and start the server once to hit `/api/health` for real.
3. Commit and send READY (rule 7).

## Task 2: wait for the product brief

After READY, tell the controller you are free. The product idea is not decided yet;
the controller will send a product brief. Until then, do not invent features.

## Task 3: Naïve REST client (plumbing, plan exempt)

Added 2026-11-06 by the controller. Read `research/naive-sdk.md` first (§2 auth, §3, §6.2, §6.4, §7).
The Naïve SDK is TypeScript-only, so the backend talks to the REST API directly.

1. `backend/app/naive/client.py`: an async `httpx` client for `https://api.vetta.sh/v1`.
   - Auth from env `NAIVE_API_KEY` (`Authorization: Bearer ...`), header `Vetta-Version: 2026-09-30`,
     base URL overridable by env `NAIVE_API_BASE` (our name; the docs define no env var for it).
   - Methods for documented routes only: agents create/get/list/update, sessions create/send/events,
     SSE stream (`after_seq` resume, order by `seq`), tool confirmations, answers, `GET /v1/digest`,
     `/v1/credits/spend`, deployments create/list, board cards list if documented.
   - `Idempotency-Key` on POSTs; Naïve errors raised as a typed exception carrying `error.code`
     (branch on code, never message); 429 honours `Retry-After`.
   - Money stays integer micro-USD end to end.
2. `backend/tests/test_naive_client.py` with `httpx.MockTransport` (no live calls, no key needed),
   covering auth headers, an error code, SSE parsing and pagination shape.
3. `backend/.env.example` gets `NAIVE_API_KEY=` and `NAIVE_API_BASE=https://api.vetta.sh`.
4. No new HTTP routes: those wait for contract v1. `pytest`, commit, READY.

Anything in the docs that is ambiguous (the doc inconsistencies in §7): follow the SDK reference,
leave a `# UNVERIFIED:` comment, and list it in your READY's `Unverified:` line.
