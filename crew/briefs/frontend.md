# Brief: frontend

```text
Lane: frontend · Controller: naive-yc-hackathon-99 · Rules: crew/CREW.md · Base: main
Branch: lane/frontend · Worktree: D:\Claude Projects\Naive YC Hackathon\.worktrees\frontend
Owns: frontend/, crew/reports/frontend.md
Do not touch: backend/ (lane backend), contracts/ and root files (controller)
Plan: exempt: scaffolding (the product brief comes later and needs a plan, rule 5)
Spend cap: none; no paid APIs without the owner's word
```

## Task 1: scaffold the app

1. Next.js (App Router, TypeScript, Tailwind, ESLint) in `frontend/`, e.g.
   `npx create-next-app@latest frontend --ts --tailwind --eslint --app --src-dir --use-npm --no-turbopack --import-alias "@/*"`
   (answer non-interactively; no nested `.git` inside `frontend/`).
2. `frontend/src/lib/api.ts`: a tiny typed client that reads `NEXT_PUBLIC_API_URL`
   (default `http://localhost:8000`), parses the contract's error shape, and exports
   `getHealth()` typed to `contracts/api.md` v0.
3. Home page: replace the starter content with a clean placeholder that has the
   project name and a backend status badge (ok / down / loading) using `getHealth()`.
   "Down" must render cleanly when the backend is not running.
4. `frontend/.env.example` with `NEXT_PUBLIC_API_URL=http://localhost:8000`, and
   `frontend/README.md` with install, dev and build commands.
5. Run `npm run lint` and `npm run build`; commit and send READY (rule 7).

## Task 2: wait for the product brief

After READY, tell the controller you are free. The product idea is not decided yet;
the controller will send a product brief. Until then, do not invent features.
