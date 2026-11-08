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
| `NEXT_PUBLIC_ASSET_HOSTS` | the gateway's `PUBLIC_ASSET_HOSTS` | Comma-separated https hosts for other images. The gateway's own ad images (D31) need no entry: the theater trusts the origin it streams from. |
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

## The live CEO orders on the theater (D31, contract v1.11.1)

- **Orders strip:** a band between the header and the panes, once the CEO gives an order.
  - Each order is a card, and the firm's steps for it fill in one plain line at a time with a department badge: "Marketing made an ad image" (with a thumbnail), "posted to X", "HR found 3 public profiles", "HR emailed Candidate A with a call invite", "HR booked a 20-min call with Candidate A", "Intake followed up with Team #7 (memo check)".
  - Above the cards, a row of desks lights up each department while its agent acts.
- **How steps find their order:**
  - By the `order_id` the gateway stamps.
  - Without one, a D31 action (ad, post, HR, follow-up) can match a recent order that names its department. Case work (tax, memo, client calls, approvals) files only by `order_id`.
  - The Chief of Staff's handoff joins only an order given in the last minute.
  - Only the CEO's own words open a card.
- **Reload:** a projector reloaded mid-order rebuilds its cards from the last 300 events (orders only; nothing replays on the panes).
- **Stage mode:**
  - The band slides open and shrinks when the camera focuses a pane.
  - The cards have three fixed slots, so order numbers never shift.
  - An order nobody acts on leaves after 2 minutes of log time. The night replay's "open the firm" shows briefly at the start, then the band closes.
- **Images:** https hosts listed in `NEXT_PUBLIC_ASSET_HOSTS`, or anything on the gateway's own origin. Other http hosts and ports are refused. An image that fails to load disappears.
- **Mock:** `scripts/mock-ceo-gateway.mjs`. A confirmed `/ceo` order (or `POST /api/dev/order {text}`) plays each department's steps with `order_id`, and an ad SVG is served at `/assets/ad_<n>.svg`.
- **Checks:**
  - `/review` ran one fix pass (7 specialists, the adversarial pass, Red Team and a browser smoke test), timeboxed. Every finding is fixed except one (below).
  - 443 tests pass, including 17 regression tests, each shown failing on the pre-fix code.
- **Open:** on the very first order, the CEO's cursor lands on the rail, because the band isn't on screen yet. From the second order on, it lands on "CEO orders".
- **Unverified:**
  - The real gateway (backend task 23 is merged) was not run with these pages; the payloads were checked against `perform.py`.
  - The no-orders layout position after the four-row grid change was not measured in a browser.

## The product shell, sign-in and the voice loop (D32, D33, contract v1.12 to v1.13.1, tasks 36/39)

- **Routes.** `/` is now the landing page (free checkup). `/signup` is client sign-up. `/login` is the CEO sign-in. `/office` is the firm: the Overview (the theater) plus one live workspace per department, with auto-follow. `/ceo` is the console.
- **Sign-in.** Email and password go to `/api/auth/login`, which returns a 12 h session kept in this tab only. No screen asks for the admin token any more; a leftover one is wiped.
  - The session goes as a Bearer header everywhere, and is never in a URL.
  - The `/office` stream opens with a fresh single-use ticket each time.
  - A refused session signs the tab out.
  - Sign out is in the `/office` sidebar and the `/ceo` header.
- **Voice loop (merged f3ce11e).**
  - One utterance is one order: browser re-deliveries, restarts and the console's own echo are dropped.
  - One reply is said once. Readbacks always play in full.
  - "Stop everything", fillers allowed, takes the STOP path. A stop command closes the open card.
- **Auto-follow** leaves the Overview only for a live agent step: never the history replayed on open, and never in `?source=` replays.
- **Decisions taken with the recommended option** (the owner: "use the recommended options for all"):
  - D32 + login ship together, with the criticals and highs fixed and the rest listed as known.
  - All the review's regression tests were written.
  - The gate edit is kept as justified: the 401 wording test changed with its sentence.
  - The stream uses the contract's ticket flow (task 36/39), not a fetch-based Bearer stream.
- **Known, not fixed yet** (next READY with D34 and the panel):
  - The landing page polls every 15 s.
  - Sign-up error wording and maxLength.
  - The mock logs sign-up details.
  - Design polish: `.how .after .note` margin, `.follow` hover, visited color, `.shot` max-height, selection and caret.
  - Seat colors are defined 4 times; there are two STATUS_WORD maps.
  - Theater re-renders inside the office.
  - Two SSE streams per tab, and the lookback is diluted by `tool.called`.
  - Auto-follow uses the browser clock (±15 s).
  - The flag hides only mail fields; the masked client name stays in earlier non-mail steps.
- **Backend items raised:**
  - The login limiter can be used to lock out the CEO's sign-in: the per-key limit is IP plus email, and the global cap is 200.
  - The logout denylist is in memory only.

## The CEO conversation (D34, contract v1.14 §5a-ter)

- In `/ceo`, everything the CEO says or types goes to `/api/ceo/talk`. The assistant acts directly, with no confirm card, and says what it did. Each action shows as a chip with Undo.
  - The console listens through the browser and speaks with TTS.
  - Hands-free keeps the mic on.
- **One turn at a time**, with "U-Tax is working on it…" on screen. Later words queue behind the current turn.
- **Stops:**
  - The gateway's own stop phrases jump the queue.
  - A bare "stop" during a turn is STOP EVERYTHING.
  - Words queued before a STOP are dropped.
  - Actions that land after a STOP are shown with their Undo, and everything is paused again.
- **Fallbacks:**
  - A 503 before the gateway takes the words falls back to the command path and its card for one minute.
  - A failure mid-turn never re-sends the words.
  - A forgotten thread (404) restarts once.
  - An undo the gateway lost shows "Undo not available".
- **Decisions taken with the recommended option:**
  - Conversation mode uses the browser ear and TTS. Realtime is used only when talk is down or the browser can't listen.
  - Spoken replies are capped at whole sentences of about 300 characters.
  - After a timeout, the console says so and never guesses.
- **Backend asks (sent to the controller):**
  - Run the stop grammar before the conversation lookup.
  - Keep a per-turn action list.
  - Fence assistant start tools that come from a turn begun before a STOP.
  - Read `pending` under the lock in check().
- **Known, not fixed:**
  - After STOP, the console keeps following the old turn for up to 6.5 min, and new words wait behind it. Releasing them needs the backend fence first.
  - The mock has no turn lock.
  - Chips from before a STOP still offer Undo with no "STOP ran since" note.

## Every order done live, step by step (D37, contract v1.18)

- **Signed-in `/office`:** each department window shows the artifact being made, live.
  - **Email:** the composer opens when the tool starts, then the real email from `payload.sent` types in, with the workpaper and memo as attachment chips, then "Sent ✓".
  - **Social:** the X and LinkedIn compose box types the post, then shows the ad image and "Posted on <channel> ✓".
  - **Documents:** one artifact per case, played back in build order. The workpaper fills as a real grid, cell by cell from `payload.cells`; then the memo's sections; then the books row. Each document gets its own Verified tab.
  - **Tax:** both methods with the real numbers, the filed one marked.
  - **HR:** the calendar invite.
- **Activity:** an activity pill shows "thinking…", "using <tool>" or "writing…". The agent's own words appear as "<Seat>'s notes" under the window, never as an email.
- **Auto-follow** jumps to a desk that is making something.
- **Privacy:** the public theater and signed-out `/office` never get activity or private content. A case flagged as a possible injection never shows its mail, cells or notes.
- **Decisions taken with the recommended option:**
  - Narration is shown as notes, because the gateway builds every email from a template.
  - The documents rest on the workpaper's computation once played, not on the ledger row.
  - Activity counts only while recent (20 s).
- **Backend asks (sent to the controller):**
  - `_shown` formats money cells without "$" or cents, and par $0.00001 as "0".
  - The activity relay opens a session's stream at seq 0, so old tool events get logged as new.
  - Public `/api/events` filters activity after slicing by id, so the 300-event lookback can come back nearly empty.
- **Known, not built yet:** the "CEO order card with the live step list" belongs to the CEO panel, which is next.

## The CEO console in the office (D33 panel + pop-out, order cards)

- **Panel:** in signed-in `/office`, the CEO console is a panel on the right.
  - It can be resized by dragging the edge or with the arrow keys, and hidden to a side tab. The layout is kept per viewer.
  - On first visit it opens by default only on screens ≥ 1600px.
- **Pop out:** opens `/ceo` in its own window, which takes the session along, or brings an open one to the front. It says so when the browser blocks the window.
- **One console across every window** (Web Locks):
  - A `/ceo` window takes the console over; the panel shrinks to "open in another window" with "Use it here".
  - A console that isn't live does nothing: no polling, no ear, no voice.
  - With no Web Locks (an old browser, or plain http on a LAN address), only `/ceo` runs a console.
- **"Your orders, live":** each order the CEO gave, the D34 assistant's included, with every step done for it and what an agent is doing now.
- **Esc** is STOP EVERYTHING only inside the panel.
- **The brief** reads itself only when a `/ceo` window opens.
- **Sign-out:** signing out in one window signs out the others with the same session.
- **Workpaper cells** follow v1.18.2 formats: money as $#,##0.00 with full precision kept, counts grouped, par as is.
- **Decisions taken with the recommended option:**
  - Web Locks rather than a BroadcastChannel heartbeat (crash-safe, not throttled).
  - The /ceo window wins on open.
  - The panel doesn't auto-play the brief.
- **Known:**
  - The theater's speech and the console's voice share one window in `/office`; the theater doesn't announce its audio to the console's ear.
  - Hiding the panel mid-turn drops that turn's on-screen result (the gateway still runs it).

## Downloading the real documents (D35)

- **Downloads:** signed in, the Accounting window's case documents offer "Workpaper (Excel)" and "Memo (Word)", and the memo email's attachment chips download the same files.
  - GET `/api/docs/<case>/<file>` with the session as Bearer; the file is saved from a blob, never via a URL token.
  - Buttons appear only for files the gateway serves: all three documents of that build verified, never for a flagged case. The books stay in the firm.
- **Decision taken with the recommended option:** gate downloads on the gateway's served set (verified), not on "built".
- **Known:** downloads live on the desk's current artifact, so once the desk moves on there is no per-case file list yet. A case list in the CEO panel would close that.
- **Backend ask:** `documents.py` writes the client's verbatim quotes with openpyxl, so a quote starting with "=" becomes a live formula in the downloaded .xlsx. Write user-derived strings as literal text.

## Reliable voice transcripts (D36, contract v1.16.1)

- **Both ears:** `/ceo` (purpose ceo, Bearer) and `/talk` (purpose talk, public) listen through the gateway's STT whenever the browser can record. The mic is recorded, cut into utterances by a level detector, and each utterance goes to `POST /api/stt` as the raw body. The text comes back like a browser-recognizer final, so all the console's ear logic is unchanged.
- **Recording:**
  - Two overlapping recorders give every clip 1.4–3 s of pre-roll, so the first word ("stop") is never cut.
  - The detector learns the room from its quietest moments, counts real time, follows a soft voice, and filters on voiced length.
- **Delivery and STOP:**
  - When the console speaks, the ear closes but still delivers what was already said.
  - Nothing said before a STOP runs after it.
  - Results arrive in spoken order.
- **Taps:** a tap ends after 8 s of silence; letting go sends what was said; a second tap works while the first is still uploading.
- **Fallbacks:**
  - A 503 means the browser recognizer for the session.
  - A 429, the network (retried once), a noisy room or a recording failure mean the browser recognizer for 2 minutes, or a retry when the browser has none.
  - An unreadable clip means "say it again".
  - On `/talk`, a busy minute shows "type for a moment" and comes back.
- **Decisions taken with the recommended option:**
  - Pre-roll by overlapping recorders, rather than an AudioWorklet WAV encoder.
  - Clips are booked including their pre-roll, about +2 s each.
- **Known:**
  - On `/talk`, an answer split into two clips across a long pause can have its second half land after the next question.
  - Each ear reopens the mic (Bluetooth headsets switch profile).
- **Backend ask:** `/api/stt` books the client-declared `seconds` when the provider reports tokens instead of a duration, so a direct caller can under-declare on the public budget.

## Cases list in the CEO console (F1, contract v1.15/v1.18.4)

- **Every case in the console (page and /office panel):** each row shows status (or Flagged) and its verified documents (workpaper .xlsx, memo .docx), each with a download button. The downloads stay reachable after the Accounting desk moves on.
- **Which cases:** the newest 12, plus any older case whose documents are ready.
- **Which files:** one rule decides what is served, shared with the Accounting window (`stepCaseDocs`): a version is served once all three of its documents passed. A flagged case never offers a file, and only names the gateway can serve get a button.
- **Downloads:** they share one hook with the Accounting window. A dead session signs the console out with the reason. A late 401 for an old session never ends a newer one.
- **Log restart:** the served files and order cards clear at once, even if the reload fails.
- **v1.18.4 (backend 45):** both ears record speech at 8 kbps Opus, so an honest clip books about 1.3× its length.
- **Decisions taken with the recommended option:**
  - Every case with documents stays listed rather than paging.
  - The list sits after the order form.
  - Download logic is shared through a hook rather than by exporting FileChip, so each component keeps its own look.
- **Known (open, informational):**
  - The console clears a case's checks per document; the gateway clears the whole set on any failure. A same-version rebuild could show "Verified ✓" downloads that 404 (backend: is that rebuild reachable?).
  - A download in flight still saves after Sign out.
  - A 403 counts as an expired session.
  - The Office fold no longer checks the actor on `doc.*` events.
  - The served map is not pruned.
  - Safari's AAC bitrate at the 8 kbps request, and transcription quality at 8 kbps, are not measured on real devices.

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

## D40 F43a+F43b (/office in Night Lime), lane/frontend-inside
- F43a: `components/ui/` DeptAvatar (inline SVG from docs/designs/inside/illustrations, flat tint instead of id gradients so a hidden copy never blanks the others), EmptyState, Switch (role=switch, caller handler); `--nl-*` tokens in globals.css. No primitives page (per the compressed clock).
- F43b: theater + office sidebar re-skin only. Avatars in the sidebar (with activity bars), pane headers, order steps and the bottom-bar stack; EmptyState in Mail, Sheet, Search, Calendar, Social and Phone; short sheet labels; lime winner/total and QA chip; segmented speeds and a lime Replay; "The firm, *live.*" header. Empty states fit by container queries (art drops first, then the line).
- Not touched: script.ts, live.ts, screen.ts, voice, API calls, every data-target/data-pane (diff checked). Cursor parking is computed in Theater.tsx script, so left as is (brief §4).
- Checked at 1440x900 and 1920x1080 (normal and stage) on the fixture: no clipped chrome, Replay visible. Skipped for time: order status chips (no status field to bind), the Social "N posts waiting" line, the rail funnel and training pills.

## D40 F43c (/ceo at the 7 cut line) + the rail fix
- /ceo: Night Lime tokens (--blue now lime, so Talk/mic and focus are lime), a lime pill Approve, Deny ghost, department avatars on approvals, standing orders and order steps. The standing orders now use a Switch with the same handlers: on pauses at once (as Pause did); off opens the existing Resume confirm. STOP EVERYTHING is unchanged (red, one click / Esc).
- Not done: image previews on social approvals. approval.requested is `{approval_id, action_type, summary}` and has no image field, so there is nothing to bind (no invented content). Hands-free stays a checkbox (lime accent) to keep CeoVoice.tsx untouched.
- Controller fix: the theater rail keeps Cases at a 96px minimum and scrolls inside itself, so it is never cut off under the bottom bar at 1440x900.
- Click-tested on the mock gateway: switch off → paused, switch on → Resume confirm → resumed; Approve clears the row. Lint, 555 tests and build green.
