# U-Tax: 3-minute demo script cards

Updated for D23–D25, D28 and D30. Nobody phones anyone. Customers and the finale judge talk to the
firm's AI on `/talk`. The CEO works from the CEO console `/ceo`, by click or by voice. The Agent
Theater is the main screen.
Voice: Chrome's speech recognition listens, and the gateway turns the words into actions with a
fixed list of phrases, so **say the phrases exactly as written here** ("approve the latest", not
"approve the memo for Team 7"). The firm answers with ElevenLabs once the D30 build is deployed
(`POST /api/tts`), else with the browser's own voice. Until then keep `ADAPTER_VOICE=none`: on an
older build `elevenlabs` stops the gateway from starting, and `openai` with a key switches `/ceo`
to a free-form model where this phrase list doesn't apply.
Each card has the time, what's on screen, what the presenter says (plain text) and what to click.
Every number on screen comes from the event log; say "estimated" for money the firm spent.

The pitch is one claim: **U-Tax works like a real tax and accounting firm, and every department
is an agent doing its real-life job.** The tour below is how the five cards prove it. Each cursor's
name tag is its department: say the department's name as its cursor moves.

---

## The department tour (where each department is seen)

| Department | What it does in a real firm | The U-Tax agent doing it | The theater moment that proves it |
|---|---|---|---|
| **Chief of Staff** (managing partner) | Takes in work, assigns it to the right department, watches the pipeline, briefs the owner. | `cos`: wakes on every new case, writes one card per step on the Naïve board, runs the fast path by handing the case from agent to agent (`send_to_agent`), writes the brief when the CEO orders "brief me" (`firm_brief`). | **Card 4:** the judge's case moves Tax → QA → Accounting with no human in between; each hand-off is a delegation the Chief of Staff made. |
| **Marketing** (business development) | Finds new clients: public posts, outreach, lead lists. | `mkt`: writes and publishes the firm's posts (`post_social`), drafts LinkedIn DMs for the human to send (`draft_dm`). | **Card 2:** the firm's own post from last night, live on X or LinkedIn (`post.published`, actor `mkt`). |
| **Intake** (client onboarding) | Opens the engagement: collects the client's facts, chases what's missing, turns away work the firm can't take, books the review meeting. | `intake`: quotes each fact from the client's own words (`extract_facts`), re-asks for missing facts (`ask_client`), declines ineligible companies kindly (`mark_not_eligible`), books the call (`book_call`). | **Card 4:** the Intake cursor fills the sheet's fact cells from the judge's answers (`facts.extracted`). Then a review-call block lands on the Calendar pane (`call.booked`). |
| **Tax** (preparer) | Researches the law and prepares the computation. | `tax`: searches Delaware law (`law_search`), runs the tested franchise-tax engine (`compute_tax`). | **Card 4:** the Search pane types the query and shows Delaware cites (`law.searched`). The sheet fills both methods and marks the winner (`tax.computed`). |
| **QA** (reviewer, partner sign-off) | A second professional checks every number before anything leaves the firm. | `qa`: re-runs the engine and re-matches every quote (`qa_check`), checks the sent memo on a phone (`phone_check`), flags anything odd (`flag_case`). | **Card 4:** a tick on every sheet cell, then "✓ QA" (`qa.passed`). Later the Phone pane shows "Memo checked on a cloud phone" (`phone.checked`; the screenshot itself stays private). |
| **Accounting** (billing, books, P&L) | Releases the client deliverable, keeps the ledger, reports the P&L. | `acct`: sends the memo, and only once QA has passed (`send_memo`). The checkup is free, so there is no invoice. The ledger is kept in code, never by an agent: the gateway books agent model spend and every paid tool run as an estimated cost (contract §6a). | **Card 4:** the Accounting cursor types the memo and clicks Send. "✓ Sent · N s after last fact" (`memo.sent`). **Card 5:** stage mode's "Saved vs spent $X / $Y" (`cost.booked` summed). |
| **HR** (recruiting) | Posts the job, screens applicants, shortlists, and leaves the decision to the hiring manager. | `hr`: reads each applicant's masked answers (`get_candidate`), records screened and shortlisted (`hr_record`). The gateway sends the questionnaire and its AI-screen disclosure. | **Card 3b:** the Hiring pane, "applicants → screened · shortlisted · Shortlist ready · waiting on CEO". After the hire it reads "1 hired". |
| **The CEO** (the only human) | The owner: sets standing orders and makes the calls only a human should. | You. Approve the first of each kind of action, make the hire, stop anything at once. | **Cards 3a and 3b:** the Human touches pane counts each touch, the log adds a line per touch (a mic icon on the spoken approval), and `/ceo`'s Standing orders show social posts "paused". |

Stage mode shows only three big numbers on the right: "Clients served", "Human touches" and
"Saved vs spent". The Human touches, Money and Hiring panes appear **only outside stage mode**.
S (or the Stage button) **toggles** it; stage mode is on when the Stage button is lit. Cards 1–2
and 4–5 run with it on, cards 3a–3b with it off.

---

## Card 1 · 0:00–0:15 · The CEO logs in
**Screen:** `/ceo`: log in now (the login click lets the brief play). The login brief plays out
loud and shows as text.
**Say:** "I'm the only human at U-Tax. I didn't work last night. When I log in, the firm tells me
what it did."
**Do:** let the first sentence of the brief play, then switch to the theater.
**If it fails:** read the brief text on screen yourself. It's the same words. Without the gateway
voice, the page speaks it with the browser's voice instead.

## Card 2 · 0:15–0:30 · Seven departments, and how clients find us
**Screen:** the Agent Theater, stage mode on (Stage button lit). Then the firm's latest post, open
in a browser tab (its link is the `url` of the newest `post.published`).
**Say:** "Seven agents, one per department, like a real firm: Intake, Tax, QA, Accounting,
Marketing, HR, and a Chief of Staff running them. N clients served, K human touches. Marketing
finds clients the way a firm does: here's its post from last night."
**Do:** point at the cursors' name tags, then the bottom bar, then switch to the post tab.
**If it fails** (no live post): skip the tab and point at the Social pane when Marketing's cursor
posts in card 5's replay.

## Card 3a · 0:30–0:50 · The CEO runs the firm by voice
**Screen:** `/ceo` voice mode on the laptop (mic on), the theater on the projector with stage
mode **off** (press S: the Stage button goes dark).
**Say (to the console, in this order, word for word):**
1. "Stop posting." It pauses at once (stopping never asks for a confirm): `/ceo`'s Standing orders
   show social posts "paused", and the theater's Operating count ticks up.
2. "Approve the latest." (Team #7's memo is the only approval waiting.) The firm reads it back,
   for example: "Approve memo email: Team #7, $85,165 → $450. That also turns emails automatic.
   Say confirm." The confirm card shows the same sentence.
3. "Confirm." The touches log adds a line with a mic icon (an Operating touch; Training if it's
   email's first-ever approval). Accounting then picks the memo up: on the live crew that can
   take a few seconds, and it may land during card 3b.
**Say after:** "Stopping is instant. Anything that starts work, the firm reads back and waits for
my own voice."
**Never press Esc on `/ceo`:** it's STOP EVERYTHING, which also pauses email and invites, and the
judge's memo in card 4 couldn't send. If it happens, Resume each kind in Standing orders
("Resume…", then "Resume").
**If it fails** (a phrase isn't understood, the firm reads its list of example phrases back): in
`/ceo`, Pause social posts only (not STOP EVERYTHING), then Approve on Team #7's card. The same
two touches, clicked instead of spoken.

## Card 3b · 0:50–1:05 · HR shortlists, the human hires
**Screen:** `/ceo`, the hiring shortlist (the CEO was emailed when HR shortlisted). The theater's
Hiring pane reads "Shortlist ready · waiting on CEO".
**Say:** "HR ran a disclosed AI screen for a paid one-hour gig, a CPA to review our memos. The AI
never hires and never declines. I do."
**Do:** click Hire on the shortlisted candidate, then Hire again on the confirm row ("It sends the
offer email"). Switch to the theater: the CEO's white cursor appears, the Operating count ticks up
and the Hiring pane reads "1 hired". Then press S to turn stage mode back on (Stage button lit).

## Card 4 · 1:05–2:35 · Finale: a judge becomes a client
**Screen:** `/talk` on the judge's phone or laptop, the theater on the projector in stage mode.
**Say:** "Judge, you're a Delaware C-corp. Tell U-Tax your numbers. Make them up if you like."
**Do:** the judge says yes to the transcription notice, answers four short questions, types an
email into the box, and confirms the read-back. Stay quiet near the mic until the page says the
intake is sent.
**Say, one line per department as its cursor moves:**
1. Intake fills the fact cells: "Intake is onboarding you: every fact comes from your own answers."
2. Tax searches and fills the sheet: "Tax is preparing it. The AI only points at the numbers;
   tested code does the math."
3. QA ticks every cell: "QA is the partner sign-off. A second agent re-checks every number and
   every quote."
4. Accounting types and sends: "Accounting releases the memo, and only after QA signs off. Our
   Chief of Staff handed it from desk to desk; no human touched it."
**Payoff:** the memo lands in the judge's inbox in under 45 seconds, for example
"$85,165 → $450". Shortly after, Intake drops a review-call block on the Calendar pane (calendar
invites must already run on their own, or the block waits for a click) and the Phone pane shows
"Memo checked on a cloud phone".
**If it fails:** the judge emails the firm instead (the client inbox), and you narrate the same
cursors. If the theater's live view breaks but the gateway still answers, click 16× and press
Replay (the full-log replay, with the judge's case at the end). If the gateway or tunnel is down,
replay can't load either: play the backup recording.

## Card 5 · 2:35–3:00 · The whole night, and the books
**Screen:** the theater, stage mode on.
**Do:** click 16×, then Replay (replay plays at the speed button's setting, and 1× would take
minutes).
**Say:** "Here's the whole night, replayed from the log: Marketing posting, Intake onboarding,
Tax, QA, Accounting, HR screening."
**Payoff:** the cursors swarm, then line up under "Saved vs spent $X / $Y" while it counts up.
**Say:** "And the firm keeps its own books: agent time and every paid tool run, booked from the
log, in code. No agent touches the ledger."
**Close:** "A real firm's departments, every one an agent, with one human who clicked when it
mattered. Educational estimates, not tax advice."

---

## Before going on stage (checklist)

**The login brief (card 1)**
- [ ] After the evening before, don't open `/ceo` in the stage browser: the brief covers what
      happened since that browser's last visit. Do every `/ceo` test (sound check, voice) in a
      **separate Chrome profile or incognito window**, and leave a minute before card 1 (the
      gateway reuses a brief for 60 s)
- [ ] If the stage browser opened `/ceo` anyway: on the `/ceo` page open DevTools, run
      `localStorage.removeItem("utax.ceo.lastSeen")` and `sessionStorage.removeItem("utax.ceo.token")`,
      then **close the tab without reloading** (a reload signs back in and uses up the brief).
      The brief then covers the whole log

**Trust, in this order** (`/ceo` Standing orders read "asks you first", "runs on its own" or
"paused"; approving one action of a kind is what makes it run on its own)
- [ ] Hiring first: an applicant's questionnaire mail is an email, and approving it makes email
      run on its own. Get a candidate shortlisted and waiting in `/ceo` (or skip card 3b and say why)
- [ ] Calendar invites: approve one rehearsal invite, so card 4's review call books by itself
- [ ] Social posts on "asks you first" or "runs on its own", not "paused"
- [ ] Last, put email back to "asks you first" (the console has no button for it):
      `curl -X POST <gateway>/api/trust -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" -d '{"action_type":"email","state":"ASK"}'`.
      Then let Team #7's case reach its memo, so the memo waits: it must be the **only** approval
      waiting, or "approve the latest" picks another one
- [ ] After card 3a, check that Standing orders show email "runs on its own" (the approval earns
      it), or card 3b's hire mail and card 4's memo wait. If it doesn't, approve Team #7's memo
      on its card. Let Team #7's memo send before card 4

**Stage**
- [ ] Theater live, last event less than 2 minutes old, stage mode on (Stage button lit)
- [ ] Card 2: the newest post open in a browser tab, and it's the firm's own (the gateway appended
      the Talk to U-Tax link)
- [ ] Card 3a rehearsed in the separate profile with the **exact** phrases ("what's pending",
      "stop posting", "approve the latest"), saying "cancel" at each readback, and "resume
      posting" afterwards. If Chrome writes number words or sound-alikes, use the click fallback.
      Hands-free **off**, the laptop's speaker low and away from the mic (the firm's own voice must
      not reach it)
- [ ] The gateway voice: if `/api/tts` isn't live, the console uses the browser's voice. Don't say
      "ElevenLabs" on stage then
- [ ] `/talk` open on a second device; mic permission granted; a test run done at 10 AM
- [ ] Card 5 rehearsed: the full-log replay at 16× ends within about 20 s. If it doesn't, start it
      earlier
- [ ] Backup: the 9 AM recording (or the demo video) queued, for when the gateway or tunnel is down
- [ ] After the demo: resume social posts in `/ceo` (card 3a paused them)
