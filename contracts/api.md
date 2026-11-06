# API contract (v0)

Owned by the controller. Lanes propose changes by message to the controller; never
edit this file on a lane branch. Every change bumps the version and is announced to
both lanes, who then `git merge main`.

## Basics

- Backend base URL: `http://localhost:8000`, every route under `/api`.
- Frontend reads the base URL from `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).
- JSON in and out, `Content-Type: application/json`.
- CORS: the backend allows origin `http://localhost:3000`.
- Errors: non-2xx status with body `{"error": {"code": "<snake_case>", "message": "<human text>"}}`.

## Endpoints

### `GET /api/health`

Liveness check. Used by the frontend home page to show backend status.

Response `200`:

```json
{ "status": "ok", "version": "0.1.0" }
```

## Changelog

- v0 (2026-11-06): basics + `GET /api/health`.
