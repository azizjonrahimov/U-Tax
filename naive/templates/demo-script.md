# U-Tax: 3-minute demo script cards

Updated for D23–D25: nobody phones anyone. Customers and the finale judge talk to the firm's AI
on `/talk`, the CEO works from the CEO console `/ceo`, and the Agent Theater is the main screen.
Each card has the time, what's on screen, what the presenter says (plain text) and what to click.
Every number on screen comes from the event log; say "estimated" for money the firm spent.

---

## Card 1 · 0:00–0:20 · The CEO logs in
**Screen:** `/ceo`, just logged in. The login brief plays out loud and shows as text.
**Say:** "I'm the only human at U-Tax. I didn't work last night. When I log in, the firm tells me
what it did."
**Do:** let the brief play about 10 seconds, then switch to the theater.
**If it fails:** read the brief text on screen yourself. It's the same words.

## Card 2 · 0:20–0:40 · The firm at a glance
**Screen:** the Agent Theater, stage mode (press S).
**Say:** "Seven agents run the firm: intake, tax, QA, accounting, marketing, HR, and a chief of
staff. This bar is the scoreboard: N clients served, K human touches. The training touches are
me approving the first email, the first post and the first invite. After that, each kind runs on
AUTO."
**Do:** point at the bottom bar, then at the touches log and the AUTO flips.

## Card 3 · 0:40–1:00 · A human decision, one click
**Screen:** `/ceo`, the hiring shortlist (the CEO was emailed when HR shortlisted).
**Say:** "HR ran a disclosed AI screen for a paid one-hour gig, a CPA to review our memos. The AI
never hires and never declines. I do."
**Do:** click Hire on the shortlisted candidate. Switch to the theater: the CEO's white cursor
appears and the touch counter ticks up by one.

## Card 4 · 1:00–2:30 · Finale: a judge talks to the firm
**Screen:** `/talk` on the judge's phone or laptop, the theater on the projector.
**Say:** "Judge, you're a Delaware C-corp. Tell U-Tax your numbers. Make them up if you like."
**Do:** the judge says yes to the transcription notice, answers four short questions, hears the
numbers read back, and types an email into the box. Stay quiet near the mic until the page says
the intake is sent. On the theater: Intake → Tax → QA → Accounting cursors work.
**Say while it runs:** "The AI only points at the numbers. Tested code does the math, and a second
agent re-checks every quote before anything is sent."
**Payoff:** the memo lands in the judge's inbox in under 45 seconds, for example
"$85,165 → $450". A calendar block drops in.
**If it fails:** the judge emails the firm instead (the client inbox), and you narrate the same
cursors. If live mode breaks, flip to `THEATER_MODE=replay`.

## Card 5 · 2:30–3:00 · The whole night in 30 seconds
**Screen:** the theater. Press Replay.
**Say:** "Here's the whole night, replayed from the log in 30 seconds."
**Payoff:** the cursors swarm, then fly into "Clients saved $X". The money panel reads "Clients
saved $X · the firm spent $Y (estimated)."
**Close:** "A firm that ran itself overnight, with one human who clicked when it mattered.
Educational estimates, not tax advice."

---

## Before going on stage (checklist)
- [ ] Sound check the brief audio, then **don't open `/ceo` again** until Card 1 (the login brief
      covers "since you were last here"; reset the stored last visit if you did)
- [ ] Theater in stage mode, live, last event less than 2 minutes old
- [ ] A shortlisted candidate waiting in `/ceo` (or skip card 3 and say why)
- [ ] `/talk` open on a second device; mic permission granted; a test run done at 10 AM
- [ ] Email trust already AUTO (else the judge's memo waits for a click: approve it live as the next touch)
- [ ] Backup: the 9 AM recording queued; `THEATER_MODE=replay` ready
