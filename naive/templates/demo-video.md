# U-Tax: "A day at U-Tax" (submission video shot list)

A recorded video of 2:30 to 3:00 for the submission. It shows that U-Tax works like a real tax and
accounting firm: each department is an agent doing its real-life job, end to end, with one human
CEO. The live pitch is `demo-script.md`; this is the recorded version, and it can show more
because nothing has to happen on cue.

**The honesty rules (the video is judged as proof):**
- Every shot shows something that happened in the event log. Nothing is mocked up in editing.
- Live shots run at 1× speed. Only the night replay (shot 15) is sped up, and it's labelled
  "Replay · 16×" on screen.
- Record the real crew on Naïve. If any shot was played by the local stand-in crew
  (`UTAX_DRIVER=local`), its voiceover must not say "agents on Naïve", and the caption says
  "stand-in".
- Use only masked labels ("Team #7", "Candidate B"). The client email in shots 3 and 8 comes from
  the team's own test address with a made-up company, never a real client.
- Each shot's **Proof** is the event or tool call behind it. Put it on screen as a small caption
  in the edit (for example `memo.sent · acct · 31 s after last fact`), with values copied from
  `/api/events`, never typed from memory.
- End on "Educational estimates, not tax advice."

---

## Shot list

| # | Time | Screen | What happens | Voiceover | Proof (event or tool call) |
|---|---|---|---|---|---|
| 1 | 0:00–0:08 | Theater, live, stage mode | Seven named cursors parked at their desks. The bottom bar reads "Firm run by 7 agents". | "This is U-Tax, a tax and accounting firm. Seven departments, and every one is an AI agent. One human: me, the CEO." | `GET /api/state` → `firm.agents: 7` |
| 2 | 0:08–0:20 | Theater, Social pane; then the post live on X or LinkedIn | Marketing's cursor types a caption, clicks Post: "Posted to x". Then it types a LinkedIn DM draft. | "Like any firm, it starts with business development. Marketing writes and publishes our posts, and drafts DMs that I send myself." | `post_social` → `post.published` (actor `mkt`); `draft_dm` → `dm.drafted` |
| 3 | 0:20–0:28 | Theater, Mail pane | A client email arrives and opens. A new row appears in Cases. | "A founder emails the firm about their Delaware franchise tax bill." | `email.received` (actor `intake`) |
| 4 | 0:28–0:36 | Naïve console, the U-Tax board | A card "case_N: extract facts" appears, assigned to `intake`. | "Our Chief of Staff is the managing partner. It assigns every step to the right department." | the Chief of Staff's `board_write` on the Naïve board (Naïve's own log) |
| 5 | 0:36–0:46 | Theater, Mail pane to Sheet | The Intake cursor highlights each number in the email and drags it into the sheet. | "Intake onboards the client. Every fact is quoted from the client's own words, or Intake asks again." | `extract_facts` → `facts.extracted` (with `quotes`) |
| 6 | 0:46–0:58 | Theater, Search pane, then Sheet | Tax types a query and Delaware cites appear. The sheet fills both methods, marks the winner and the total. | "Tax prepares the return math. It looks up the law, and tested code computes both methods. The AI only points at the numbers." | `law_search` → `law.searched`; `compute_tax` → `tax.computed` (`engine_version`) |
| 7 | 0:58–1:06 | Theater, Sheet | QA ticks every cell, then "✓ QA". | "QA is the partner sign-off: a second agent re-runs the math and re-checks every quote." | `qa_check` → `qa.passed` (`checks`) |
| 8 | 1:06–1:18 | Theater, Mail pane; then the test inbox | Accounting types the memo and clicks Send: "✓ Sent · N s after last fact". Cut to the inbox: the memo, with its `UTX-…` reference. | "Accounting releases the memo, and only once QA has passed. Here it is in the client's inbox." | `send_memo` → `memo.sent` (`memo_ref`, `seconds_since_last_fact`) |
| 9 | 1:18–1:26 | Theater, Phone pane | The memo, rendered on a phone screen. | "QA opens the memo on a real phone, to check it reads right where the client will read it." | `phone_check` → `phone.checked` (provider `mobilerun`) |
| 10 | 1:26–1:34 | Theater, Calendar pane | Intake drags a review-call block onto the calendar. | "Intake books the review call, like a firm's client coordinator." | `book_call` → `call.booked` |
| 11 | 1:34–1:46 | Theater, stage mode off: Money pane and the Cases tag | "Clients saved $X" and "Firm spent (est.)" tick up. The Cases header shows the CRM stage. | "Then the books. Every model call and every sponsor run is booked from the log, in code: what we saved clients against what the firm spent. No agent touches the ledger." | `cost.booked` (summed into `/api/state` `money`); `crm.synced` |
| 12 | 1:46–1:56 | Theater, Hiring pane; then the questionnaire mail | "1 applicant". The applicant gets the questionnaire, with the line "This screen is run by an AI agent. A human makes the final hiring decision." | "HR is recruiting a CPA for a paid one-hour gig. Every applicant gets a questionnaire, and is told an AI runs the screen." | `hr.applicant`; `hr.questionnaire_sent` (actor `system`) |
| 13 | 1:56–2:06 | Theater, Hiring pane; then the CEO's inbox | "screened · shortlisted", then "Shortlist ready · waiting on CEO". The CEO gets an email. | "HR screens the answers and shortlists. It never hires and never declines." | `get_candidate`, `hr_record` → `hr.screened`, `hr.shortlisted`; `ceo.notified` (reason `shortlisted`) |
| 14 | 2:06–2:24 | `/ceo` voice mode, then the theater (stage mode off) | The CEO says "Hire Candidate B." The firm reads it back, ending "Say confirm." The CEO says "Confirm." The theater's Hiring pane reads "1 hired", and the touches log shows a mic icon. | "The decisions that matter stay with the human. I make the hire, by voice, and the firm waits for my own confirm." | `decide_candidate` → `ceo.proposed`; `ceo.confirmed` (`via: "voice"`); `hr.hired` (actor `ceo`); `touch.operating` (`channel: "voice"`); `hr.decision_mailed` |
| 15 | 2:24–2:36 | Theater, `/?source=replay` at 16×, stage mode | The whole night replays: cursors swarm, then fly into "Clients saved $X". Label: "Replay · 16×". | "That was one client and one hire. Here's the whole night, replayed from the log." | `GET /api/events` (the full log) |
| 16 | 2:36–2:52 | `/ceo`, just logged in | The morning brief plays aloud and shows as text. | "Next morning, the Chief of Staff briefs me: clients served, saved against spent, and the one decision it needs from me today." | `firm_brief` → `brief.generated` (actor `cos`); `ceo.notified` (channel `login`) |
| 17 | 2:52–3:00 | Theater, stage mode | The three big numbers: clients served, human touches, saved vs spent. Then the end card. | "A real firm's departments, every one an agent, and one human. Educational estimates, not tax advice." | `GET /api/state` (`firm`, `touches`, `money`) |

If the cut runs long, shorten shot 15 first, then shot 4. Don't cut shots 5–8: the case going
through every department is the proof.

---

## Sponsor keys per shot, and the no-key fallback

| Shot | Needs | Without it |
|---|---|---|
| 2 | The Naïve social workspace with X and LinkedIn connected (`ADAPTER_SOCIAL=naive`; no sponsor key) | `ADAPTER_SOCIAL=local` writes the same `post.published` and the Social pane animates it, but there's no live post to cut to. Skip the X or LinkedIn cut. |
| 3, 8 | **AgentMail** (`ADAPTER_MAIL=agentmail`, the clients inbox) | `ADAPTER_MAIL=local`, then, from `backend/`, `python -m app.devtools inject ../fixtures/inbox/01-team7-happy.eml` (writes the same `email.received`). Shot 8 then has no inbox cut: hold on the theater's "✓ Sent" line instead. |
| 4 | Naïve (the live crew) | No board exists without Naïve: drop shot 4 and say the routing line over shot 5. |
| 9 | **Mobilerun** (`ADAPTER_DEVICE=mobilerun`; it also needs AgentMail) | `ADAPTER_DEVICE=local` renders the memo headlessly at phone size (`phone.checked`, provider `local`). Say "at phone size", not "on a real phone". |
| 10 | **AgentMail** calendar (`ADAPTER_CALENDAR=agentmail`) | `ADAPTER_CALENDAR=local` writes the same `call.booked`. No invitation reaches an inbox, so don't show one. |
| 11 | HubSpot for the CRM tag (`ADAPTER_CRM=hubspot`; optional) | `ADAPTER_CRM=local` still shows the stage tag. Don't say "HubSpot" over it. |
| 12, 13 | **AgentMail** (the hiring inbox, the questionnaire mail and the CEO's email) | With `ADAPTER_MAIL=local`, from `backend/`: `python -m app.devtools inject --inbox hiring ../fixtures/hiring/01-applicant-ea.eml`, then a second mail from the same sender (`POST /api/dev/inject-email` with `inbox: "hiring"`) as the questionnaire answer. Without AgentMail there are no real mails to cut to, so stay on the theater's Hiring pane. |
| 14, 16 | **ElevenLabs** (`ADAPTER_VOICE=elevenlabs`): the firm's spoken readback and the morning brief | Without the key, `/api/tts` and `/api/brief/audio` answer 503 and the page speaks with the browser's own voice (`speechSynthesis`). Listening is Chrome's speech recognition either way. If voice fails, click Hire: the touch shows `channel: "click"`, so say "one click", not "by voice". |
| all | **Naïve** (`NAIVE_API_KEY`: the seven agents) | The local stand-in crew (`UTAX_DRIVER=local`) plays the same tool calls. Use it only for a rehearsal or a labelled fallback, never as "agents on Naïve" (the honesty rules above). |

---

## Recording checklist

**Capture**
- [ ] **OBS Studio** (preferred): a Display Capture or Window Capture source, 1920×1080, 30 fps,
      recorded as MKV (survives a crash) and remuxed to MP4 (File → Remux Recordings). Add
      Desktop Audio so the firm's voice is captured, and record the mic on its own track.
- [ ] Or **Windows Game Bar**: **Win+Alt+R** starts and stops recording the active window,
      **Win+Alt+M** toggles the mic, and clips land in `Videos\Captures`. It records one app window
      only (not the desktop, and it doesn't follow you between windows), so record the theater,
      `/ceo` and the inbox as separate clips and cut them together.
- [ ] Do Not Disturb on (no notification toasts), and other tabs and apps closed.

**Browser**
- [ ] Chrome (speech recognition needs it), zoom 100%, full screen (F11). Full screen also hides
      the address bar, which must never show a token.
- [ ] Never record `.env`, a terminal holding keys, the admin token, or a real email address. The
      theater and `/ceo` show masked labels only. Check every frame of the inbox cuts (shots 8, 12
      and 13) before export.

**Theater**
- [ ] Live shots: the theater live (the default with a gateway), speed 1×, last event less than
      2 minutes old.
- [ ] Stage mode (press S) for the wide shots 1, 15 and 17. Stage mode **off** for shots 11, 12,
      13 and 14: the Money, Hiring and Human touches panes show only outside stage mode.
- [ ] The overnight part (shot 15) in **replay mode**: open `/?source=replay`, or press Replay in
      live mode, then 16× (4× if it's too fast to follow). Let the ending play out, through the
      cursors flying into "Clients saved".
- [ ] Trust staged for the shots you want: email and calendar invites on AUTO for shots 8 and 10
      (or the step waits for a click), and a candidate shortlisted before shot 14.

**Voice (shots 14 and 16)**
- [ ] `/ceo` voice mode tested first ("what's waiting for me?"), hands-free off, speaker low and
      away from the mic so the firm's voice can't confirm itself.
- [ ] Don't open `/ceo` before shot 16 is recorded, or the morning brief starts from that visit
      (or reset the stored last visit).

**Edit and export**
- [ ] Voiceover recorded separately, read from the table above, and laid over the clips.
- [ ] Proof captions added from `/api/events` (the honesty rules above), and "Replay · 16×" on
      shot 15.
- [ ] End card: "U-Tax: a tax and accounting firm run by 7 AI agents and 1 human · Educational
      estimates, not tax advice."
- [ ] Export 1080p MP4, 2:30 to 3:00. Check the submission form's length and size limits before
      the final export.
