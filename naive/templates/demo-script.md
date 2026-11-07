# U-Tax: 3-minute demo script cards

Updated for D23–D25 and D28: nobody phones anyone. Customers and the finale judge talk to the
firm's AI on `/talk`, the CEO works from the CEO console `/ceo` (by click or by voice), and the
Agent Theater is the main screen.
Each card has the time, what's on screen, what the presenter says (plain text) and what to click.
Every number on screen comes from the event log; say "estimated" for money the firm spent.

---

## Card 1 · 0:00–0:15 · The CEO logs in
**Screen:** `/ceo`, just logged in. The login brief plays out loud and shows as text.
**Say:** "I'm the only human at U-Tax. I didn't work last night. When I log in, the firm tells me
what it did."
**Do:** let the first sentence of the brief play, then switch to the theater.
**If it fails:** read the brief text on screen yourself. It's the same words.

## Card 2 · 0:15–0:25 · The firm at a glance
**Screen:** the Agent Theater, stage mode (press S).
**Say:** "Seven agents run the firm. This bar is the scoreboard: N clients served, K human
touches. I approve the first email, post and invite; after that, each kind runs on AUTO."
**Do:** point at the bottom bar, then at the touches log and the AUTO flips.

## Card 3a · 0:25–0:45 · The CEO runs the firm by voice
**Screen:** `/ceo` voice mode on the laptop (mic on), the theater on the projector.
**Say (to the console, in this order):**
1. "Stop posting." The theater's trust panel flips `social_post` to PAUSED at once (stopping
   never asks for a confirm).
2. "Approve the memo for Team 7." The firm reads it back, for example: "Approve the memo for
   Team #7: $450 to their inbox. Say confirm." (the figure is Team #7's own) The confirm card shows the same sentence.
3. "Confirm." The memo cursor moves and the touch counter ticks up, with a mic icon on the touch.
**Say after:** "Stopping is instant. Anything that starts work, the firm reads back and waits for
my own voice."
**If it fails:** in `/ceo`, pause social posts only (not STOP EVERYTHING: that would also pause
email and invites, and the judge's memo in card 4 couldn't send), then Approve on Team #7's card.
The same two touches, clicked instead of spoken.

## Card 3b · 0:45–1:00 · A human decision, one click
**Screen:** `/ceo`, the hiring shortlist (the CEO was emailed when HR shortlisted).
**Say:** "HR ran a disclosed AI screen for a paid one-hour gig, a CPA to review our memos. The AI
never hires and never declines. I do."
**Do:** click Hire on the shortlisted candidate. Switch to the theater: the CEO's white cursor
appears and the touch counter ticks up by one.

## Card 4 · 1:00–2:35 · Finale: a judge talks to the firm
**Screen:** `/talk` on the judge's phone or laptop, the theater on the projector.
**Say:** "Judge, you're a Delaware C-corp. Tell U-Tax your numbers. Make them up if you like."
**Do:** the judge says yes to the transcription notice, answers four short questions, types an
email into the box, and confirms the read-back. Stay quiet near the mic until the page says the
intake is sent. On the theater: Intake → Tax → QA → Accounting cursors work.
**Say while it runs:** "The AI only points at the numbers. Tested code does the math, and a second
agent re-checks every quote before anything is sent."
**Payoff:** the memo lands in the judge's inbox in under 45 seconds, for example
"$85,165 → $450". A review-call block follows shortly after on the calendar pane (calendar trust
must already be AUTO, or it waits for a click).
**If it fails:** the judge emails the firm instead (the client inbox), and you narrate the same
cursors. If live mode breaks, flip to `THEATER_MODE=replay`.

## Card 5 · 2:35–3:00 · The whole night in 30 seconds
**Screen:** the theater. Press Replay.
**Say:** "Here's the whole night, replayed from the log."
**Payoff:** the cursors swarm, then fly into "Clients saved $X". The money panel reads "Clients
saved $X · the firm spent $Y (estimated)."
**Close:** "A firm that ran itself overnight, with one human who clicked when it mattered.
Educational estimates, not tax advice."

---

## Before going on stage (checklist)
- [ ] Sound check the brief audio, then **don't open `/ceo` again** until Card 1 (the login brief
      covers "since you were last here"; reset the stored last visit if you did)
- [ ] Theater in stage mode, live, last event less than 2 minutes old
- [ ] A shortlisted candidate waiting in `/ceo` (or skip card 3b and say why)
- [ ] Card 3a: `/ceo` voice mode tested on the stage mic (say "what's waiting for me?"),
      hands-free **off**, the laptop's speaker low and away from the mic (the firm's own voice
      must not reach it), social posts on AUTO or ASK (not already PAUSED), and Team #7's memo the
      **only** approval waiting (email on ASK). If voice mode is down, do card 3a's click fallback
- [ ] Between cards 3a and 3b, check the trust panel: email must read AUTO (the approval earns
      it), or card 3b's hire mail and card 4's memo wait. If it still says ASK, click AUTO for email
- [ ] `/talk` open on a second device; mic permission granted; a test run done at 10 AM
- [ ] Email trust AUTO by card 4 (else the judge's memo waits for a click: approve it live as the next touch)
- [ ] Backup: the 9 AM recording queued; `THEATER_MODE=replay` ready
- [ ] After the demo: resume social posts in `/ceo` (card 3a paused them)
