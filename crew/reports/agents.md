# Report: agents lane (P3: Naïve agents, prompts, voice)

## Outcome
Tasks 1 and 2 are done and checked offline. Task 3 waits for Saturday, when the owner gets Naïve
access (D26).

- **Task 1 (merged as 4af1e70):** `naive/` declares the firm.
  - Chief of Staff as coordinator, with a board and a roster of six.
  - Each agent has its own identity, vault, gateway token and seat URL `<base>/<actor>` (contract
    v1.4).
  - Tools are deny-by-default: each agent gets exactly its §6 column plus the board.
  - Sunday 8 AM brief schedule, and a one-time first-run briefing.
  - The env check fails closed on a bad URL or bad tokens.
- **Task 2 (READY on `lane/agents-t2`):** the prompts for all 7 agents, `voice-intake.md` for `/talk`, and
  the templates:
  - memo, re-ask, not-eligible ×3, HR questionnaire, hired and declined
  - CEO update, digest and login brief
  - job post and client-sourcing posts
  - 3-minute demo script cards and a backup slide
- **Voice:** Naïve numbers have no voice ("`voice` is always `false`"). No number was bought and
  no money was spent. The owner chose the `/talk` page (D21, then D23).
- **Checks:**
  - `npx tsc --noEmit` passes, and `npm run check` passes.
  - The engine validates the config.
  - The check enforces the exact tool policy, the contract §6 names and Agent column, per-seat
    URLs and tokens, every template's rules, and a port of the gateway's post guard (with
    self-tests).
  - 14 bad-env cases are refused.
  - Mutation tests show the guards fail when weakened.
- **gstack `/review`:**
  - Task 1: 3 passes, stopped at the 3-cycle cap without converging. About 35 findings fixed.
  - Task 2: 2 passes. Every in-lane finding from pass 2 is fixed. Not run to convergence: what
    remains is cross-lane or needs the live org (below).
  - `/verify` is ENV-blocked: gstack-checks needs `origin/main`, and this repo has no remote.

Unverified: the model ids (`z-ai/glm-5.3-flash`, `deepseek/deepseek-v4-flash-0731`). That Naïve
accepts 7 vaults and the non-secret `X-UTax-Actor` header. That board-woken and `wait:true`
sessions run with the member's own identity. That the data-exfil guard doesn't make MCP calls ask
after `get_case`. That `intake` fires once at creation. That `naive up --dry-run` passes against
the real org. Not one of these could be checked without a key.

## What merged
- Task 1: `lane/agents` 090790157, merged as 4af1e70.
- Task 2: pending (`lane/agents-t2`, see the READY message for the sha).

## Open items (from /review, owned elsewhere or live-only)
1. Backend: serve `POST /mcp/<actor>` (no branch had it at review time). Without it, `naive up`
   leaves the agents with no tools, and that shows only as `session.degraded`.
2. Backend: load every `naive/templates/*.md` strictly, mint `voice-intake.md` server-side, and
   post customer turns only from `/talk`. These are agreed in v1.5; check them once built.
3. Frontend: put a consent gate on `/talk` before the mic opens. The spoken consent alone streams
   audio first.
4. Gateway: enforce the 255-character limit on X posts. Today only the prompt and the templates
   enforce it.
5. Gateway: emails spoken aloud ("jane at example dot com") aren't masked. The voice prompt now
   stops callers from saying them.
6. check.mjs: the post-guard port is ASCII. Python's Unicode `\d` and `\w` refuse a little more.
7. After the event: pause the Sunday schedule with `naive deploy pause <dep>`.

## What the owner must do (Saturday, after the Naïve plan is active)
1. Sign in and confirm the org: `naive login --email <you> --password '<pw>'`, then `naive whoami`
   and `naive plan show`.
2. Check the models: `naive models get z-ai/glm-5.3-flash` and
   `naive models get deepseek/deepseek-v4-flash-0731`. If either is missing, tell the agents lane
   and it swaps the id.
3. In a shell where the backend's values are exported (the same token values the backend uses):
   ```bash
   export UTAX_MCP_URL=https://<tunnel-or-render-host>/mcp
   export UTAX_TOOL_TOKEN_COS=... UTAX_TOOL_TOKEN_INTAKE=... UTAX_TOOL_TOKEN_TAX=... \
          UTAX_TOOL_TOKEN_QA=... UTAX_TOOL_TOKEN_ACCT=... UTAX_TOOL_TOKEN_MKT=... UTAX_TOOL_TOKEN_HR=...
   cd naive && npm install && npm run check && naive up --dry-run
   ```
   Send the dry-run output to the controller. Then run `naive up`.
4. X and LinkedIn (posts only; the gateway posts as Marketing's persona):
   `naive identity social activate --identity utax-mkt`, then
   `naive identity social portal --identity utax-mkt --platform x --platform linkedin`. Open the
   link it prints and authorize both accounts. Confirm with
   `naive identity social accounts --identity utax-mkt`.
5. First live check (with the agents lane): run one new case end to end. Confirm that each agent's
   session shows its own identity, that the gateway logs the right actor, and that no MCP call
   stops for approval.

## Lesson
Read the platform's own engine before writing its config. The blueprint package's `index.js`
showed that `tasks[]` silently creates an extra org "CEO" agent outside our tool policy, which no
doc page mentions. The engine's own `loadConfig` turned "UNVERIFIED until we have a key" into an
offline check we could run all night.
