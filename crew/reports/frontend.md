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
| Stylesheet pass and clock polish | 80df2c2 (this READY) |

## What the owner must do

- Set `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_THEATER_TOKEN` for the build (the theater token is public by design). The admin token is never a build variable: the CEO types it at `/ceo`.
- Set `NEXT_PUBLIC_ASSET_HOSTS` to the gateway's `PUBLIC_ASSET_HOSTS`, or social images won't show.
- Give the gateway an `OPENAI_API_KEY` for voice on `/talk` (without it, `/talk` types), and test voice once on the venue network. If WebRTC is blocked, the page falls back to typed chat after 15 s.
- Stale local servers I started early in the night and couldn't stop (I wasn't permitted to kill processes): ports 3000, 3100, 3200, 3300 and 8010. Stop them by PID before the event.

## Lesson

Fold the whole log from id 0 and treat that fold as the only truth; never seed it from a snapshot. Every hard bug tonight came from two sources of truth meeting:
- the brief re-seeding approvals the log had already resolved;
- a stale reconnect cursor after a store reset;
- a watchdog comparing a state snapshot taken after the stream had moved on.

The fix each time was to derive from the event log and to check, after each await, that the result still belonged to the current session.
