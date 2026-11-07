# Frontend lane report

## Outcome

The three U-Tax pages are done for the event. All three are Next.js 16 (App Router), built to
`contracts/api.md` v1.7.8:

- **`/` Agent Theater**: the projector view.
  - Every frame comes from a logged event: a pure script turns events into cursor steps, and a fold turns steps into the screen.
  - Live mode follows the gateway's SSE stream. It is seeded from `/api/state`, watched by a 15 s watchdog that resyncs after a store reset and reconnects a half-open stream, and it treats a wrong token as a terminal error.
  - Replay plays the whole log and ends with "Night shift replayed" and a count-up of the saving.
  - Fixture mode plays the contract sample offline.
  - A possible-injection case never shows its mail subject or body.
  - Images load only from `NEXT_PUBLIC_ASSET_HOSTS`.
  - Stage mode (`S`) and 1080p scaling are for the room.
- **`/talk` "Talk to U-Tax"**: the finale page.
  - Consent comes first, and the microphone is requested only after "I agree, start talking". Voice runs on an OpenAI Realtime session minted by the gateway.
  - Typed chat with browser speech is the fallback for every failure.
  - Turns are speaker-labelled, and customer answers are never dropped. `reply_email` comes only from the typed field, and a `request_id` makes retries return the same case.
  - After sending, the page follows the case live: the estimate is shown and read aloud, then the CEO sign-off, then "Memo sent".
  - At capacity, it shows the gateway's own sentence.
- **`/ceo` CEO console**: sign-in with the admin token.
  - The token is checked before it is kept, held only in this tab, and sent only as a Bearer header.
  - The login brief is written and spoken.
  - Approvals, with a confirm whenever one Approve releases others of its kind; outdated and superseded approvals can't be approved.
  - Standing orders, with Pause and a Resume that restores the state from before the pause.
  - Hiring: Hire/Decline for every screened candidate.
  - Blocked cases: Retry / Re-ask / Not eligible.
  - A typed order to the Chief of Staff.
  - The console survives a gateway reset.

Checks at the last READY: `npm run lint` passes, `npm run build` passes, and `npm test` passes 273 tests in 6 files (pure logic: script, screen fold, rail, live, talk, ceo). Every READY went through `/review` (specialists plus an adversarial pass), and the live slices and the two pages also went through `/qa` and `/design-review` on the running app. Browser checks always ran against a gateway and theater I started and stopped in the same step.

Unverified:
- The voice path (WebRTC/SDP with OpenAI Realtime) is checked only by reading the code and by unit tests: there was no `OPENAI_API_KEY`.
- On `/ceo`, Hire/Decline and Retry/Re-ask/Not eligible never ran against a live gateway, because the fixtures can't produce a screened candidate or a BLOCKED case. Their request shapes and confirms are checked in code and tests.
- The `/ceo` and `/talk` reviews each stopped at 3 cycles without converging. The controller accepted the open Lows: no `request_id` on a typed order, `briefSince` after a reset that regrew past last-seen, and a 4th late transcript appending out of order.

## What merged

| Slice | Main commits |
|---|---|
| Scaffold, theater slices 1–2 (screen, rail, fold, panes) | merged before slice 3 |
| Slice 3: live + replay, watchdog, flags, v1.7.x | 723af77 |
| `/talk` | 08e9467 |
| `/ceo` | da17c31 (HEAD only; f42ea0e alone doesn't compile, 1a2320b repairs it) |
| `/ceo` v1.7.8 (RESUME, 409 superseded) | 2ceccf6 |
| Stylesheet pass and clock polish | 80df2c2 |
| `/ceo` voice mode (v1.8 to v1.8.5, design D28) | this READY (HEAD only: the first "wip" commit 95d7510 doesn't compile alone) |

## `/ceo` voice mode (contract v1.8 to v1.8.5)

- **Inputs:** Talk (tap or hold), Hands-free, and STOP EVERYTHING (button or Esc, no confirm). The Realtime session is minted with `mode: "ceo"`. Without a key, or without WebRTC, it falls back to browser speech or typing through `/api/ceo/command`, with answers read aloud. The theater rail shows a mic on voice touches.
- **Confirm rules, in code** (`frontend/src/ceo/voice.ts`, 110 tests):
  - A proposal is confirmed only by the CEO's own words, typed words, or a click. Model output never confirms.
  - The grammar is identical to the gateway's (v1.8.4/v1.8.5): the whole utterance must be a confirm phrase; never a question, an echo, the readback's own words, or a non a-z letter.
  - Spoken words count only from speech that started:
    - after the readback was heard in full: the model's reply must carry it in order, with the same verb, figures, targets and status words, and must not be cut off;
    - outside every stretch of agent audio (the model, the browser's voice, the brief), plus an 800 ms echo tail.
  - Otherwise the card is click or typed only, and says so.
- **STOP:**
  - It fences anything already in flight: no new card, no `response.create`, and a confirm that lands during STOP is paused again.
  - Its touch is sent with `channel: "click"`. Backend 18b honours it; before that, the gateway logged "voice".
- **Checks:**
  - `/qa` found and fixed 3 issues (`ISSUE-001` was High: a spoken confirm never left the console).
  - `/design-review` found and fixed 3.
  - `/review` ran 3 cycles: 7 specialists, the adversarial pass and Red Team each cycle. It found 11 CRITICALs, all fixed and checked in the browser. It stopped at the cycle cap without converging.
  - Open Lows:
    - Ceo.tsx and CeoVoice share one `speechSynthesis` queue: the brief's `cancel()` cuts a voice line. This fails safe: the card stays click-only.
    - A non-busy Realtime error clears the "response running" flag. It recovers via `wantResponse`.
    - A card from a typed "confirm" shows 58 s although the gateway's proposal may be older. The gateway's 409 still rules.
    - The mock gateway does nothing for unblock, decide and order.
- **Tested against the real gateway routes** (main's backend, local driver, no OpenAI key): typed proposals, "I can't confirm that", typed and clicked confirms, the gateway's `confirm` answer after a reload, "stop everything" with a card open, and Esc.
- **Unverified:**
  - The live Realtime leg. There was no `OPENAI_API_KEY`; WebRTC was stubbed in the browser and the event order follows the docs.
  - Whether a real model's reply transcript carries the readback closely enough to arm cards by voice. If it doesn't, cards stay click or typed only, which fails safe.
  - Echo levels in the venue.
  - Browser `SpeechRecognition` on real audio.

## What the owner must do

- Set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_THEATER_TOKEN` for the build (the theater token is public by design). The admin token is never a build variable: the CEO types it at `/ceo`.
- Set `NEXT_PUBLIC_ASSET_HOSTS` to the gateway's `PUBLIC_ASSET_HOSTS`, or social images won't show.
- Give the gateway an `OPENAI_API_KEY` for voice on `/talk` (without it, `/talk` types), and test voice once on the venue network. If WebRTC is blocked, the page falls back to typed chat after 15 s.
- Test `/ceo` voice once with the key, on the projector speakers:
  - Say "approve the latest", let the agent read the proposal, then say "confirm".
  - If the card says "U-Tax didn't read the whole proposal", the agent paraphrased the readback. Tighten `naive/prompts/ceo-voice.md` ("read the proposal word for word") or confirm by click.
  - Prefer a headset mic in the room. Hands-free on open speakers is guarded, but any voice in the room can still say "confirm" after a readback.
- Stale local servers I started early in the night and couldn't stop (I wasn't permitted to kill processes): ports 3000, 3100, 3200, 3300 and 8010. Stop them by PID before the event.

## Lesson

Fold the whole log from id 0 and treat that fold as the only truth; never seed it from a snapshot. Every hard bug tonight came from two sources of truth meeting:
- the brief re-seeding approvals the log had already resolved;
- a stale reconnect cursor after a store reset;
- a watchdog comparing a state snapshot taken after the stream had moved on.

The fix each time was to derive from the event log and to check, after each await, that the result still belonged to the current session.
