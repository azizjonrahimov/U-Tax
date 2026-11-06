# Naive YC Hackathon

Next.js frontend (`frontend/`) + FastAPI backend (`backend/`), built by a crew:
one controller (lead) session and one Claude session per lane, each in its own
terminal and git worktree.

- **If you are a lane** (your session name is `frontend` or `backend`): read
  `crew/CREW.md`, then your brief in `crew/briefs/<lane>.md`, then start.
- **The API contract** between the halves is `contracts/api.md`. Both lanes build
  to it; only the controller edits it.
- Ports: frontend `http://localhost:3000`, backend `http://localhost:8000`.
