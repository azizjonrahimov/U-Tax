---
# gstack: design-md-format=spec
name: U-Tax
description: "A calm, exact ledger that shows its work: light paper for clients, a dark control room for the firm, and every number set like a receipt."
colors:
  paper: "#F6F7F9"
  surface: "#FFFFFF"
  ink: "#0E1420"
  ink-muted: "#4A5568"
  ink-faint: "#6B7280"
  line: "#DDE2EA"
  primary: "#0B6E4F"
  on-primary: "#FFFFFF"
  primary-hover: "#095C42"
  brand: "#E5372A"
  on-brand: "#000000"
  warning: "#9A5B00"
  error: "#B42318"
  room-bg: "#07070B"
  room-panel: "#0F0F16"
  room-panel-2: "#151520"
  room-line: "#24243A"
  room-text: "#ECEBF5"
  room-dim: "#A3A2BA"
  room-faint: "#85849F"
  room-primary: "#3DDC84"
  room-on-primary: "#000000"
  seat-cos: "#FF4D3D"
  seat-intake: "#4DA3FF"
  seat-tax: "#FFB547"
  seat-qa: "#3DDC84"
  seat-acct: "#C77DFF"
  seat-mkt: "#FF6FB5"
  seat-hr: "#2EE6D6"
typography:
  display:
    fontFamily: Schibsted Grotesk
    fontWeight: 800
    fontSize: clamp(2.25rem, 4.2vw, 4rem)
    letterSpacing: -0.02em
  heading:
    fontFamily: Schibsted Grotesk
    fontWeight: 700
    fontSize: 1.5rem
    letterSpacing: -0.01em
  body:
    fontFamily: Inter
    fontSize: 1rem
    lineHeight: 1.55
  label:
    fontFamily: Inter
    fontWeight: 600
    fontSize: 0.75rem
    letterSpacing: 0.06em
  mono:
    fontFamily: JetBrains Mono
    fontFeature: tnum
rounded:
  sm: 4px
  md: 8px
  lg: 12px
  full: 9999px
spacing:
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  2xl: 48px
  3xl: 80px
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.on-primary}"
    rounded: "{rounded.md}"
  button-primary-hover:
    backgroundColor: "{colors.primary-hover}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
  input:
    borderColor: "{colors.ink-faint}"
    rounded: "{rounded.sm}"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
  nav-link:
    textColor: "{colors.room-dim}"
  workspace-window:
    backgroundColor: "{colors.room-panel}"
    rounded: "{rounded.lg}"
---

# U-Tax

## Overview

**Creative North Star:** an accounting firm that shows its work. Clients see a calm, exact ledger; the CEO and the audience see the firm's control room, where every department drives its tools in plain view and every figure reads like a receipt.

**Product context:** U-Tax is an accounting firm run by seven agent departments (Chief of Staff, Intake, Tax, QA, Accounting, Marketing, HR). The first product is a free Delaware franchise-tax checkup for startups. A typical case turns an $85,165 bill into $450. Peers are fintech SaaS for founders (Mercury, Ramp, Brex, Stripe). The project is a marketing site plus an operating app plus a projector view.

**Mode per surface:**
- `/` landing: Persuade.
- `/signup` and `/login`: Operate (short forms).
- `/office` shell and department workspaces: Operate and Experience. They are recorded for the demo video.
- Theater (`/office` Overview): Experience, on a projector.

**Reference sites (research, 2026-11-07, via web search; no screenshots taken):**
- Mercury is the category's design benchmark: calm, product-led, the dashboard shown instead of illustrated.
- Ramp quantifies its claims and shows the math rather than saying "save money".
- Brex: calm colors, sharp sections, a clearly stated disclaimer.
- Sources: designrevision.com/blog/fintech-saas-landing-pages, blendb2b.com/blog/best-fintech-website-examples, utsubo.com/blog/fintech-website-trust-design-patterns, landingdoctors.com/teardowns/mercury-com.

**Key characteristics:**
- The number is the hero: "$85,165 → $450", set in tabular mono, with the old figure struck through. That is Ramp's "show the math", applied to our one real example.
- The product is shown, never illustrated: the landing page reads the live firm (the tax-saved counter from `/api/state`), and the office is the firm working.
- Two rooms, one family: light paper for clients (daytime laptops), the dark control room for the firm (projector, video). They share faces, scale and the seat colors.
- Seat colors are wayfinding: each department keeps the cursor color it has in the theater, everywhere it appears.

## Colors

**Strategy:** Restrained.
- One action color (ledger green, `primary`) plus ink neutrals.
- Green means money kept and the next step. The brand red is the logo mark and nothing else.
- Seat colors appear only as department identity (sidebar dots, badges, cursors), never as decoration.

**Light or dark:** decided by the use scene.
- Clients reach `/`, `/signup` and `/login` on a laptop, usually in daylight, so those pages are light (`paper`, `surface`, `ink`).
- The office is watched on a projector and recorded for the demo video, beside the existing theater, so it is dark (the `room-*` tokens are the theater's own values).
- Dark surfaces keep hierarchy through three panel steps (`room-bg` < `room-panel` < `room-panel-2`) and borders (`room-line`), not by inverting lightness. `room-dim` and `room-faint` both pass AA on `room-panel` (7.7:1 and 5.3:1).

**Contrast (checked):**

| Pair | Ratio |
|---|---|
| `ink` on `surface` | 18.4:1 |
| `ink-muted` on `surface` | 7.5:1 |
| `ink-faint` on `surface` | 4.8:1 |
| `on-primary` on `primary` | 6.3:1 |
| `primary` on `paper` | 5.8:1 |
| `room-primary` on `room-panel` | 10.7:1 |

White on `brand` is only 4.3:1, so the logo mark uses `on-brand` (black) text, as the theater does.

## Typography

- **Display and headings:** Schibsted Grotesk (Google Fonts, SIL OFL 1.1, variable; loaded with `next/font/google`). It's a news-brand grotesk from Bakken & Bæck: confident and exact, never cute. Used at 700–800 for headlines and section heads.
- **Body and UI:** Inter.
  - Inter is on the overused-as-display list, so it never sets a headline here.
  - It stays as the body and UI face because the theater (the Overview) already runs on it. These are Operate surfaces, and one UI face across the whole product beats a second one.
- **Mono:** JetBrains Mono with tabular figures, for every amount, count, tool name and address bar. A figure always lines up like a receipt.
- **Scale:**
  - Display: clamp(2.25rem, 4.2vw, 4rem).
  - Heading: 1.5rem.
  - Body: 1rem.
  - Label: 0.75rem, uppercase, tracked 0.06em, for small UI labels only (window chrome, table heads). Never a kicker above a headline.
  - The hero figure ($85,165 → $450) may reach 4rem in mono. No display text goes past 4rem.

## Layout

- **Landing (hybrid, creative):**
  - The first viewport is the claim plus the math: headline, the struck-through figure, two CTAs that name their outcome ("Get my free checkup", "Talk to U-Tax"), and a live line from the firm.
  - Then "how it works" as the seven departments in order, as one horizontal run (a pipeline), not a card grid.
  - Max width 1120px, gutters 24px (16px on phones).
- **Office (grid-disciplined):**
  - A 248px left sidebar lists the departments in the firm's order: Overview, Chief of Staff, Intake, Tax, QA, Accounting, Marketing, HR, then the CEO console.
  - The main area holds one department workspace: the window takes about two thirds of the width, with the step list and KPIs as a column on its right.
  - Below 900px the sidebar becomes a horizontal strip, and the KPIs fall under the window.
- **Spacing:** 4px base. Use the token steps above. Sections on the landing page breathe at 80px; the app works at 16–24px.

## Elevation & Depth

- **Light:**
  - Surfaces sit on `paper` with a 1px `line` border.
  - The one raised object, the sign-up card, gets `0 1px 2px rgba(14,20,32,.06), 0 8px 24px rgba(14,20,32,.08)` (offset and soft blur).
- **Dark:** depth comes from the panel steps and borders. The workspace window adds an offset shadow (`0 12px 32px rgba(0,0,0,.45)`). No zero-offset colored glow anywhere: an active seat shows its color as a fill or a dot, not a halo.

## Shapes

- **Radii:** sm 4px for inputs, chips and tabs; md 8px for buttons and sidebar items; lg 12px for windows and the sign-up card. Full rounding only for status dots.
- **Nesting:** a tab inside the window uses 12px − 4px = 8px at its top corners.
- **No cards inside cards.** The workspace window is the one container on its side.

## Components

- **Buttons:**
  - Primary: `primary` fill, `on-primary` text, 8px radius, 44px tall. Hover `primary-hover`. Focus-visible is a 2px `ink` outline with a 2px offset (a `room-text` outline on dark). Disabled is 45% opacity with a not-allowed cursor.
  - Secondary: `surface` fill, 1px `line` border, `ink` text.
  - Labels name the outcome: "Get my free checkup", never "Get started".
- **Inputs:**
  - 44px tall, 1px `ink-faint` border, 4px radius. A visible label sits above, never a placeholder used as the label.
  - Error: an `error` border, plus a message under the field tied by `aria-describedby`.
  - Focus: a 2px `primary` outline.
- **Sidebar item:**
  - The seat dot, then the name, with a live status on the right ("working", "idle", a count).
  - Active: `room-panel-2` fill and `room-text`.
  - When auto-follow opens a department, its item pulses once (a 600ms fill fade), and an "Auto-following" chip shows in the header; any click turns auto-follow off.
- **Workspace window (the hero):**
  - Title bar: tabs (one per tool the department used, the newest active) and an address bar in mono naming the tool or site ("AgentMail · inbox", "utax · compute_tax", "X · compose").
  - Body: the tool's view drawn from events.
  - Cursor: in the seat color, it travels to the element each step touches, presses, and types.
  - Empty state: "Waiting for <department>'s next step", with the last step's time.
  - Error state: a `tool.called` with status `error` or `refused` shows as a red note in the window, never hidden.
- **Step list:** each `tool.called` or domain event as one line (time, verb phrase, status icon). The newest is on top and highlighted, and a step needing approval shows "Waiting for your OK".
- **KPI tiles:** a figure in mono with a plain label underneath. Real counts from `/api/state` and the log only. With no data the tile shows "—", never a fake sparkline.

## Do's and Don'ts

- **Do** show a real figure or a real step wherever an illustration might go. The firm's log is the art.
- **Do** keep each department's seat color identical everywhere: cursor, dot, badge, window accent.
- **Do** put the disclaimer "Educational estimate — not tax advice" in the landing footer and under every figure that's an estimate.
- **Do** design the empty, loading and error states of every workspace and form.
- **Do** honor `prefers-reduced-motion`: cursors jump instead of glide, with no pulse.
- **Don't** add a feature-card grid with icons in colored circles. The departments run as one pipeline.
- **Don't** use purple-to-blue gradients, glowing halos, gradient text or gradient buttons.
- **Don't** put a pill label or kicker above the hero headline.
- **Don't** use invented numbers (users, stars, testimonials). The only social proof is the live tax-saved counter.
- **Don't** fake a workspace step. If no event happened, the window waits.

## Motion

- **Approach:** intentional. Motion explains what an agent did. It never decorates.
- **Easing:** enter ease-out; exit ease-out; move ease-in-out (the cursor).
- **Duration:** micro 80ms (press), short 200ms (tab switch, step entering), medium 350ms (window content change), long 600ms (cursor travel, the sidebar pulse). The theater's `--t` time scale applies.
- **The one authored moment:** an agent at work. Its cursor crosses the window to the field the step touched, presses, and the text types in at reading speed. At the same moment the step lands in the list and the department's sidebar dot lights up.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-11-07 | Initial design system created | Created by /design-consultation for D32 from the product context, the existing theater tokens and fintech landing-page research (Mercury, Ramp, Brex). The owner gave standing approval to auto-decide its questions, so each took the recommended option. The HTML preview was skipped for the 3 h timebox: the built pages plus /design-review serve as the preview. |
| 2026-11-07 | Light for client pages, dark for the office | The use scene: founders on daytime laptops, versus the projector and recorded video. |
| 2026-11-07 | Ledger green as the only action color; brand red as the logo only | Green reads as money kept. White on red fails AA. |
| 2026-11-07 | Risks taken | (1) The hero is a figure set in mono, not an illustration. (2) The landing page shows the live firm (a counter and a line from the log). (3) Seat colors as wayfinding across theater, office and badges. |
