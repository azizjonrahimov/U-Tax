# Crew rules (Naive YC Hackathon) — hackathon-lite captain

The lanes' contract with the controller. It is the gstack `/captain` model
(`~/.claude/skills/gstack/captain/SKILL.md`, `docs/captain.md`) cut down for a
hackathon: no CI, no PRs, no merge trains, no version stamps. Lanes read this at
session start. Add rules at the end; never renumber.

## Parameters

| Parameter | Value |
|---|---|
| `REPO` | local git repo at `D:\Claude Projects\Naive YC Hackathon` (no remote yet) |
| `BASE` | `main` (the controller's checkout; only the controller commits here) |
| `CONTROLLER` | Claude session `naive-yc-hackathon-99` (message it with SendMessage) |
| `LANES` | `frontend` (branch `lane/frontend`, worktree `.worktrees/frontend`), `backend` (branch `lane/backend`, worktree `.worktrees/backend`), `agents` (branch `lane/agents`, worktree `.worktrees/agents`) |
| `MERGE_METHOD` | `git merge --no-ff <pinned sha>` into `main`, by the controller only |
| `CHECKS` | frontend: `npm run lint` and `npm run build` in `frontend/`; backend: `pytest` in `backend/`; agents: `npx tsc --noEmit` in `naive/` plus `naive up --dry-run` once a key exists |
| `PRODUCT` | U-Tax. Source of truth: `docs/designs/u-tax.md`; rules: `U-TAX-CLAUDE.md`; shapes: `contracts/api.md` v1 |
| `SPEND` | no paid services or API keys without the owner's word, asked through the controller. Standing: the agents lane may spend up to $5 of Naïve credit on the voice and config spikes once the owner's key exists (design doc E2, owner said "Start now (recommended)" 2026-11-06) |

## Standing approvals (the owner's words)

| Rule | Date | Words | Covers |
|---|---|---|---|
| mandate | 2026-11-06 | "I want to create a frontend and backend terminals as well while you will be a lead to manage all of them in this folder" | a controller plus `frontend` and `backend` lanes in this folder |
| stack | 2026-11-06 | chose "Next.js + FastAPI (recommended)" | Next.js/React/TypeScript/Tailwind in `frontend/`, Python FastAPI in `backend/` |
| process | 2026-11-06 | chose "Hackathon-lite (recommended)" | the rules below in place of full trains |
| repo | 2026-11-06 | chose "New standalone repo (recommended)" | this folder is its own git repo |

## Rules

1. **Session start.** Run the gstack install check from `D:\Claude Projects\CLAUDE.md`.
   Your first message to the controller says `gstack: ok` (or the failure, and you stop).
2. **One path.** Every question, plan, contract change and READY goes to `CONTROLLER`
   by SendMessage, never lane to lane. Make the first line of every message a
   self-contained summary.
3. **File ownership.** `frontend` owns `frontend/` and `crew/reports/frontend.md`.
   `backend` owns `backend/`, `fixtures/`, `db/` and `crew/reports/backend.md`.
   `agents` owns `naive/` and `crew/reports/agents.md`. Everything else (root files,
   `contracts/`, `docs/`, `research/`, `reference/`, `crew/` apart from your report) is
   the controller's; ask.
4. **The contract.** Build to `contracts/api.md` exactly. Need a change? Send the
   controller the exact diff you want and why; keep working on something else until
   it lands on `main`, then `git merge main`.
5. **Plan before build.** Scaffolding and fixes are exempt. Before building a product
   feature, send the controller a plan of 10 lines or fewer: what the user sees
   before and after, files you'll touch, any contract change, and the open questions.
   Build after the controller replies `APPROVED` (it relays to the owner when the
   decision is the owner's).
6. **Commit small** on your lane branch with clear messages. Never commit to `main`,
   never merge your branch anywhere, never push.
7. **READY is the only merge trigger.** When a unit of work is done, run `CHECKS` for
   your lane, commit, and send:
   ```text
   READY lane/<lane> <full 40-char sha>
   what: <one line per user-visible change>
   checks: <command> -> <result>, for each CHECKS command
   contract: v<n> (unchanged | needs <change>)
   run: <exact commands to start it>
   Unverified: <claims you did not check yourself, or none>
   head frozen from now on
   ```
   After READY, do not push new commits to that branch until the controller says
   `MERGED` or sends it back. Work in progress on the next item goes in a new local
   branch off your READY head, or waits.
8. **Syncing.** Merge `main` into your branch only when the controller tells you to,
   or at the start of new work after a `MERGED`. Never rebase.
9. **Keep going.** Come back only for a contract change, a decision that is the
   owner's, missing access, or a conflict with the other lane's files.
10. **Report.** When your brief is done, write `crew/reports/<lane>.md`: Outcome (with
    its `Unverified:` line), what merged, what the owner must do, and one Lesson.
11. **Secrets.** No keys in git. Read them from `.env` (gitignored) and document the
    variable name in your lane's README.

## Crew-specific rules

Number from 12, each with its date and the owner's words where there are any.

12. **U-Tax rules are product law** (2026-11-06, owner's `U-TAX-CLAUDE.md`): math only in
    tested code; one gateway `perform(action)`; events are the only truth; every theater
    animation comes from a real event; sponsors behind ports with stand-ins; legal limits
    (no filings, no SSN/EIN, "Educational estimate — not tax advice", human makes the hire,
    no automated LinkedIn DMs); masked by default on screen. Changing any of them needs the
    owner's word through the controller.
13. **Plan is approved.** `docs/designs/u-tax.md` went through office hours, CEO, outside
    voice, eng and design review with the owner, and the owner said "Start now
    (recommended)" on 2026-11-06. Briefs say `Plan: approved: docs/designs/u-tax.md`. New
    scope outside the design doc still needs a rule-5 plan.
14. **Owner instructions go in chat, with exact commands** (2026-11-06, `U-TAX-CLAUDE.md`:
    "The user doesn't read generated README files"). Anything the owner must do goes to the
    controller as numbered steps with exact commands.
