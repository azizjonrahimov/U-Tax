# Report: agents lane (P3: Naïve agents, prompts, voice)

> Windows: run every `naive` command below as `naive.cmd` (PowerShell blocks `naive.ps1` by default).

## Outcome
Tasks 1, 2 and 4 are done, merged and checked offline. Task 3 waits for Saturday, when the owner
gets Naïve access (D26).

- **Task 1 (merged as 4af1e70):** `naive/` declares the firm.
  - Chief of Staff as coordinator, with a board and a roster of six.
  - Each agent has its own identity, vault, gateway token and seat URL `<base>/<actor>` (contract
    v1.4).
  - Tools are deny-by-default: each agent gets exactly its §6 column plus the board.
  - Sunday 8 AM brief schedule, and a one-time first-run briefing.
  - The env check fails closed on a bad URL or bad tokens.
- **Task 2 (merged):** the prompts for all 7 agents, `voice-intake.md` for `/talk`, and
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

- **Task 4: four sponsor adapters** in `backend/app/gateway/adapters/` (owned for this task only),
  each built from the sponsor's official docs against its port, and tested with
  `httpx.MockTransport` only. No live call was made, apart from one unauthenticated Apify POST
  that was disclosed and judged harmless.
  - **Mobilerun (DevicePort):** QA opens each sent memo on a cloud phone and screenshots it.
    - The memo is found only by its last line, `Reference: UTX-…` (contract v1.7).
    - It fails closed: no screenshot unless the phone saw exactly one match with the disclaimer
      visible.
  - **Apify (ScrapePort):**
    - Fetches only the allowlisted Delaware law pages, and refuses a redirect to another page.
    - Searches only `site:delaware.gov` hosts.
    - Spend is capped per run (`maxTotalChargeUsd` 0.10), and results are cached for 6 h.
  - **HubSpot (CRMPort):**
    - Only the masked label (`Team #N` / `Candidate X`), our key and the deal stage are sent.
    - It upserts by a unique `utax_key`, so a retry never creates a duplicate.
    - Stages only move forward.
  - **Monid (ToolBuyPort):**
    - Only tools on the owner's list can be bought, each with a price ceiling.
    - A daily cap reserves the ceiling of every in-flight run and every run that may have been
      charged.
    - The same purchase key is never paid for twice.
    - Input that carries an email address is refused.
  - **Tests:** 160 adapter tests. The full backend suite is green on merged main (771 passed).
  - **/review:** Mobilerun and Apify had 2 passes. HubSpot and Monid had 5 specialists and 3
    adversarial cycles, converged. Cycle 2 caught a cap bypass: unsure runs released their
    reservation.
- **memo_ref footer:** `Reference: {{memo_ref}}` is the memo's last line, and `check.mjs` enforces
  it. Merged together with backend task 9.

Unverified: the model ids (`z-ai/glm-5.3-flash`, `deepseek/deepseek-v4-flash-0731`). That Naïve
accepts 7 vaults and the non-secret `X-UTax-Actor` header. That board-woken and `wait:true`
sessions run with the member's own identity. That the data-exfil guard doesn't make MCP calls ask
after `get_case`. That `intake` fires once at creation. That `naive up --dry-run` passes against
the real org. Not one of these could be checked without a key.

## What merged
- Task 1: `lane/agents` 090790157, merged as 4af1e70.
- Task 2: merged as 48906cc. The v1.6 follow-up merged as b9f90da.
- Task 4: Mobilerun and Apify merged as a251fe6. HubSpot and Monid (7260a1b) merged as 86af197.
- memo_ref footer: aea4d4e, merged as c3f8ed6 after backend task 9.

## Task 4: still unverified (no sponsor keys tonight)
All request and response shapes come from the official API references (read 2026-11-07). The first
live run on Saturday must check these:
- **Mobilerun:**
  - the task, screenshot and device routes;
  - whether screenshots come in time order, and whether their URLs expire;
  - that the phone agent's prompt and the default model (`mobilerun/mobile-agent-fast`) can find
    and read the memo on a real device;
  - every fail-closed guard trusts the phone agent's own report.
- **Apify:**
  - the authenticated run-sync route and response (only a 402 was seen);
  - whether the fee page renders its table without JavaScript (if not, switch to
    `apify/web-scraper`);
  - the per-run cost (not reported back to the gateway yet).
- **HubSpot:**
  - the default pipeline's stage ids in a fresh portal;
  - that a missing `utax_key` property answers 400 "does not exist";
  - that `utax_key` may be both `id` and a property.
- **Monid:**
  - that a COMPLETED run always carries `billing`;
  - that FAILED/STOPPED runs, and 409/422 answers, are unbilled (FAILED/STOPPED are treated as
    "may be charged");
  - the provider and endpoint names, and the prices, of the tools we pick.

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

## What the owner must do for the sponsor adapters (Saturday)
Keys go only in `backend/.env`, which is gitignored and never committed. The backend lane wires the
adapters (task 10). Each needs `ADAPTER_<PORT>=<name>` once its keys are set.
1. **Mobilerun** (`ADAPTER_DEVICE=mobilerun`):
   - Create an API key and put it in `MOBILERUN_API_KEY`.
   - Start one Android cloud phone, and sign it in to the inbox that receives memos (Gmail by
     default; `MOBILERUN_MAIL_APP` changes the app).
   - Optional: `MOBILERUN_DEVICE_ID` pins the phone.
   - Set `MOBILERUN_MEMO_SENDER` to the address memos are sent from. It's strongly advised: without
     it, anyone who learns a reference could mail the inbox a look-alike.
   - Optional: `MOBILERUN_MODEL`.
2. **Apify** (`ADAPTER_SCRAPE=apify`):
   - Create an API token and put it in `APIFY_TOKEN`.
   - Check that the account can run `apify/cheerio-scraper` and `apify/google-search-scraper`, and
     set a monthly usage limit in the console.
3. **HubSpot** (`ADAPTER_CRM=hubspot`):
   - In Settings → Properties, create a single-line text property `utax_key` with "unique values"
     on Contacts and again on Deals.
   - Create a private app with scopes `crm.objects.contacts.write` and
     `crm.objects.deals.write`, and put its token in `HUBSPOT_TOKEN`.
   - If the pipeline isn't the default one, set `HUBSPOT_PIPELINE`, and set `HUBSPOT_STAGE_MAP`
     to `{"memo_sent": "<stage id>", "call_booked": "<stage id>"}`.
4. **Monid** (`ADAPTER_TOOLBUY=monid`):
   - Create an API key and put it in `MONID_API_KEY`.
   - Use the $1 free credit (signup code `MONID-SIGNUP`) or top up (minimum $5), and add a run cap control in the
     dashboard.
   - Pick each tool with `POST /v1/discover`.
   - Set `MONID_TOOLS` as JSON: `{"<name>": {"provider": "...", "endpoint": "...", "max_usd":
     "0.05"}}`.
   - Optional: `MONID_DAILY_CAP_USD` (default 1.00).
   - Nothing is bought until `MONID_TOOLS` names a tool.

## Lesson
Read the platform's own engine before writing its config. The blueprint package's `index.js`
showed that `tasks[]` silently creates an extra org "CEO" agent outside our tool policy, which no
doc page mentions. The engine's own `loadConfig` turned "UNVERIFIED until we have a key" into an
offline check we could run all night.

Second lesson, from task 4: for anything that spends money, the hard question isn't "did it fail?"
but "might it have been charged?" Every reviewer cycle found another path where a failure after
sending let a retry pay twice, or let the cap forget a run. The fix that held was to treat the
key as "may be charged" by default once the request has left, and to keep its money reserved
until a human checks.
