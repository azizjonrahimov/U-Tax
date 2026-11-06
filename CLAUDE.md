# Naive YC Hackathon

Next.js frontend (`frontend/`) + FastAPI backend (`backend/`), built by a crew:
one controller (lead) session and one Claude session per lane, each in its own
terminal and git worktree.

- **The product is U-Tax.** Read `U-TAX-CLAUDE.md` (rules), then `docs/designs/u-tax.md`
  (source of truth). `HANDOFF.md` has the full planning history.
- **If you are a lane** (your session name is `frontend`, `backend` or `agents`): read
  `crew/CREW.md`, then your brief in `crew/briefs/<lane>.md`, then start.
- **The API contract** between the halves is `contracts/api.md`. Both lanes build
  to it; only the controller edits it.
- Ports: frontend `http://localhost:3000`, backend `http://localhost:8000`.

## Skill routing

Use the gstack skill for these requests (`crew/CREW.md` rule 15 has the details):
- A product or scope idea to sharpen → `/office-hours`
- A diff that's about to become a READY → `/review`
- The running app (theater, `/talk`, `/ceo`, the HTTP surface) to test and fix → `/qa`
- A failing test, an error, or wrong output → `/investigate`
- How the theater looks → `/design-review`
- Pushing, PRs, deploys → `/ship`, and only the controller runs it; lanes never push or merge
