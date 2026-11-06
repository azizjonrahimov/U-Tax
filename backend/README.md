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
uvicorn app.main:app --reload --port 8000
```

Then `GET http://localhost:8000/api/health` returns `{"status": "ok", "version": "0.1.0"}`.

## Test

```bash
pytest
```

## Layout

- `app/main.py`: the app, CORS for `http://localhost:3000`, routes.
- `app/errors.py`: the contract error shape. Raise `APIError(status, "snake_code", "message")`
  from any route; 404s, validation errors (422 `validation_error`) and unhandled
  exceptions (500 `internal_error`) are mapped to the same shape.

## Environment variables

None yet. Secrets go in `backend/.env` (gitignored) and get listed here.
