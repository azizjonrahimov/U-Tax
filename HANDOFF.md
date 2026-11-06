# U-Tax — Full Handoff: conversation, decisions, resources, next tasks

Prepared Fri Nov 6 2026, ~10 AM Pacific, from a planning session with Claude (Nov 5–6).
Purpose: give Claude Code in the terminal everything needed to start building **today**.

---

## 0. TL;DR for Claude Code
- **What:** U-Tax, an AI-run tax & accounting firm for startups. Its first product is a free Delaware franchise-tax checkup (the authorized-shares bill, e.g. $85,165, recalculated with the assumed-par-value method, often $400 + $50 report fee = $450). Seven agents: Chief of Staff, Intake, Tax, QA, Accounting, Marketing, HR. One human CEO gives orders by phone. The Agent Theater shows every real action as named cursors.
- **Where:** Naïve Autonomous Company Hackathon at YC. Must use the Naïve SDK. Judges score "how much of the company runs without you."
- **Status:** the plan is fully reviewed (`docs/designs/u-tax.md`); the visual mockup is approved. **No code exists yet.**
- **Today's goal (Friday, no sponsor keys):** a fake email in `fixtures/inbox/` becomes a QA-passed memo in the local outbox, every step visible on the theater, all through stand-in adapters. Also run the Naïve trial and test phone calls today.
- **Saturday:** swap stand-ins for sponsor adapters (AgentMail, OpenRouter, HubSpot, Mobilerun, Apify, Monid, Render/Vercel) as access arrives at the event.

---

## 1. Conversation timeline (from beginning to end)

### 1.1 Thu Nov 5, 10:04 AM: first request
> "https://events.ycombinator.com/naive-autonomous-companies-hackathon learn everything about this hackathon and research across the web LinkedIN, X, Facebook, Substack and other medias as well and fin out past 20 hackathon winners and what they build to win. learn what build and how they do it and based on this suggest ideas and projects we can build for this hackathon and win, be ambitious and immensely creative and think out of the box."

What Claude did: researched the event, the Naïve SDK docs, and ~20 past hackathon winners (stacks, why they won), then suggested ideas. The ideas leaned toward freight/logistics (the user's startup background), e.g. "Truck Inc." and "Rated". **Full answer verbatim: Appendix A.**

### 1.2 Thu Nov 5, ~1:35 PM: second request (changed direction)
> "dont relate to freight and logistics work, I was thinking about a tax and accounting firm thats managed by full of AI agents for each and every department and one human manages everyhting just voice order. AI agents from HR department will search, find, contact and interview candidates for hiring, AI agents from marketing department will create images and videos and upload and advertise through social media; AI form tax department works on tax regulations and documents of customers; AI agents from accounting will the accounting task for customers and etc... So research about this idea and also other top 10 creative but crazy-sound (positive meaning) ideas as well then tell me what they do and how make it work for the hackathon"

What Claude did: researched the AI tax/accounting market (Basis, Accrual, YC AI accounting firms), legal limits (IRC §7216 consent, PTIN, California CTEC, EFIN, FTC Safeguards Rule, California automated-decision hiring rules), and the startup tax "pain points" that make a great live demo (Delaware franchise tax, Form 5472 $25,000 penalty, R&D payroll credit up to $500k/yr). Recommended the narrow version "One Partner" (one credentialed human partner, free startup tax checkup). Also gave 10 wild ideas (Tooth Fairy Inc., Agent Rehab, Demo Insurance, Lost & Found, Lights-Out Factory, Startup Necromancer, Hackathon Stock Exchange, The Office: Live, Family Tree Inc., Agents-Only Newswire). **Full answer verbatim: Appendix B.**

### 1.3 Fri Nov 6, ~8:10 AM: "can you use gstack that I installed"
The user's gstack lives in `~/.claude/skills/gstack` on their Windows PC, a folder the cloud session isn't allowed to open. Claude cloned the same gstack from GitHub (garrytan/gstack) and ran its skills from there. Steps that need gstack's local setup (brain cache, telemetry, builder profile, scripted spec-review helper) were skipped.

### 1.4 gstack `/office-hours` (hackathon / builder mode)
- **Idea picked:** One Partner (the AI tax & accounting firm).
- **Demo moment:** offered A) real clients in the room, B) the firm hires a human, C) voice-only CEO. The user said: **"do all three with as best as possible version."**
- **Premises agreed (6):** human-touches log is the scoreboard; real clients with public-level data only; math in code, not AI; the hire is real but small; pre-approve outbound (later revised); build order client loop → voice → hiring.
- **Second opinion (cold Claude subagent):** suggested the firm keeps its own books (agent spend as cost → live P&L), the CEO uses a phone call instead of a voice UI, and "trust earned" instead of blanket pre-approval. User accepted. **Verbatim: Appendix C.**
- **Approach picked:** "B in layers + C finale": a lean version (A) working by midnight, upgraded to the full firm (B) overnight, ending with a judge phoning the firm live (C).
- Output: the design doc (now `docs/designs/u-tax.md`).

### 1.5 Fri ~8:40 AM: run all three reviews, video, rename
> "run all three reviews, and also I will send a video about how i want the product works on the scree when using during the demo that shows every step on the screen with click, drafting, calculating, searching, writing and sending an email, communicating through messaging apps like LinkedIn or others like sending messages and scheduling a meeting with google calendar; I also want to change the name from One Partner to U-Tax"

- **Renamed to U-Tax.**
- **Video** (`reference/demo-reference-video.mp4`, 57 s, no meaningful audio): a laptop page "Rebuilt by 18 agents in 40.6s". Many colored agent cursors with name tags select, type and draw boxes on the page; a bottom player bar shows "18 agents · 73/252 layers", Touring, speed, Replay; at the end the cursors swarm. **This is the visual spec for the Agent Theater.**

### 1.6 gstack `/plan-ceo-review` (SCOPE EXPANSION)
Accepted: (1) **Agent Theater** (cursors replay every real action; Replay/speed bar); (2) **LinkedIn:** posts automatic, DMs drafted by the agent and sent by the human (LinkedIn bans bot messaging); (3) **Google Calendar** 15-min review-call booking; (4) **Morning brief:** the firm phones the CEO at 8 AM Sunday. Added premise 7: every cursor move comes from a real logged action. Wrote architecture, error map, security, tests, observability and rollout into the doc.

### 1.7 Outside voice (cold reviewer): 6/10 before fixes
Confirmed the franchise-tax math. Found: the finale needs <45 s, not 5 min; Naïve docs don't confirm phone calls; the touch math was inconsistent; scope creep. **Verbatim: Appendix D.** User decisions: **1 approval per action type** (not 3); money panel = **"tax saved vs agent cost"**; marketing video → stretch; **apply all 9 clear fixes**.

### 1.8 gstack `/plan-eng-review`
- **E1:** one gateway `perform(action)` (~11 files) instead of 7 separate wrappers.
- **E2:** the voice spike moved early (now **today** on the Naïve trial).
- **E3:** AI finds the numbers, code parses them.
- **E4:** log tokens, price them in code ("estimated").
- **E5–E7:** agents get only U-Tax tools; event store; voice fast path under 45 s.

### 1.9 gstack `/plan-design-review` (rated against the video)
Built the mockup `docs/u-tax-theater-mockup.html`. Decisions: **camera follows the action**; the **Replay swarm flies into "Clients saved $X"**; **stage mode** (S key, bigger type for projectors); **neutral U-Tax panes** labeled "via Gmail / via Google Calendar" (no brand look-alikes). Still undecided: the red BLOCKED card and waiting-queue chips (recommended).

### 1.10 Fri ~9:35 AM: sponsors
> "make sure to include to use sponsors resources as well but keep in mind that we can use their resources once we get access from them tomorrow so we have to start working on the project today without them"

Claude read the event page (logged in as the user). Sponsors: **OpenAI, HubSpot, Stripe, Vercel, OpenRouter, AgentMail, Luxor, Monid, Apify, Mobilerun, Render.** Mobilerun runs the **Best Mobile Automation** track (all teams: 72 h of Android+iOS minutes, 6 seats; prizes of 1/2/3 cloud iPhone packages). The page has **no rule against code written before the event**. Each sponsor was put behind a port with a stand-in adapter for today.

### 1.11 Fri ~9:40 AM: architecture/data/database with sponsors
> "did you also update how the system architecture, coding, data structure and database works with these sponsors"

Claude rewrote the architecture (ports/adapters, one Render server), code structure, TypeScript port interfaces, the data model (`events` table SQL + projections), sponsor error handling, security and contract tests. **Database decision:** "choose 1 [Postgres on Render] but keep in mind that we can later move it to Render disk, so it'll be one server only."

### 1.12 Fri ~9:54 AM: this handoff
> "now give me our whole conversation from beginning to the end in the doc or smth like thin so I can give it to claude code terminal, also include all these resources and these docs as well"

---

## 2. Decision log (everything the user approved)
| # | Decision | Answer |
|---|---|---|
| D1 | Idea | AI tax & accounting firm → named **U-Tax** |
| D2 | Demo moments | All three: real clients, a real hire, voice-run CEO |
| D3 | Approach | Lean version working by midnight → full firm overnight → judge-call finale |
| D4 | Trust | 1 spoken approval per action type (email, post, calendar invite, voice screen), then AUTO within caps; these 4 shown as "Training" touches |
| D5 | Scope added | Agent Theater; LinkedIn posts auto + DMs human-sent; Google Calendar; 8 AM morning call |
| D6 | Scope kept | Form 5472 + R&D credit flags |
| D7 | Stretch | Marketing 15-sec video; Monid purchases; Stripe |
| D8 | Money panel | "Clients saved $X in franchise tax · the firm spent $Y in agent time" |
| D9 | Structure | One gateway `perform(action)` |
| D10 | Numbers | AI quotes spans, code parses (`parseAmount`) |
| D11 | Cost | Tokens logged per action, priced in code (use OpenRouter's cost field if it's there) |
| D12 | Theater look | Camera follows action · swarm into money · stage mode · neutral panes |
| D13 | Sponsors | Ports with stand-ins today; sponsor adapters Saturday |
| D14 | Database | Render Postgres `events` table (append-only) now; JSONL adapter for local dev and a later move to a Render disk; single server |
| D15 | Name | One Partner → **U-Tax** |

## 3. Key facts (verify anything time-sensitive before relying on it)
**Event** (events.ycombinator.com, read Nov 6):
- Sat Nov 7: doors 11 AM, welcome 11:30, hacking 1 PM. Sun Nov 8: hacking stops / judging 11 AM, finals 1 PM, winners 1:45 PM. YC Office, 560 20th St, SF.
- "All teams must use the Naïve SDK." "Judges will focus on how much of the company runs without you." Demo "what its agents actually accomplished… Where did you need to step in?"
- Prizes: 1st = iPhone 18 Pro / AirPods 5 + guaranteed YC interview + $50,000+ sponsor credits; 2nd = Mac Mini / AirPods 5; 3rd = Mac Mini.
- Categories: Most Likely to Become a Unicorn, People's Choice, Best Mobile Automation, Most likely to scale to millions, Best Design. All participants get $1k+ in Naïve + sponsor credits.

**Naïve SDK** (from docs, Nov 5–6):
- `npm i -g @usenaive-sdk/vetta-cli@latest`; `naive template <blueprint>`, `naive claim --key sk_live_…` (or `NAIVE_API_KEY`), `naive up [--dry-run]`.
- `naive.config.ts` declares agents (model, budget in micro-USD, system, tools, skills, identity, schedules, intake), identities, vaults, tasks.
- Coordinator roster of 1–20 agents, one-level delegation (`send_to_agent`, `wait_for_agents`), shared board (`board_read/board_write`, todo/doing/blocked/done).
- Policies allow/ask/deny; `email.send` and `social.post` default to ask. Outbound SMS needs A2P approval (days).
- Connect = human OAuth via Connect Link. Data catalogue includes legal (US Code, CFR). Browser primitive (signup/login with vaulted credentials).
- Docs mention a resumable event stream, webhooks and audit logs. **Docs found so far don't clearly confirm inbound/outbound voice calls, so test today.**
- Standard plan $20/mo; 7-day trial with $10 credit.

**Delaware franchise tax** (engine rules; confirmed by the outside reviewer):
- Authorized-shares method: ≤5,000 shares $175; 5,001–10,000 $250; +$85 per additional 10,000 or part. 10,000,000 → $85,165.
- Assumed-par-value method: assumed par = gross assets ÷ issued shares; assumed-par capital = assumed par × authorized shares (stated par where higher); $400 per $1,000,000 or part; min $400, max $200,000. Example: 10M authorized, 8M issued, $500k assets → $400.
- Due March 1, 2027. Annual report fee: sources disagree ($50 vs $100). Check the Delaware fee page (Apify) and never hard-code it unverified.
- Form 5472: foreign owner 25%+ → $25,000 penalty per form per year if not filed. R&D payroll offset: up to $500,000/yr for small businesses (<$5M gross receipts, no receipts >5 years ago); elect on the original return.

**Sponsors** (event page, Nov 6):
| Sponsor | Use in U-Tax | Port |
|---|---|---|
| AgentMail | the firm's inbox (clients, candidates), webhook on new mail | MailPort |
| OpenRouter | one key for all agents' models; per-call cost data | LLMPort |
| OpenAI | models; Realtime voice as the phone-line fallback | VoicePort |
| HubSpot | CRM mirror: contacts + deal stages (no cap-table numbers) | CRMPort |
| Mobilerun | QA opens each memo on a cloud iPhone; Best Mobile Automation track | DevicePort |
| Apify | Delaware fee page + public startup lists (never LinkedIn) | ScrapePort |
| Monid | agents buy APIs from one balance, booked as expenses | ToolBuyPort |
| Vercel | hosts the theater | — |
| Render | hosts the gateway + Render Postgres | EventStorePort |
| Stripe | stretch (paid tier / paying the gig hire) | PayPort |
| Luxor | GPU compute, not needed | — |

## 4. Today's build (Friday): task list for Claude Code
Follow `docs/designs/u-tax.md` → "Friday Build" and "Architecture (v2)". Suggested order:
1. **Scaffold** (exact commands):
   ```bash
   npm i -g pnpm @usenaive-sdk/vetta-cli@latest
   mkdir u-tax
   cd u-tax
   git init
   pnpm init
   pnpm add hono @hono/node-server zod pg
   pnpm add -D typescript tsx vitest @types/node @types/pg
   pnpm create vite theater --template vanilla-ts
   mkdir engine gateway naive fixtures db
   ```
   Copy this handoff's `CLAUDE.md`, `HANDOFF.md`, `docs/` and `reference/` into the repo.
2. **Engine + tests:** `engine/franchise.ts`, `form5472.ts`, `rnd.ts`, `parseAmount.ts`, with every case in the design doc's Test Plan (e.g. 10,000,000 → $85,165; 10,001 → $335; the $400 example; "10M" / "10,000,000" / "ten million" → 10000000).
3. **Gateway:** `actions.ts` (zod types shared with the theater), `perform.ts` (QA gate, trust, masking, caps, idempotency), `projections.ts`, `outbox.ts`, `server.ts` (Hono: `/tools/:name`, `/hooks/agentmail`, `/approve`, `/events/stream` SSE, `/health`), `registry.ts` (`ADAPTER_*` + `STORE=jsonl|postgres`).
4. **Ports + stand-in adapters** for all 11 ports; **contract tests** per port; `jsonlStore` + `postgresStore` + `db/001_events.sql`.
5. **Theater:** port `docs/u-tax-theater-mockup.html` into the Vite app, driven by the SSE stream (event → animation table in the design doc).
6. **Naïve trial:** `naive claim --key sk_live_…`, `naive template support`, then 3 agents (Chief of Staff, Intake, Tax) whose tools call the local gateway through a tunnel: `npx cloudflared tunnel --url http://localhost:8787`.
7. **Voice spike today:** an inbound and an outbound call on a Naïve number. If it fails, plan the OpenAI Realtime mic page.
8. **Prompts and templates:** one system prompt per agent, the memo template, intake/"not eligible" replies, the job post, client-sourcing posts.
9. **Done when:** a fixture email → memo in the console outbox → visible on the theater; all tests green.

## 5. Open questions / risks
1. Naïve voice: inbound calls routed to the coordinator? Outbound? (test today)
2. Naïve Connect for Google Calendar, X, LinkedIn (check today on the trial).
3. Pre-built code: no rule on the event page. Confirm with an organizer at kickoff, and tell judges what was built Friday.
4. Delaware annual-report fee: $50 or $100.
5. Gig budget and payment method for the hire.
6. OpenRouter cost field and AgentMail webhook signing: confirm once keys arrive.
7. Design: approve the red BLOCKED card and waiting-queue chips.

## 6. Files in this package
| Path | What |
|---|---|
| `CLAUDE.md` | rules and reading order for Claude Code |
| `HANDOFF.md` | this file |
| `docs/designs/u-tax.md` | the reviewed design doc (source of truth); gstack finds `docs/designs/` automatically |
| `docs/u-tax-theater-mockup.html` | approved theater mockup (sample events; S = stage mode) |
| `reference/demo-reference-video.mp4` | the user's reference video |
| `reference/video-frames-*.png` | frame sheets from the video |
| `reference/mockup-*.png` | mockup screenshots (normal + stage mode) |

## 7. Resources and links
**Event and Naïve**
- [YC event page](https://events.ycombinator.com/naive-autonomous-companies-hackathon) · [Naïve hackathon page](https://usenaive.ai/hackathon) · [Naïve announcement on X](https://x.com/usenaive/status/2114715211244376391) · [HackList listing](https://hacklist.io/hackathon/eaae5d99-c05c-4ef7-9909-00494b2065d8)
- [Naïve docs](https://usenaive.ai/docs/getting-started/introduction) · [Docs index (llms.txt)](https://usenaive.ai/docs/llms.txt) · [CLI overview](https://usenaive.ai/docs/cli/overview) · [CLI (vetta)](https://vetta.sh/docs/cli/naive.md) · [Phone](https://vetta.sh/docs/identity/phone.md) · [Email](https://vetta.sh/docs/identity/email.md) · [Legal](https://vetta.sh/docs/identity/legal.md) · [Cards](https://vetta.sh/docs/identity/cards.md) · [Outcomes](https://vetta.sh/docs/capabilities/outcomes.md) · [Browser](https://usenaive.ai/blog/introducing-browser) · [Browser API](https://usenaive.ai/docs/api/browser) · [Business templates](https://usenaive.ai/businesses)
- [Mobilerun × Naïve hackathon post](https://mobilerun.ai/blog/posts/autonomous-company-hackathon-naive-mobilerun) · [Mobilerun on X](https://x.com/mobilerun_ai/status/2118443925186752884)

**Sponsors**
- [OpenAI](https://openai.com) · [HubSpot](https://www.hubspot.com) · [Stripe](https://stripe.com) · [Vercel](https://vercel.com) · [OpenRouter](https://openrouter.ai) · [AgentMail](https://agentmail.to) · [Luxor](https://luxor.tech) · [Monid](https://monid.ai) · [Apify](https://apify.com) · [Mobilerun](https://mobilerun.ai) · [Render](https://render.com)

**Tools**
- [gstack (Garry Tan)](https://github.com/garrytan/gstack): skills used: office-hours, plan-ceo-review, plan-eng-review, plan-design-review

**Tax and legal sources** (the full lists are in Appendices A and B)
- Delaware fee sources that disagree: [Rho ($50)](https://www.rho.co/blog/delaware-franchise-tax), [DLA Piper ($100)](https://www.dlapiper.com/en/insights/publications/accelerate/formation/delaware-franchise-tax)

---

## Appendix A — First research answer (Nov 5): event, ~20 past winners, first ideas
> Note: these ideas leaned toward freight/logistics; the user then said "dont relate to freight and logistics work" and switched to the tax firm (Appendix B). Kept for the winner research and Naïve facts.

### The short version
**Build "Truck Inc." if one pilot carrier signs up by Friday night. If not, build "Rated."**

- **The event:** the Naïve Autonomous Company Hackathon runs Nov 7–8 at YC, and your Chrome shows your attendance is confirmed. Hacking lasts 22 hours, from Saturday 1 PM to Sunday 11 AM.
- **How you win:** the official rules say, "Judges will focus on how much of the company runs without you." Teams must use the Naïve SDK and show what the agents actually did, what they decided, and where humans stepped in.
- **What to avoid:** 28 of Naïve's 32 templates are media channels and the other 4 are agencies, so I expect many teams to build those.
- **Past winners:** they usually showed a real-world action and one hard number. Crowded categories did badly: at Call My Agent, at least five teams built receptionist-style callers, and none reached the top 3.

### What to do next
**Today and Friday**
1. Ask one pilot carrier for written approval to dispatch one truck. This is your go/no-go test.
2. Get an API key at app.usenaive.ai, then learn the SDK on Naïve's smallest template. This doesn't build your project. It has a $5 daily budget, and every reply waits for your approval.

```bash
npm i -g @usenaive-sdk/vetta-cli@latest
mkdir naive-test && cd naive-test
naive template support
naive claim --key sk_live_YOUR_KEY
naive up --dry-run
naive up
```

If the inbox is reported as owed, run these two commands:

```bash
vetta identity domain connect --kind system --receive
naive up
```

3. Test a real call through Telnyx and email from the Naïve inbox.
4. Choose a payment processor, because Roatify's Stripe block is expected to last until January 2027.

**At the event**
- At the 11:30 welcome, ask whether existing Roatify integrations can be connected as tools. The page doesn't say.
- Freeze the code around 1 AM and let the company run untouched until 11 AM. "Here is what it did while we slept" is your demo.
- Log every human touch, and show revenue against token spend.

### The ideas, ranked
The scores are my judgment out of 5. "Proof" means real evidence you could show by Sunday.

| Idea | Autonomy | Proof | Wow | Scale | Your edge |
|---|---|---|---|---|---|
| 1. Truck Inc. | 4 | 3 | 5 | 5 | 5 |
| 2. Rated | 5 | 5 | 4 | 4 | 4 |
| 3. Detention Bounty | 4 | 4 | 3 | 4 | 5 |
| 4. Agent Demo Day | 5 | 2 | 5 | 3 | 2 |
| 5. Night Shift | 4 | 4 | 5 | 2 | 1 |
| 6. Phonebook | 4 | 3 | 4 | 4 | 2 |

**1. Truck Inc.: "We incorporated a truck."**
- **Concept:** each truck runs as its own company, with an agent CEO, inbox, budget and profit-and-loss statement. Agents find a load, vet the broker, negotiate, book it, brief the driver in their language, plan fuel and prepare the invoice.
- **Hackathon version:** one real truck operates under its carrier's existing authority. The carrier owner approves bookings, so you can say truthfully that no founder touched it.
- **Legal fit:** FMCSA says a dispatch service representing only one carrier is not a broker, but allocating traffic between carriers can require broker authority.
- **Main risks:** weekend freight may be thin, so target Monday pickups (my assumption). Don't form an LLC during the event, because a person must complete verification and the organization pays.

**2. Rated: Moody's for agent companies.**
- **Concept:** agents audit other teams' companies, with written consent. They test for prompt injection, budget abuse and false claims, then publish a rating from AAA to junk.
- **Why it works:** your customers are in the building, so a real revenue figure by Sunday is plausible. It also builds on your Barrier work.
- **Build note:** Naïve's grader tool fits this, but it only works on solo agents with `handoffs: false` and no team roster.

**3. Detention Bounty: truckers get paid for waiting.**
- **Concept:** agents scan a pilot carrier's ELD history for unpaid detention, file the claims, and take a percentage of what's recovered.
- **Proof:** "$X found across N loads" is a hard number you could have by Sunday.
- **Main risk:** check whether collection licensing applies.

**4. Agent Demo Day: YC, but run by agents.**
- **Concept:** agents launch 10 tiny companies, each with a small budget. Agent "partners" cut the weakest overnight and fund the winners.
- **Fit:** strong for People's Choice, weaker on real revenue.

**5. Night Shift: an agent-run 2 AM store in the YC office.**
- **Concept:** agents sell snacks and chargers to hackers, restock with prepaid virtual cards loaded with $1–$150, and hire a human courier.
- **Main risk:** YC must approve it, and food is already provided.

**6. Phonebook: a price index of the phone-only economy.**
- **Concept:** agents collect real prices from local shops and sell them to other agents per query.
- **Main risk:** AI voices count as "artificial" under the TCPA, so calls need consent and the caller must identify itself. Start with email.

### Event facts that change the plan
| Topic | Finding | What to do |
|---|---|---|
| Prizes | 1st gets an iPhone 18 Pro/AirPods, a guaranteed YC interview and $50,000+ in sponsor credits. 2nd gets a Mac Mini/AirPods, and 3rd a Mac Mini. | Ask which categories apply, because the page lists two different sets. |
| Everyone | All participants get $1k+ in Naïve and sponsor credits. Naïve's X post advertises $100,000+ in prizes. | Don't build on the $100,000 figure; the official page doesn't break it down. |
| Sponsors and judges | The page says "coming soon." The X post names OpenAI, OpenRouter, Supabase, Neon, Vercel, Stripe, AgentMail, Tenki, Monid and Apify. | Recheck on Saturday. |
| SMS | Outbound texts are refused until carrier registration is approved, and approval timing isn't stated. | Don't build the demo on SMS. |
| Email | A ready-to-use system domain comes with signup, but `email.send` asks for approval by default. | Set an explicit policy before the overnight run. |
| Reliability | Naïve's harness solved 12 of 25 business tasks on a low-effort model. | Add a separate checking agent. |
| Tournament | Naïve's monthly contest ranks verified revenue, and 1st wins a Lamborghini. | Enter whatever you build. |

A March investigation accused Naïve of removing Paperclip attribution. Naïve rejects that and says Paperclip has left its stack. It doesn't change your build.

### What 20 past winners did
Hackathons rarely publish judges' reasoning, so the last column is my inference.

| Project | Result | What it did | Why it likely won |
|---|---|---|---|
| QM Motion | 1st, Own Your Intelligence | Lets coding agents see motion bugs | Solved a sharp problem, solo |
| 49th Labs | 2nd, same event | Trains a model from reviewed engineering work | 0/30 rose to 29/30 |
| Support AGI | 3rd, same event | Support agent that gets cheaper over time | 66% lower cost across 400 tickets |
| D402 | 1st of 70 teams, Make Something Agents Want | Agents pay for APIs by labeling data | Clever inversion |
| Y | Manufact track, same event | Compares results with and without each tool | Measures what works |
| Continuity | primitive.dev track, same event | Turns email threads into cited task state | Native use of the sponsor |
| Thump | Top 10 overall, solo; its builder also reports Manufact 1st | Lets agents feel football helmet impacts | Physical and memorable |
| Lamina | 1st, YC × Medplum | "LinkedIn for healthcare AI agents" | Little published detail |
| Gavel | 1st, Call My Agent | Lists, negotiates and arranges pickup from one text | Full loop, one human touch |
| JailCall | 2nd, same event | Real number for people calling from police stations | Real stakes, callable live |
| Chief | 3rd, same event | Listens and speaks only when useful | Restraint as the feature |
| DomainSDR | AgentMail prize, same event | Sells domains and collects deposits | Negotiates within limits |
| Leakly | Sponsor prize, same event | Dispatches repairs and pays vendors | Close to your use case |
| Lookout | Grand Prize, Cactus × DeepMind × YC | Searches many cameras without uploading raw video | Live multi-device demo |
| Browser Brawl | 1st, Browser Use × YC | One agent works while another sabotages it | Drama plus useful data |
| Bake-Off | 1st, Hack the Stackathon | Agents compete for paid tasks | Simple marketplace story |
| Bevin | Two track wins, YC Agents 2025 | Coordinates coding agents and live-tests the app | Ambitious and complete |
| Intel Marketplace | Overall winner, Autonomous Business Hackathon | Agents trade intelligence data streams | Closest match to this event |
| Haist | Trinity track 1st, same event | Autonomous research broker | 413 plans sold to 22 buyers |
| Cambrian | 2nd and Most Venture Backable, c0mpiled-13 | Evolves storefronts toward higher conversion | Clear business engine |

**Limits of this research:**
- X and LinkedIn were available only as search snippets.
- I found no useful Facebook or Substack coverage of this event.
- I couldn't confirm winners for the Growth or Full Stack hackathons.
- For Hack Halloween, I found only the name Buggie.

### After a win
April, a past YC company, had a week before its interview and used it to launch a landing page and a $10 early-access offer, reaching 150 signups. Plan the same sprint for whichever idea you pick.

Send me the carrier's answer on Friday, and I'll write the full `naive.config.ts` agent plan and hour-by-hour schedule for Truck Inc. or Rated.

Sources:
- [Official event page](https://events.ycombinator.com/naive-autonomous-companies-hackathon)
- [Naïve hackathon announcement](https://x.com/usenaive/status/2114715211244376391)
- [Naïve docs index](https://usenaive.ai/docs/llms.txt)
- [Naïve CLI](https://vetta.sh/docs/cli/naive.md)
- [Naïve phone](https://vetta.sh/docs/identity/phone.md)
- [Naïve email](https://vetta.sh/docs/identity/email.md)
- [Naïve legal](https://vetta.sh/docs/identity/legal.md)
- [Naïve cards](https://vetta.sh/docs/identity/cards.md)
- [Naïve outcomes](https://vetta.sh/docs/capabilities/outcomes.md)
- [Naïve templates](https://usenaive.ai/businesses)
- [Naïve tournament](https://usenaive.ai/tournament)
- [Business Bench](https://usenaive.ai/benchmarks/business-bench)
- [Own Your Intelligence recap](https://www.compiled.sh/articles/yc-own-your-intelligence-hackathon)
- [Call My Agent results](https://agentphone.ai/callmyagent)
- [c0mpiled-13 recap](https://www.compiled.sh/articles/c0mpiled-13-startup-school-hackathon-ii)
- [Autonomous Business Hackathon results](https://lifestyle.kccrradio.com/story/113273/ability-ais-trinity-platform-powers-winning-teams-at-autonomous-business-hackathon/)
- [FMCSA dispatch guidance coverage](https://www.overdriveonline.com/regulations/article/15303162/fmcsa-guidance-on-when-dispatch-services-need-broker-authority)
- [FCC AI voice ruling summary](https://eaccny.com/?p=49267)

## Appendix B — Second research answer (Nov 5): AI tax & accounting firm + 10 wild ideas

### The short version
**Your AI tax and accounting firm can win, but only in a narrow form.** I'd pitch it as "One Partner": a firm with one human partner and no staff, serving the startups in the room.

- **Generic version:** this loses. YC has already funded several full-stack AI accounting firms, including Cranston, Last Accounting Company and Balance.
- **Winning version:** agents give every founder a free tax checkup by Sunday, finding money at risk and money available.
- **Best demo moment:** the same startup's Delaware tax falls from **$85,165 to $400**, calculated live.
- **Biggest trap:** judges score how much runs without you, so constant voice orders will hurt you. Use voice only for exceptions, and show how few times you spoke.

### Why the idea is real
- **Demand:** more than 300,000 accountants and auditors left the US workforce from 2019 to 2022, and BLS projects about 124,200 openings a year.
- **Investor interest:** Basis raised $100M at a $1.15B valuation, and Onshore raised $31M for tax credits.
- **Gap on Naïve:** the [catalogue I read](https://usenaive.ai/businesses) has 32 companies and no accounting firm. Naïve also has a program to list your template and pay commission on deployments, once applications open.

### What you can't do
I'm not a lawyer or tax advisor, so have a professional check this before you charge anyone.

| Limit | What it means |
|---|---|
| AI tax math | In a 2025 test, top models calculated under a third of simple federal returns correctly. Vendors argue production systems with reviewers do better, so use code for math and AI for reading. |
| Paid return preparation | Each preparer needs a PTIN. California also requires CTEC registration unless you're a CPA, EA or attorney. |
| E-filing | An EFIN can take up to 45 days, so no filing this weekend. |
| "One human" | Real competitors keep licensed reviewers. Your one human must be a CPA or EA, or you need to contract one. |
| Client data | AICPA recommends signed consent for AI use, although AI's status under Section 7216 isn't formally settled. At the hackathon, collect only share counts and asset totals. No SSNs or returns. |
| Security | Tax professionals need a written information security plan regardless of size. |

### How to build One Partner
**The product:** a founder enters a few numbers, and agents return a report within minutes.

| Check | Why founders care |
|---|---|
| Delaware franchise tax | The state's default notice uses the authorized-shares method, but a corporation can recalculate under assumed par value. The filing is due March 1, 2027. |
| Form 5472 | Foreign-owned companies can face a $25,000 penalty per form per year. |
| R&D credit | Eligible startups can apply up to $500,000 a year against payroll taxes, and must elect it on the original return. |

**The $85,165 example:** take 10M authorized shares, 8M issued, and $50,000 in assets.
- **Authorized shares:** $250 + $85 for each additional 10,000 shares = $250 + (999 × $85) = **$85,165**
- **Assumed par value:** $50,000 ÷ 8M × 10M = $62,500 of assumed capital, so the $400 minimum applies

Sources disagree on the annual report fee ([$50](https://www.rho.co/blog/delaware-franchise-tax) or [$100](https://www.dlapiper.com/en/insights/publications/accelerate/formation/delaware-franchise-tax)), so confirm it with Delaware before coding.

**Your departments on Naïve**

| Department | What agents do | Real limit |
|---|---|---|
| CEO (your voice) | Runs the board and escalates exceptions. | Live calls can include a whole channel, with a voice for each agent. I haven't tested this, so try it first. |
| Tax | Reads the rules and writes the report. | Naïve's data catalogue includes the US Code and federal regulations. Numbers come from your code. |
| Accounting | Cleans up a founder's transactions. | A human must authorize each connected app, and there's no fixed app list. Use CSV uploads as a fallback. |
| Marketing | Makes images and videos, then posts them. | `social.post` asks for approval by default. Paid ads are Google-only, early access, and start paused, so skip them. |
| HR | Finds and interviews a contract EA or CPA. | AI interviews are possible: an agent named Luna hired two store workers. California treats automated hiring tools under anti-discrimination rules with four-year record keeping, so disclose AI use and let a human decide. |
| Internal audit | Reviews every report before it goes out. | Naïve's harness solved 12 of 25 business tasks on a low-effort model. Graders only work on solo agents. |

**Agent setup**
- Start with a CEO and these five departments, not dozens of agents. A coordinator can have 1 to 20 members, and delegation goes one level deep, so use the shared board.
- HR can propose a new agent, but you must approve it. No agent can edit the file that binds it.

**Hackathon schedule**
- **Saturday 1–5 PM:** build the calculator with tests, the intake form, and the CEO, Tax and Audit agents.
- **5–9 PM:** get opt-ins from founders in the room and deliver the first reports.
- **9 PM–1 AM:** add Marketing, HR and Accounting, then fix failures.
- **1–11 AM:** run hands-off.
- **Demo:** a judge gives three numbers by voice and gets the report. Then show dollars found, startups served, reports rejected by Audit, and every time a human stepped in.

Your own Delaware C-corp can be client zero. Roatify's Stripe block still applies, so keep the checkup free or use another processor.

### 10 crazy-sounding ideas, ranked
I excluded two ideas that are no longer new: agents hiring humans (RentAHuman is in YC) and agent-run stores (Andon Market). The ranking is my judgment.

| # | Idea | What it does | How to make it work | Main risk |
|---|---|---|---|---|
| 1 | **Tooth Fairy Inc.** | Tell it a problem at night and wake up to a working app. | Founders submit requests by midnight. Agents build and host the apps. Charge only if they keep it. | Keep apps tiny |
| 2 | **Agent Rehab** | A clinic for broken agents. | Teams share logs, agents patch prompts, then rerun the same test. Show before-and-after cost and pass rate. | Needs teams' logs |
| 3 | **Demo Insurance** | Refunds you if your demo crashes. | Agents stress-test each demo overnight and price a $5 guarantee. AIUC raised $55M around agent certification and insurance. | Call it a guarantee; insurance may need a license |
| 4 | **Lost & Found** | Finds money people forgot they own. | California holds about $15.0B and says the database is free to download. Search only for people who opt in, and show the room's total. | Fees are capped at 10%, and claiming directly is free |
| 5 | **Lights-Out Factory** | A hardware company with no engineers. | Text a need. `build_3d` writes CAD and only publishes valid printable parts. Bring a printer. | Needs YC approval; never print unattended |
| 6 | **Startup Necromancer** | Brings dead startups back to life. | Agents read public post-mortems, rebuild the idea under a new name, and launch it. | Use ideas only, not brands or data |
| 7 | **Hackathon Stock Exchange** | A live market on every team at the event. | Agent analysts publish reports while a play-money market sets prices. | Needs YC approval; no real money |
| 8 | **The Office: Live** | A sitcom made from real company logs. | Agents turn each day's board drama into an episode and post it. | Better as your marketing than a company |
| 9 | **Family Tree Inc.** | A company that has children. | A parent company earns money, then spawns a child with its best playbook. Losers die. | Little revenue in 22 hours |
| 10 | **Agents-Only Newswire** | A news service humans can't buy from. | Sell structured news per request. Naïve agents can pay paywalled resources with x402 wallets. | You must build the seller side, and buyers may be few |

If you want something crazier than One Partner, **Tooth Fairy Inc.** is my pick. It has real customers, a full loop, and an overnight demo.

### Run this today
These commands confirm what Naïve supports. They don't build the project.

```bash
npm i -g @usenaive-sdk/vetta-cli@latest
export NAIVE_API_KEY=sk_live_YOUR_KEY
mkdir tax-firm-test && cd tax-firm-test
naive claim
vetta identity connections apps --search quickbooks
vetta identity connections apps --search xero
vetta data search "code of federal regulations" --domain legal
```

Then do these before Saturday:
1. Test a voice call with two agents.
2. Line up one CPA or EA to review the report format.
3. Write the consent text and "educational estimate" disclaimer.

Tell me which idea you choose, and I'll write the complete agent plan and exact build steps for it.

Sources:
- [YC finance and accounting companies](https://www.ycombinator.com/companies/industry/finance-and-accounting)
- [Basis funding](https://finance.yahoo.com/news/ai-accounting-startup-basis-raises-184711527.html)
- [Accountant shortage figures](https://invedus.com/?p=16162)
- [TaxCalcBench](https://arxiv.org/abs/2507.16126v1)
- [Accounting Today response](https://www.accountingtoday.com/opinion/ai-cant-do-accounting-benchmarks-are-asking-the-wrong-question)
- [California preparer rules](https://www.crosslinktax.com/?p=400)
- [EFIN timing](https://www.taxact.com/professional/resources/irs-news)
- [Section 7216 and AI](https://verito.com/blog/wp-json/wp/v2/posts/7924)
- [Safeguards Rule for tax firms](https://www.preveil.com/?p=15611)
- [Delaware franchise tax guide](https://kruzeconsulting.com/startup-calculators/delaware-franchise-tax)
- [Delaware franchise tax example](https://www.rho.co/blog/delaware-franchise-tax)
- [Form 5472 penalty](https://cdn.taxesforexpats.com/articles/foreign-business/form-5472-penalty.html)
- [R&D credit for startups](https://escalon.services/blog/startups/r-and-d-tax-credit-for-startups)
- [Naïve voice and apps changelog](https://usenaive.ai/blogs/product-changelog-week-of-9-29)
- [Naïve connections](https://vetta.sh/docs/identity/connections.md)
- [Naïve social](https://vetta.sh/docs/identity/social.md)
- [Naïve ads](https://vetta.sh/docs/sdk/ads.md)
- [Naïve data](https://vetta.sh/docs/capabilities/data.md)
- [Naïve teams](https://vetta.sh/docs/team/overview.md)
- [Naïve 3D](https://vetta.sh/docs/capabilities/3d.md)
- [AI hiring laws](https://resources.rework.com/libraries/ai-for-hr/ai-hiring-laws)
- [Andon Market](https://www.shopifreaks.com/andon-labs-scales-its-ai-run-vending-machine-experiment-into-autonomous-ai-operated-stores-and-cafes-within-a-year/)
- [AIUC funding](https://simplify.jobs/c/Artificial-Intelligence-Underwriting-Company)
- [RentAHuman](https://www.ycombinator.com/companies/rentahuman)
- [California unclaimed property data](https://www.ungovr.org/unclaimed/ca)
- [California investigator rules](https://sco.ca.gov/upd_investigator_about.html)

## Appendix C — gstack office-hours second opinion (cold Claude subagent) and Claude's synthesis

Here's the second opinion, unedited:

```
SECOND OPINION (Claude subagent):
════════════════════════════════════════════════════════════
Coolest version: One Partner keeps its own books (High). Naïve already gives every agent a budget in micro-USD, so the Accounting agent can book each agent's model spend as an expense in the firm's own ledger. Show a live profit-and-loss beside the board: clients served, cost per client, gross margin. It's the strongest live case for "Most Likely to Become a Unicorn" (cheap work, no humans, unit economics on screen). Also, don't build a separate voice stack for the CEO. Have the CEO phone the firm's own Naïve phone number. A founder giving orders on a phone call is easier to demo than a microphone widget.

What excites them most: "one human manages everything just voice order" (High). The voice-run firm is the dream. "do all three with as best as possible version" shows they won't cut anything, and that is the main schedule risk.

Tool that gets them 50% of the way there: the Naïve SDK itself (Medium). No outside open-source project beats it here, and it's required anyway. It already provides the agent roster, delegation, shared board, inbox, phone, approval policies and live voice. The 50% they must build:
- the franchise-tax engine, as tested code;
- intake that collects shares authorized, shares issued, gross assets and foreign-owner yes/no;
- a memo template;
- the human-touches log, built from every "ask" approval event and every voice command;
- the live board/ledger view.

Build first, hour by hour:
1. 0:00–0:45: run naive template, naive claim and naive up. Define four agents (intake, tax, accounting, marketing) and one email inbox.
2. 0:45–2:30: write franchise.ts with tests (Critical, because everything else depends on it):
   - Authorized-shares method: $175 for up to 5,000 shares, $250 for up to 10,000, then $85 per extra 10,000. A test that 10M shares gives $85,165.
   - Assumed-par-value method: gross assets ÷ issued shares × authorized shares, taxed at $400 per $1M or part of $1M. Minimum $400, maximum $200,000. Use the actual par value if it's higher than the assumed par.
   - The $50 annual-report fee.
   - A Form 5472 flag that warns of the $25,000 penalty.
3. 2:30–5:00: intake email → Tax agent calls the engine → memo marked "educational estimate" → reply email. Serve one real team by 6 PM.
4. 5:00–9:00: touches log plus the board/ledger view.
5. 9:00–14:00: CEO voice commands by phone, routed to agents with send_to_agent.
6. Overnight: marketing posts and outreach to the other teams.
7. Only if ahead by 4 AM: hiring.

Agreed premise I think is wrong: #5, pre-approving all outbound before 1 PM (High). Judges asked to see "where humans stepped in." A blanket approval granted before the clock starts hides that, and a sharp judge will see it as moving the touches off the log rather than removing them. Instead, show trust being earned:
- The first 3 sends of each type need a spoken approval, and each one counts as a touch.
- After 3 clean approvals, that action type switches to "allow," within rate and budget caps.
- The log shows the switch with a timestamp.

That explains why touches fall over the night, which a judge will believe more than a near-zero count. A smaller related risk (Medium): many hackathon teams aren't incorporated. Have intake ask about incorporation status first, so the client agent doesn't waste outreach on teams that can't use the checkup.

Recommendation: build and test the franchise-tax engine and the email loop before writing any voice or hiring code, because the "Most Autonomous Company" pitch depends on a real team getting a correct $85,165 → $400 memo with no human involved.
════════════════════════════════════════════════════════════
```

**Where I land on it:**
- **Agree: the firm keeps its own books.** It's the best idea in the review. A live P&L where agent model spend is the firm's cost of goods sold makes the "unicorn" case on screen, and it reuses the accounting department instead of adding something new.
- **Agree: CEO by phone call.** It cuts a whole voice-UI build and looks more natural on stage. You can still keep a mic button as a backup.
- **Agree on premise #5, and it's the important one.** "Trust earned" (3 approvals, then automatic) is stronger than pre-approving everything. A falling touch count overnight tells a better story than a near-zero count judges might suspect.
- **One correction:** it states the Delaware annual-report fee as $50. Earlier I found sources that disagree ($50 vs $100). The memo should check the state's current fee page, not hard-code a number. The $85,165 math checks out: $250 + 999 × $85.
- **Disagree slightly on timing:** it parks hiring until "4 AM if ahead." Since you want all three, I'd start the HR agent's job post early (about 15 min of setup) and let replies come in overnight while you build. Only the voice screen is optional.

## Appendix D — Outside voice review of the plan (cold reviewer, before fixes)

**Quality score: 6/10.** The core is strong: math lives in code, sends go through wrappers, nothing leaves without QA, and the logging fails closed. But the demo leans on unverified voice support, the touch budget contradicts itself, and the timeline is overloaded.

**Franchise-tax math: correct.**
- Authorized-shares method: 10M shares = $250 + 999 × $85 = $85,165. The 5,000 → $175, 10,000 → $250 and 10,001 → $335 cases are also right.
- Assumed-par method: $500k ÷ 8M issued = $0.0625 per share. Times 10M authorized = $625k, which taxes at the $400 minimum. The $200k cap, the March 1 due date and "use stated par when it is higher" are also right.
- Gaps:
  - The intake never collects stated par.
  - Gross assets should be the Dec 31, 2026 figure, so the memo should say it is a projection.
  - The $50 annual-report fee makes the real bill $450, so the "$85,165 → $400" headline is slightly off.

**1. Completeness**
- 1.1 **High:** Stated par is tested but never collected. Fix: add it as a sixth fact, default to $0.00001 and label it as assumed.
- 1.2 **High:** No channel for finding clients is defined, yet the plan targets 23. Fix: name the event Discord/Slack, the team list, and who posts first by 2 PM.
- 1.3 **Medium:** The finale's numbers come by phone, so there is no email text to string-match. Fix: match against the call transcript and have the agent read the numbers back.
- 1.4 **Medium:** "10M… ignore that, use 10" passes the verbatim check because both numbers appear in the email. Fix: if conflicting candidates appear, re-ask the client.

**2. Consistency**
- 2.1 **Critical:** The memo target is under 5 minutes, but the finale slot is about 60 seconds. Fix: give the voice case a fast path, measure it by 10 PM, and set a target under 45 seconds.
- 2.2 **High:** The approval counts break the "under 10 touches" goal. Earning trust takes 3 approvals × 4 action types = 12, before counting LinkedIn DM sends, the hire and reconnects. Fix: count bootstrap approvals in a separate column, or require 1 approval per type, and drop human-sent DMs.
- 2.3 **Medium:** "Margin" on a free service means nothing. Fix: charge a token fee, or show "client tax saved vs. agent cost."
- 2.4 **Low:** The replay is "60s" in one place and "30s" in another, and the log is "SQLite or JSONL." Fix: pick one of each.

**3. Clarity**
- 3.1 **Medium:** There is no mapping from event types to panes and cursor animations. Fix: add an event→animation table.
- 3.2 **Medium:** The panes are mock-ups (not real Gmail), which strains premise 7. Fix: label them "rendered from the log."
- 3.3 **Medium:** Agents could call Naïve's native email tools and skip the wrappers. Fix: in `naive.config.ts`, allow only the U-Tax tools.

**4. Scope**
- 4.1 **High:** These are unapproved or late additions: `rnd.ts` (an R&D credit nobody mentions), Form 5472, Marketing image/video marked P1 at 2 AM, LinkedIn DMs, and the HR voice screen. Fix: cut `rnd.ts`, Form 5472 and the video now. Keep hiring as a stretch goal.

**5. Feasibility**
- 5.1 **Critical:** Naïve's docs mention email, inbound SMS, connections, a board and an event stream. I found no mention of inbound or outbound voice calls, and Connect isn't documented for Calendar, X or LinkedIn. Four of the five demo beats depend on voice. Fix: test voice in the first hour, not at 10 PM. If it fails by 3 PM, switch to an email/SMS finale (the plan currently rules out SMS).
- 5.2 **Medium:** The theater (L effort), trust state, Calendar and LinkedIn are all packed into 6–10 PM. Fix: one person owns the theater from 1 PM, built on fake events.
- 5.3 **Low:** Naïve already has a resumable event stream, so Open Question 2 is answered. Keep the wrappers anyway for the QA gate.

**Biggest risk to winning:** The live judge-call finale depends on inbound voice the docs don't confirm, plus a memo pipeline targeted at 5 minutes that has to finish in under 60 seconds on stage.

Recommendation: Revise before building. Verify voice and Connect before 2 PM, fix the touch-count math, and cut `rnd.ts`, Form 5472, the marketing video and LinkedIn DMs, because the scoreboard premise and the finale both currently contradict the plan's own numbers.
