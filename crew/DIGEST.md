# Digest (Naive YC Hackathon · U-Tax) — 2026-11-07 19:27 local · SATURDAY LIVE

## Needs you

Captain (naive-yc-hackathon-99)
- A (now): free memory (0.4 GB free: close the ChatGPT app and spare Chrome tabs), then reply "restart gateway". The live gateway was killed for low memory at ~18:10, so the firm is offline. Or "move to Render".
- B (now): check the `frontend` tab in the crew window. It's been silent ~2 h with 4 files dirty; probably a prompt.
- C (after the frontend READY): rerun crew/scripts/deploy-frontend.ps1 (Vercel).
- D (decide): keep "no confirm" for approve/deny/hire by voice (D34, irreversible), or ask for a confirm on those three only.
- E (after the event): rotate every key pasted in chat; pause the Naïve schedule.

## New

- all · 0m · preflight live: 9 green, 3 red (gateway down, public URL 502, ceo-assistant missing until the gateway restart + naive up)
- backend · 5m · tasks 31–38b merged (stop exemption, deliverability, documents D35, the CEO conversation D34, 429 backoff, preflight/archive, outreach replies); 39 (no session in URLs) HELD with the frontend
- agents · 2m · ceo-assistant, documents routing, HR reply handling merged
- frontend · 2h · the voice-loop fix merged (needs a Vercel redeploy); D32 shell + login built but NOT ready (stalled)
- REHEARSAL: order 1 (advertise → X + LinkedIn posted) and order 2 (hire 2 → ad posted, 2 outreach emails, calls booked) PASSED LIVE; case_1 e2e PASSED; documents e2e pending the gateway

## In progress

| Thread | Owns | State | Last report |
|---|---|---|---|
| frontend | `frontend/` | D32 shell + login + auth changes (then D34 conversation, panel, D35 docs view); STALLED | 2h |
| backend | `backend/` | resting; task 39 held for the frontend | 3m |
| agents | `naive/` | resting | 2m |
