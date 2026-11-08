# D41 FIXES RUNBOOK: design fidelity, multi-task orders, the department tour, email, payment history

Written 2026-11-08 07:20 local, from the owner's 06:59 request, after reading the code on `main`
and `backend/data/events.jsonl` (625 events, last 15:04Z).

The work has five parts, in the owner's priority order:
- **A** Design: `/ceo` and `/office` must look like `docs/designs/inside/ceo-1440.png` and `office-1920.png`.
- **B** Multi-task orders: when the CEO says 3–4 tasks at once, the firm must capture all of them and do them one by one.
- **C** The department tour: each task is done in its own department panel. The office clicks that department in the left sidebar, stays while it works, then moves on.
- **D** Email: the firm talks with customers by email, and you can see it.
- **E** Payment history: payments to the outside agent show in the office, in the CEO console, and in Stripe.

The reference code in `docs/designs/fixes/reference/` is tested; copy it, don't retype it:
- `split_tasks.py` with 17 tests
- `order_queue.py` with 4 tests
- `tour.ts` with 3 vitest tests

The gate script is `crew/scripts/proto-classes.mjs`.

---

## 0. Rules for this run (read first)

1. **Time.** Hacking stops at 11:00; our freeze is **10:00**.
   - Feature merges stop at **09:30**.
   - Full rehearsal runs 09:30–09:50 (§G).
   - Anything not READY by 09:30 is **cut**, not rushed. Each part has a cut line.
2. **Revert point.** Before the first D41 merge, the controller runs `git tag pre-d41 main`.
   - To undo one feature: `git revert -m 1 <its merge sha>`.
   - The older point `6820dc8` still stands.
3. **Lanes and order of landing.** Lanes and worktrees stay as in `crew/CREW.md`.
   - **Backend B51 lands and the gateway restarts before agents A43 runs `naive up`.** The prompts call `utax.task_done`, which must exist first.
   - Frontend can build everything against fixture events before the backend lands.
4. **Never break the rehearsed path.**
   - A single order still wakes the Chief of Staff with exactly `CEO order_<id>: <words>`.
   - The fast path, approvals, STOP (Esc), sign-in, the docked console panel and `?d=` / `?source=` keep working.
   - Every new piece fails closed and quietly (logs, no 500s).
5. **Honesty rules stand.**
   - No invented numbers or customers.
   - No cold email to strangers.
   - Testnet tokens are never shown as dollars.
   - A Stripe record that mirrors a Tempo payment says "mirror" everywhere it appears.
6. Run `CHECKS` before every READY.
   - Frontend: `npm run lint`, `npm run build`, `npx vitest run`.
   - Backend: `pytest -q`.
   - Agents: `npx tsc --noEmit` and `naive up --dry-run`.

### Plan at a glance

| Id | Lane | What | Box | Needs | Cut line (if late) |
|---|---|---|---|---|---|
| F44 | frontend | fonts (Geist, Geist Mono, Instrument Serif) | 15 min | – | never cut |
| F45 | frontend-voice | `/ceo` page ported from the prototype DOM | 70 min | F44 | keep the old layout with the new fonts |
| F46 | frontend | `/office` Overview ported, panel closed by default | 60 min | F44 | keep the sidebar and header port only |
| B51 | backend | multi-task orders: split, merge, queue, `task_done`, `task_no` stamping | 75 min | – | never cut (it is the core bug) |
| A43 | agents | CoS task rules, `task_done` in firm rules, assistant `tasks[]`, "email our clients" row | 40 min | B51 contract | – |
| F47 | frontend-voice | console: task stepper on order cards, read-back waits for silence | 35 min | B51 events | keep the stepper, drop the debounce |
| F48 | frontend | the department tour (`tour.ts`, sidebar press, TaskBanner) | 50 min | B51 events (fixtures first) | tour without the press animation |
| B52 | backend | `GET /api/payments` | 20 min | – | – |
| B53 | backend | Stripe test-mode mirror of each contractor payment, plus backfill | 30 min | B52 | UI shows "Stripe: not recorded" honestly |
| F50 | both | payments ledger: Accounting desk tab, Overview rail row, console card | 40 min | B52 | console card only |
| F49 | frontend | Intake "Inbox" (email threads) and Mail pane seeded from history | 35 min | – | last-email line in the Mail pane only |
| D-owner | owner | live email round trip from a personal mailbox (§D.4) | 5 min | – | – |
| B54 | backend | stretch: real Stripe-recorded MPP payment to PixelAgent | 25 min | Stripe profile | skip unless everything else merged by 09:15 |

---

## 1. Root causes (evidence, so nobody fixes the wrong thing)

### Problem A. The pages don't look like the images

D40 applied the colors but **never ported the layouts**, and the fonts the CSS asks for are never loaded.

1. **Fonts.**
   - `frontend/src/app/layout.tsx` loads only `Inter`, `JetBrains_Mono` and `Schibsted_Grotesk`.
   - `globals.css`, `ceo.css` and `office.css` use `var(--font-geist)`, `var(--font-geist-mono)` and `var(--font-instrument-serif)`, which are **never defined**.
   - So every page renders in Inter, and the serif accent ("The firm, *live.*") falls back to **Georgia**.
2. **`/ceo` structure.**
   - `Ceo.tsx` still renders the old single column (`header` → `main` → stacked `section`s).
   - 57 of the prototype's 72 classes don't exist in the code, among them `app top cmd rail nav stop hero kpis orb appr kan side feed spend`.
   - Run `node crew/scripts/proto-classes.mjs` to see the list.
3. **`/office` structure.**
   - 32 of 58 prototype classes are missing (`head orders ord stage rail bar bottom livepill funnel thumb …`).
4. **The docked console panel.**
   - On a screen ≥ 1600 px wide, `/office` **opens the docked CEO panel on the first visit** (`Office.tsx`: `setPanelOpen(saved ? saved.open : window.innerWidth >= 1600)`).
   - It takes ~380 px. `office-1920.png` has no panel, so at 1920 the page can never match.

### Problem B. Only the first of several tasks is captured

In the log, every CEO order is a single task (`voice.command` ids 78, 123, 298, 319, 406, 479, 495, 573). For example, 573 is "So getting an outside designer to make a shared card." It was cut at the first pause. Three causes stack:

1. **Speech is cut at the first pause.**
   - The console's speech-to-text sends each utterance as soon as the speaker pauses.
   - The first sentence becomes an order proposal, and its read-back starts speaking while the CEO is still talking.
2. **A newer proposal kills the older one.** `perform._propose` cancels every open proposal with reason `superseded` (id 120 shows this).
   - So "Advertise the firm." … "Hire two people." leaves one proposal.
   - The voice model calling `utax.order` twice does the same.
3. **There is no notion of tasks.**
   - `free_order` accepts 3–300 characters as **one** order (the typed command field caps at 600).
   - `pin_order` pins one `text`, and the Chief of Staff makes "one card per department, all at once" (in parallel).
   - Nothing runs tasks in sequence, and nothing says a task is finished.

There is also a side bug: the STT invents non-English text from room noise (ids 532, 538, 567: Urdu, Amharic, Korean).

### Problem C. All work shows in one window

`/office` auto-follow (`office/follow.ts`) jumps to whichever desk acted last, at most every 6 s. With parallel cards, it bounces between desks, and the Overview shows everything at once.

Two pieces don't exist yet:
- a "current task" the office can follow
- a "task finished" signal it can move on from

### Problem D. "Not sending emails / not talking to customers"

Sending works. AgentMail sent seven client emails successfully:
- `client.asked` (id 21)
- `memo.sent` (ids 50, 260)
- `client.followed_up` (id 304)
- the hiring outreach mails

Inbound mail (ids 4, 23, 208) was answered. The real gaps:

1. **You can't see it.**
   - The theater only animates events that arrive **after** you open the page (`script.ts` starts at `last_event_id`).
   - So the Mail pane says "Inbox is quiet" even though there is a full thread.
   - No screen lists the email conversations.
2. **There is only one customer** (`case_1`, Team #1).
   - Nobody new has written to the firm inbox, and the firm (correctly) doesn't cold-email strangers.
3. **There is no order that emails our customers.**
   - The Chief of Staff's order table has no row for "email our clients", so such an order produces nothing.

### Problem E. Payments are invisible, and nothing is in Stripe

The log has three real `payment.sent` events: 0.50 pathUSD each on Tempo Moderato testnet. Two were confirmed on-chain from here (status `0x1`, blocks 43176646 and 43185016). There is also one `agent.sale`. But:

1. They appear only as a step inside an order card, and in the Market pane while it animates. There is **no ledger** anywhere.
2. **Nothing reaches Stripe.** The contractor payment is pure Tempo MPP.
   - The Stripe-recorded MPP path needs a Stripe **profile id** (`profile_test_…`), which the account doesn't have yet.
   - Stripe's own docs: create a profile, then `GET /v2/network/business_profiles/me`.

---

## A. Design fidelity: port the prototypes, don't restyle

**Principle:** the prototype HTML is the spec. Copy its DOM (tags and class names exactly) and its CSS into the components, then bind the live data and the **existing** handlers. Don't redesign, don't rename classes, and don't keep old layout wrappers around the new ones.

### F44: fonts (15 min, frontend lane, first)

In `frontend/src/app/layout.tsx`:

```tsx
import { Geist, Geist_Mono, Instrument_Serif, Inter, JetBrains_Mono, Schibsted_Grotesk } from "next/font/google";
const geist = Geist({ subsets: ["latin"], variable: "--font-geist", display: "swap" });
const geistMono = Geist_Mono({ subsets: ["latin"], variable: "--font-geist-mono", display: "swap" });
const instrumentSerif = Instrument_Serif({ subsets: ["latin"], weight: "400", style: ["normal", "italic"], variable: "--font-instrument-serif", display: "swap" });
// add geist.variable, geistMono.variable, instrumentSerif.variable to the <html> className next to the existing ones
```

**Check:** in the browser console on `/office`, run:

```js
getComputedStyle(document.querySelector("h1, .head h1") || document.body).fontFamily
```

It must start with the Geist or Instrument Serif family name, not Inter. Also run `Select-String -Path frontend\src -Pattern "Instrument_Serif" -Recurse` to confirm there is exactly one loader.

### F45: `/ceo` (70 min, frontend-voice lane)

The target is `docs/designs/inside/ceo-console-prototype.html` (screenshot `ceo-1440.png`). The binding table is in `docs/designs/inside/INSIDE-BRIEF.md` §3 and stays valid.

1. **CSS.** Create `frontend/src/components/ceo/ceo-page.css` from the prototype's `<style>` block, **verbatim**, with three changes:
   - Move every rule under the root class `.cx` (`.app` → `.cx.app`, `.top` → `.cx .top`, `body` → `.cx`). Move the `:root` tokens onto `.cx`.
   - Delete the demo-only animations (`.reveal`, `.r1`–`.r4`).
   - Keep every number (sizes, radii, gaps, shadows) as is.
   
   Import it in `Ceo.tsx` next to `ceo.css`. `ceo.css` stays for the `panel` variant and the shared bits.
2. **DOM.** Only for `variant === "page"` in `CeoConsole`, replace the returned tree with the prototype skeleton:

   ```
   div.cx.app
     header.top     (.brand(.mark .chip) · label.cmd(input, .kbd, button.mic) · span.status(.dot) · updated · div.me)
     nav.rail       (a.nav ×7 with .lbl and .n counters, then button.stop pinned at the bottom)
     main           (section.hero(div.card.brief, div.card.voice) · section(.sh + div.appr) · section.split(hiring kanban, standing orders) · your orders)
     aside.side     (blocked cases/.clear · .feed live activity · .card.spend money · NEW Payments card from E)
   ```

   Leave `variant === "panel"` exactly as it is (the docked panel inside `/office` must keep its compact layout).
3. **Binding (move existing JSX, keep the handlers).** Map each current section by its `aria-labelledby` id:

   | Current | Goes to |
   |---|---|
   | header title/updated/sign-out | `.top` (sign-out into `.me` menu or a ghost button at its right) |
   | `or-h` "Order to the Chief of Staff" input + Send | `label.cmd` in the top bar (the same `onSubmit`); `button.mic` calls the existing talk/hold handler |
   | `brief-h` brief + Play | `div.card.brief` (kicker, serif-italic headline word, brief text, 4 `.kpi` tiles from the brief counts, `btn-lime` Play with `.wave`) |
   | `<CeoVoice/>` | `div.card.voice`: its hold-to-talk button gets class `orb`, hands-free becomes `<Switch>`, 3 `.chips` fill the command field |
   | `ap-h` approvals | `.sh` header + `div.appr` 2-column `.ap` cards (`.ap-h` head, `.ch` channel chip, `.post` preview with the real `image_url`, `.ap-f` footer with `.why` + Deny `btn-ghost` + Approve `btn-lime`) |
   | `hr-h` hiring | `.kan` 4 columns (`.col`, `.cand` cards with `.ini` initials, `.tagc` chip, `.ghostcol` dashed empty) |
   | `tr-h` standing orders | the second half of `section.split` (`.ord` rows with avatar, mono detail, `.st` chip, `<Switch>`) |
   | `ord-h` "Your orders, live" | under the hero, full width, using the order cards plus the F47 stepper |
   | `bl-h` blocked | `aside.side` top; when empty, the `all-clear` illustration in `.clear` |
   | activity / money | `aside.side` `.feed` and `.card.spend` (`.bar` + `.legend`) |
   | STOP EVERYTHING | `button.stop` in the rail, same handler, still instant, Esc still works |

   Rail links are anchors to section ids (`#brief`, `#approvals`, …), with counts from the same state.
4. **Keep:** every `role`, `aria-*`, `sr-only` live region, error and notice banners (put them at the top of `main`), the sign-in screen and the confirm dialog.
5. **Gate:**
   - `node crew/scripts/proto-classes.mjs` prints `/ceo: … 0 missing`.
   - Then the visual check in §G.

### F46: `/office` Overview (60 min, frontend lane, after F44)

The target is `docs/designs/inside/office-prototype.html` (screenshot `office-1920.png`). The binding table is in INSIDE-BRIEF §4.

1. **CSS.** Create `frontend/src/components/office/office-page.css` from the prototype `<style>`, scoped under `.ox` the same way. Import it in `Office.tsx`.
2. **Sidebar** (`Office.tsx`):
   - Replace the `nav.side` content with the prototype markup: `.brand`, `div.ov` (Overview), 7 × `div.dept` rows (avatar 30 px + name + thin activity bar + mono count), `.bottom` (`.livepill`, `a.sbtn` "CEO console ↗", `a.sbtn` "Sign out").
   - Use `<button>` elements carrying the prototype classes so keyboard and `onClick={() => open(seat)}` keep working.
   - The "Following live" toggle goes into `.bottom` as a `<Switch>`.
3. **Main** (`Theater.tsx` Overview): wrap it into `div.head` (title with the serif `live.`, mono subtitle, `.pills`: LIVE + clock + Stage) → `section.orders` (`article.ord` ×3) → `section.stage` (panes plus `div.rail`) → `div.bar` (`.avs .sum .prog .seg` 1×/4×/16×, Sound, Stage, Replay `.tb.lime`).
   - Keep all theater logic (`script.ts` targets, timing, cursors). Change only containers and classes.
4. **Panel default.** In `Office.tsx` change `setPanelOpen(saved ? saved.open : window.innerWidth >= 1600)` to `setPanelOpen(saved ? saved.open : false)`.
   - Before the demo the owner clicks "Hide" once, because a saved `open: true` overrides the default.
5. **Gate:**
   - `proto-classes.mjs` prints `/office: … 0 missing`.
   - At 1920×1080 and 1440×900 there is no page scroll, no clipped text, and the bottom bar is on one line.

**Cut line A:** at 08:45, whichever page isn't READY keeps its current layout with F44 fonts. Report what's left.

---

## B. Multi-task orders: capture all tasks, run them one by one

### B.1 Event and contract changes (contract v1.20; captain records them)

| Event | Actor | Payload | Notes |
|---|---|---|---|
| `order.queued` | system | `{order_id, of, tasks: [{no, text}]}` | right after the order's `voice.command`; `text` follows the same reader rule as `voice.command.text` (stripped for public theater readers if that one is) |
| `order.task_started` | system | `{order_id, task_no, of, text}` | the gateway then wakes the CoS (B.3) |
| `order.task_done` | the caller's seat, or system | `{order_id, task_no, of, status, summary, by}` | `status` is one of `done`, `waiting_on_you`, `blocked`, `failed`, `timeout`, `cancelled`; idem `task_done:{order_id}:{task_no}` |
| `order.done` | system | `{order_id, of, done, waiting_on_you, other}` | after the last task ends |
| any order-linked event | – | + `task_no` | stamped by the gateway wherever it stamps `order_id` today |

Other v1.20 changes:
- `ceo.cancelled` gets a new `reason: "merged"`.
- The `order` CEO tool's arguments become `{text?, tasks?: string[≤6]}`.
- New MCP tool `utax.task_done`.
- New endpoint `GET /api/payments` (E).
- New event `payment.recorded` (E).

### B51 backend (75 min)

**1. Splitter.** Copy `docs/designs/fixes/reference/split_tasks.py` into `backend/app/ceo_tools.py` (same names) and its cases into `backend/tests/test_split_tasks.py`.
- Raise `FREE_ORDER_MAX` from 300 to 1200, and `ORDER_MAX` from 500 to 1500.
- In `_ceo_command`, change `_str(args, "text", max_len=600)` to `max_len=1500`.

**2. `pin_order` produces tasks:**

```python
def pin_order(gw, args):
    raw = args.get("tasks") if isinstance(args.get("tasks"), list) else [args.get("text", "")]
    tasks: list[str] = []
    for item in raw:
        for t in split_tasks(str(item)):
            if t.lower() not in {x.lower() for x in tasks}:
                tasks.append(t)
    tasks = tasks[:TASKS_MAX]
    if not tasks:
        raise ToolRefused("bad_input", "say the order again")
    if len(tasks) == 1:
        return {"text": tasks[0], "tasks": tasks}, f'Tell the Chief of Staff: "{tasks[0]}". Say confirm.'
    lines = "; ".join(f"{i}) {t}" for i, t in enumerate(tasks, 1))
    return ({"text": " · ".join(tasks), "tasks": tasks},
            f"{len(tasks)} tasks for the Chief of Staff, one by one: {lines}. Say confirm.")
```

- In the `order` `CeoTool` schema, add `"tasks": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 6}` and drop `text` from `required`. Either field is enough.
- Change the description to: "An order for the Chief of Staff. When the CEO names several tasks, pass ALL of them in `tasks`, in the order said, in ONE call."
- `realtime_tools()` picks the schema up. Also append the same sentence to the realtime session instructions (find them with `grep -rn "realtime_tools(" backend/app`).
- `heard_confirms(..., readback)` must still accept a plain "confirm" for the numbered read-back. Add a test.
- `ORDER_READBACK_PUBLIC` becomes "N tasks for the Chief of Staff. Say confirm." when N > 1. The public `args` gain `"tasks": N`.

**3. Merge instead of supersede (`perform._propose`).** When `tool == "order"` and an **unexpired open order proposal** exists:
- Build the new proposal from `old_tasks + new_tasks`, deduplicated and capped at 6, through `pin_order(self, {"tasks": ...})`.
- Cancel the old one with reason `"merged"` (not `"superseded"`).
- Every other combination keeps today's behavior (a newer proposal supersedes).

Tests:
- "Advertise the firm." followed by "Hire two people." 3 s later gives one open proposal with 2 tasks and a numbered read-back.
- An approve proposal after an order proposal still supersedes.

**4. Queue.**
- Copy `reference/order_queue.py` to `backend/app/gateway/order_queue.py` and its tests to `backend/tests/test_order_queue.py`.
- In `Gateway.__init__`, add `self._orders = OrderQueue()`.
- Where the gateway rebuilds state from the log on boot, also call `self._orders.apply(e.type, e.payload, time.time(), e.actor)` for every event.

**5. Placing an order.**
- Factor the CEO branch of `_voice` into `_ceo_order(texts: list[str], channel)`.
- `carry_order` calls `gw._ceo_order(pinned.get("tasks") or [pinned["text"]], channel)`.
- `_voice(speaker="ceo")` calls it with `[text]`, so old callers keep working.

In `_ceo_order`, under the lock:

```python
masked = [self._mask_ceo(t) for t in texts]          # same chain as _voice: redact_ids → mask_spoken_emails → mask_text
text = masked[0] if len(masked) == 1 else " · ".join(f"{i}) {t}" for i, t in enumerate(masked, 1))
written = self._emit([voice.command(text=text, tasks=masked if len(masked) > 1 else absent, ...), touch.operating(...)])
order_id = f"order_{written[0].id}"
self._emit([NewEvent(type="order.queued", actor="system", provider="local",
                     payload={"order_id": order_id, "of": len(masked),
                              "tasks": [{"no": i, "text": t} for i, t in enumerate(masked, 1)]})])
self._orders.add(order_id, masked)
running = self._orders.active()
self._advance_orders()                                # may start task 1 and wake the CoS
return ("Sent to the Chief of Staff." if len(masked) == 1 and not running else
        f"Queued {len(masked)} task{'s' if len(masked) > 1 else ''}"
        + (f" after the task that is running now." if running else f". Starting 1 of {len(masked)}: {masked[0]}."))
```

**6. `_advance_orders()`**, under the lock. Never raise out of it: catch, log and return.

```python
def _advance_orders(self) -> None:
    now = time.time()
    a = self._orders.active()
    if a:
        o, t = a
        if getattr(t, "_waiting", False):             # set by the emit hook below
            self._finish_task(o, t, "waiting_on_you", "waiting on your approval in the console")
        elif self._orders.due_timeout(now):
            self._finish_task(o, t, "timeout", "no word from the department; moved on")
    for o in self._orders.orders:                       # order.done once per finished order
        if o.finished and not getattr(o, "_done_sent", False):
            counts = Counter(t.status for t in o.tasks)
            self._emit([NewEvent(type="order.done", actor="system", provider="local",
                                 payload={"order_id": o.order_id, "of": o.of, "done": counts["done"],
                                          "waiting_on_you": counts["waiting_on_you"],
                                          "other": o.of - counts["done"] - counts["waiting_on_you"]})],
                       )                                 # idem key: f"order_done:{o.order_id}"
            o._done_sent = True
    nxt = self._orders.next_to_start()
    if nxt:
        o, t = nxt
        self._emit([NewEvent(type="order.task_started", actor="system", provider="local",
                             payload={"order_id": o.order_id, "task_no": t.no, "of": o.of, "text": t.text})])
        self._orders.start(o.order_id, t.no, now)
        n = o.order_id.removeprefix("order_")
        self._wake(f"CEO order_{n}: {t.text}" if o.of == 1 else f"CEO order_{n} task {t.no}/{o.of}: {t.text}")
```

- `_finish_task(o, t, status, summary)`: if `t.status` is already ended, do nothing. Otherwise emit `order.task_done` (idem `task_done:{order_id}:{no}`), **then** call `self._orders.finish(...)`.
- On boot, `apply()` marks `_done_sent` for orders that already have an `order.done` (set it there).
- Call `_advance_orders()`:
  - at the end of `perform()`, right after every successful handler, inside the same lock
  - from `main.py` `tick()`: add `gw.advance_orders` (a public wrapper that takes the lock) to the tick list next to `advance_market_jobs`
  - at the end of `_ceo_order`

**7. Emit hook.** In `_emit`, where it stamps `order_id` today (around line 971), stamp `task_no` on every event whose payload has an `order_id` but no `task_no`:

```python
no = self._orders.task_no_for(e.payload["order_id"]); payload = {**e.payload, "task_no": no} if no else e.payload
```

After the write, for each written event with `order_id` and `task_no`, call `self._orders.touch(order_id, task_no, event.actor, time.time())`. If `event.type == "approval.requested"` and that task is running, set `task._waiting = True` (finished in `_advance_orders`). **Don't emit from inside `_emit`.**

Market job events written by the tick already carry `order_id`; they get `task_no` the same way.

**8. MCP tool `utax.task_done`.**
- Add it to `TOOLS` in `mcp.py` with this schema:

  ```json
  {"type":"object","additionalProperties":false,"required":["order_id","task_no","status"],
   "properties":{"order_id":{"type":"string","pattern":"^order_[0-9]{1,12}$"},
                 "task_no":{"type":"integer","minimum":1,"maximum":6},
                 "status":{"type":"string","enum":["done","blocked","failed"]},
                 "summary":{"type":"string","maxLength":200}}}
  ```

  Use this description: "D41: the last call of a card made for a CEO order task. Ends that task so the next one starts. Call it once, whatever the outcome."
- `TOOL_ACTORS["task_done"] = AGENTS` and `_handlers["task_done"] = self._task_done`.
- `_task_done` does this:
  1. `_valid_order(order_id)` else `Refused("not_found")`.
  2. If the task already ended, return `{"ok": True, "duplicate": True}`.
  3. If it is queued (not started), return `Refused("not_running", "task k has not started; the gateway starts it")`.
  4. Otherwise `_finish_task(o, t, status, clip(mask(summary), 200))`, then `_advance_orders()`.
  5. Return `{"ok": True, "next": "task 3/3 starts now"}` or `"that was the last task"`.
- Add the tool to contract §6 and to whatever list makes `naive up` expose it (search `MARKET_TOOLS` for the pattern).

**9. STOP and undo.**
- In `ceo_tools.carry_pause`, when `target == "everything"`, also call `gw._cancel_orders()`. That function uses `_orders.cancel_all()` to emit `order.task_done status "cancelled"` for each task, then `order.done`.
- Where an order's Undo is handled (`grep -n "undo" backend/app/gateway/perform.py`), call `_orders.cancel_order(order_id)` the same way.

**10. STT language** (the noise transcripts). In the `/api/stt` handler (`grep -rn "transcriptions" backend/app`):
- pass `language="en"`
- drop a transcript that has no Latin letters, by returning an empty text, which the console already treats as nothing heard

**Tests** (`backend/tests/test_orders_d41.py`), with a fake waker that records wake texts:

1. Order "Advertise the firm, hire 2 people and email our clients" → confirm. You get `order.queued` with 3 tasks, `task_started` 1, and exactly one wake `CEO order_<n> task 1/3: Advertise the firm`.
2. `task_done(order, 1, "done")` as `mkt` → `task_done` 1, `task_started` 2, a wake for task 2. Calling it again returns `duplicate`.
3. An `approval.requested` stamped with task 2 → after `advance`, `task_done` 2 is `waiting_on_you` and task 3 starts.
4. Timeout: frozen clock with no events for the soft limit → `task_done` `timeout`, then `order.done`.
5. A single order keeps the legacy wake text exactly (`CEO order_<n>: <text>`).
6. Two orders: the second order's task 1 waits until the first order's last task ends.
7. STOP everything → running and queued tasks end `cancelled`, `order.done` follows, and no further wakes.
8. Boot replay of the log mid-order → `active()` is the right task, and no duplicate `task_started` or wake.

### A43 agents (40 min, after the B51 contract lands; run `naive up` only after B51 is merged and the gateway restarted)

**`naive/prompts/chief-of-staff.md`:**
- In the wake list, after the `CEO order_<id>: <words>` item, add:

  > `CEO order_<id> task <k>/<N>: <words>`: one task of a CEO order with N tasks. **Work only on this task.** The gateway sends the next task only after this one ends. Never start, card or mention a later task yourself.
- In "CEO orders", replace the first bullet's "all at once" sentence with:

  > Make the cards this order (or this task) needs. Title them "order_<id> task k/N: <part>" (single-task orders keep "order_<id>: <part>"). The body's first line is `order_<id> task k/N` (single: `order_<id> task 1/1`). Mark **exactly one** card per task as the reporter by adding the line `report: yes`. Pick the department that finishes the task: advertise → `marketing`; hire N → `hr`; share card → `marketing`; follow up and email clients → `intake`.
- Add:

  > If the task needs no agent ("brief me"), do it yourself and call `utax.task_done {order_id, task_no, status: "done", summary}` before you end. If you can't tell what it means, ask your one short question **and** call `utax.task_done` with `status: "blocked"` and the question as the summary, so the CEO's other tasks go on. If the "already running" rule applies, call `utax.task_done` with `status: "done"` and summary "already running".
- New row in the orders table:

  | Order | Cards |
  |---|---|
  | "email our clients", "email every customer", "check in with our customers", "follow up with everyone" | `utax.list_open_cases`, then `get_case` for each; for every case whose memo was sent, oldest first, at most 5: `intake` "case_N: follow up" (body `order_<id> task k/N`, case_N; `report: yes` on the last one). No case with a memo yet: no card; `utax.task_done` done "no client has a memo yet". |
  | "get new customers by email", "cold email people" | No card for email: we only email people who wrote to us first. Say so in one sentence, and route it as "advertise" instead. |

**The shared rules file** (`naive/prompts/_firm-rules.md`, or whichever file every seat includes; check `naive/naive.config.ts`). Add:

> **CEO order tasks.** If your card's body has a line `report: yes`, your **last** tool call for that card is `utax.task_done {order_id, task_no, status, summary}`. Use status `done`, `blocked` (waiting on someone) or `failed`. Take `order_id` and `task_no` from the body's first line `order_<id> task k/N`, and write a one-line summary. Call it exactly once. Without `report: yes`, don't call it.

**`naive/prompts/ceo-assistant.md`:** after the `utax.order` line, add:

> When the CEO names several tasks in one go ("advertise, then hire two people, and email our clients"), call `utax.order` **once** with `tasks: ["Advertise the firm", "Hire 2 people", "Email our clients"]`. Use short imperatives, in the order said, at most 6. Never call `utax.order` twice in a row for one request: the gateway joins them anyway. The read-back lists the tasks; the CEO confirms once.

Also add the mapping "email our clients / customers" → `utax.order` "email our clients".

Then run `npx tsc --noEmit`, then `naive up --dry-run`, then `naive up`. **Order matters:** B51 must be live first.

### F47 console (35 min, frontend-voice lane)

1. **`ceo/orders.ts` `foldOrders`:**
   - On `order.queued`, attach `tasks: {no, text, status: "queued", seat: null}[]` to the card `order_id`.
   - On `order.task_started` and `order.task_done`, update the status. On any order-linked event with `task_no`, set that task's `seat` with `seatOf(ev)`.
   - Render under the card's quote a stepper: one row per task with a number badge, the text (1-line clamp) and the department avatar (22 px).
   - Badge states: ✓ lime when done; pulsing lime dot when running; amber "waiting on you"; faint ○ when queued; strike for cancelled.
   - Card header chip: "Task 2 of 3".
   - Add tests in `orders.test.ts`.
2. **Read-back waits for silence** (owner rule: never talk over the CEO).
   - In `CeoVoice.tsx`, where a command response with a proposal triggers speaking the read-back, keep only the newest proposal in a ref.
   - Speak it after **1500 ms with no utterance onset**. Reset the timer on every onset from the STT detector (`lib/stt.ts` exposes onset to the recorder).
   - When a merged proposal replaces an older one (`ceo.cancelled reason "merged"`), never speak the older one.
   - Show "Listening for more… (3 tasks so far)" under the orb while waiting.
   - **Cut line:** if this is not READY by 09:15, drop it; the gateway merge still captures every task.

---

## C. The department tour: one department at a time, in its own panel

### F48 (50 min, frontend lane; start with fixture events, then switch to live when B51 lands)

1. Copy `docs/designs/fixes/reference/tour.ts` to `frontend/src/office/tour.ts` and `tour.test.ts` next to it.
   - If `UTaxEvent` has the same fields, use it instead of `TourEvent`.
   - Seats are the existing `SEATS` (`cos, intake, tax, qa, acct, mkt, hr`).
   - Money events show at **Accounting**; contractor job events at **Marketing**.
2. **State.** Where `Office.tsx` folds the log on open and then each live batch (the same place `office` is built), also fold `tour = foldTour(tour, events)`.
   - Fold **history too**, so a reload in the middle of an order resumes the tour.
3. **Follow logic.** Add `TOUR_DWELL_MS = 4000` in `follow.ts`. In the existing auto-follow effect, add `if (activeTask(tour)) return;` at its top (the tour wins). Then add the tour effect:

   ```ts
   useEffect(() => {
     if (!follow) return;
     const target = tourTarget(tour);
     if (!target || target === view) return;
     const t = setTimeout(() => goTo(target), followDelay(lastSwitch.current, Date.now(), TOUR_DWELL_MS));
     return () => clearTimeout(t);
   }, [follow, tour, view]);
   ```

   `goTo(seat)` does the "click":
   - `setPressing(seat)`, then 650 ms later `setPressing(null); setView(seat); lastSwitch.current = Date.now()`.
   - Do **not** call `open()`: it turns follow off. Leave `?d=` alone.
   - The sidebar row gets the classes `tour-press` (while pressing) and `tour-on` (the tour's desk).

   CSS (office-page.css):

   ```css
   .ox .dept.tour-on{box-shadow:inset 3px 0 0 var(--lime);background:rgba(190,242,67,.06)}
   .ox .dept.tour-press{transform:scale(.98);transition:transform .15s}
   .ox .dept.tour-press::after{content:"";position:absolute;inset:0;border-radius:inherit;border:1px solid var(--lime);animation:tourRing .65s ease-out forwards}
   @keyframes tourRing{from{opacity:.9;transform:scale(.96)}to{opacity:0;transform:scale(1.04)}}
   @media (prefers-reduced-motion:reduce){.ox .dept.tour-press,.ox .dept.tour-press::after{animation:none;transform:none}}
   ```

   `.dept` needs `position:relative`.
4. **TaskBanner** (`frontend/src/components/office/TaskBanner.tsx`) at the top of `Department` (`main.of-dept`) when the active task's seat is this desk. Also show it on the CoS desk while `seat === "cos"`.
   - Kicker: mono 11 px, letter-spacing .08em, lime: `ORDER · TASK 2 OF 3`.
   - Task text: 20 px Geist, `--text`, 2-line clamp, full text in `title`.
   - Status chip: running = lime with a pulse dot; `waiting_on_you` = amber `#FFB547`; done = green `#3DDC84`; timeout or blocked = faint.
   - Stepper: N pills in a row. Done is lime ✓, current is a pulsing ring, queued is faint ○. Hover shows the task text.
   - Panel style: `background:var(--panel)`, `border:1px solid var(--line)`, `border-radius:16px`, padding `14px 18px`, margin-bottom 14 px.
5. **Moving on.**
   - After `order.task_done`, the banner shows "✓ Task 2 done" (or "Waiting on you", amber) for 2.5 s. Then the next `task_started` sends the tour to the CoS desk and then to the next department.
   - After `order.done`, a toast in `.head` reads "All 3 tasks done ✓" for 4 s. Then `setView("overview")` if follow is still on.
6. **Focus the task inside the desk.**
   - In `office/workspace.ts` `stepOf`, copy `payload.order_id` and `payload.task_no` onto `WsStep` (`order?: string; task?: number`).
   - While touring, the desk's steps list adds class `in-task` to that task's steps, and CSS `.of-dept.touring .steps li:not(.in-task){opacity:.45}`.
   - The Window shows the newest in-task artifact.
7. The "Following live" switch label reads "Touring task 2 of 3" while a tour runs.
   - A manual sidebar click still turns follow off (the existing `open()`).
   - `?d=` and `?source=` behave as today (no tour).
8. **Tests:** `tour.test.ts` (3 cases, already passing) plus one `follow.test.ts` case: with an active task, `followTarget` is ignored.

**Cut line C:** without the press animation, the tour still switches desks with the banner. That is the minimum.

---

## D. Email: customers by email, visible

### D.1 What stays

The AgentMail sending and the inbox polling work. Don't touch the adapters.

### D.2 F49 Inbox window and Mail history (35 min, frontend lane)

1. **New pure module `frontend/src/office/mail.ts`:** `foldMail(prev, events) → Thread[]`, grouped by `case_id`, newest thread first. Use these events:

   | Event | Direction | Label |
   |---|---|---|
   | `email.received` | in | – |
   | `client.asked` | out | "asked for the missing numbers" |
   | `memo.sent` | out | the memo |
   | `client.followed_up` | out | label by `kind` |
   | `call.booked` | out | "invite: review call" |
   | `payment.link_created` | out | "filing-help link" |
   | `payment.received` | in | "paid $49" |

   Each message is `{id, ts, dir, subject, body (≤ 600 chars, already masked in the payload), provider, status}`. Add tests: the case_1 log gives one thread with 7 messages in order.
2. **`frontend/src/components/office/Mail.tsx`**, a mail app inside the **Intake** desk. Add a segmented control in the Intake desk header: `Workspace | Inbox`.
   - Left list, 280 px: initials avatar, masked name, subject, time, and a lime dot when the last message is inbound and unanswered.
   - Right side: the thread as bubbles. Inbound is panel-colored on the left. Outbound is lime-tinted on the right, with mono "sent via AgentMail · <time>".
   - Header: "Team #1 · case_1 · 7 messages".
   - With no threads, show `EmptyState art="inbox"` "No email yet / Intake checks the firm inbox every 30 s and answers each client."
   - **Fold the full log on open** (the same fold as the desks), so history shows.
   - The tour switches the Intake desk to `Inbox` when the running task's latest event is one of the mail events above.
3. **Overview Mail pane.**
   - If `theater/screen.ts` has a pure fold usable over the log, seed the Mail pane with the latest mail event on open, with no animation.
   - If not, keep the EmptyState and add one line under it: "Last email: <subject> · <time> · Open Intake inbox ↗" (opens `?d=intake&tab=inbox`).
   - `initialView` must read `tab`.

### D.3 Orders that email customers

This is A43's "email our clients" row. It produces one `intake` "case_N: follow up" per client with a memo, using the existing templated follow-up. No free-text emails: firm rule, we never send numbers we didn't compute.

### D.4 Owner: a live email round trip (5 min; do it once before 09:30 and keep the thread for the demo)

1. Find the firm inbox address. In PowerShell, from the repo:

   ```
   Get-Content backend\.env | Select-String "AGENTMAIL" | Select-String "INBOX|ADDRESS"
   ```

   It is also in the landing footer and on `/talk`.
2. From your **personal Gmail** (a new sender, so it becomes a new case), send to that address:
   - Subject: `Delaware franchise tax question`
   - Body:

     > Hi, we're a Delaware C-corp. Authorized shares: 10,000,000. Issued shares: 1,000,000. Par value: $0.0001. Total gross assets on our last return: $50,000. No foreign owner holds 25% or more. Can you estimate our franchise tax?
3. Within about a minute, the office follows Intake → Tax → QA → Accounting. Then the memo arrives in your Gmail. Expect the total $450: $400 tax plus the $50 annual report fee, versus $85,165 by the authorized-shares method.
   - If it is in Spam, mark it "Not spam" now, before the demo.
4. Open `/office?d=intake&tab=inbox`: the new thread shows your email (in) and the memo (out).

**If no reply arrives within 2 minutes:**
- `curl.exe -s http://localhost:8000/api/health`
- then look at the last events: `Get-Content backend\data\events.jsonl -Tail 20`
- Look for `email.received`. If it is missing, the poller isn't running; restart the gateway.

---

## E. Payment history in the office, the console and Stripe

### B52 `GET /api/payments?token=` (20 min, backend)

- Use the same token rule as `/api/state`. All fields are public payload fields.
- Build it from the event log (projections if they hold events, else the store; cache 5 s), newest first, at most 100 rows:

```json
{"data":[{"id":379,"ts":"2026-11-08T14:06:10Z","direction":"out","kind":"contractor","counterparty":"PixelAgent",
          "job_id":"job_2","order_id":"order_406","task_no":1,"amount":"0.50","token":"pathUSD",
          "rail":"tempo-testnet","status":"ok","tx_hash":"0x02f0…","explorer_url":"https://explore.tempo.xyz/tx/0x02f0…",
          "stripe":{"id":"pi_…","mode":"mirror","dashboard_url":"https://dashboard.stripe.com/test/payments/pi_…"}},
         {"direction":"in","kind":"agent_sale","counterparty":"0x12…ab", "rail":"tempo-testnet", "...": "..."},
         {"direction":"in","kind":"client","counterparty":"Team #1","amount":"49.00","token":"USD","rail":"stripe", "...": "..."}],
 "totals":{"contractors_paid":{"amount":"1.50","token":"pathUSD"},"agent_sales":{"amount":"0.50","token":"pathUSD"},"clients_paid_usd":"0.00"}}
```

The rows come from:
- `payment.sent` (out; `status` from the envelope; failed ones listed as failed)
- `agent.sale` (in)
- `payment.received` (in, USD)
- `payment.recorded` (joined onto its `payment.sent` row by `tx_hash`)

Testnet tokens never go into USD totals. Add contract §4. Tests: the 3 real `payment.sent` rows come out with explorer links.

### B53 Stripe record: the test-mode mirror (30 min, backend; works today, no new Stripe setup)

**Why a mirror:** the payment itself happens on Tempo testnet. Stripe records an on-chain Tempo payment natively only through the Stripe-profile MPP path (B54), which this account can't use yet. The mirror puts one clearly labelled test-mode record per contractor payment into **this account's Stripe Dashboard → Payments (test mode)**.

1. In `backend/app/gateway/adapters/stripe_pay.py`, add a method using the same `_post` helper and idempotency style:

   ```python
   def mirror_contractor_payment(self, *, tx_hash: str, amount_usd: str, token: str, job_id: str,
                                 contractor: str, explorer_url: str, order_id: str | None) -> str:
       cents = max(50, round(float(amount_usd) * 100))          # Stripe's card minimum is $0.50
       body = self._post("/v1/payment_intents", {
           "amount": str(cents), "currency": "usd",
           "payment_method": "pm_card_visa", "payment_method_types[]": "card", "confirm": "true",
           "description": f"U-Tax paid {contractor} for {job_id}: MIRROR of Tempo testnet tx {tx_hash[:10]}…",
           "metadata[utax_kind]": "contractor_payment_mirror", "metadata[job_id]": job_id,
           "metadata[contractor]": contractor, "metadata[amount_token]": f"{amount_usd} {token}",
           "metadata[tempo_tx]": tx_hash, "metadata[explorer_url]": explorer_url,
           **({"metadata[order_id]": order_id} if order_id else {}),
       }, f"utax-mirror-{tx_hash}")
       pi = body.get("id")
       if not isinstance(pi, str) or not pi.startswith("pi_") or body.get("status") != "succeeded":
           raise Rejected("stripe did not record the mirror")
       return pi
   ```

   Only with a key that starts with `sk_test_`. With a live key, do nothing: a mirror charge in live mode would be a real charge.
2. **A tick job `gw.mirror_payments`**, added to the `main.py` tick list. It runs **outside** `self._lock` like `advance_market_jobs` (contract §6f).
   - Each tick, take the oldest `payment.sent` with status ok whose `tx_hash` has no `payment.recorded`. At most one per tick.
   - Call the adapter, then emit:

     ```
     NewEvent(type="payment.recorded", actor="system", provider="stripe",
              payload={job_id, tx_hash, order_id?, task_no?, stripe_id, mode: "mirror",
                       dashboard_url: f"https://dashboard.stripe.com/test/payments/{pi}"})
     ```

     with idem `stripe_mirror:{tx_hash}`.
   - On an error, log it and retry next tick; after 3 failures, give up for this process.
   - This also **backfills the 3 existing payments** on the first ticks.
   - Setting `STRIPE_MIRROR=0` turns it off.
3. **Webhook safety.** `/api/stripe/webhook` handles only `checkout.session.*` today, so the mirror never becomes revenue.
   - Add a test: a `payment_intent.succeeded` with `metadata.utax_kind=contractor_payment_mirror` creates no `payment.received`.
4. **Owner check:**
   - Open Stripe Dashboard → turn on **Test mode** → **Payments**: three $0.50 payments, each described "U-Tax paid PixelAgent for job_N: MIRROR of Tempo testnet tx …".
   - Each payment's metadata shows the tx hash.

### B54 stretch: Stripe-recorded MPP (only if everything above is merged by 09:15)

1. Get the profile id (PowerShell, key from `.env`, never pasted in chat):

   ```
   $k = (Get-Content backend\.env | Select-String '^STRIPE_SECRET_KEY=').ToString().Split('=',2)[1]
   curl.exe -s https://api.stripe.com/v2/network/business_profiles/me -u "${k}:" -H "Stripe-Version: 2026-07-29.preview"
   ```

   If there is no `profile_test_…` id, create a profile in the Dashboard (https://docs.stripe.com/get-started/account/profile) and run it again.
2. In `contractors/pixelagent.py`, switch the 402 method to `mpp.methods.stripe.create(network_id=<profile id>, livemode=False, client=StripeClient(key), store=SQLiteStore(...), deposit_addresses={"tempo": "0x5ff8d73e8bccd3701c9aef78389f3b9771172b5c"})`. That address is the existing test deposit address.
3. Run `market-e2e` once. If the challenge asks for a token the U-Tax wallet doesn't hold and the faucet can't fund it, **abandon**: revert the PixelAgent change and keep the mirror.
4. On success, record `payment.recorded mode "mpp"`. The mirror skips any `tx_hash` already recorded.

### F50 ledger UI (40 min, split across both frontend lanes)

1. **Office, Accounting desk** (frontend lane). Add a segmented control in the Accounting desk header: `Workspace | Payments`. Payments shows:
   - Totals strip: "Paid to contractors **1.50 pathUSD** · Received from agents **0.50 pathUSD** · Client payments **$0**". Amounts in lime mono.
   - Table rows: time (mono) · what (avatar + "Paid **PixelAgent** for job_2 · share card") · amount (lime mono "0.50 pathUSD") · rail chip ("Tempo testnet") · proof ("tx 0x02f0…270a ↗" to the explorer, and a chip "Stripe test · mirror ↗" to `dashboard_url`) · status chip.
   - A new row flashes lime once (`@keyframes flash`). With no rows, show `EmptyState` "No payments yet / Contractor payments and sales show here with their on-chain proof."
   - Data comes from `GET /api/payments`: fetch on open, every 15 s, and right after any `payment.*` or `agent.sale` event on the stream.
   - The tour switches the Accounting desk to `Payments` when the running task's latest event is `payment.sent` or `payment.recorded`, with that row highlighted. Open it directly with `?d=acct&tab=payments`.
2. **Overview right rail** (F46 markup): in the Money block, add the row "payments · 3 ↗", which opens `?d=acct&tab=payments`.
3. **CEO console** (frontend-voice lane): in `aside.side`, a "Payments" card under the money card with the last 5 rows (compact) and "Open the ledger ↗" to `/office?d=acct&tab=payments`. The new component is `frontend/src/components/ceo/Payments.tsx`.

---

## G. Gates, rehearsal and acceptance

### Design gate (A)

1. `node crew/scripts/proto-classes.mjs` must exit 0 (it prints `0 missing` for both pages).
2. **Visual check, 60 s.** Open `docs\designs\inside\ceo-1440.png` and the live `/ceo` at window width 1440, side by side. Do the same for `office-1920.png` against `/office` full-screen at 1920. Use the replay source if the firm is idle.

   Check these:
   - same columns
   - same card order
   - Geist text and a serif italic accent
   - lime buttons
   - no docked panel on `/office`
   - nothing clipped

   Post both screenshots to the controller with READY.

### Live acceptance (09:30 rehearsal; the captain runs it; each line passes or the feature is reverted)

| # | Do | Expect |
|---|---|---|
| T1 | `/ceo`, type: `Advertise the firm, hire 2 people and email our clients` → Send → Confirm | Read-back "3 tasks for the Chief of Staff, one by one: 1) Advertise the firm; 2) Hire 2 people; 3) Email our clients". The order card shows 3 steps. `/office` presses **Chief of Staff**, then **Marketing**, and stays until task 1 ends. Then Chief of Staff, then **HR**, then **Intake**, then "All 3 tasks done ✓" |
| T2 | Voice: say "Advertise the firm." *(pause 1 s)* "Then hire two people." | One read-back with 2 tasks, spoken only after you stop talking |
| T3 | Order: `get an outside agent to make a share card` | The tour goes Marketing → **Accounting**: the Payments tab shows the new row with a tx link. Within a minute, the "Stripe test · mirror" chip appears and Stripe Dashboard (test) → Payments has it |
| T4 | §D.4 email round trip | The reply is in Gmail. The Intake Inbox shows the thread |
| T5 | Esc (STOP everything) during T1 | The running task ends `cancelled`, the rest is cancelled, and nothing else starts |
| T6 | Reload `/office` in the middle of T1 | The tour resumes on the right desk and task |

To watch the events (PowerShell):

```
Get-Content backend\data\events.jsonl -Wait -Tail 0 | Select-String '"type":\s*"(order\.|payment\.|email\.|memo\.|client\.)'
```

### Records

The captain records D41 in `docs/designs/u-tax.md`, bumps `contracts/api.md` to v1.20 (B.1 and E), and adds a D41 line to `frontend/DESIGN.md`. After the event: rotate every key pasted in chat (unchanged).

---

## H. Prompt for the lead session (the owner pastes this)

> Read `docs/designs/fixes/FIXES-RUNBOOK.md` (D41) completely, then run it as the controller. Hand each lane its tasks from the plan table with exact file paths from the runbook. Copy the tested reference code from `docs/designs/fixes/reference/`; don't retype it.
>
> Order:
> 1. Tag `pre-d41`.
> 2. F44 fonts first.
> 3. In parallel: F45 (frontend-voice), F46 then F48 then F49 and F50-office (frontend), B51 then B52, B53 (backend).
> 4. A43 after B51 is merged and the gateway restarted.
> 5. F47 and F50-console after B51/B52.
>
> Feature merges stop at 09:30. Then run the §G rehearsal T1–T6 yourself and report pass or fail per line. Revert any feature that fails, with `git revert -m 1 <merge>`. Freeze at 10:00.
>
> Respect the cut lines. Never break the single-order wake text, STOP, approvals or sign-in. Never put a key in chat or git. Don't ask me questions you can answer from the runbook or the code. Give me one digest line per merge, and tell me exactly which owner steps are due (§D.4 email test, the "Hide" click on the docked panel, the Stripe Dashboard check).
