# U-Tax — product brief and rules for Claude Code

> This file sits next to the crew's own `CLAUDE.md` (which was not changed). Controller: read this, `HANDOFF.md` and `docs/designs/u-tax.md`, then brief the lanes. This answers the digest item "No product idea yet."

U-Tax is our team's build for the **Naïve Autonomous Company Hackathon at YC** (SF, Sat Nov 7 2026 11 AM → Sun Nov 8 2026 11 AM; hacking 1 PM Sat → 11 AM Sun). It's an AI-run tax & accounting firm: 7 agents (Chief of Staff, Intake, Tax, QA, Accounting, Marketing, HR), one human CEO who runs it by phone, and an "Agent Theater" screen where every real agent action is replayed as a named cursor.

## Read these first, in this order
1. `HANDOFF.md`: the whole planning conversation, every decision and why, all resources and links, and today's task list.
2. `docs/designs/u-tax.md`: the reviewed design doc (office hours → CEO → outside voice → eng → design reviews, then the sponsor plan). **It's the source of truth for what to build.**
3. `docs/u-tax-theater-mockup.html`: the approved visual direction for the Agent Theater (open it in a browser; press S for stage mode).
4. `reference/`: the user's reference video (`demo-reference-video.mp4`) plus frame sheets and mockup screenshots.

## Non-negotiable rules (decided in review — don't change without asking the user)
- **Math lives in tested code, never in an LLM.** The AI only *points at* numbers in a message (quoted spans); `parseAmount()` converts them.
- **One gateway `perform(action)`** owns every rule: QA gate, trust counter, masking, caps, idempotency. Naïve agents get **only** U-Tax tools (native email/social tools denied).
- **Events are the only source of truth** (append-only). Cases, trust, touches and money are projections rebuilt from events. Single server, one writer.
- **Every theater animation comes from a real logged event.** Never animate anything that didn't happen.
- **Sponsors sit behind ports.** Stand-in adapters today; sponsor adapters Saturday via `ADAPTER_*` env switches. The same contract tests run against both.
- **Event store:** `EventStorePort` with a JSONL adapter (local dev now, later a Render disk) and a Postgres adapter (Render Postgres in production).
- **Legal limits:** no tax returns filed; never collect SSNs/EINs (redact if pasted); every memo says "educational estimate — not tax advice"; hiring uses a disclosed AI screen and the human makes the final call; no automated LinkedIn DMs (the agent drafts them, the human sends).
- **Client privacy on the projector:** masked by default ("Team #7"); show a real name only when the client opted in.

## How to work with this user
- Write task steps **in chat, in detail, with exact commands**. The user doesn't read generated README files.
- In voice conversations, never interrupt; let them finish.
- Ask before changing any approved decision above; record new decisions in `docs/designs/u-tax.md`.
- The user has **gstack** installed. Useful next commands: `/plan-eng-review` (lock code and tests), `/review` before merging, `/qa` on the running theater, `/ship`.

## Stack and how it fits the existing crew scaffold
The design doc suggests TypeScript (Hono gateway + Vite theater). **This repo already has a Next.js `frontend/` and a FastAPI `backend/`, with a crew building them.** The architecture doesn't depend on language, so map it onto the existing scaffold unless the user says otherwise:
- **Gateway** (`perform()`, ports/adapters, event store, SSE, `/tools/*`, `/hooks/*`) → `backend/` (FastAPI). Engine + `parse_amount` as pure Python with pytest.
- **Agent Theater** → `frontend/` (Next.js), ported from `docs/u-tax-theater-mockup.html`, reading the SSE stream.
- **Contract:** add the U-Tax routes to `contracts/api.md` (controller only).
- **Confirm this mapping with the user before lanes start.**

## Notes from `research/naive-sdk.md` that update the plan
- Phone: numbers can be provisioned, and **inbound SMS** wakes an agent; outbound SMS isn't available. Limits mention "voice calls at 10 min", so verify voice calls in the Studio today. The fallback remains the OpenAI Realtime mic page.
- `session.usage` events carry `consumed_micro_usd`: use it for the money panel (better than pricing tokens by hand).
- Approvals map to `POST /v1/sessions/{id}/tool_confirmations` (allow/deny): wire the trust-earned flow to it.
- Naïve's Studio has a "Computer panel" with a live view of an agent's sandbox, browser and phone. It could be a second demo surface next to the theater.
- Blocker from the crew digest: Naïve needs a paid plan or trial before any call works (402 `subscription_required`).
