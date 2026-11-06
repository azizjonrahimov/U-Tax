# Naive YC Hackathon notes (append-only, newest at the bottom)

The controller's working log. One line per event with a UTC timestamp. Never edited
after the fact; a correction is a new line. The digest is derived from here.

---
- 2026-11-06 16:30Z OWNER "I want to create a frontend and backend terminals as well while you will be a lead to manage all of them in this folder" (direct)
- 2026-11-06 16:30Z OWNER chose "Next.js + FastAPI (recommended)", "Hackathon-lite (recommended)", "New standalone repo (recommended)" (direct); no product idea given yet
- 2026-11-06 16:30Z SETUP repo init on main; contract v0; crew rules; briefs for frontend and backend (scaffold only, plan exempt)
- 2026-11-06 16:28Z LANE backend started: gstack ok, Task 1 FastAPI scaffold, owns backend/
- 2026-11-06 16:31Z CORRECTION previous line's time should read 16:31Z (backend start), not 16:28Z
- 2026-11-06 16:31Z LANE frontend started: gstack ok, Task 1 Next.js scaffold, owns frontend/
- 2026-11-06 16:40Z READY lane/backend 2b9742f46c2c from backend (pytest 8 passed; owner-path check clean; controller reran pytest at pin: 8 passed)
- 2026-11-06 16:40Z MERGED lane/backend 2b9742f46c2c into main as d9a5461
- 2026-11-06 16:40Z NOTE backend: CORS headers absent on unhandled 500s (ServerErrorMiddleware outside CORSMiddleware); deferred until the frontend reads 500 bodies
- 2026-11-06 16:41Z ADDRESSES ListAgents does not list the lanes; reply by pipe address. backend = uds:\.\pipe\LOCAL\cc-msg-c6cf18b40803c8f3b3a8d6b8fcfa3627 · frontend = uds:\.\pipe\LOCAL\cc-msg-046d19a1fe0c480120308f833af5cfbd
- 2026-11-06 16:41Z SENT backend MERGED d9a5461, stand by for product brief
- 2026-11-06 16:42Z LANE backend synced to main 3d366c6, idle, waiting for product brief
- 2026-11-06 16:55Z OWNER sent hackathon page + shared Claude Desktop chat; "continue the task with your frontend and backend terminals ... research, learn and find out anything you need all across the web and you can use as many subagents as you want"
- 2026-11-06 16:55Z RESEARCH event page read -> research/event.md (Naive SDK required; judged on how much runs without you; hacking Nov 7 13:00 to Nov 8 11:00)
- 2026-11-06 16:55Z BLOCKED shared chat claude.ai/share/ca3d21ad... behind Cloudflare for the headless browser (403 on snapshot API); asked owner to paste it
- 2026-11-06 16:55Z RESEARCH started: Naive SDK docs -> research/naive-sdk.md; sponsors -> research/sponsors.md
