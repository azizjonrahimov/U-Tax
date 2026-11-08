# Digest (Naive YC Hackathon · U-Tax): 2026-11-08 03:30 local · FEATURE FREEZE · demo day (ends 11:00 AM PST)

## Needs you

Captain (naive-yc-hackathon-99)
- A (now): restart the gateway window (Ctrl+C, ↑, Enter). It loads backend 41-44: the live work stream, STOP fences, cell formats, the xlsx security fix.
- B (now): redeploy the website: `powershell -ExecutionPolicy Bypass -File .\crew\scripts\deploy-frontend.ps1`. It loads D32-D37: landing, login, departments, live artifact windows, the CEO panel + pop-out, the conversation voice, doc downloads and STT.
- C (when you can): paste the OpenAI, Apify and Stripe (sk_test_) keys in chat; the Captain wires them.
- D (rehearsal): sign in → /office → order "follow up with Team 1" in the panel; watch it happen; then record the demo video.
- E (after the event): rotate every key pasted in chat; pause the Naïve schedule.

## New

- frontend · 0m · F1 Cases list in the CEO console merged (da03d87); STT records at 8 kbps
- backend · 30m · 45 merged (/api/stt books server-bounded seconds); v1.18.4
- frontend · 0m · D36 STT merged (41bd1afc): both ears through /api/stt, pre-roll, STOP ordering
- backend · 30m · 44 SECURITY merged (xlsx cells always literal); contract v1.18.3
- frontend · 40m · D35 docs downloads merged (eeb4c28)
- frontend · 55m · the CEO panel + pop-out + live order cards merged (2318e9e6)
- backend · 75m · 43 merged (cell fmt, relay replay skip, events types=); v1.18.2
- frontend · 85m · D37 live artifact windows merged (06420677): emails type and send, posts publish, the workpaper fills cell by cell
- backend · 2h · 42 (agent.activity relay, admin artifact content, receipt guard) v1.18, and 41 (stop-first, per-turn actions, STOP fences) v1.18.1 merged
- all · GitHub CI green on every merge

## In progress

| Thread | Owns | State | Last report |
|---|---|---|---|
| frontend | `frontend/` | resting (freeze); woken for rehearsal fixes | 0m |
| backend | `backend/` | task 46: can a documents rebuild reuse a version key (check first) | 0m |
| agents | `naive/` | resting | 2h |
