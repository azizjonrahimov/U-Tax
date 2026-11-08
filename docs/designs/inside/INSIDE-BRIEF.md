# D40 INSIDE BRIEF — `/ceo` and `/office` in Night Lime

**For:** the controller (`naive-yc-hackathon-99`) → the `frontend` lane.
**Written:** Sun Nov 8 2026, ~06:55, by Claude (the owner's planning chat), from the owner's two screenshots (CEO console at 06:08, office theater at 06:06) and `frontend/src/components/{ceo,office,theater}` on disk.
**Owner's words (verbatim):** "inside design looks like this; make it professional design as well with UI/UX designs, click buttons, mix of colors, images, illustrations, and et..."
**Authority:** the owner's standing auto-decide rule. This is D39 phase 2 (re-skin by tokens), extended with avatars, illustrations and real images. This brief is the rule-5 plan: reply `APPROVED`.
**Clock:** freeze **10:00**. All D40 work merged and deployed by **09:15**, rehearsed at **09:30**. Cut lines in §7.

## Files (in `docs/designs/inside/`)
| File | What |
|---|---|
| `ceo-console-prototype.html` | **target for `/ceo`.** Open in a browser; switches toggle. SAMPLE data |
| `office-prototype.html` | **target for `/office` Overview (theater).** SAMPLE data; sample cursors only |
| `ceo-1440.png`, `ceo-full.png`, `ceo-390.png`, `office-1440.png`, `office-1920.png` | how they render (laptop, full page, phone, projector) |
| `illustrations/avatar-{cos,intake,tax,qa,acct,mkt,hr,ceo}.svg` | **original** department avatars (squircle, seat-color gradient, line glyph) |
| `illustrations/empty-{inbox,sheet,social,phone,calendar,search}.svg`, `all-clear.svg` | empty-state illustrations (line art, lime accent) |
| `sprite.svg` | the same art as `<symbol>`s (`#av-mkt`, `#empty-inbox`, …) |

---

## 0. Guardrails (the demo is rehearsed; protect it)
1. **Skin, don't rewire.** Change CSS and add presentational JSX. **Don't** touch:
   - event handling, `theater/script.ts` timing or cursor targets, `theater/live.ts`, `ceo/voice.ts`/`CeoVoice.tsx` logic, the API calls
   - any `data-testid` or selector a test uses
   All existing tests must stay green.
2. **STOP EVERYTHING stays instant:** one click or Esc, as today. Restyle it; never make it hold-to-confirm or add a dialog.
3. **No invented numbers:** the prototypes are sample data. In the app every value comes from what the components already read (`/api/state`, the admin console API, the event stream). If a value isn't available, hide that element. Never hard-code a figure.
4. **Seat colors stay** (`DESIGN.md`) as department identity. Lime is for actions/highlights only. Keep the QA green only on QA's avatar/cursor.
5. **Projector first for `/office`:** it must look right at **1920×1080** (stage) and **1440×900**, with no clipped text and no clipped bottom bar.

## 1. What's wrong now (from the owner's screenshots)
**CEO console**
1. One narrow column in a sea of black: about 55% of the 1561 px screen is empty.
2. Everything is the same weight: no hierarchy, no numbers you can scan, tiny gray labels.
3. The approve buttons are **blue**, off-brand (D39 lime is the action color).
4. The brief is a wall of text. Its numbers ($84,765, $0.00) are buried in a sentence.
5. Approvals show truncated text ("…show $85,165…") but no preview of what will be posted, even though the post has an image.
6. Hiring is a plain list. Standing orders read as a long list of "RUNS ON ITS OWN / Pause".
7. Voice controls (Talk, Hands-free) are small and look like secondary chrome, though voice is the product's core.

**Office theater**
1. Text is clipped: "rendered…", "Gross assets (proj. D…", "Search law N…", the money panel's "Contractors paid", the bottom bar's "Rep[lay]".
2. Empty panes say "No mail yet." / "No posts yet." / "No searches yet." and look broken on a projector.
3. Agent cursors are parked in a row at the bottom, overlapping labels.
4. The sidebar has dots only; departments have no identity beyond a 10 px dot.
5. Order cards cut text mid-word ("…franchise t…", "job" fragment).
6. The panes all have the same weight, so you can't tell where the action is.

## 2. Shared pieces (build first, ~35 min): `frontend/src/components/ui/`
- **Tokens.** If D39's Night Lime tokens live only in landing CSS, lift them to `globals.css` `:root` (same names as LANDING-BRIEF §3.1) so `ceo.css`, `office.css` and `theater.css` can use them. Panels: `--panel` glass with `--line` borders, radius 14–16px.
- **`DeptAvatar.tsx`:** `({seat, size=28})` renders the matching avatar as **inline SVG** (copy the paths from `illustrations/avatar-*.svg`; no network fetch, no `<img>`). Used in: the office sidebar, order steps, the bottom bar, the CEO approvals, the activity feed and standing orders.
- **`EmptyState.tsx`:** `({art, title, line})` renders an inline SVG from `illustrations/empty-*.svg` + a title + one mono line. The copy is in §4.
- **`Switch.tsx`:** `role="switch"`, `aria-checked`, 40×22, lime when on. It replaces the "Pause/Resume" buttons visually; it **calls the same handlers**.
- **`Button` variants** (if not already from D39): lime / ghost / sm; focus ring 2px lime + 2px offset.

## 3. `/ceo`: target `ceo-console-prototype.html`
**Layout:** 3 columns, `232px | 1fr | 340px`, under a 64px top bar.
- ≤ 1280px: the rail collapses to icons (76px).
- ≤ 980px: one column; the rail is hidden and the right column moves below.
- Use `minmax(0,1fr)` columns so nothing forces sideways scroll on a phone.

| Area | Content (bind to what `Ceo.tsx` already has) |
|---|---|
| **Top bar** | U mark + "U-Tax" + `ceo` chip; the **command field** (the existing text input + Send, restyled as a pill with `⌘K` hint and a lime mic button that triggers the existing Talk); status pill "firm open · 7 agents"; "updated hh:mm:ss"; CEO avatar |
| **Left rail** | Brief · Approvals (lime count badge) · Cases · Hiring · Standing orders · Money (anchor links to the sections) · "Office, live ↗" · **Stop everything** pinned bottom (red outline, Esc hint, instant) |
| **Brief card** | kicker `your brief · since hh:mm`; headline with one serif-italic word ("Good morning. The firm kept *working.*"); the existing brief sentence; **4 KPI tiles** from the brief's own numbers (clients served, memos sent, clients saved in lime, spent est.) with tiny sparklines only if a series exists (else no sparkline); **Play the brief** lime button with a small animated waveform (calls the existing play); "Open the office ↗" |
| **Voice card** | a large lime **orb** (the existing Talk/hold control, restyled; a pulse ring when listening); "Hold space to talk"; **Hands-free as a Switch** (same handler); 3 suggestion chips that fill the command field (the same examples as today's placeholder) |
| **Waiting on you** | 2-column cards: department avatar + name + time; channel chip (X / in / email); a **preview**. For social posts with an `image_url`, show the real image (the PixelAgent share card or `ad_*.png`) in a rounded frame above the full post text; else the full text. Footer: "first X post → then automatic" (from the trust state) + **Deny** (ghost) + **Approve** (lime). Same handlers |
| **Hiring** | full-width card, a 4-column board: applied / screened / shortlisted / hired, with counts. Candidate cards: initial avatar (HR teal gradient) + name + stage chip ("questionnaire out"). Empty columns are dashed boxes with a hint ("HR calls next", "your yes") |
| **Standing orders** | 2-column list: department avatar + kind + mono detail ("approved 22:14 · 12 sent since" if available) + status chip ("runs on its own" lime / "asks first" amber) + **Switch** (Pause/Resume handlers) |
| **Right column** | **Blocked cases** (count; `all-clear` illustration + "No case is waiting on a human." when 0); **Live activity** (the latest public events as a timeline: avatar + sentence + mono time; amounts and tx in lime mono); **Where the money went** (stacked bar + legend from `money.spent_by_provider`; hide if absent) |

## 4. `/office` Overview (theater): target `office-prototype.html`
**Layout:** sidebar 248px | main.
- Main rows: header, orders (3 cards), stage grid (`1.25fr 1fr 1fr 300px`, 2 rows), bottom bar.
- Everything fits at 1440×900 and 1920×1080 with no page scroll.

| Area | Change |
|---|---|
| **Sidebar** | Overview item highlighted (lime inset bar). Departments: **`DeptAvatar` (30px)** + name + a thin activity bar (width = steps ÷ max steps, seat color) + mono step count. Bottom: live dot, "CEO console ↗", Sign out (no underlines) |
| **Header** | "The firm, *live.*" (serif accent) + mono "every move rendered from the action log" + LIVE pill + clock (JetBrains Mono) + "S · stage" |
| **Order cards** | 2-line clamp on the quote (`-webkit-line-clamp: 2`, full text in `title`). Status chip (done / running / **waiting on you** amber). Steps as rows with `DeptAvatar` 22px. The card with the newest activity gets the lime border glow |
| **Panes** | Header: a small tinted icon tile (seat color of the pane's main department) + title + right meta (ellipsis, `title` attr for the full text). The pane that is animating gets `.focus` (brighter border + shadow) |
| **Empty states** | `EmptyState` in every empty pane, never a bare "No … yet": Mail "Inbox is quiet / Intake checks the firm inbox every 30 s…"; Social "Nothing to post yet / Marketing posts after QA and your first OK."; Phone "No calls yet / HR rings applicants here; QA opens memos on a real phone."; Calendar "Open slots / Review calls land here after each memo."; Search "No searches yet / Tax cites the law it used, right here."; Sheet "Waiting for numbers / The engine fills this the moment Intake has all five." |
| **Sheet** | Shorter labels (Authorized-shares / Assumed-par) so no row clips. Values in JetBrains Mono tabular; the losing method struck through in faint; the winner and the total in lime; a `✓ QA` chip on the total when `qa.passed` |
| **Market** (F41) | contractor tile (dashed, "PA"), check chips (green), "U-Tax wallet → coin → PixelAgent" with the coin animation **only on `payment.sent`**, tx link (lime mono) |
| **Social** | when a post or image exists, show the **real image** (`image_url`) as a thumbnail, with a mono line under it ("2 posts waiting for your OK · X, LinkedIn" from state) |
| **Right rail** | Human touches: big number (JetBrains Mono 34px) + "training 4/5" with 5 segment pills. Money: clients saved big in lime, then `firm spent (est.)` and `contractors paid` rows (never clipped). Hiring: a mini funnel (applicants/screened/shortlisted bars in HR teal) |
| **Bottom bar** | avatars stack + "Firm run by 7 agents · N clients · K human touches" + progress + **segmented 1×/4×/16×** + Sound + Stage + **Replay (lime)**; `flex-wrap: nowrap`, and nothing clipped at 1440 |
| **Cursors** | **don't change `script.ts` targets.** Only CSS: the name tag gets a soft shadow; the parked/idle cursor position (if it's a CSS/layout constant) moves into the bar's left area, so idle cursors don't cover labels. If parking is computed in script, leave it |
| **Stage mode** | keep it working; check it at 1920×1080 after the re-skin |

## 5. Other inside pages (only if time remains after §3–§4)
`/talk` and the department workspaces get tokens + `DeptAvatar` + `EmptyState` only. No layout changes.

## 6. Accessibility and quality
- Contrast AA (text `--dim` on panels ≥ 7:1). Every icon-only button has an `aria-label`. Switches use `role="switch"`. Focus rings are visible.
- `prefers-reduced-motion`: no pulses, coins or reveals.
- No layout shift when data arrives (reserve heights for KPI tiles and panes).

## 7. Tasks (frontend lane; F43) and cut lines
| Task | Box | Done when |
|---|---|---|
| F43a ui primitives + tokens + avatars + empty states (§2) | 35 min | a primitives page renders; tests green |
| F43b `/office` re-skin (§4) | 75 min | matches `office-1920.png` and `office-1440.png`; no clipped text; theater tests green |
| F43c `/ceo` re-skin (§3) | 75 min | matches `ceo-1440.png` / `ceo-full.png`; all actions work (approve, deny, pause/resume, stop, talk, hands-free, play brief, send) |
| F43d QA + deploy | 20 min | `npm run lint`, `npm run build`, `npm run test`; gstack `/design-review` vs the prototypes (fix anything < 8/10); `/qa` click-through of every button on `/ceo` and `/office`; deploy with `crew/scripts/deploy-frontend.ps1` |

**Order:** F43a → **F43b (office first; it's on the projector)** → F43c → F43d.
**Cut lines:**
- At **08:30** if F43c isn't started, ship `/ceo` with tokens + lime buttons + avatars + approval image previews only.
- At **09:15** stop all D40 work. If anything is broken, **revert the D40 merge commit(s)**; the old skin works.

## 8. Acceptance checklist
- [ ] `/office` at 1920×1080 and 1440×900: no clipped text anywhere (sidebar, orders, panes, rail, bottom bar); Replay is visible
- [ ] Every empty pane shows an illustration + a helpful line
- [ ] Department avatars appear in the sidebar, order steps, bottom bar, the CEO approvals, the activity feed and standing orders
- [ ] `/ceo`: KPI tiles, voice orb, image previews on social approvals, hiring board, standing-order switches, activity timeline
- [ ] Approve is lime; Stop everything is instant (click/Esc)
- [ ] All numbers live (none from the prototypes)
- [ ] Rehearsal flow still works: sign in → `/office` → voice order → the first payment asks → approve → post
- [ ] Tests, lint and build are green; deployed

## 9. Prompt for the lead session (owner pastes this)
```
Read docs/designs/inside/INSIDE-BRIEF.md (D40). Record D40 in docs/designs/u-tax.md, brief the frontend lane with F43a–F43d and APPROVED, office first. Guardrails §0 are strict: skin only, no changes to theater/voice logic or test selectors, STOP stays instant, no invented numbers. Deploy by 09:15; at 09:15 revert anything broken. Rehearse at 09:30.
```
