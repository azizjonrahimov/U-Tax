# D42 RUNBOOK: immersive design, email that always answers, live tax forms and Excel

Written 2026-11-08 ~08:30, from the owner's 08:01 and 08:10 requests.

The owner sent a reference video: a scroll-driven 3D WebGL agency site. It has a glowing glass emblem bursting into particle dust, an iridescent chrome ring, dot-matrix display type, a pill nav, and floating iridescent glass cards under violet light. The owner also sent getlayers.ai as a source of style ideas.

**Read D41 (`docs/designs/fixes/FIXES-RUNBOOK.md`) first.** D42 lands **on top of** D41 and never replaces it. Where D41 and D42 touch the same file, D41 merges first.

Everything here was investigated against `main` and the live log, and the reference code is tested:

| Part | Reference code | Tests |
|---|---|---|
| Design | `docs/designs/immersive/` (prototype, screenshots) | visual |
| Tax forms | `docs/designs/tax-forms/reference/tax_returns/` | 7 tests: engine, the real IRS Form 1120 filled, the workbook recalculated in LibreOffice |

Proof images:
- `docs/designs/immersive/acts.png`
- `docs/designs/tax-forms/proof/Form_1120_DRAFT_case_9_p1.png` (the real 2025 IRS Form 1120, filled by our code)

---

## 0. Rules and time

1. **Time.** Hacking stops at 11:00. The D41 freeze is 10:00.
   - D42 is about 3 hours of work for one lane, so it needs **two extra Claude Code sessions** started now:
     - `frontend-3d`: the immersive landing (§P1–P2)
     - `backend-tax`: tax forms (§T1–T4)
   - The existing lanes take D42 work only after their D41 items are READY.
   - The captain asks the owner whether to move the freeze to **10:30**. Without that, D42 items not READY by 10:00 are cut.
2. **Order of value.**
   1. Email reliability (§M), because every demo depends on it.
   2. Live tax forms and Excel (§T), the "wow".
   3. The immersive landing (§P), for the best-design prize.
   4. The design carried into the console and office (§P3).
3. **Honesty and IP.**
   - The reference video's studio logo, portfolio images and project names (any client names on its cards) are **not** to be copied.
   - getlayers.ai assets are paid. We don't download or ship them.
   - We build **our own** emblem (the U), our own cards (our real artifacts), and our own shaders. Copying another company's site or assets can disqualify us.
   - Tax output is always "**DRAFT, estimate for review, not filed**". The firm never files a return.
4. Every new piece degrades gracefully:
   - no WebGL → a static poster image
   - reduced motion → a still frame
   - a Stripe, AgentMail or OpenAI error → log it, retry, never a 500
   - The fast path, approvals, STOP and the D41 tour keep working.
5. Run CHECKS before every READY (as D41 §0.6). Contract **v1.21** for the new events (§C).

### Plan

| Id | Session | What | Box | Cut line |
|---|---|---|---|---|
| M1 | backend | thread every client email (`in_reply_to`) | 15 min | never cut |
| M2 | backend + agents | every email case runs on the fast path; no 10–40 min board gaps | 30 min | never cut |
| M3 | backend | autopilot safety net: deterministic steps run themselves after 45 s | 35 min | keep M1, M2 |
| M4 | backend | mail health, plus the CEO console chip | 15 min | cut |
| T1 | backend-tax | multi-tax engine (federal + state) in the gateway | 30 min | never cut if T is shipped |
| T2 | backend-tax | Form 1120 draft PDF + page images + workbook + `form.step` / `sheet.cell` events + memo attachments | 45 min | workbook only |
| T3 | frontend (after D41) | Tax desk "form view" and Accounting desk "Excel view" with the live cursor | 50 min | the form view only |
| T4 | agents | intake asks for the new facts; tax/acct prompts; services | 20 min | – |
| P1 | frontend-3d | immersive landing hero (WebGL, scroll acts) + P1b 3D models | 95 min | the act-0 hero only |
| P2 | frontend-3d | live data on the glass cards, ticker, fallbacks, performance | 30 min | static cards |
| P3 | frontend (after D41) | emblem, Firm Orb, glass panels and Doto display type in `/ceo`, `/office`, `/talk`, `/login` | 40 min | orb + emblem only |

---

## M. Email: the client always gets an answer, fast, in one thread

### What the log shows (case_1, the only real client so far)

| Time | What happened | Gap |
|---|---|---|
| 22:35:25 | first email in | – |
| 23:14:40 | Intake first looks at it | **39 min** (the CoS board card waited) |
| 23:14:53 | the first re-ask needed the CEO's approval (trust ASK), approved 23:15:08 | – |
| 23:16:01 | ask sent | – |
| 23:16:18 | client replied | – |
| 23:25:59 | the reply was read | 9.5 min |
| 23:41:20 | facts complete (READY) | 16 min |
| 00:26:12 | tax computed | **45 min** (the tax card waited for a board digest) |
| 00:29:19 | memo sent | – |
| 02:00:06 | a later reply | – |
| 04:03 | Naïve was down ("agents missing") | **2 h 3 min** |
| 04:08:49 | memo v2 sent | – |

Root causes:

1. **Email cases use the board, not the fast path.** The gateway wakes `New case case_N` / `Client replied case_N`, and the CoS makes one board card at a time. Each next step waits for a board digest, which takes 10–45 minutes. Only `/talk` cases get `FAST PATH` (memo in under 45 s).
2. **Nothing runs the deterministic steps when Naïve is slow or down.** compute, QA, documents and memo are pure gateway code; only reading the email needs an LLM.
3. **Asks are not threaded.**
   - `_ask_client` (and the not-eligible and follow-up mails) send `OutMail(subject="Re: …")` **without `in_reply_to`**, so Gmail may start a new conversation.
   - Only the memo sets `in_reply_to`.
4. The first email of each kind needs one CEO approval (trust ASK → AUTO). This is by design and now AUTO for email. Keep it, and tell the owner to keep it AUTO for the demo.

### M1 Threading (15 min)

1. Track the newest inbound id in the `Case` projection: `last_inbound_message_id`, set from `email.received` `private.message_id`. Also keep `first_subject` (the client's original subject).
2. Pass `in_reply_to=case.last_inbound_message_id` (or the memo id for follow-ups after the memo) in every client mail:
   - `_ask_client`
   - `_mark_not_eligible`
   - every `client.followed_up` sender
   - the signup welcome (none: there's no inbound yet)
   - `_send_memo`, already done: keep it
   
   The subject stays `Re: <first_subject>`.
3. If the AgentMail adapter sets `In-Reply-To` but not `References`, set `References` to the same id. Find where `in_reply_to` is mapped: `grep -rn "in_reply_to" backend/app/gateway`.
4. Test: the fake mail port records `in_reply_to` on the ask. Ask → reply → memo all carry the thread id.

### M2 Every email case on the fast path (30 min)

**Gateway** (`_route_inbound`):

- A new client case wakes `FAST PATH case_N` instead of `New case case_N`.
- A reply on an asked or sent case wakes `FAST PATH case_N` too: when `case.asks > 0`, or `client_replied`.
- **Do not change `case.priority`.** Priority also means a `/talk` case: the public bucket and the "there" greeting.
- Add `fast_until` to the case: now + 10 min whenever the gateway wakes FAST PATH for it.
- `list_open_cases` returns `"in_flight": true` while `now < fast_until` and the status changed within the last 10 min.
- The once-per-entity wake bookkeeping at `perform.py` ~line 604 already accepts `FAST PATH`. Keep it.

**`naive/prompts/chief-of-staff.md`:**

- Fast path section, first line: "Every `FAST PATH case_N` wake runs this chain, whatever started the case (an email, the public `/talk` page, or a reply)."
- Routing rule 2: "Skip cases marked `priority` **or `in_flight`**."
- The chain stays: `intake` (when facts are missing) → `tax` → `qa` → `accounting`, with `send_to_agent` and `wait: true`.
- New for D42: if `intake` reports that it asked the client, stop the chain. The client's reply comes back as another FAST PATH wake.

**Test:** `test_mail_fast.py`. A new email gives a `FAST PATH case_N` wake. A reply after an ask gives `FAST PATH`. A "thanks!" after the memo wakes nothing (unchanged).

### M3 Autopilot safety net (35 min)

**Rule:** the gateway's tick runs `drive_cases()` (outside `self._lock` for mail I/O, like `advance_market_jobs`). For each open case **not** blocked, with no pending approval for it and email not PAUSED:

| Case status | Unchanged for | The gateway runs, as that seat |
|---|---|---|
| `READY` | 45 s | `compute_tax` as `tax` |
| `COMPUTED` | 45 s | `qa_check` as `qa` |
| `QA_PASSED` and no documents yet | 45 s | `prepare_documents` as `acct` |
| `QA_PASSED` and documents verified | 45 s | `send_memo` as `acct` |

- Run at most one step per case per tick, through the **same handler** the MCP tool uses (`self._handlers[name]`), with the actor set to the seat. Never run a parallel implementation.
- Every event the step writes gets `payload.via = "autopilot"`.
- Tools stay idempotent. If an agent did the step meanwhile, the handler answers "already done" or `Refused`; log it at info and move on.
- Turn it off with `AUTOPILOT=0`. Default on.

`INTAKE_PENDING` with an unread client message older than 90 s (stretch, cut first):
- Call OpenAI (the key is already in `.env`) once with a JSON schema for the facts + verbatim quotes.
- Feed the result into the **same** `_extract_facts` validator (quotes must be verbatim, as today), as `intake`, `via: "autopilot"`.
- If it refuses, do nothing. A human sees the case in the morning brief.

UI:
- An autopilot step shows in the office and the order cards with a small chip "autopilot" (faint, mono), so nobody mistakes it for an agent.
- The CEO brief counts "N steps by autopilot".

Tests, with a frozen clock and fake ports:
1. READY + 45 s → `tax.computed` with `via: "autopilot"`.
2. The chain goes on through `memo.sent` within 4 ticks.
3. An agent already computed → no duplicate.
4. A pending approval → nothing.
5. `AUTOPILOT=0` → nothing.

### M4 Mail health (15 min, cut first)

- `GET /api/admin/mail-health` returns `{"last_poll_at", "last_inbound_at", "last_outbound_at", "provider": "agentmail", "ok": true}`.
  - `ok` is false if the last poll is older than 2 minutes or the last send failed.
- CEO console top bar chip: "Email · live · checked 12 s ago" (lime dot). It goes amber when not ok.

### M5 Owner live test (8 min, after M1–M3 and T1–T2 are live; replaces D41 §D.4)

This shows the whole loop: we email the client, the client replies with what we asked for, and the firm does the tax work.

1. From personal Gmail, write to the firm inbox:
   - Subject: `Tax help for our startup`
   - Body:

     > Hi, we're a Delaware C-corp based in California. Last year we had $1,200,000 in sales, $200,000 cost of goods, and $700,000 of other business expenses. What do we owe?

2. Within about a minute, a **threaded** reply arrives (same Gmail conversation). It asks only for the Delaware franchise facts still missing: authorized shares, issued shares, par value, total gross assets, and whether a foreign owner holds 25% or more.
3. Reply in the same thread:

   > Authorized shares: 10,000,000. Issued shares: 1,000,000. Par value: $0.0001. Total gross assets: $50,000. No foreign owner holds 25% or more.

4. Within about 2 minutes, the memo arrives in the same thread. Its "What you owe" table:
   - **Federal $63,000** (Form 1120)
   - **California $26,520** (Form 100)
   - **Delaware franchise $450** ($400 + $50 annual report)
   - **Total $89,970**
   
   The draft Form 1120 PDF and the workbook are attached.
5. `/office`: the Intake inbox shows all four messages. The Tax desk shows Form 1120 being filled. The Accounting desk shows the Excel being built.

To find the inbox address and watch the events:

```
Get-Content backend\.env | Select-String "AGENTMAIL" | Select-String "INBOX|ADDRESS"
Get-Content backend\data\events.jsonl -Wait -Tail 0 | Select-String '"type":\s*"(email\.|client\.|facts\.|tax\.|form\.|sheet\.|doc\.|memo\.)'
```

---

## T. Real tax forms, real calculations, live Excel

### What exists

Today the firm does **only the Delaware franchise tax**:
- facts: `incorporated_delaware`, `authorized_shares`, `issued_shares`, `stated_par`, `gross_assets`, `foreign_owner_25`, and optionally receipts
- `prepare_documents` writes the workpaper `.xlsx`, the memo `.docx` and the books `.xlsx`, emitting `doc.step` / `doc.created` / `doc.verified`
- the memo goes out with attachments: `OutMail.attachments` exists since v1.15

The office only shows `doc.step` sentences. No real tax form is filled, and no income tax is computed.

### Scope (be exact; anything outside goes to a person)

- **Client:** a **C-corporation**, single-state. LLC, S-corp and individual returns are answered "a person prepares this" (`human_review` → the case is BLOCKED with that reason).
- **Taxes:**

  | Tax | How it's computed |
  |---|---|
  | Federal corporate income tax, Form 1120 | 21% of taxable income, IRC §11(b) |
  | State tax, by where the company is based | see the table below |
  | Delaware franchise tax | the existing engine, kept as is, when incorporated in Delaware |

- **State coverage** (rates with sources in `rates.py`, checked 2026-11-08):

  | State | Tax | Rule | Form | Due |
  |---|---|---|---|---|
  | CA | corporation franchise tax | 8.84%, min $800 (none in the first taxable year) | Form 100 | Apr 15 |
  | NY | corporation franchise tax | max(6.5% business income [7.25% over $5M], FDM by NY receipts, capital base 0 for small business) | CT-3 | Apr 15 |
  | TX | franchise (margin) tax | $0 at or under $2.65M revenue (No Tax Due + PIR); else 0.75% (0.375% retail) of margin, or EZ 0.331% if lower (≤ $20M) | 05-158 | May 15 |
  | FL | corporate income tax | 5.5% above $50,000 | F-1120 | May 1 |
  | IL | income + replacement tax | 9.5% (7% + 2.5%) | IL-1120 | Apr 15 |
  | DE | corporate income tax | 8.7% (only if based in DE) | 1100 | Apr 15 |
  | WY, SD | – | no corporate income tax | – | – |
  | NV, WA, OH | gross receipts tax | `human_review` | – | – |
  | any other | – | `human_review` | – | – |

- **Simplifications, printed in every memo:**
  - one state (no apportionment)
  - state income = federal taxable income
  - no NOL, credits or estimated payments
  - "draft on the 2025 forms, not filed"

### T1 Engine in the gateway (30 min)

1. Copy `docs/designs/tax-forms/reference/tax_returns/{__init__,rates,engine}.py` to `backend/app/tax_returns/`. Copy `test_engine.py` to `backend/tests/test_tax_returns_engine.py` and change the import to `app.tax_returns.engine`.
2. **New facts** (add them wherever `FACT_LABELS` and the facts schema live; `grep -rn "FACT_LABELS" backend/app`):
   - `entity_type` (string)
   - `state` (2-letter)
   - `gross_receipts` (exists: keep the cents convention used there and convert to dollars for the engine)
   - `cost_of_goods`
   - `total_deductions`
   - optional: `first_year_in_state` (bool), `retail_wholesale` (bool), `compensation`
   
   `extract_facts` must still require a verbatim quote per fact, as today.
3. **Services.**
   - A case gets `services`, set by `extract_facts`'s new optional argument `services: ["de_franchise", "income_tax"]`. Default: what the email asks for. A Delaware C-corp asking "what do we owe" gets both.
   - The "missing facts" check unions the required facts of the chosen services, so the ask email lists only those (labels in `engine.FACT_LABELS`).
4. **`compute_tax`.** When `income_tax` is in `services`:
   - after the existing DE result, call `compute_returns(facts)`
   - emit one event per result:

     ```
     NewEvent(type="tax.return_computed", actor="tax", case_id=…, payload={"jurisdiction", "tax", "form", "due", "amount_usd", "status", "steps", "source"})
     ```

   - plus `tax.summary {"total_usd", "assumptions"}`
   - Any `human_review` → `_block(case, reason)` with the step text. The CEO sees it in Blocked.
5. **`qa_check` adds checks:**
   - federal line 30 = gross − returns − cogs + other − deductions
   - tax = round(21% × max(line 30, 0))
   - state amount = engine recomputed independently (call the engine again from the facts)
   - total = the sum
   
   A mismatch fails QA as today.

### T2 Draft Form 1120, page images, workbook, live events (45 min)

1. **Blank form:** already committed at `backend\assets\forms\f1120_2025.pdf` (the official IRS file, public domain). Only if it's missing, run this on the owner's machine (PowerShell, from the repo):

   ```
   New-Item -ItemType Directory -Force backend\assets\forms | Out-Null
   Invoke-WebRequest https://www.irs.gov/pub/irs-pdf/f1120.pdf -OutFile backend\assets\forms\f1120_2025.pdf
   cd backend; .venv\Scripts\python -m pip install pypdf pypdfium2 openpyxl
   ```

   `form1120.fill()` refuses a file that isn't the 2025 revision.
2. Copy `form1120.py`, `workbook.py`, `map_fields.py` and `test_forms.py` to `backend/app/tax_returns/` and `backend/tests/`. Point `BLANK` at `backend/assets/forms/f1120_2025.pdf`.
3. **In `_prepare_documents`**, when `income_tax` is in `services` and QA passed, after the existing three documents:
   - `Form_1120_DRAFT_case_N.pdf` = `fill(blank, out, header, fed.lines)`.
     - `header`: `name` = "<masked name> (DRAFT, not for filing)", `state`, `city` if known, and `ein` = "XX-XXX" + last 4 if given, else blank.
     - **Never put a real EIN or address into a public event.**
   - `render_pages(out, assets_dir)` → page 1 and page 3 PNGs (Schedule J), saved under the existing assets route as `forms/case_N/f1120_p1.png` and `f1120_p3.png`. **Admin/session readers only**; the public theater gets no form images.
   - `U-Tax_Returns_case_N.xlsx` = `workbook.build(path, facts, returns, masked_name)`.
4. **Live events** (written in one batch; the frontend paces them):
   - one `form.step` per fill step:

     ```
     {doc: "f1120", n, of, key, label, value, page, rect: [x0, y0, x1, y1] (fractions, top-left origin), image: "/assets/forms/case_N/f1120_p<page+1>.png"}
     ```

   - one `sheet.cell` per workbook step:

     ```
     {book: "U-Tax_Returns_case_N.xlsx", n, of, sheet, cell, label, value, formula}
     ```

   - then `doc.created` and `doc.verified` for both files, as for the existing docs.
   - `doc.verified` for the workbook checks that `Summary!B6` equals the engine's total. For the PDF, it reads back fields `f1_47` / `f1_48` (lines 30 / 31).
5. **Memo.**
   - The memo template gets a "What you owe" table: one row per `tax.return_computed` result, with jurisdiction, tax, form, due date and amount. It also gets the assumptions list and the "draft, not filed" line.
   - `send_memo` attaches the PDF and the xlsx with the existing workpaper/memo attachments. Keep the existing size limit; the 1120 PDF is ~0.35 MB.
6. **Tests:**
   - The 7 reference tests.
   - An end-to-end with fake ports: the case_9 facts → `tax.return_computed` ×2 → 25 `form.step` + ~30 `sheet.cell` → attachments on the memo.

### T3 Office: watch the form and the sheet being filled (50 min, frontend after D41)

**Tax desk "Form" view** (`frontend/src/components/office/FormView.tsx`):
- Use the Window chrome. Address bar: `irs.gov/pub/irs-pdf/f1120.pdf · draft for Team #9`. Tab: "Form 1120".
- The page PNG fills the window width. Over it, an absolutely positioned layer has one `<span class="fv-val">` per filled field, at `left: x0*100%; top: y0*100%; width: (x1-x0)*100%; height: (y1-y0)*100%`. Right-aligned, mono, `#1a4bd6` (ink blue, like a typed form).
- **Playback.** Steps play in order, one every 450 ms (`TIMING.formStepMs`):
  1. The existing agent cursor glides to the field center (300 ms ease).
  2. The value types in character by character (the existing typing effect).
  3. A lime 1 px ring flashes around the field.
  4. The page container scrolls so the field stays in the middle third.
  5. On `page` change, crossfade to the next page image.
- The right side panel lists the lines as they fill: "Line 30 Taxable income · $300,000". The final line 31 row is lime.
- With `prefers-reduced-motion`, show everything filled, no cursor.

**Accounting desk "Excel" view** (`ExcelView.tsx`):
- Excel-like chrome. Address bar: `U-Tax_Returns_case_9.xlsx`.
- A formula bar showing `fx  =ROUND(MAX(B7,0)*0.21,0)` for the active cell.
- Column letters A–D and row numbers. Bottom tabs per sheet (Inputs, Federal 1120, State CA, Summary).
- Cells fill in order, one every 350 ms. The cursor selects the cell (lime 2 px border), the formula shows in the bar, then the result types in (money format `$63,000`).
- Totals get a lime fill (the workbook does the same).
- On sheet change, the tab switches.

**Wiring:**
- `office/workspace.ts` `stepOf`: `form.step` → seat `tax`, `sheet.cell` → seat `acct`. Add both to the D41 `tour.ts` `TYPE_SEAT` map, so the tour shows the Tax desk while the form fills and Accounting while the sheet builds.
- The desks' segmented controls (D41) get the new tabs `Form` and `Excel`. They auto-select when those events arrive.
- History: a reload shows the last finished form and sheet instantly (fold from the log, no animation).
- CEO console: the order or case card shows "Form 1120 · line 31 · $63,000" as its newest step, with "Open ↗" to `/office?d=tax&tab=form&case=case_9`.

### T4 Agent prompts (20 min)

- **`intake.md`:** read the email for both services. Ask only for missing facts, in plain words (labels from `FACT_LABELS`). Never ask for an EIN or an SSN. If the client is an LLC, S-corp or individual, say a person will prepare it and `mark_not_eligible` with the existing reason closest to it, or block with the reason.
- **`tax.md`:** after `compute_tax`, say in one line which taxes were computed and the total. Never state a rate yourself; quote the engine's steps.
- **`accounting.md`:** `prepare_documents` now also makes the draft 1120 and the workbook. Mention both in the summary.
- **`_firm-rules.md`:** "We prepare drafts and estimates. We never file a return, never e-sign, never send a number the engine did not compute."
- Then run `npx tsc --noEmit`, `naive up --dry-run`, `naive up`. **Only after T1/T2 are merged and the gateway restarted.**

---

## P. Design: "the firm, alive", the immersive look from the owner's video, made ours

### What we take from the video, and what we don't

| Video element | Our version |
|---|---|
| glowing particle emblem + iridescent chrome ring | **the U-Tax "U"** built from ~9,000 lime/department-colored particles inside an iridescent metal ring (three.js `MeshPhysicalMaterial` with `iridescence`) |
| particle burst on scroll | the U bursts into a storm colored by our 7 department colors. Caption: "Every dot is a move" |
| floating iridescent glass cards | glass cards showing **our real artifacts**: Form 1120 (filled), Live Ledger (Excel), Inbox (thread), Paid Agent (tx), 7 Agents |
| dot-matrix display type | **Doto** (Google Fonts, OFL) for display lines. Geist for body, Instrument Serif italic for one accent word |
| pill nav "WORK —— CONTACT" | pill "OFFICE —— CEO CONSOLE" (top right, glass) |
| violet stage light, bloom | violet rim light + lime key light, UnrealBloom (strength 0.45) |
| **not taken** | their logo, their portfolio images or client names, their copy |

The palette stays Night Lime: black `#050505`, lime `#BEF243` for actions. Violet `#8b5cf6` and iridescence are only for glass and light.

### P1 Immersive landing (70 min, `frontend-3d` session)

The prototype is tested and screenshotted in `docs/designs/immersive/`:
- `immersive.js`: the scene. One file, three.js r169, with EffectComposer + UnrealBloom + RoomEnvironment.
- `immersive.html`: the page with acts, captions, scrim and nav.
- `firm-orb.js`: the small orb for the console and office.
- `acts.png`, `i0..i3.png`: what it must look like.

Steps:

1. `cd frontend; npm i three@0.169.0` (exact version), then `npm i -D @types/three@0.169.0`.
2. Copy `immersive.js` to `frontend/src/components/site/immersive/scene.ts`. Add types where TS complains; don't change the logic. Imports: `three` and `three/examples/jsm/...` (Next resolves `three/examples/jsm/postprocessing/EffectComposer.js`).
3. `Immersive.tsx` (`"use client"`):
   - mounts a fixed full-viewport `<canvas>` inside the landing hero section only
   - `const s = createImmersive(canvas, { cards })` in `useEffect`, and `s.dispose()` on unmount
   - scroll progress = the hero section's own scroll (wrapper height `600vh`, canvas `position: sticky; top: 0; height: 100vh`)
   - acts as in `immersive.html`: four `.act` blocks switched by progress, plus the `.scrim` gradient
   - load it with `next/dynamic(() => import("./Immersive"), { ssr: false })`. SSR renders the poster.
4. **Fonts.** Add `Doto` via `next/font/google` (`Doto({ subsets: ["latin"], weight: ["700", "800"], variable: "--font-doto" })`), together with the D41 Geist fonts.
   - Class `.dot-display { font-family: var(--font-doto); font-weight: 800; letter-spacing: .01em; text-transform: uppercase; }`.
   - The card textures draw text on a 2D canvas, so call `await document.fonts.load('800 92px Doto')` before `createImmersive`.
5. **Landing order.**
   - The immersive section **replaces the current hero (S1)**.
   - Then the existing D39 sections, unchanged in content. They inherit the new type: section titles in Doto, one Instrument Serif italic word each.
   - The existing ribbons stay below the fold only.
6. **Fallbacks:**
   - No WebGL (`!canvas.getContext("webgl2")`) → show `poster.webp`: export `i0.png` → webp.
   - `prefers-reduced-motion` → one still frame (already in `scene.ts`), with the acts as a normal vertical layout.
   - Phones (< 768 px): points 4,000, bloom off, DPR 1.5, no mouse parallax. Cards in a single column path. Headline 44 px.
7. **Performance:**
   - Keep ≥ 50 fps at 1440×900 on the demo laptop (Chrome DevTools > Performance), and pause when the tab is hidden.
   - Lighthouse performance ≥ 70 on the landing. The canvas is lazy (dynamic import after first paint).
8. **Acceptance:** side by side with `docs/designs/immersive/acts.png`.
   - Act 0: the U inside the ring, headline left, CTA lime.
   - Act 1: the storm, "Every dot is a move".
   - Act 2: the cards fly past on both sides.
   - Act 3: the emblem re-forms, with "One firm. One human." and the CTA.
   - No text overlapping a bright card; the scrim handles it.

### P1b 3D models (part of P1; +25 min)

`docs/designs/immersive/models.js`: original procedural models, no downloaded assets. The current `immersive.js` already places them; the result is `models.png` (m0–m3).

| Model | Maker | Where it appears |
|---|---|---|
| **Glass sculpture**: twisted torus knot in clear iridescent glass (`transmission: 1`, `iridescence: 1`) with a pulsing lime heart and a pedestal with a lime rim | `makeGlassSculpture()` | the end of the card flight, act "Built to be checked" (p 0.70–0.82) |
| **Crystal flowers**: three rings of cupped iridescent petals, glowing core, pollen points, one color per department | `makeFlower(hex, size)` / `makeGarden(center, n)` | clusters beside every glass card |
| **Chrome spines**: twisted columns of iridescent chrome shards (`InstancedMesh`) | `makeShardSpine(count, height)` | beside the first card, and one further back on the other side |

Each model has an `update(t)` method that the scene calls every frame. The models are visible only while the cards are, and the sculpture only around its act.

- Copy `models.js` to `frontend/src/components/site/immersive/models.ts` next to `scene.ts`.
- Phones (< 768 px): skip the gardens, keep one spine. In the sculpture, use `transmission: 0.6` with `thickness: 0.6` (transmission is expensive).
- If fps on the demo laptop is below 50: halve the flower count, then lower `TorusKnotGeometry` segments to 160 × 24.

### P2 Live data on the glass (30 min)

1. **Card faces come from live state** (`/api/state`), never invented numbers. The canvas card texture takes `{title, caption}`; captions:

   | Card | Caption |
   |---|---|
   | Form 1120 | "federal return · line 31 · $<latest>" from the newest `tax.return_computed` Federal, or "filled live · draft" if none |
   | Live Ledger | "<N> cells · formulas on screen" from the newest workbook |
   | Inbox | "<N> client emails answered" (count of outbound client mails) |
   | Paid Agent | "<amount> <token> · tempo testnet" from `/api/payments` (D41 B52) |
   | 7 Agents | "<clients> clients · <touches> human touches" from public state |

   Refresh every 30 s by redrawing the texture: `texture.needsUpdate = true`.
2. **Ticker** (bottom right, glass pill): the newest public event as one line, "Tax filled Form 1120 line 31 · 14:02". On each new event, call `s.pulse(seatColor)` (the key light flashes the department's color).
3. **Clicking a card** opens the matching place: Form → `/office?d=tax&tab=form`, Ledger → `?d=acct&tab=excel`, Inbox → `?d=intake&tab=inbox`, Paid → `?d=acct&tab=payments`, 7 Agents → `/office`. Use a raycaster on click; the cursor becomes a pointer on hover.

### P3 The same language inside: `/ceo`, `/office`, `/talk`, `/login` (40 min, frontend after D41)

1. **Emblem.** `frontend/src/components/ui/Emblem.tsx`:
   - a static, crisp SVG version of the ring + U (a ring with an iridescent conic gradient stroke and a dotted U), for the sidebar brand, the console top bar, favicon-size uses and the login card
   - no WebGL inside dense UIs
2. **Firm Orb** (`firm-orb.js` → `frontend/src/components/ui/FirmOrb.tsx`, the point sphere):
   - **CEO console voice card:** replaces the lime circle orb. `setMode("listening")` while the mic is open; `setLevel(rms)` from the existing STT level meter; `setMode("speaking")` during TTS; `pulse(seatColor)` on each live event.
   - **Office header** (the D41 `.head`): a 56 px orb next to "The firm, *live.*" that pulses per event.
   - One WebGL context per page at most (the orb is the only WebGL inside).
3. **Glass panels.** Shared CSS in `globals.css`, applied to the cards, panes and windows already in the D41 markup (just add the class `glass`):

   ```css
   .glass{position:relative;background:linear-gradient(180deg,rgba(255,255,255,.045),rgba(255,255,255,.015));border:1px solid rgba(255,255,255,.08);border-radius:16px;backdrop-filter:blur(14px) saturate(140%)}
   .glass::before{content:"";position:absolute;inset:-1px;border-radius:inherit;padding:1px;pointer-events:none;
     background:conic-gradient(from 180deg at 50% 50%,rgba(190,242,67,.0),rgba(139,92,246,.35),rgba(46,230,214,.25),rgba(190,242,67,.35),rgba(190,242,67,0));
     -webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);-webkit-mask-composite:xor;mask-composite:exclude;opacity:.55}
   .glass.focus::before,.glass:hover::before{opacity:1}
   ```

4. **Display type.** Page and section titles (`The firm, live.`, `Your brief`, `Waiting on you`, department names in the dept header) use `.dot-display` (Doto) at their current sizes. Body stays Geist. Numbers stay mono.
5. **Background.** `/ceo` and `/office` get a very faint violet stage glow, with the panels still readable:

   ```css
   radial-gradient(1200px 700px at 85% -10%, rgba(139,92,246,.10), transparent 60%), radial-gradient(900px 600px at 0% 110%, rgba(190,242,67,.06), transparent 60%), #050505
   ```

6. **Pill nav.** The landing and `/talk` use the same glass pill nav: "OFFICE —— CEO CONSOLE".
7. **Do not change** D41 layouts, handlers or class names. P3 only adds classes, components and tokens.

---

## C. Contract v1.21 (the captain records it)

**Events:**

| Event | Actor | Payload |
|---|---|---|
| `tax.return_computed` | tax | `{jurisdiction, tax, form, due, amount_usd, status: "ok" \| "no_tax_due" \| "human_review", steps[], source}` |
| `tax.summary` | tax | `{total_usd, assumptions[]}` |
| `form.step` | tax | `{doc, n, of, key, label, value, page, rect[4], image}` (admin and session readers only; the public theater gets `{doc, n, of, label}`) |
| `sheet.cell` | acct | `{book, n, of, sheet, cell, label, value, formula}` |
| any step run by M3 | – | carries `payload.via = "autopilot"` |

Other changes:
- Facts gain `entity_type`, `state`, `cost_of_goods`, `total_deductions`, plus optional `first_year_in_state`, `retail_wholesale`, `compensation`. `extract_facts` gains the optional `services[]`.
- `list_open_cases` rows gain `in_flight`.
- Wakes: `FAST PATH case_N` is now used for every email case.
- New endpoint: `GET /api/admin/mail-health`.

## G. Gates and rehearsal

- `CHECKS` per lane, plus the 7 reference tests plus the new M and T tests.
- **R1:** the M5 email test passes end to end: threaded ask, your threaded reply, then the memo with the $89,970 table and the draft 1120 + workbook attached. The office shows Intake → Tax (Form view filling) → QA → Accounting (Excel view building).
- **R2:** kill Naïve (stop the Naïve process) and send another email. The autopilot finishes the case within 4 minutes, with "autopilot" chips. Restart Naïve.
- **R3:** the landing on the demo laptop at 1440×900 matches `acts.png` at ≥ 50 fps. Also check a phone (Chrome device mode, iPhone 14) and the reduced-motion fallback.
- **R4:** `/ceo` and `/office` show the emblem, the orb pulsing on events, glass panels and Doto titles, with D41 layouts unchanged.

## H. Prompt for the lead session (the owner pastes this)

> Read `docs/designs/D42-RUNBOOK.md` completely; it lands on top of D41.
>
> Keep D41 going in the existing lanes. Start two extra sessions now: **frontend-3d** for §P1–P2 and **backend-tax** for §T1–T2. Give the backend lane §M1–M3 as its next items after its D41 work. Give the agents lane T4 and the M2 prompt changes, and run `naive up` only after the backend changes are merged and the gateway restarted.
>
> Copy the tested reference code from `docs/designs/tax-forms/reference/` and `docs/designs/immersive/`; don't retype it.
>
> Ask me once whether to move the freeze to 10:30. Respect the cut lines. Never copy the reference video's or getlayers.ai's logo, images or names. Never file a return or put a real EIN in an event. Report one digest line per merge. Tell me exactly when to do M5 (the email test) and R1–R4.
