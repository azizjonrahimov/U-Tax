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

## Theater `naive.confirm` and the ElevenLabs voice (contract v1.9 §6b-bis, v1.10 to v1.10.2 §5c, D30)

- **Theater:** each `naive.confirm` shows on the rail as "Naïve check: board_write allowed for Chief of Staff · N auto, 0 clicks", red for a deny.
- **Speaking:** `src/lib/tts.ts`, one line at a time.
  - It sends `POST /api/tts` and falls back to the browser's voice on a 503 or 429 (resting the gateway voice 60 s), on any failure, or on an autoplay refusal.
  - Pieces are at most 600 chars. A stalled piece is silenced and counts as not heard.
- **/ceo:** speaks through the gateway with the token in a header, unmasked (v1.10.2). Listening stays off while anything plays, plus 800 ms.
- **/talk:**
  - Speaks registry keys only: `question.1`–`question.5`, `estimate.authorized` and `estimate.assumed_par` with `{total_cents}`, and `memo.on_its_way`. Other lines use the browser's voice.
  - Listens through browser SpeechRecognition ("Speak answers"). A phrase counts as an answer only while a question waits and the page is silent.
  - Listening stops before the email prompt, on focus of the email field, and on Start over.
- **Checks:** `/review` ran one pass (7 specialists, the adversarial pass and Red Team), timeboxed by the controller. All 13 fixes are applied, including the room-speech CRITICAL on /talk. 413 tests pass.
- **Open:**
  - The next TTS piece isn't fetched while the current one plays, so there is a short gap between lines.
  - A burst of `naive.confirm` events can speed up the projector.
  - The /talk listening rules live in the component, so they have no unit test.
- **Unverified:**
  - A real mic and recognizer in Chrome (the owner checks this at the venue).
  - The real `/api/tts` (backend task 21) and the phrase registry (agents lane). My keys are a proposal until the registry lands.

## Deploying the frontend on Vercel (the owner's account; the lane doesn't deploy)

**Before you start:** the gateway runs behind your tunnel, and you know its `https://…` host.
- Every `NEXT_PUBLIC_*` value is baked into the build. If the tunnel host changes, for example when a cloudflared quick tunnel restarts, the site must be redeployed with the new host.
- Use a named tunnel or another fixed host if you can.

1. In Vercel, choose **Add New → Project** and import the GitHub repo.
2. Set **Root Directory** to `frontend`. Leave the framework preset on Next.js and keep the default build command (`next build`) and output.
3. Under **Environment Variables**, set these for Production (and Preview, if you use it):

| Name | Value | Notes |
|---|---|---|
| `NEXT_PUBLIC_API_URL` | `https://<tunnel-host>` | No trailing slash, and no `/api` (the pages add it). |
| `NEXT_PUBLIC_THEATER_TOKEN` | the gateway's `THEATER_TOKEN` | Public by design: read-only, and it ends up in the page. It must be the same value as the gateway's, and never empty (event mode refuses an empty token). |
| `NEXT_PUBLIC_ASSET_HOSTS` | the gateway's `PUBLIC_ASSET_HOSTS` | Comma-separated hosts. Without this, social images don't show on the theater. |
| `NEXT_PUBLIC_SHOW_BLOCKED` / `NEXT_PUBLIC_SHOW_QUEUE` | leave unset (both default to on) | Set either to `0` to hide that card. |

   **Never** put `ADMIN_TOKEN`, `OPENAI_API_KEY`, `ELEVENLABS_API_KEY` or any other secret in Vercel. The CEO types the admin token at `/ceo`, where it stays in that browser tab only.
4. Click **Deploy**. Note the site URL, e.g. `https://u-tax.vercel.app`.
5. **On the gateway**, then restart it:
   - Add that URL to `THEATER_ORIGIN` (comma-separated, no trailing slash, e.g. `http://localhost:3000,https://u-tax.vercel.app`). Without it, CORS blocks the theater, `/ceo`, `/talk` and `/api/tts`.
   - Set `PUBLIC_TALK_URL` to `https://<site>/talk`. Keep it fixed once posts start, because post approvals digest the text before the link.
6. **Smoke test**, about 1 minute:
   - `https://<site>/` shows "LIVE · gateway" and the clock ticking. "Theater token rejected" means the token doesn't match.
   - `/ceo`: sign in with the admin token. The brief appears.
   - `/talk`: the consent screen shows.
   - If anything shows "Can't reach the gateway", check `NEXT_PUBLIC_API_URL` and `THEATER_ORIGIN`.

## Real-mic check in Chrome (2 minutes, at the venue, on the real speakers)

Use desktop Chrome on the laptop that will run the demo, with the mic you'll use (a headset is best). Allow the microphone when Chrome asks.

**/talk (60 s):**
1. Open `https://<site>/talk` and click **I agree, start talking**.
   - Expect: "U-Tax listens through your browser…", and the first question spoken in the ElevenLabs voice.
   - If the voice sounds robotic, that's the browser's own voice: `/api/tts` isn't up (check `ELEVENLABS_API_KEY` on the gateway).
2. While U-Tax speaks, the hint says "U-Tax is speaking". When it stops, it says "Listening".
3. Say "Yes, we're a Delaware C corporation". It appears as your answer about a second after you stop, and the next question follows.
   - Nothing appears: the hint says why (mic blocked, or "Listening isn't working on this network"). Typing still works.
4. Talk over U-Tax while it asks a question. Nothing you say during its speech becomes an answer.
5. Click into the email box. "Speak answers" turns off. Type an email and send. The estimate is read out in the firm's voice.

**/ceo (60 s):**
1. Open `https://<site>/ceo` and sign in. Turn on **Hands-free**.
   - Expect: "Hands-free: the browser is listening". With no Realtime key that's correct.
2. Say "approve the latest", or type it.
   - The card appears, and the readback plays in the firm's voice: "… Say confirm."
   - While it plays the status is not "listening", so the speakers' "Say confirm" can't count.
3. After the readback ends, wait a beat and say "confirm". You get "Done: …", and the approval leaves the list.
   - "That wasn't taken as a confirm" means you spoke over the voice or too soon: say it again, or click **Confirm**.
4. Press **Esc**. You get "Paused everything…". Resume the kinds under Standing orders.

If either page can't hear you at all: check Chrome's site permission (the lock icon → Microphone → Allow), and that the laptop isn't on a network that blocks Google's speech service. Both pages always work by typing and clicking.

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
