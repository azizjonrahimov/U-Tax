# Backend (FastAPI)

Python 3.12 FastAPI app. Builds to `contracts/api.md` (v0).

## Setup

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn app.main:app --reload --port 8000 --env-file .env
```

(`--env-file .env` loads `backend/.env`; drop it if you have no `.env` yet.)

Then `GET http://localhost:8000/api/health` returns `{"status": "ok", "version": "0.1.0"}`.

## Test

```bash
pytest
```

## Layout

- `app/main.py`: the app, CORS for `http://localhost:3000`, routes.
- `app/naive/client.py`: async client for the Naïve REST API (`NaiveClient`). See below.
- `app/errors.py`: the contract error shape. Raise `APIError(status, "snake_code", "message")`
  from any route; 404s, validation errors (422 `validation_error`) and unhandled
  exceptions (500 `internal_error`) are mapped to the same shape.

## Environment variables

Copy `.env.example` to `.env` (gitignored) and fill it in. Never commit the key.

| Variable | Required | Meaning |
|---|---|---|
| `NAIVE_API_KEY` | for Naïve calls | Org-scoped key (`sk_live_...`) from the Studio or `naive keys create`. Sent as `Authorization: Bearer`. |
| `NAIVE_API_BASE` | no | Host only, default `https://api.vetta.sh`. The client appends `/v1`. |

## Naïve client

The Naïve SDK is TypeScript-only, so `app/naive/client.py` calls the REST API with httpx.

```python
from app.naive import NaiveClient, NaiveError

async with NaiveClient() as naive:            # reads NAIVE_API_KEY / NAIVE_API_BASE
    agent = await naive.create_agent(
        name="triage", model="z-ai/glm-5.3-flash",
        budget={"cap_micro_usd": 50_000_000, "max_task_micro_usd": 5_000_000, "period": "month"},
    )
    session = await naive.create_session(agent_id=agent["id"], message="Hello")
    async for event in naive.stream(session["id"]):
        print(event["seq"], event["type"])
```

- Money is integer micro-USD (1 USD = 1_000_000); floats raise `TypeError`.
- Errors raise `NaiveError`; branch on `.code` (`subscription_required`, `insufficient_credits`,
  `session_running`, `version_conflict`, ...), never on `.message`.
- POSTs send an `Idempotency-Key` (reused on retry); 429s wait for `Retry-After`.
- Lists return `Page(data, has_more, next_cursor)`; `paginate()` walks all pages.
- `stream()` parses SSE, reconnects with `after_seq` after a dropped connection, and skips repeats.
- Tests use `httpx.MockTransport`; nothing calls the live API.
