# U-Tax: 3-minute demo script cards

Updated for D23–D25, D28 and D30. Nobody phones anyone. Customers and the finale judge talk to the
firm's AI on `/talk`. The CEO works from the CEO console `/ceo`, by click or by voice: the firm
speaks with ElevenLabs, and Chrome's speech recognition listens. The Agent Theater is the main screen.
Each card has the time, what's on screen, what the presenter says (plain text) and what to click.
Every number on screen comes from the event log; say "estimated" for money the firm spent.

The pitch is one claim: **U-Tax works like a real tax and accounting firm, and every department
is an agent doing its real-life job.** The tour below is how the five cards prove it. Each cursor's
name tag is its department: say the department's name as its cursor moves.

---

## The department tour (where each department is seen)

| Department | What it does in a real firm | The U-Tax agent doing it | The theater moment that proves it |
|---|---|---|---|
| **Chief of Staff** (managing partner) | Takes in work, assigns it to the right department, watches the pipeline, briefs the owner every morning. | `cos`: wakes on every new case, writes one card per step on the Naïve board, runs the fast path, writes the morning brief (`firm_brief`). | **Card 1:** the login brief on `/ceo`, played aloud (`brief.generated`, actor `cos`). **Card 4:** each hand-off between cursors is a delegation it made. |
| **Marketing** (business development) | Finds new clients: public posts, outreach, lead lists. | `mkt`: writes and publishes the firm's posts (`post_social`), drafts LinkedIn DMs for the human to send (`draft_dm`). | **Card 2:** the firm's own post from last night, live on X or LinkedIn (`post.published`, actor `mkt`). |
| **Intake** (client onboarding) | Opens the engagement: collects the client's facts, chases what's missing, turns away work the firm can't take, books the review meeting. | `intake`: quotes each fact from the client's own words (`extract_facts`), re-asks for missing facts (`ask_client`), declines ineligible companies kindly (`mark_not_eligible`), books the call (`book_call`). | **Card 4:** the Intake cursor highlights each quote in the Mail pane and drags it into the sheet (`facts.extracted`). Then a review-call block lands on the Calendar pane (`call.booked`). |
| **Tax** (preparer) | Researches the law and prepares the computation. | `tax`: searches Delaware law (`law_search`), runs the tested franchise-tax engine (`compute_tax`). | **Card 4:** the Search pane types the query and shows Delaware cites (`law.searched`). The sheet fills both methods and marks the winner (`tax.computed`). |
| **QA** (reviewer, partner sign-off) | A second professional checks every number before anything leaves the firm. | `qa`: re-runs the engine and re-matches every quote (`qa_check`), opens the sent memo on a phone (`phone_check`), flags anything odd (`flag_case`). | **Card 4:** a tick on every sheet cell, then "✓ QA" (`qa.passed`). Later the Phone pane shows the memo as the client sees it (`phone.checked`). |
| **Accounting** (billing, books, P&L) | Releases the client deliverable, keeps the ledger, reports the P&L. | `acct`: sends the memo, and only once QA has passed (`send_memo`). The checkup is free, so there is no invoice. The ledger is kept in code, never by an agent: the gateway books every model call and sponsor run as an estimated cost (contract §6a). | **Card 4:** the Accounting cursor types the memo and clicks Send. "✓ Sent · N s after last fact" (`memo.sent`). **Card 5:** the money panel, "Clients saved $X · the firm spent $Y (estimated)" (`cost.booked` summed). |
| **HR** (recruiting) | Posts the job, screens applicants, shortlists, and leaves the decision to the hiring manager. | `hr`: reads each applicant's masked answers (`get_candidate`), records screened and shortlisted (`hr_record`). The gateway sends the questionnaire and its AI-screen disclosure. | **Card 3b:** the Hiring pane, "applicants → screened · shortlisted · Shortlist ready · waiting on CEO". After the hire it reads "1 hired". |
| **The CEO** (the only human) | The owner: sets standing orders and makes the calls only a human should. | You. Approve the first of each kind of action, make the hire, stop anything at once. | **Cards 3a and 3b:** the touch counter ticks up, a mic icon marks a voice touch, and the log shows `social_post → PAUSED`. |

Stage mode (press S) shows only three big numbers on the right: clients, human touches, saved vs
spent. The Human touches, Money and Hiring panes appear **only outside stage mode**, so press S to
leave stage mode for cards 3a–3b, and press it again before card 4.

---

## Card 1 · 0:00–0:15 · The managing partner briefs the CEO
**Screen:** `/ceo`, just logged in. The login brief plays out loud and shows as text.
**Say:** "I'm the only human at U-Tax. I didn't work last night. When I log in, our Chief of Staff,
the firm's managing partner, tells me what every department did."
**Do:** let the first sentence of the brief play, then switch to the theater.
**If it fails:** read the brief text on screen yourself. It's the same words. If ElevenLabs is
down, the page speaks it with the browser's voice instead.

## Card 2 · 0:15–0:30 · Seven departments, and how clients find us
**Screen:** the Agent Theater, stage mode (press S). Then the firm's latest post, open in a
browser tab (its link is the `url` of the newest `post.published`).
**Say:** "Seven agents, one per department, like a real firm: Intake, Tax, QA, Accounting,
Marketing, HR, and a Chief of Staff running them. N clients served, K human touches. Marketing
found those clients: here's its post from last night."
**Do:** point at the cursors' name tags, then the bottom bar, then switch to the post tab.
**If it fails** (no live post): skip the tab and point at the Social pane when Marketing's cursor
posts in card 5's replay.

## Card 3a · 0:30–0:50 · The CEO runs the firm by voice
**Screen:** `/ceo` voice mode on the laptop (mic on), the theater on the projector with stage
mode **off** (press S).
**Say (to the console, in this order):**
1. "Stop posting." The theater's touches log shows `social_post → PAUSED` at once, and `/ceo`'s
   Standing orders read paused (stopping never asks for a confirm).
2. "Approve the memo for Team 7." The firm reads it back, for example: "Approve the memo for
   Team #7: $450 to their inbox. Say confirm." (the figure is Team #7's own) The confirm card
   shows the same sentence.
3. "Confirm." The memo cursor moves and the touch counter ticks up, with a mic icon on the touch.
**Say after:** "Stopping is instant. Anything that starts work, the firm reads back and waits for
my own voice."
**If it fails:** in `/ceo`, pause social posts only (not STOP EVERYTHING: that would also pause
email and invites, and the judge's memo in card 4 couldn't send), then Approve on Team #7's card.
The same two touches, clicked instead of spoken.

## Card 3b · 0:50–1:05 · HR shortlists, the human hires
**Screen:** `/ceo`, the hiring shortlist (the CEO was emailed when HR shortlisted). The theater's
Hiring pane reads "Shortlist ready · waiting on CEO".
**Say:** "HR ran a disclosed AI screen for a paid one-hour gig, a CPA to review our memos. The AI
never hires and never declines. I do."
**Do:** click Hire on the shortlisted candidate. Switch to the theater: the CEO's white cursor
appears, the touch counter ticks up by one and the Hiring pane reads "1 hired". Then press S to
go back to stage mode.

## Card 4 · 1:05–2:35 · Finale: a judge becomes a client
**Screen:** `/talk` on the judge's phone or laptop, the theater on the projector in stage mode.
**Say:** "Judge, you're a Delaware C-corp. Tell U-Tax your numbers. Make them up if you like."
**Do:** the judge says yes to the transcription notice, answers four short questions, types an
email into the box, and confirms the read-back. Stay quiet near the mic until the page says the
intake is sent.
**Say, one line per department as its cursor moves:**
1. Intake highlights the judge's words: "Intake is onboarding you: every fact is quoted from your
   own words."
2. Tax searches and fills the sheet: "Tax is preparing it. The AI only points at the numbers;
   tested code does the math."
3. QA ticks every cell: "QA is the partner sign-off. A second agent re-checks every number and
   every quote."
4. Accounting types and sends: "Accounting releases the memo, and only after QA signs off."
**Payoff:** the memo lands in the judge's inbox in under 45 seconds, for example
"$85,165 → $450". Shortly after, Intake drops a review-call block on the Calendar pane (calendar
trust must already be AUTO, or it waits for a click) and QA's phone check shows the memo on the
Phone pane.
**If it fails:** the judge emails the firm instead (the client inbox), and you narrate the same
cursors. If live mode breaks, switch the theater to the full-log replay (the Replay button, or
`/?source=replay`).

## Card 5 · 2:35–3:00 · The whole night, and the books
**Screen:** the theater. Press Replay.
**Say:** "Here's the whole night, replayed from the log: Marketing posting, Intake onboarding,
Tax, QA, Accounting, HR screening."
**Payoff:** the cursors swarm, then fly into "Clients saved $X". The money panel reads "Clients
saved $X · the firm spent $Y (estimated)."
**Say:** "And the firm keeps its own books: every model call and every sponsor run is booked from
the log, in code. No agent touches the ledger."
**Close:** "A real firm's departments, every one an agent, with one human who clicked when it
mattered. Educational estimates, not tax advice."

---

## Before going on stage (checklist)
- [ ] Sound check the brief audio, then **don't open `/ceo` again** until Card 1 (the login brief
      covers "since you were last here"; reset the stored last visit if you did)
- [ ] Theater in stage mode, live, last event less than 2 minutes old; S toggles it (cards 3a–3b
      run with stage mode off)
- [ ] Card 2: the newest post open in a browser tab, and it's the firm's own (the gateway appended
      the Talk to U-Tax link)
- [ ] A shortlisted candidate waiting in `/ceo` (or skip card 3b and say why)
- [ ] Card 3a: `/ceo` voice mode tested on the stage mic in Chrome (say "what's waiting for me?"),
      hands-free **off**, the laptop's speaker low and away from the mic (the firm's own voice
      must not reach it), social posts on AUTO or ASK (not already PAUSED), and Team #7's memo the
      **only** approval waiting (email on ASK). If voice mode is down, do card 3a's click fallback
- [ ] Between cards 3a and 3b, check `/ceo`'s Standing orders: email must read AUTO (the approval
      earns it), or card 3b's hire mail and card 4's memo wait. If it still says ASK, click AUTO
      for email
- [ ] `/talk` open on a second device; mic permission granted; a test run done at 10 AM
- [ ] Email trust AUTO by card 4 (else the judge's memo waits for a click: approve it live as the next touch)
- [ ] Calendar invites on AUTO by card 4, or the review-call block waits for a click
- [ ] Backup: the 9 AM recording (or the demo video) queued; the theater open at `/?source=replay`
      in a second tab
- [ ] After the demo: resume social posts in `/ceo` (card 3a paused them)
