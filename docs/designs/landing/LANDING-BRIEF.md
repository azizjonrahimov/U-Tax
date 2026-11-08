# D39 LANDING BRIEF — U-Tax site redesign, "Night Lime"

**For:** the controller (`naive-yc-hackathon-99`) → the `frontend` lane (or a second `site` lane, see §10).
**Written:** Sun Nov 8 2026, ~1:45 AM, by Claude (the owner's planning chat), against `frontend/` on disk at ~1:30 AM (Next 16.4.5, React 19.3, Tailwind 4, `components/site/Site.tsx`, `DESIGN.md` D32).
**Owner's words (verbatim):** "I also want our project's landing page and website design, UI/UX will look like this professional in this video, so also make something that tells claude code does make ours similar to with high professional design and UI/UX".
**Authority:** the owner's word overrides `DESIGN.md`'s "light paper landing" rule. The controller records D39 (§2) and updates `DESIGN.md` before the lane starts. This brief **is** the rule-5 plan: reply `APPROVED`.

**Files (all in the repo):**
| Path | What |
|---|---|
| `reference/landing-reference/reference-video.mp4` | the owner's video (17 s; the site is the middle band of a vertical clip) |
| `reference/landing-reference/frame-01-hero.png` … `frame-09-cta-footer.png` | full-width crops of every section, 1440 px wide |
| `docs/designs/landing/hero-prototype.html` | **a working U-Tax hero in this style, made tonight.** Open it in a browser. Its tokens, ribbon canvas, nav, badge, headline, CTAs and live-case card are the target. Port it; don't eyeball the video for the hero |
| `docs/designs/landing/hero-1440.png`, `hero-390.png` | how the prototype renders on desktop and phone |

---

## 0. Rules (read first)

1. **Copy the craft, not the brand.** The video shows a product called "Relay". Never use its name, its chevron logo, its copy, its people photos, its customer names or its numbers. Every word on our page is U-Tax's.
2. **No fabricated proof.** No testimonials, customer logos, people photos, "1,400 teams", star ratings or invented metrics. Every number on the page is either **live from our API** (`/api/state`, the public event stream) or a **fixed fact about the product** (e.g. "$85,165 → $450", the worked example in our design doc). If the API is unreachable, show `—` or hide the tile. Never fall back to a made-up number.
3. **Legal lines stay visible:** "Educational estimate, not tax advice". U-Tax files nothing (D36). No SSN/EIN, ever.
4. **The product is the illustration.** Like the reference, every visual is a product surface (case canvas, run log, approval card, charts), built in HTML/CSS/SVG/canvas. No stock art, no 3D libraries, no Lottie.
5. **Don't break what works:** `/signup`, `/login`, `/office`, `/talk`, `/ceo` keep their behavior. Phase 1 is `/` only; phase 2 (§9) re-skins the others by tokens.

---

## 1. What the reference does (frame by frame)

| Frame | Section | What to take from it |
|---|---|---|
| `frame-01-hero` | Nav + hero | sticky blurred nav; mono **status pill** with a glowing dot; lime pill CTA; mono **badge**; 2-line headline, **second line an italic serif word**; gray subhead; lime + ghost CTAs; mono fine print; a tilted **glass card** showing the product live (canvas of nodes + "last runs" log); **glowing light ribbons** sweeping behind, pinching to a bright point between text and card; faint dot grid |
| `frame-02-glue` | Section intro + feature 01 | **section label pill** ("• content service"); big sentence with one italic serif word; right-aligned gray paragraph; numbered feature row (`01` mono), left copy / right visual: a **run timeline** (mono step names, lime duration bars, ms axis) |
| `frame-03-approvals` | Feature 02 + 03 | a chat-style **approval card** (channel, bot line, key/value rows with a lime left rule, lime **Approve** + dark **Hold**, "waiting 26 min"); **isometric 3D bars**, gray with one lime, mono axis |
| `frame-04-band` | Kinetic band | a **full-width lime rounded band** with a dot grid; words animate in one by one; a dark app-icon tile centered |
| `frame-05-cards` | Two feature cards | **hub diagram** (central lime tile, 2-letter mono nodes on a line); **region bars** (mono labels, lime/gray progress, %), lock-icon footnote |
| `frame-06-bento` | Metrics bento | heatmap week grid; **82% ring**; avatar row + count; two **sine curves** (recovered vs failed); dot map. Then a blurred-in "case studies" header (**blur-to-sharp reveal**) |
| `frame-07-quotes-pricing` | Metric + quote columns, pricing intro | each column: small mono metric caption, a tiny chart (line / bar ticks / progress bar), a **mono tag chip**, then a quote. Pricing title with an italic serif "for seats" |
| `frame-08-pricing` | Pricing | three glass plan cards: mono plan name, big price, mono "a month", check-list rows, full-width pill button; the **middle card is highlighted** (brighter border, lime mono chip top-right, lime button whose dark fill **sweeps across on hover**); horizontal ribbons wave behind |
| `frame-09-cta-footer` | Final CTA + footer | split: **dark brand card** + **lime card** ("what now" mono kicker, headline with italic serif word, a pill email field with a dark button); a mono **status row** (three facts with dots); footer columns with mono headers; mono legal line |

**Motion seen:** the ribbons drift; sections fade up with a slight **blur→sharp**; band words land one by one; bars fill; ring counts up; the nav stays sticky over a dark blur.

---

## 2. Decision record (controller adds the row and edits `DESIGN.md`)

`| D39 | The site looks like the owner's reference: "Night Lime" (2026-11-08) | Owner: "I also want our project's landing page and website design, UI/UX will look like this professional in this video …". Auto-decided: **(1)** `/` becomes dark (overrides D32's light landing): near-black, a single lime action color, glowing light ribbons, glass product cards, an italic serif accent word in each headline, mono microcopy; **(2)** every visual is a live U-Tax surface bound to `/api/state` and the public event stream, with no invented numbers, testimonials or logos; **(3)** phase 2 re-skins `/signup`, `/login`, `/office`, `/talk`, `/ceo` by token swap; seat colors are kept as wayfinding; **(4)** reference files: `reference/landing-reference/`, target prototype: `docs/designs/landing/hero-prototype.html`. |`

**`DESIGN.md` edits (controller):**
- Front-matter `colors`: add the §3.1 tokens. `primary` → `#BEF243` (`on-primary` `#0B0D06`). `brand` → `#BEF243` (the "U" mark is now lime with dark text). Keep every `seat-*` and `room-*` value (the theater stays legible).
- Typography: display/heading → **Geist** 500 (tight tracking); accent → **Instrument Serif Italic**; mono → **Geist Mono** for site microcopy (the theater keeps JetBrains Mono).
- "Light or dark" paragraph: replace with "All public pages are dark (D39). Hierarchy comes from glass panels and borders, not lightness."
- Note: lime (`primary`) and the QA seat green (`#3DDC84`) are close. Lime is for **actions and highlights only**; the QA seat keeps green only as a cursor/badge color, and never sits on a lime surface.

---

## 3. The design system ("Night Lime")

### 3.1 Tokens (put in `src/app/globals.css` `:root` and Tailwind `@theme inline`)
```css
--bg: #050505;            /* page */
--bg-2: #0A0B09;          /* alternate section */
--panel: rgba(18,20,16,.72);  /* glass card, with backdrop-filter: blur(18px) */
--panel-solid: #0F110D;   /* when blur isn't supported */
--line: rgba(255,255,255,.08);  --line-2: rgba(255,255,255,.14);
--text: #F2F3EE;  --dim: #A1A39B;  --faint: #6F7169;   /* dim ≥ 7:1, faint ≥ 4.5:1 on --bg */
--lime: #BEF243;  --lime-2: #D4FF6A;  --on-lime: #0B0D06;  --cyan: #7CC7FF;  /* cyan: ribbons only */
--r-card: 20px;  --r-inner: 12px;  --r-pill: 999px;
--shadow-card: 0 40px 120px -40px rgba(0,0,0,.9), inset 0 1px 0 rgba(255,255,255,.05);
--glow-lime: 0 0 0 1px rgba(190,242,67,.4), 0 8px 30px -8px rgba(190,242,67,.55);
```
Sampled from the video: lime band `#BDF242`, CTA `#BCF240`, page `#000–#0B0B0B`, field `#F6F6FA`.

### 3.2 Type (`next/font/google` in `src/app/layout.tsx`)
- `Geist` (400/500/600) → `--font-sans`. Headlines **500**, `letter-spacing: -0.035em`, `line-height: 1.02`. Hero `clamp(42px, 4.9vw, 72px)`; section titles `clamp(32px, 3.6vw, 52px)`; card titles 22–26px.
- `Instrument_Serif` (400, `style: "italic"`) → `--font-serif`. **Exactly one word or short phrase per headline**, never body text.
- `Geist_Mono` (400/500) → `--font-mono` for pills, badges, labels, logs, axis ticks, fine print (12–13px, lowercase, `/` as separator).
- Body 17px, `--dim`, `line-height: 1.55`, max 52ch.

### 3.3 Layout
- Container `max-width: 1280px`, side padding 64px (≥ 960px) / 16px (phone). Section spacing 160px desktop / 96px phone.
- Section pattern: label pill → title (left) + paragraph (right, `--dim`) → content grid (12 cols, 24px gap).
- Numbered rows: `01` mono `--faint` above a 26px title; copy left (5 cols), visual right (7 cols); alternate sides.

### 3.4 Components (build in `src/components/site/ui/`)
| Component | Spec |
|---|---|
| `StatusPill` | mono 12.5px, 1px `--line-2`, pill, dark fill, dot 7px lime with glow. Live text from `/api/health`+`/api/state`: "firm open · 7 agents" / "firm resting" (offline) |
| `Badge` | mono 12.5px, 8px radius, square dot |
| `SectionLabel` | pill, `--panel`, mono 12.5px, round lime dot |
| `Button` | `lime` (pill, `--glow-lime`, hover lift 1px) and `ghost` (6% white fill, `--line-2`); 44px min height |
| `GlassCard` | `--panel`, `--line`, `--r-card`, `--shadow-card`, `backdrop-filter: blur(18px)` (fallback `--panel-solid`) |
| `Headline` | `({ lead, accent, tail })` renders the serif italic accent |
| `MonoTag` | the bordered mono chip ("measured across …" style) |
| `RunTimeline` | rows: check icon, mono step, bar (lime done / gray pending), mono duration, axis |
| `IsoBars` | SVG isometric prisms (top rhombus + two faces), gray faces, lime for the max; mono day/department labels |
| `Ring` | SVG circle, lime stroke with round caps; the number counts up on view |
| `SineCompare` | two SVG sine paths (lime + gray) with node dots |
| `Heatmap` | 7×N grid of rounded rects, gray, the max in lime |
| `HubDiagram` | central lime "U" tile, 2-letter mono nodes on hairlines, one dot travelling along a line |
| `LimeBand` | full-width lime rounded (28px) band, dot grid (`radial-gradient` on `--on-lime` at 8%), kinetic words |
| `Ribbons` | the canvas from the prototype (§5) |

### 3.5 Motion (no animation library; CSS + `IntersectionObserver`)
- **Reveal:** `opacity 0→1`, `translateY 14px→0`, `filter blur(8px)→0`, 0.9s `cubic-bezier(.2,.7,.2,1)`, stagger 60–120ms per child. Fires once at 15% visibility.
- **Kinetic band:** words land one by one (120ms apart), each from `blur(6px)` + 20px below.
- **Counters:** numbers count up 900ms on first view, using tabular figures (`font-variant-numeric: tabular-nums`).
- **Bars and rings** fill from 0 on view (700ms).
- **Hover:** cards lift 2px and brighten the border to `--line-2`; buttons lift 1px.
- `prefers-reduced-motion: reduce` → everything static, ribbons drawn once, counters show final values.

---

## 4. The page `/`, section by section (U-Tax content)

Keep the existing live wiring in `Site.tsx` (it already reads `money.tax_saved_cents` and `firm.clients` from `/api/state` with the public theater token) and its existing link targets for signup and the live theater. Reuse them; don't invent new endpoints.

**S1 Nav** (`frame-01`): `[U] U-Tax` · Product · How it works · The firm, live · Pricing · `StatusPill` · **Start free check** (→ the existing signup route). Sticky, blurred. Phone: logo + CTA only.

**S2 Hero** (`frame-01`, prototype = target):
- Badge: `live tonight / {firm.clients} startups checked` (offline: `open beta / delaware franchise tax`)
- H1: "The tax firm runs / *itself*."
- Sub: "Seven AI departments find the client, check the math twice and send the memo. One human signs off when it matters."
- CTAs: **Get your free check** (signup) · **▶ Watch the firm work, live** (the existing live-theater link)
- Fine print: `free / educational estimate, not tax advice / no SSN, ever`
- Card: the latest real case. Header `Delaware franchise tax · 5 steps / 1 branch · running|done`. Nodes: Email received (intake) → `$85,165 → $400` (tax · engine, from the case's `result`) / Form 5472 flag (tax · rule, lit only if flagged) → QA → memo. **Last memos:** the 3 latest `memo.sent` from the public stream (masked "team #n", time, seconds from `seconds_since_last_fact`). If D38 is live, a `payment.sent` row ("share card · paid PixelAgent · 0.50") may appear.

**S3 "Most of tax is *paperwork*."** (`frame-02`)
- Label: `how it works`
- Right paragraph: "Read the email. Find the numbers. Do the math twice. Write the memo. U-Tax takes the paperwork and leaves you the decision."
- **01 One case, every step:** "A case moves from inbox to memo on its own. If a number is missing, Intake asks; if QA disagrees, nothing is sent." Visual `RunTimeline` from one real case's events (`email.received → facts.extracted → law.searched → tax.computed → qa.passed → memo.sent`), with durations in seconds from the event timestamps.

**S4 "02 Approvals where the CEO already is"** (`frame-03`, top)
- Copy: "The first send of every kind waits for a spoken yes. After that it runs on its own, inside the limits the CEO set."
- Visual: an approval card styled like the reference (a "#ceo" channel, a "U-Tax" line). Example: "First LinkedIn post of the night. Send it?" with rows `action social_post` / `rule first one needs a human` / `then AUTO within 10/hr`, **Approve** / **Hold**, `waiting 2 min`.
- Caption chip: `how approvals look` (it's an illustration of a real flow, not a live record).

**S5 "03 Every number is on the record"** (`frame-03`, bottom)
- Copy: "Every step is an event: who did it, when, and what it cost. Replay any case before you trust it with money."
- Visual `IsoBars`: **actions per department tonight** from public `tool.called` counts by actor (cos, intake, tax, qa, acct, mkt, hr); the max in lime; caption `actions tonight · {total} total`.

**S6 Lime band** (`frame-04`)
- Kicker: `[ the bill we see most ]`
- Kinetic line: "$85,165" (struck through) "→ $450."
- Small: "Same company. Same shares. The other legal method."
- A dark `U` tile in the center.

**S7 Two cards** (`frame-05`)
- **"One gateway, every tool"**: "Agents never hold a key. Every email, post, payment and booking goes through one gateway that checks it first." `HubDiagram` around the U tile, nodes `AM agentmail`, `ST stripe`, `HS hubspot`, `OR openrouter`, `AP apify`, `MR mobilerun` (text initials only, no brand logos). Footnote `🔒 keys stay in the gateway`.
- **"Where tonight's work went"**: bars per department share of `tool.called` (%), mono labels, lime for the top one. Footnote: `counted from the event log`.

**S8 Bento** (`frame-06`), all live, each tile with a `live` mono chip:
- `Ring`: **% of steps with no human**, = 1 − operating touches ÷ `tool.called` count
- Big counter: **franchise tax saved** = `money.tax_saved_cents`
- Avatars → **department dots** (7 seat-color dots) + "7 departments, 1 human"
- `SineCompare`: recoveries vs failures (`naive.recovered` vs failed tool calls), or hide if 0
- `Heatmap`: events per hour tonight

**S9 "What the agents *actually* wrote"** (replaces the reference's testimonials, `frame-07`)
- Three columns, each a real public artifact with its department dot and time: the latest memo **subject** (masked), the latest **post text**, the latest **QA check line**.
- Above each, a tiny chart (line = memos over time, bar ticks = posts, progress = QA pass rate) and a `MonoTag` (`from the event log`).
- **No people, no quotes from humans.**

**S10 Pricing: "You pay for *answers*, not seats."** (`frame-07/08`)
- Three plan cards:
  - **Free check**, $0: memo + workpaper, educational estimate
  - **Filing help**, $49: worksheet + a 20-minute call; you file it yourself (D36 wording)
  - **For agents**, 0.50 pathUSD per check, paid by machine (MPP); show only if D38 41d merged, else "for agents: soon"
- Layout as `frame-08`: three glass cards, check-list rows, full-width pill buttons. **Filing help** is the highlighted middle card, with the lime chip `recommended` (never "most picked" or anything that claims usage we can't show) and the hover-sweep lime button.
- No estimator slider (it would need invented math).

**S11 Final CTA** (`frame-09`)
- Dark card: U-Tax mark + "An accounting firm run by agents, with every step on the record." + mono `built at the YC Naïve hackathon`
- Lime card: kicker `what now`; "Bring the bill you *dread* most."; "Send your Delaware numbers. If U-Tax can't check them tonight, it will tell you so."; a pill email field + dark **Start free check** button. It opens the existing signup flow (prefill the email); keep its validation and rate-limit messages.

**S12 Footer** (`frame-09`)
- Mono status row: `● firm open` · `● {memos} memos sent tonight` · `● educational estimates only`
- Columns: Product (Free check, The firm live, Pricing) · Company (About, Built at YC) · Legal (Not tax advice, Privacy)
- Mono line: `© 2026 U-Tax · U-Tax files nothing; estimates are educational`

---

## 5. Ribbons (port from `hero-prototype.html`)
- A Canvas 2D component `Ribbons.tsx` (`"use client"`), absolutely positioned behind the hero (and a dimmer copy behind the S10/S11 transition, as in the reference).
- **Algorithm (from the prototype):** a wide blurred beam (`filter: blur(28px)`, 60px line, lime at 10%); then 120 bezier strands with `globalCompositeOperation = 'lighter'` and a glow (`shadowBlur` 16). Each strand comes in from the top-right, sweeps to a pinch point at (50% w, 74% h), and exits bottom-right. Alpha and width are highest at the bundle center. About 10% of strands are cyan `#7CC7FF`, the rest lime / yellow-lime. A radial highlight sits at the pinch. Slow drift: `sin(t·0.0006 + seed)·26px`.
- **Performance:** device pixel ratio capped at 2; pause on `visibilitychange` and when off-screen (`IntersectionObserver`); draw once under reduced motion; on phones, canvas `opacity: .35`. Target 60 fps on an M1 and ≥ 30 fps on a mid phone. If a frame takes > 20 ms for 30 frames in a row, halve the strands.
- No WebGL and no libraries.

## 6. Responsive
- ≥ 1200: as specified.
- 960–1199: hero columns `1.12fr 1fr`, font clamps shrink.
- < 960: single column; nav = logo + CTA; CTAs stack full width; the hero card hides its node canvas and keeps the header + last memos; bento → 1 column; numbered rows stack visual under copy; lime band text `clamp(36px, 9vw, 56px)`.
- No horizontal scroll at 320 px. Test at 390×844 and 1440×900.

## 7. Quality bars
- **Accessibility:** contrast AA (body `--dim` on `--bg` ≥ 7:1, small mono `--faint` ≥ 4.5:1, `--on-lime` on lime ≥ 12:1); visible focus ring (2px lime + 2px offset); every chart has an `aria-label` summary sentence; ribbons and decoration are `aria-hidden`; real buttons and links; keyboard order follows layout.
- **Performance:** LCP < 2.5 s (the hero text is the LCP, not the canvas); CLS < 0.05 (reserve card heights); landing JS ≤ 120 KB gz beyond Next's runtime; fonts with `display: swap` and only the weights listed.
- **States:** the API is down → every live tile shows `—` with "the firm is resting" and the page still looks complete; loading → skeleton lines at 6% white.
- **SEO:** keep the existing `metadata` title/description; add an Open Graph image later (not tonight).

## 8. Honesty and legal guardrails (re-read before READY)
- Search the diff for numbers: every one is live, the fixed worked example ($85,165 / $400 / $450 / $50), or a product setting ($49, 0.50, 10/hr, 7 departments).
- No people, quotes, ratings, customer names or third-party logos (the hub nodes are text initials).
- "Educational estimate, not tax advice" appears in the hero fine print, pricing and the footer.

## 9. Phase 2 (only after `/` is merged and if there's time before 06:30)
- Apply the tokens to `/signup`, `/login`: dark glass form cards, lime primary button, Geist/Instrument Serif headline ("Start your *free* check").
- `/office` shell and department workspaces, `/talk`, `/ceo`: swap the primary color to lime, panels to glass, and labels to Geist Mono where they're microcopy. **Don't** change seat colors, theater layout or animation timing (rehearsed demo).
- Re-run `/design-review` on each.

## 10. Task plan (frontend; F42)
Recommendation to the controller: open a **second frontend lane `site`** (worktree `.worktrees/site`, branch `lane/site`, owns `frontend/src/components/site/**`, `frontend/src/app/page.tsx`, `frontend/src/app/layout.tsx` fonts block, and `frontend/src/app/globals.css` tokens block), so it runs in parallel with `frontend`'s D38 Market pane (F41). If only one frontend lane is possible: F41 first (it's in the live demo), then F42.

| Task | Box | Done when |
|---|---|---|
| F42a tokens + fonts + `ui/` primitives (Pill, Badge, Label, Button, GlassCard, Headline, MonoTag) | 40 min | the tokens are in `globals.css`; the fonts load; a primitives story page renders |
| F42b Nav + Hero + `Ribbons` (port the prototype exactly, bind live data) | 60 min | pixel check vs `hero-1440.png` / `hero-390.png`; live badge and last memos work; offline state OK |
| F42c S3–S5 (RunTimeline, approval card, IsoBars) | 50 min | data from the public stream; reduced motion OK |
| F42d S6–S8 (LimeBand, two cards, bento) | 50 min | counters/ring/bars animate once; `—` offline |
| F42e S9–S12 (artifacts, pricing, CTA split, footer) | 40 min | the signup prefill works; legal lines present |
| F42f QA | 30 min | checks below green |

**Checks before READY:**
- `npm run lint`, `npm run build`, `npm run test`
- gstack `/design-review` with the reference frames: score each section against its frame, fix anything below 8/10
- gstack `/qa` on `/` at 1440×900 and 390×844, with screenshots attached to the READY
- Lighthouse (desktop + mobile) for performance and accessibility ≥ 90
- §8 honesty search
- then redeploy with `crew/scripts/deploy-frontend.ps1`

**Time box overall:** 4.5 h. If behind at 05:30: ship S1–S2 + S6 + S10–S12 (hero, band, pricing, CTA, footer); hide S3–S5/S7–S9 behind a flag; finish after the demo.

## 11. Acceptance checklist
- [ ] At first glance at 1440 px, `/` reads like `frame-01`: black, lime CTA and status pill, italic serif "itself.", glowing ribbons pinching between text and card, a live glass card
- [ ] Each section has its matching frame's structure (label pill → serif-accent title → right paragraph → product visual)
- [ ] All numbers live or fixed facts (§8); offline shows `—`
- [ ] 390 px phone: no horizontal scroll, readable over ribbons, CTAs full width
- [ ] Reduced motion: static and complete
- [ ] Lighthouse ≥ 90 / ≥ 90; LCP < 2.5 s
- [ ] `/signup`, `/login`, `/office`, `/talk`, `/ceo` behave exactly as before

## 12. Prompt for the lead session (owner pastes this)
```
Read docs/designs/landing/LANDING-BRIEF.md. Record D39 and update frontend/DESIGN.md per §2, then open a `site` lane (§10, or queue it after F41 if you can't) and brief it with F42a–F42f and APPROVED. Port docs/designs/landing/hero-prototype.html exactly for the hero; follow §0 and §8 strictly (no invented numbers, no testimonials, nothing from the reference brand).
```
