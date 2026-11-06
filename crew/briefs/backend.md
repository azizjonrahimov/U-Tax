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
