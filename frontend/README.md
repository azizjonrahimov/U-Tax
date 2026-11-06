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
npm run lint    # ESLint
npm run build   # production build
npm run start   # serve the production build
```

## Layout

- `src/lib/api.ts` — typed backend client built to `contracts/api.md` (parses the
  contract error shape into `ApiError`).
- `src/components/BackendStatus.tsx` — home page badge: loading / ok / down,
  polls `GET /api/health` every 10s.
