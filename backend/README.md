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

## Public URL for Saturday (Naïve agents → `/mcp/<seat>`)

Naïve agents reach the gateway over public HTTPS at `https://<public-host>/mcp/<seat>` with
`Authorization: Bearer <that seat's token>` (contract §6). Seats: `cos intake tax qa acct mkt hr`.
A seat whose token is unset or shorter than 32 characters is closed (401). There is no
local-only bypass, because a tunnel's requests also arrive from localhost.

### Step 1 (both variants): make the tokens

Run this once. Paste the output into `backend/.env` (cloudflared) or the Render environment.
Never commit it.

```bash
python -c "import secrets; [print(f'UTAX_TOOL_TOKEN_{s}={secrets.token_urlsafe(32)}') for s in 'COS INTAKE TAX QA ACCT MKT HR'.split()]; print(f'ADMIN_TOKEN={secrets.token_urlsafe(32)}'); print(f'THEATER_TOKEN={secrets.token_urlsafe(32)}')"
```

Each Naïve agent gets only its own `UTAX_TOOL_TOKEN_<SEAT>`, in its own vault entry, with
the URL `https://<public-host>/mcp/<seat>`.

### Env both variants need

| Variable | Value |
|---|---|
| `UTAX_TOOL_TOKEN_COS` … `_HR` | the seven tokens from step 1 |
| `ADMIN_TOKEN`, `THEATER_TOKEN` | from step 1 (never empty on a public URL) |
| `THEATER_ORIGIN` | the theater's public origin(s), comma-separated, e.g. `https://u-tax.vercel.app` |
| `PUBLIC_TALK_URL` | the public `/talk` page, e.g. `https://u-tax.vercel.app/talk` (posts are refused without it) |
| `CEO_CONSOLE_URL` | the public `/ceo` page |
| `PUBLIC_MODE` | `event` (60 /talk requests a minute per IP, for the venue's shared IP) |
| `NAIVE_API_KEY` | the org key, for the gateway's wake-ups (§6b) |
| `CEO_EMAIL`, `MAIL_INBOX_CLIENTS`, `MAIL_INBOX_HIRING` | the CEO's address and the two inboxes |

Leave `UTAX_DRIVER` empty when the real Naïve crew runs. Set it to `local` only for the
stand-in crew. Every `ADAPTER_*` stays `local` unless the owner has approved that
sponsor's key.

### Variant A: cloudflared quick tunnel from the event laptop

1. Fill in `backend/.env` with the env above. `STORE=jsonl` is fine, and the events go
   to `backend/data/events.jsonl`.
2. Terminal 1:
   ```bash
   cd backend
   .venv/Scripts/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --env-file .env
   ```
   (macOS/Linux: `.venv/bin/python`.) Bind to `127.0.0.1`, not `0.0.0.0`. The
   tunnel is the only way in, and it sets `CF-Connecting-IP`, which the rate limiter
   trusts only from loopback.
3. Terminal 2:
   ```bash
   npx cloudflared tunnel --no-autoupdate --url http://localhost:8000
   ```
   It prints `https://<random-words>.trycloudflare.com`. That is `<public-host>`. The URL
   changes on every restart, so re-send it to the agents if the tunnel restarts.
4. Check it from any machine:
   ```bash
   curl https://<public-host>/api/health
   curl -X POST https://<public-host>/mcp/cos -H "Authorization: Bearer $UTAX_TOOL_TOKEN_COS" \
        -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
   ```
   The first returns `{"status": "ok", ...}`. The second lists three tools:
   `list_open_cases`, `get_case`, `firm_brief`. The same call with another seat's token
   must return 401.

Not yet verified: the tunnel itself. Tonight's live test ran on localhost only.

### Variant B: Render (stable URL, Postgres)

1. Render dashboard → New → **PostgreSQL** (any plan). Copy its **Internal Database URL**.
2. Create the table once from your machine, with the **External Database URL**:
   ```bash
   psql "<external database url>" -f db/001_events.sql
   ```
3. Render → New → **Web Service** → this repo. Use these settings:
   - Root Directory: `backend`
   - Runtime: Python
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - Health Check Path: `/api/health`
4. Environment: everything in the table above, plus:

   | Variable | Value |
   |---|---|
   | `PYTHON_VERSION` | `3.12.7` |
   | `STORE` | `postgres` |
   | `DATABASE_URL` | the Internal Database URL from step 1 |

   With `STORE=postgres` the gateway refuses to boot if `ADMIN_TOKEN` or `THEATER_TOKEN`
   is empty. Don't use `STORE=jsonl` on Render: its disk is wiped on every deploy.
5. `<public-host>` is `<service>.onrender.com`. Run the two checks from variant A step 4.
6. Deploy by stopping the old instance before starting the new one. Two instances on one
   database can each hold the advisory lock in turn, so don't run both at once (known,
   deferred).

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
