# Frontend

Next.js (App Router, TypeScript, Tailwind CSS, ESLint). Dev URL: http://localhost:3000.

## Setup

```bash
cd frontend
npm install
cp .env.example .env.local   # optional; the default already points at localhost:8000
```

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | Backend base URL; routes live under `/api` |

## Commands

```bash
npm run dev     # dev server on http://localhost:3000
npm test        # Vitest (src/**/*.test.ts): event → script, screen fold, rail, formatting
npm run lint    # ESLint
npm run build   # production build
npm run start   # serve the production build
```

## Theater

`/` is the Agent Theater. It plays `public/fixtures/events.sample.jsonl` (a byte copy of
`contracts/events.sample.jsonl`; a test fails if they drift). Keys and controls: `S` or the
Stage button toggles stage mode; 1×/4×/16× speed; Sound plays briefs (gateway audio, falling
back to browser speech); Replay restarts. Flags (default on): `NEXT_PUBLIC_SHOW_BLOCKED`,
`NEXT_PUBLIC_SHOW_QUEUE` (set to `0` to hide). `NEXT_PUBLIC_THEATER_TOKEN` is sent as `?token=`.

## Layout

- `src/lib/api.ts` — typed backend client built to `contracts/api.md` (parses the
  contract error shape into `ApiError`).
- `src/components/BackendStatus.tsx` — home page badge: loading / ok / down,
  polls `GET /api/health` every 10s.
