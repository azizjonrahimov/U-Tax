# Brief: frontend (P2: the Agent Theater)

```text
Lane: frontend · Controller: naive-yc-hackathon-99 · Rules: crew/CREW.md · Base: main
Branch: lane/frontend · Worktree: D:\Claude Projects\Naive YC Hackathon\.worktrees\frontend
Owns: frontend/, crew/reports/frontend.md
Do not touch: backend/ fixtures/ db/ (backend), naive/ (agents), contracts/ docs/ research/ reference/ and root files (controller)
Plan: approved: docs/designs/u-tax.md (rule 13)
Spend cap: none
```

## Task 1: scaffold (in progress)
Finish it as briefed in the first version of this file: Next.js (App Router, TS,
Tailwind, ESLint) in `frontend/`, `src/lib/api.ts`, the health badge, `.env.example`,
lint and build green. Send READY.

## Task 3: the Agent Theater (after the scaffold READY; `git merge main` first)
Read, in order: `U-TAX-CLAUDE.md`, `docs/designs/u-tax.md` (What Makes This Cool,
Premise 7, Event → theater animation, UX, Design Review decisions), `contracts/api.md`
v1, `contracts/events.sample.jsonl`. Open `docs/u-tax-theater-mockup.html` in a browser
(press S for stage mode) and look at `reference/mockup-*.png` and
`reference/video-frames-*.png`. The mockup is the approved look; the video is the feel.

Build it in slices, one READY each:

1. **Event → cursor script, pure and tested.** A function from an ordered event list to a
   deterministic list of cursor steps (pane, target, agent, action, text), following the
   design doc's event → animation table. Tests: same events → same script; unknown type
   → skipped and logged; zero events → empty state. Add a test runner (Vitest) to
   `frontend/`. Copy `contracts/events.sample.jsonl` into `frontend/public/fixtures/`
   (committed; re-copy when the contract changes).
2. **The screen.** Port the mockup into Next.js: header, Mail · Sheet · Search ·
   Calendar panes (plus compact Social and Phone panes), rail (Human touches, Money,
   Hiring, Cases), bottom bar ("Firm run by 7 agents · N clients · K human touches"),
   colored named cursors with the design tokens (bg #07070b, accent #ff4d3d, Inter +
   JetBrains Mono, one color per agent, CEO white), "camera follows the action", stage
   mode on S, speed 1×/4×/16×. Panes are labeled "via Gmail · rendered from log", never
   brand look-alikes. Driven by the fixture file for now.
3. **Live and replay.** `?source=fixture|live|replay`. Live: `/api/state` then
   `/api/events/stream` (SSE, reconnect with `Last-Event-ID`). Replay: `/api/events`,
   compressed idle gaps, the ending swarm flying into "Clients saved $X" counting up.
   SSE drop on stage → auto-reconnect, never a blank screen. Rail numbers come from
   `/api/state`, not from client-side guesses.

Rules that matter most here: **every animation comes from a real event** (premise 7);
payloads arrive masked, so render them as given; amounts are integer cents, format them.

Not decided yet (owner's call, do these last and behind a flag): the red BLOCKED case
card with the QA cursor circling the bad number, and "waiting" queue chips.

Check your work in a real browser with gstack `/browse` (never the claude-in-chrome
tools). Screenshots of each slice in your READY are welcome.

## Contract v1.1 additions (2026-11-06, design D21 and D22)
- **Slice 4: mic page** `/mic` (contract §8): CEO / Judge toggle, reply-email field,
  OpenAI Realtime via `POST /api/voice/session`, falling back to typed text + browser
  speech on 503; posts transcripts to `POST /api/voice`; reads back the numbers heard;
  follows the judge's case live. This is the demo finale, so make it big and calm.
- The 8 AM morning brief plays as audio on the theater from `GET /api/brief/audio`
  (fallback: browser `speechSynthesis`).
- The BLOCKED card and queue chips are decided: build both, flags default on
  (`NEXT_PUBLIC_SHOW_BLOCKED`, `NEXT_PUBLIC_SHOW_QUEUE`).

## Contract v1.2 (2026-11-06, design D23-D25): replaces the `/mic` slice above
The owner never has to speak. Slice 4 is now two pages (contract §8):
- **`/talk` "Talk to U-Tax"**: public; a customer or the finale judge talks with the firm's
  AI agent (OpenAI Realtime via `POST /api/voice/session`; fallback typed chat + browser
  speech). The agent speaks for the firm, gathers the facts, reads the numbers back, takes
  a reply email, posts to `POST /api/voice` with `speaker: "customer"`, then shows the case
  moving live. This is the finale, so make it big and calm.
- **`/ceo` console**: admin token; on login it plays and shows the brief from
  `GET /api/ceo/brief?since_id=` (remember `last_event_id` locally), lists pending approvals
  with Approve/Deny (`POST /api/approve`), and has an optional typed order box.

## Contract v1.4 (2026-11-06): for `/ceo` and the rail
- `/ceo` gets pause/resume switches per action type (email, social_post, calendar_invite)
  via `POST /api/trust`, and Hire/Decline buttons on shortlisted candidates via
  `POST /api/candidates/{id}/decision`.
- A new event `case.flagged` (qa) shows on the case card as a small amber "flagged" chip.
- v1.4.2: `/ceo` also lists BLOCKED cases with Retry (to READY), Re-ask (to INTAKE_PENDING)
  and Not eligible buttons → `POST /api/cases/{id}/unblock`, plus a Resume button on any
  PAUSED action type (`POST /api/trust`).
- v1.5: `/talk` posts **customer turns only** (`turns: [{speaker:"customer", text}]`), never the
  AI's read-back; `reply_email` only from the typed field; open with a recording/transcription
  consent line. `/ceo` lists **every screened candidate** with Hire/Decline, not only shortlisted.

## Task: `/ceo` voice mode (contract v1.8 §5b + §8, design D28)
The owner wants to run the whole firm by voice. Build the console half against the contract,
and use a mock gateway in the tests until backend task 18 merges.
1. **Inputs:** a mic button (push-to-talk) and a hands-free toggle.
   - Mint the session with `POST /api/voice/session {mode:"ceo"}` (admin token), and
     connect over WebRTC to `realtime_url`, as `/talk` does.
2. **Function calls:**
   - Each `response.function_call_arguments.done` goes to `POST /api/ceo/tool`. Send the
     result back as `conversation.item.create` `function_call_output`, then
     `response.create`.
3. **Proposals:**
   - Show a confirm card with the readback and a countdown to `expires_at`.
   - Confirm only from the CEO's own `conversation.item.input_audio_transcription.completed`
     text, matched by the console's grammar (`confirm`, `yes`, `do it`, `approve it`, `go
     ahead`; a cancel word cancels), or from a click. Then call `POST /api/ceo/confirm` with
     `via` and `heard`.
   - Never confirm from model output. Test that a model message saying "confirmed" does
     nothing.
4. **STOP EVERYTHING:** a big button, plus the `Esc` key.
   - It calls `/api/ceo/tool {name:"pause", args:{action_type:"everything"}}` with no
     confirm, and shows the result at once.
5. **Hands-free:** speak each new `approval.requested` and `case.blocked` from SSE as a
   short line ("Approval needed: memo for Team #7, $450. Say approve or deny.").
6. **Fallback** (mint 503, or no WebRTC):
   - Browser `SpeechRecognition` (feature-detected), or typing, goes to
     `POST /api/ceo/command`.
   - Answers are spoken with `speechSynthesis`, and the confirm card works the same.
7. **Theater:** the touches rail shows a mic icon for `channel: "voice"`.
8. **Checks:** run `/qa` on `/ceo`, plus `/design-review` for the projector.
   - Accessibility: the mic state is announced, and every action can also be clicked.

Touch frontend/ only, then READY.
