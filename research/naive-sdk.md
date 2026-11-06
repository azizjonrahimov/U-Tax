# Naïve SDK: build guide for the hackathon

Read 2026-11-06 by downloading every page listed in the docs index (`https://usenaive.ai/docs` points to
`https://vetta.sh/docs/llms.txt`, 201 pages), plus `https://usenaive.ai/skill.md`,
`https://api.vetta.sh/v1/openapi.json`, npm, PyPI and the `usenaive` GitHub org.
Code blocks below are **copied verbatim from the docs** unless a block is labelled *OUR SKETCH*.
**UNVERIFIED** means we could not confirm it from the docs or without an API key.

> **Naming.** The product is "Naive Managed Agents". Older identifiers still say `vetta`: the API host
> `api.vetta.sh`, the `Vetta-Version` header, `Vetta-Signature` on webhooks, `VETTA_MCP_TOKEN`, model
> `vetta/auto`. They're the same product. Doc links on `vetta.sh/docs/*` redirect to `usenaive.ai/docs/*`.
> (https://usenaive.ai/docs/index.md, https://usenaive.ai/skill.md)

---

## TL;DR for tomorrow

1. **There is no Python SDK.** The SDK is TypeScript only (`@usenaive-sdk/managed-agents`), plus a CLI
   (`@usenaive-sdk/cli`). FastAPI talks to the **REST API** at `https://api.vetta.sh/v1` with a Bearer key,
   which is a full option because the CLI and the SDK are thin clients over it.
2. Before anything works, the org needs a **plan** (Standard $20/mo, 7-day trial, card required). Every
   other route answers `402 subscription_required` until then.
3. The core loop: **create agent (budget required) → create session with a message → stream events →
   answer approvals and questions → read cost**.
4. Multi-agent = a **coordinator agent with a `multiagent` roster** plus a **board** (cards
   todo/doing/blocked/done). Assigning a card starts the assignee's session. This is the "company" shape.
5. We can show what the agents did from `GET /v1/digest`, `GET /v1/sessions/{id}/events`, SSE
   `/stream`, the board API, `/v1/credits/spend`, `/v1/audit_logs` and webhooks. We can render our own
   UI from these, or show the Studio at https://app.usenaive.ai.
6. **Custom client-executed tools are "coming soon".** To give agents our backend today, serve an **MCP
   server over public HTTPS** (FastAPI + an MCP lib, via a tunnel for local dev), or host a `fullstack`
   Naive app with `mcp: "/mcp"`.

---

## 1. What the SDK is, languages, packages, versions

Sources: https://usenaive.ai/docs/index.md, https://usenaive.ai/docs/sdk/typescript.md,
https://usenaive.ai/docs/quickstart.md, https://usenaive.ai/skill.md, npm registry (queried 2026-11-06).

- **Naive Managed Agents** is a hosted agent runtime. You define an *agent* (model, harness, prompt,
  tools, skills, budget). You run it as *sessions* that can run for hours, sleep at storage cost and
  resume. Each session gets a disposable Linux micro-VM ("computer") when its tools need one. The
  docs say: "you pay per finished task, in real dollars, priced before each call runs."
- **Blueprints/templates** are a layer on top. A blueprint is a whole company as code: a repo with a
  `naive.config.ts`, applied with `naive up`. You run it in the **Studio** (https://app.usenaive.ai).
- **Languages:** TypeScript SDK only. The SDK page says: *"TypeScript only; there is no Python
  package."* PyPI has no package for it: `naive` on PyPI is an unrelated project by another author, and
  `usenaive`, `naive-sdk`, `vetta` and similar names return 404.
  - Python path: plain REST (httpx) and SSE. `GET https://api.vetta.sh/v1/openapi.json` needs no key
    and describes the live routes (OpenAPI 3.1, version `2026-09-30`). The docs say request and response
    *bodies are not described yet*, so the docs pages are the body reference.
  - For raw LLM calls from Python, the **model proxy** is Anthropic- and OpenAI-compatible (§5).

| Package | Purpose | Latest on npm (2026-11-06) | Install |
|---|---|---|---|
| `@usenaive-sdk/managed-agents` | Typed TS client | `0.12.0` | `npm i @usenaive-sdk/managed-agents` |
| `@usenaive-sdk/cli` | `naive` CLI | `1.0.0` | `npm i -g @usenaive-sdk/cli` |
| `@usenaive-sdk/blueprints` | `defineProject` / engine behind `naive up` | `0.18.0` | dev dependency in a blueprint repo |

Gotchas from the docs:
- The SDK is **ESM-only**. Set `"type": "module"`, or use `.mts`/`.mjs`, or use
  `await import("@usenaive-sdk/managed-agents")` in CommonJS. If `tsc` builds it, set `module` and
  `moduleResolution` to `node16`/`nodenext`. This matters for Next.js: import it in server code only,
  because it carries the secret key.
- If an old `@usenaive-sdk/vetta-cli` is installed, run `npm rm -g @usenaive-sdk/vetta-cli` first.
  The old SDK name `@usenaive-sdk/vetta` should become `@usenaive-sdk/managed-agents`.
- `npx @usenaive-sdk/cli …` works without a global install. The quickstart uses `jq` to capture ids.
  On Windows, use Git Bash with jq installed, or copy the ids by hand.

---

## 2. Auth and access

Sources: https://usenaive.ai/docs/api/authentication.md, https://usenaive.ai/docs/quickstart.md,
https://usenaive.ai/skill.md, https://usenaive.ai/docs/platform/pricing.md, https://usenaive.ai/hackathon

- **Sign up:** https://app.usenaive.ai/register, or the CLI:
  ```bash
  naive auth register --email you@acme.com --password '…'   # sign up; a confirmation mail is sent
  naive login --email you@acme.com --password '…'           # sign in; stores a session token
  naive login --api-key sk_live_...                         # or: a key from the Studio / `naive keys create`
  naive whoami                                              # confirm the principal and organization
  ```
  Registration returns `confirmation_required: true`, so someone has to click the email link first.
- **The plan comes first.** The quickstart says: "A new organization needs a plan before anything outside
  billing answers." Standard is $20/month with $20 of credit. The 7-day free trial needs a card and
  gives **$10** of trial credit. Pro is $200/month with $200 of credit. Included credit rolls over.
  ```bash
  naive plan subscribe                # Standard; add --plan pro for Pro
  naive plan show                     # status: active once the checkout completes
  naive credits topup --usd 50        # prints a hosted checkout link
  naive credits show
  ```
  `402 subscription_required` means there is no plan. `402 insufficient_credits` means the balance is
  empty. Fixing one does not fix the other.
- **API keys** are org-scoped, start with `sk_live_`, and are **shown once**:
  ```bash
  naive keys create --name quickstart --scopes agents:write,sessions:write
  export NAIVE_API_KEY="sk_...the secret shown once..."
  ```
  Header: `Authorization: Bearer sk_live_...`. Pin `Vetta-Version: 2026-09-30` in production. A person's
  login produces a `vt_` session token, which is accepted wherever a key is.
- **Scopes:** `agents:read|write`, `sessions:read|write`, `computers:write`, `proxy:write`,
  `files:read|write`, `billing:read|write`, `webhooks:*`, `audit:read`, `admin`. `GET /v1/digest` needs
  `sessions:read` and `agents:read`. A key missing a scope gets `403 forbidden`; a bad key gets `401`.
- **Env vars named in the docs:** `NAIVE_API_KEY`, which overrides the CLI profile and is also used by
  blueprint apps. `NAIVE_WEBHOOK_SECRET` is the name used in the webhook verifier example.
  `--api-base-url` overrides the host. The CLI flag is `--api-base-url`; we found no env var for the
  base URL. For the proxy: `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`.
- **Hackathon credits:** the docs and https://usenaive.ai/hackathon say nothing about credits or a
  waitlist. Our own `research/event.md` (from the YC event page) says every team gets "$1k+ in Naïve and
  sponsor credits". **UNVERIFIED:** how the credits are delivered (promo code, credited org, or Pro
  upgrade). Ask at kickoff. Until then, budget for the $20 Standard plan or the trial.
- **Rules from skill.md worth following:** never echo, log or commit a key. Prefer `naive claim < key.txt`
  or `NAIVE_API_KEY` over putting the key in argv. Branch on `error.code`, never on `error.message`.

---

## 3. Core concepts and their exact API

### 3.1 Agent: a versioned config with a required budget
Sources: https://usenaive.ai/docs/concepts/agents.md, https://usenaive.ai/docs/capabilities/agent.md

Fields: `name`*, `model`*, `budget`*, `harness` (default `pi`), `system`, `window`
(`immediate|priority|loose`), `tools`, `mcp_servers`, `skills`, `identity`, `multiagent`, `handoffs`,
`output_schema`, `description`, `metadata`. Every edit mints an immutable version. A running session
keeps the version it started on.

```typescript TypeScript
const agent = await naive.agents.create({
  name: "Refunder",
  model: "z-ai/glm-5.3-flash",
  harness: "pi",
  skills: ["refund-policy"],
  budget: { cap_micro_usd: 50_000_000, max_task_micro_usd: 5_000_000, period: "month" }, // $50 / $5
  window: "immediate",
  system: "You process refunds.",
});
```

```bash cURL
curl -fsSL https://api.vetta.sh/v1/agents \
  -H "authorization: Bearer $NAIVE_API_KEY" \
  -H "content-type: application/json" \
  -d '{
    "name": "Refunder",
    "model": "z-ai/glm-5.3-flash",
    "harness": "pi",
    "skills": ["refund-policy"],
    "budget": { "cap_micro_usd": 50000000, "max_task_micro_usd": 5000000, "period": "month" },
    "window": "immediate",
    "system": "You process refunds."
  }'
```

- Agents can be defined in a checked-in **`.agent.yaml`** file and synced with
  `naive agent apply -f refunder.agent.yaml`, which upserts by name (the GitOps path).
- Versioning: `naive.agents.update(id, { expected_version, ... })` returns `409 version_conflict` if the
  version is stale. Also `naive.agents.getVersion(id, 2)` and `naive.agents.rollback(id, { to_version: 2 })`.
- **Models:** the catalogue is live, with "no curated list". Use `GET /v1/models` (paged, any valid key)
  or `naive models list`. The docs examples use `z-ai/glm-5.3-flash`, `openai/gpt-oss-120b`,
  `deepseek/deepseek-v4-flash-0731`, `google/gemma-4-31…`, and `vetta/auto`, which picks per request.
  skill.md's sample config uses `model: "claude-sonnet-5"`. **UNVERIFIED:** whether Claude or GPT
  frontier ids are in the catalogue. Check on day one with a key.
- **Harnesses:** `pi` (default), `vetta` (preview, invite-only), `hermes`, `claude_code`. Check
  `GET /v1/harnesses` (`runnable`). Note that `claude_code` and `hermes` agents **don't get** the
  platform built-ins: web_search/fetch, image and video generation, `publish_file`, the managed browser.
  (https://usenaive.ai/docs/concepts/harnesses.md)

### 3.2 Budgets: priced before each call, required on every agent
Source: https://usenaive.ai/docs/concepts/budgets.md

- Money is always **integer micro-USD** on the wire (`1 USD = 1_000_000`). Fields:
  `cap_micro_usd` (per period), `max_task_micro_usd` (per session, ≤ cap), `period` (`day|week|month`).
- The pre-flight gate checks, in order: org balance, agent cap, task ceiling, session budget. On a
  breach the call is refused, a `budget.exceeded` event fires and the session idles with
  `stop_reason: "budget_paused"`. **Raising the cap auto-resumes the session.**
- Session cap: `naive.sessions.create({ ..., budget_micro_usd: 2_000_000 })` and
  `naive.sessions.setBudget(session.id, 5_000_000)` (REST `PATCH /v1/sessions/{id}/budget`).
- Reading spend: `naive agent spend <agt> --by component` returns
  `{ "by_component": { "model": …, "computer": …, "search": …, "media": … } }`. Agents can call the
  built-in `session_spend` tool.
- **Gotcha:** web tools, media and compute are **booked after the fact**, so spend can finish past the
  cap and the balance can go negative. "Treat the agent's `cap_micro_usd` and the organization's balance
  as the real limits." (https://usenaive.ai/docs/platform/pricing.md)

### 3.3 Sessions: one run, durable
Sources: https://usenaive.ai/docs/concepts/sessions.md, https://usenaive.ai/docs/concepts/session-operations.md,
https://usenaive.ai/docs/sdk/sessions.md, https://usenaive.ai/docs/api/sessions.md

Statuses: `queued → running → idle | completed | failed | cancelled`. `idle` is resumable and carries a
`stop_reason`: `end_turn`, `awaiting_input`, `awaiting_approval`, `awaiting_answer`,
`awaiting_delegation`, `budget_paused`, `interrupted`, `max_iterations`, `context_exhausted`, `error`.

| What | TS SDK (from the SDK reference) | REST |
|---|---|---|
| Create + start | `client.sessions.create({ agent_id, message, agent_version?, budget_micro_usd?, window?, output_schema?, metadata? })` | `POST /v1/sessions` |
| Send / steer | `client.sessions.send(id, { message, interrupt?, queue?, model?, mode? })` → `{ session_id, status, accepted_seq }` | `POST /v1/sessions/{id}/messages` (202) |
| Approve or deny | `client.sessions.confirmTool(id, { tool_call_id, decision: "allow"\|"deny", reason?, scope?: "call"\|"session" })` | `POST /v1/sessions/{id}/tool_confirmations` |
| Answer a question | `client.sessions.answer(id, { tool_call_id, answers })` | `POST /v1/sessions/{id}/answers` |
| Get / list | `client.sessions.get(id)`, `client.sessions.list({ agent_id?, deployment_id?, limit })` | `GET /v1/sessions/{id}`, `GET /v1/sessions?agent_id=&deployment_id=&status=` |
| Interrupt / cancel | `client.sessions.interrupt(id)`, `client.sessions.cancel(id)` | `POST …/interrupt`, `POST …/cancel` |
| Events (paged) | `client.sessions.events(id, { after_seq?, limit? })` | `GET /v1/sessions/{id}/events` |
| Stream | `client.stream(sessionId, { afterSeq?, maxRetries? })` (async generator) | `GET /v1/sessions/{id}/stream?after_seq=N` (SSE) |
| Plan mode | `client.sessions.plan(id)`, `client.sessions.writePlan(id, { markdown, base_version })` | `GET/PUT /v1/sessions/{id}/plan` |

- Sending to a `running` session without `interrupt` or `queue` returns `409 session_running`.
- To wait for a send, wait for events with `seq > accepted_seq`. Don't poll `status`.
- **Sessions can't be deleted.** `cancel` is the only terminal operation, and records plus events are
  permanent. Sandbox files go away on cancel unless the agent called `publish_file` first.
- **Structured results:** pass `output_schema` (JSON Schema) on the agent or session. When the session
  goes idle it carries a typed `structured_output`. Use this for machine-readable "what did it decide".
- **Coming soon (don't build on these):** session `overrides`, attaching `computer`/`skills`/`vault_ids`
  at create time, and mid-session tool updates.

### 3.4 Events, logging and observability (how we show what the agents did)
Sources: https://usenaive.ai/docs/concepts/events-and-streaming.md, https://usenaive.ai/docs/api/events.md,
https://usenaive.ai/docs/capabilities/observability.md

Each event is `{ id, seq, type, session_id, data, created_at }`. `seq` is gap-free and resumable; order
by `seq`, not `created_at`. The log replays for **at least 72 hours** by cursor, and session records are
kept for the life of the org.

Key types: `session.running`, `message.delta`, `message.completed` (the agent's reply has no
`role:"user"`), `tool.started {name,args}`, `tool.completed {name,result,duration_ms}`,
`tool.confirm {tool_call_id,name,args}`, `tool.confirm.resolved {decision,reason,actor,…}`,
`session.usage {token_usage,consumed_micro_usd,active_seconds,tool_calls}`,
`session.idle {stop_reason,pending_actions?,structured_output,…}`, `budget.exceeded`,
`media.job.*`, `browser.session.*`, `thread.created|idle|terminated` (team delegation), `plan.updated`.
Some types (`tool.confirm.resolved`, `plan.updated`, `session.mode`, `outcome.evaluation.*`) appear on
the events **list** only with `include_accepted=true`. Use that flag when building the "decisions" view.

```bash cURL
curl -N "https://api.vetta.sh/v1/sessions/$SID/stream?after_seq=0" \
  -H "authorization: Bearer $NAIVE_API_KEY" \
  -H "accept: text/event-stream"
```
Each SSE message carries one event as JSON in `data:`, with `seq` as the SSE `id:`.

```typescript TypeScript
let answer = "";
for await (const e of client.stream(sessionId)) {
  if (e.type === "message.completed" && e.data.role !== "user") answer = String(e.data.content);
}
```

Other read surfaces for a demo dashboard:
- **`GET /v1/digest`** returns the dashboard side rail in one call: `agents`, `waiting.awaiting_approval`,
  `waiting.awaiting_answer`, `waiting.budget_paused`, `failed`, `recent` (50), `channels`.
  (https://usenaive.ai/docs/api/digest.md)
- **Board:** `GET /v1/agents/{id}/board` returns cards in column order. Also `GET /v1/boards/{id}/cards`
  and `GET /v1/boards/{id}/cards/{cid}/comments`. A card has `title, body, note, status, assignee,
  blocked_by, woken_session_id, wake_count, …`. (https://usenaive.ai/docs/api/board.md)
- **Spend:** session objects carry `consumed_micro_usd` and `token_usage`. `GET /v1/credits/spend`
  (filters `session_id, agent_id, deployment_id, from, to`) returns totals and the 50 most expensive
  agents and sessions. `GET /v1/credits/ledger` has line items. `GET /v1/agents/{id}/spend?by=component`.
- **Audit log:** `GET /v1/audit_logs` (filters `actor, resource, action, request_id, from…`), scope
  `audit:read`. Approvals record the approving principal.
- **Workspace archive:** `GET /v1/sessions/{id}/workspaces/{phase}` returns a tar.gz of the sandbox at
  `end`, `card_start` or `card_end`, or `?kind=diff` for the git diff. Good for "here is what it built".
- **Traces:** spans follow OTel GenAI conventions (`invoke_agent` → `chat` / `execute_tool`). OTLP
  export is *coming soon*.
- **Push:** webhooks (§4) can deliver `session.idle`, `message.completed` and `budget.exceeded` to our
  FastAPI.

### 3.5 Tools and permissions (human-in-the-loop)
Sources: https://usenaive.ai/docs/capabilities/tools.md, https://usenaive.ai/docs/concepts/policies.md

```jsonc
{
  "tools": {
    "default_config": { "permission": "allow" },
    "configs": {
      "browser": {
        "enabled": true,
        "permission": "ask",
        "config": { "allowed_domains": ["example.com"] }
      },
      "bash": { "enabled": true, "permission": "allow" }
    }
  }
}
```
- `allow` runs silently. `ask` pauses with `tool.confirm` and the session idles with
  `awaiting_approval` and `pending_actions[]`. `deny` hides the tool from the model.
- **Ask by default** (these stay `ask` even under a deny default unless named in `configs`):
  `social.post`, `email.send`, `legal.*`, `wallet.*`, `card.*`, `trading.*` writes, `company.set_*`,
  `apps.request_access`, `mobile`, `data_run`, `generate_video`, `edit_video`, `create_character`.
  Agents can't set `generate_video`/`edit_video` to `allow` themselves; a person's key can.
- **Escalation tools:** `ask_operator` (agent asks a question, `stop_reason: awaiting_answer`, answered
  via `/answers`), `request_tools` (agent asks for more tools; approving mints a new agent version), and
  `propose_plan`. These can never be `allow`.
- The approval loop, verbatim:
  ```typescript TypeScript
  const s = await naive.sessions.get(sessionId);
  for (const a of s.pending_actions) {
    if (a.kind !== "tool") continue;       // a question is answered, not approved — see below
    await naive.sessions.confirmTool(sessionId, {
      tool_call_id: a.tool_call_id,
      decision: a.name === "refund" ? "allow" : "deny",
      reason: "out of policy",
    });
  }
  ```
  Branch on `pending_actions[].kind` (`tool` or `question`), not on `stop_reason` alone.
  `naive session inbox` lists everything that is waiting on a person.
- **Data-exfil guard:** once a chat reads people's data (mail, contacts, connected apps), it gets
  restricted. `bash` is refused, and sends, `web_fetch`, and MCP or connected-app tools ask *every time,
  even at `allow`*. This can stall an "autonomous" email agent. Keep readers and doers in separate
  sessions or agents.
- **Autonomy tradeoff:** a scheduled run that waits on an approval **skips the next fires**
  ("Skipped: waiting on you"). Every `ask` gate is a place the company stops without us. For the
  judges, decide which gates to keep (money, posting) and log the rest as decisions instead.

### 3.6 Memory and state
Sources: https://usenaive.ai/docs/capabilities/skills.md, https://usenaive.ai/docs/capabilities/files.md,
https://usenaive.ai/docs/team/board.md, https://usenaive.ai/docs/concepts/templates.md, https://usenaive.ai/skill.md

There is **no general agent "memory" primitive**. skill.md lists "memory" under *Coming soon, do not
promise*. What persists today:
- **Session transcript.** It is durable, and a session can sit idle for days and continue. Context is
  compacted automatically.
- **Files.** The agent calls `publish_file` to save into org-scoped storage. `find_files`, `fetch_file`
  and `view_image` read them back across sessions.
- **Skills.** Versioned instruction docs (`naive skill push`), loaded on demand.
- **The board.** Durable cards and comments. The docs call it the shared state of a team.
- **Project context.** Template setup answers, readable via the `project_context` tool.
- **Our own DB.** Expose it through an MCP tool, or use a Naive `fullstack` app's managed Postgres
  (`apps` tool: `db_query`, `db_rest`, …).

### 3.7 Scheduling and long-running work
Source: https://usenaive.ai/docs/capabilities/deployments.md

```typescript TypeScript
const deployment = await naive.deployments.create({
  agent_id: agent.id,
  cron: "0 9 * * *",
  budget_micro_usd: 5_000_000, // required: $5 per run
  window: "loose",
  input_template: "Summarize what changed in main overnight.",
  on_idle: "https://acme.dev/hooks/naive", // webhook fired when each run goes idle
});
```
```bash CLI
naive deploy create --agent agt_01j8rk2vq6m4zx9kp7n3c5hwae \
  --cron "0 9 * * *" \
  --budget-usd 5 \
  --window loose \
  --prompt "Summarize what changed in main overnight and open issues for regressions." \
  --identity ava-sales \
  --on-idle https://acme.dev/hooks/naive
```
- Cron is 5-field and **fires at most every 5 minutes**. Each fire is a normal session tagged
  `deployment_id`. Use `naive deploy run <dep>` to fire one now (useful in a demo),
  `naive deploy runs <dep>` to list fires, and `pause`/`resume`.
- Note the doc inconsistency: the reference calls the prompt field `prompt`, but the TS example uses
  `input_template`. **UNVERIFIED** which one REST accepts. Check `openapi.json` or try both.
- If the agent sits on a board, each schedule gets **one standing card** ("Recurring: …") and each fire
  comments on it.
- Other wake sources: **inbound email/SMS** to an identity's inbox or number starts a session for the
  owning agent (https://usenaive.ai/docs/identity/inbound.md), and **board card assignment** starts the
  assignee.
- Completion window: `loose` is cheapest for unattended work. A model the pool doesn't host on
  `priority`/`loose` returns `400 window_unavailable`; `immediate` always works.

### 3.8 Multi-agent teams
Sources: https://usenaive.ai/docs/team/overview.md, https://usenaive.ai/docs/capabilities/team.md,
https://usenaive.ai/docs/team/board.md, https://usenaive.ai/docs/capabilities/tools.md#handoffs

- A team is an agent with a `multiagent` object (`type: "coordinator"`, `agents` pinned by version,
  `board: true`). There is no separate team object.
- **Delegation:** the coordinator calls `send_to_agent` with `wait: true`. The member runs in its own
  thread on the coordinator's budget, and the result folds back. The coordinator parks with
  `awaiting_delegation`. Only one level deep.
- **Handoff:** `send_to_agent` with `wait: false` starts the target's own top-level session on its own
  budget. Chains stop at depth 4, and one session can start at most 8 handoffs. Who an agent may call is
  set by `handoffs: true | false | "team" | [names]`, and every agent can always reach the company's
  **CEO**.
- **Board:** the tools are `board_read` and `board_write` (`op: "create" | "assign" | …`), with
  statuses `todo/doing/blocked/done`. **Assigning a card starts the assignee's session** (or the board
  clock does, within a minute). The coordinator gets a digest session when cards reach done or blocked.
  The recommended pattern: "the coordinator puts the plan on the board and assigns each card; the board
  starts the crew."
- An `advisor` roster entry is refused at save.
- Doc conflict: skill.md (the agent onboarding manifest) still lists "multiagent teams" as coming soon,
  but the Team pages say "**Available now**" and describe shipped runs. We trust the Team pages.
  **UNVERIFIED** until we run it.

### 3.9 Identity: the company's email, phone, accounts and money
Sources: https://usenaive.ai/docs/identity/overview.md, https://usenaive.ai/docs/identity/email.md,
https://usenaive.ai/docs/identity/domains.md, https://usenaive.ai/docs/capabilities/tools.md

- An **identity (persona)** owns inboxes, numbers, connections and vault credentials. Attach it with
  `naive identity attach --agent … --identity …` or set `identity` on the agent, and set it on
  schedules too, or a fire "runs as no persona".
- **Fastest email:** every org gets one free **system domain**, so no DNS work is needed:
  `naive identity domain connect` (no `--domain`) or `POST /v1/domains {"kind":"system"}`, then
  `naive identity email provision --identity ava --address ava@<domain>`. Sending costs `$0.0008`
  per email.
- Agent email tools are `email.inboxes`, `email.read` and `email.send` (`email.send` asks by default).
- Phone: numbers can be provisioned and inbound SMS wakes the agent. **Outbound SMS is not available**:
  `feature_not_configured`.

---

## 4. The Studio, deploy, and reading activity for our own UI

Sources: https://usenaive.ai/docs/index.md, https://usenaive.ai/skill.md, https://usenaive.ai/docs/cli/naive.md,
https://usenaive.ai/docs/concepts/templates.md, https://usenaive.ai/docs/capabilities/webhooks.md,
https://github.com/usenaive/agency-blueprint

- **Studio = https://app.usenaive.ai.** It's the web dashboard where the company runs. The docs mention
  chats with agents, a side panel (`open_panel`: app, file, card, board, files, templates, listings), a
  **Computer panel** with a live view of the agent's sandbox, browser and phone, approvals, the board, a
  Hire page that creates the `CEO`, settings and credits, an Access panel for apps, and "Deploy on
  Studio" for templates. Agents can draw live cards in chat with `show` and `compose` (spend by day,
  roadmap, schedule, post views). This is probably our cheapest live demo surface.
  **UNVERIFIED:** the exact screens, since we couldn't log in.
- **"Deploy" has three meanings:**
  1. **Deployments**: cron schedules for agents (§3.7).
  2. **Blueprint apply**: `naive up` reconciles a `naive.config.ts` (skills → identities → vaults →
     apps → agents → tasks) by name and is idempotent. Run `naive up --dry-run` first. The Studio does
     the same through `POST /v1/blueprints/installs`.
  3. **Hosted apps**: `frontend_only` or `fullstack` (managed Postgres plus object store) web apps.
     The agency-blueprint's build emits `dist/api/*` as **Vercel-style Node functions**.
     **UNVERIFIED:** whether a Python/FastAPI server can be hosted there. Assume not; host FastAPI
     ourselves (Render or Vercel are sponsors) and connect it via MCP.
- Published blueprints: `agency` (`blank`, `seo-geo`; the repo also has `product-design`) and `media`
  (`faceless`, `longform`, `clipping`). Repos: https://github.com/usenaive/agency-blueprint and
  https://github.com/usenaive/media-blueprint (MIT, updated 2026-11-05). They're good references for a
  crew design: seven agents with roles, deny-by-default tools, schedules and approvals.
  ```bash
  naive template agency                          # clones usenaive/agency-blueprint → ./agency
  naive template media --template clipping       # media has no default: choose faceless or clipping
  ```
  `naive init` scaffolds a fresh `naive.config.ts`. The docs say "a hand-written config that names no
  `template` is not capped", which implies a custom config without a published blueprint works.
  **UNVERIFIED.**
- The `naive.config.ts` shape, verbatim from skill.md:
  ```ts
  import { defineProject } from "@usenaive-sdk/blueprints";

  export default defineProject({
    name: "my-agency",
    blueprint: "agency",
    template: "seo-geo",          // the crew; switch it here and re-run `naive up`
    apps: [
      { name: "dashboard", type: "fullstack", deploy_dir: "dist", mcp: "/mcp",
        env: { API_KEY: { from_env: "NAIVE_API_KEY" } } },
    ],
    agents: [
      {
        name: "strategist",
        model: "claude-sonnet-5",
        budget: { cap_micro_usd: 50_000_000, max_task_micro_usd: 5_000_000, period: "month" },
        system: "Be terse.",
        tools: { default_config: { permission: "allow" },
                 configs: { bash: { enabled: true, permission: "ask" } } },
        allowed_apps: ["dashboard"],
        schedules: [{ cron: "0 9 * * 1", timezone: "Europe/Berlin", input: "Weekly plan", budget_micro_usd: 500_000 }],
        intake: { message: "Read the brief in the app and open your first three drafts." },
      },
    ],
    removed: { agents: ["old-helper"] },
  });
  ```
  `tasks[]` seeds board cards (`{ key, title, body?, assignee?, blocked_by? }`). New templates are
  expected to use `tasks` instead of `intake`.
- **Reading activity for our own Next.js UI:** yes, it's fully readable over the API (§3.4): digest,
  sessions list, events list with `include_accepted=true`, SSE stream, board cards and comments, spend,
  ledger, audit logs, files. **Webhooks** push to our FastAPI:
  ```bash CLI
  naive webhook add \
    --url https://example.com/hooks/naive \
    --events session.idle,message.completed,budget.exceeded
  ```
  Payloads are signed (`HMAC-SHA256` over `{timestamp}.{raw_body}`, header `Vetta-Signature: v1=<hex>`
  and `Vetta-Timestamp`). Reject anything older than 300s and reply 2xx within 10s. The URL must be
  **public HTTPS**: localhost and private IPs are refused, so use a tunnel for local dev. Stream-only
  types (`tool.*`, `message.delta`) are **never** delivered to webhooks, so use SSE or event polling
  for those.
- The API is also an **MCP server** (`POST /v1/mcp`, tools named like `agents_create`), so a
  "meta" agent could operate the account. Declaring it needs the `admin` scope.

---

## 5. Built-in integrations and custom tools

Sources: https://usenaive.ai/docs/capabilities/tools.md, https://usenaive.ai/docs/platform/pricing.md,
https://usenaive.ai/docs/capabilities/data.md, https://usenaive.ai/docs/identity/*.md, https://usenaive.ai/docs/api/proxy.md

**Built-in tools** (on `pi`/`vetta`): `bash`, `read`/`write`/`edit`/`ls`/`find`, `browser` (goto, click,
type, act, extract, observe, screenshot, login, signup…; `allowed_domains`, `allow_writes`),
`mobile` (cloud Android or iPhone, ask by default, $0.05 or $0.08 per minute), `web_search` ($0.00166
per call), `web_fetch` ($0.00083), `generate_image`, `generate_video`, `clip_video`,
`generate_3d_model`, `build_3d`, `transcribe_audio`, `generate_speech`, `generate_sound_effect`,
`generate_music`, `create_character` and related, `data_search`/`data_describe`/`data_run` (18 data
domains: people, companies, funding, SEO, news, jobs, reviews…), `find_stock_photo`, `apps`
(hosted apps, DB, storage), `publish_file`, `fetch_file`, `find_files`, `view_image`, `session_spend`,
`show`, `compose`, `open_panel`, `crm.read`/`crm.write` (only if the template declares a CRM),
`ask_operator`, `request_tools`, `write_plan`, `propose_plan`, `send_to_agent`, `wait_for_agents`,
`list_agents`, `board_read`, `board_write`, `project_context`, `list_as_template`,
`pay_out_earnings`.

**Identity-scoped:** `email.*`, `phone.numbers`/`phone.read` (no send yet), `social.accounts`,
`social.post`, `social.status`, `social.post_metrics`, `social.cancel` (needs a social workspace),
`trading.*` (a brokerage over OAuth), `connections.search|connect|status`, and
`company.set_timezone|set_logo|set_brand`.

**Connections:** about 1,400 third-party apps via OAuth or API key, using a human-opened consent link.
Tools register as `<connector>.<tool>` (e.g. `gmail.fetch_emails`). The agent never sees the token.
(https://usenaive.ai/docs/identity/connections.md)

**Payments and money:** cards (prepaid virtual cards bought from org credits) and legal/KYC/company
formation are documented. The **agent wallet is "Coming soon · not available yet (Phase 5)"**. We found
no Stripe-style "accept customer payments" primitive. The only Stripe mention is `pay_out_earnings`
for marketplace template earnings. **UNVERIFIED** whether cards and legal work on a new trial org.
For taking customer payments, plan on the Stripe sponsor API behind our own MCP tool.

**Custom tools (our FastAPI as tools):**
- Organization-defined, client-executed **custom tools are "Coming soon"**: "you describe the arguments
  as a JSON Schema, the model emits a tool-use event, your application runs it and returns the result."
  Don't plan on it.
- **Today: the MCP connector.** Declare it on the agent:
  ```jsonc
  {
    "mcp_servers": [
      { "type": "url", "name": "tracker", "url": "https://mcp.example.com/mcp" }
    ]
  }
  ```
  ```jsonc
  {
    "tools": {
      "default_config": { "permission": "ask" },
      "configs": {
        "tracker.get_issue": { "enabled": true, "permission": "ask" },
        "tracker.delete_project": { "enabled": false }
      }
    }
  }
  ```
  Rules: the URL must be **public HTTPS** (localhost, private, `.local` and `.internal` are refused);
  up to 20 servers per agent and 100 tools per server. MCP tools default to `ask`, so set `allow` per
  tool. Auth goes through a **vault credential** (`static_bearer` or `mcp_oauth`) matched by URL and
  injected server-side. Inline `Authorization` headers are rejected. A server that is down produces
  `session.degraded`, not a failed session.
  → Plan: FastAPI mounts an MCP streamable-HTTP endpoint, we expose it with a tunnel or host it on
  Render, and we store our bearer in a Naive vault. From https://usenaive.ai/docs/identity/vault.md
  (`env_var` credentials return `501 feature_not_configured`; use `static_bearer`):
  ```bash CLI
  # one vault per identity (by id or name); credentials are sealed into it by vault id
  naive vault create --name ava-secrets --identity ava

  # MCP static bearer, keyed by server URL; the value is read from stdin, never echoed
  naive vault set --vault vlt_... --mcp-bearer https://mcp.example.com/mcp
  ```
  ```typescript TypeScript
  const vault = await client.vaults.create({ display_name: "ava-secrets", identity_id: "idn_..." });
  await client.vaults.credentials.create(vault.id, {
    kind: "static_bearer",
    key: "Task tracker API key",
    value: "tok_...",
    mcp_server_url: "https://mcp.example.com/mcp",
  });
  ```
  The vault belongs to an identity, so the agent must hold that identity for the bearer to apply.
  This is implied by the docs; **UNVERIFIED** end to end.
- **Alternative:** a Naive-hosted `fullstack` app declares `"mcp": "/mcp"`. Every agent allowed on the
  app gets `<app-name>.<tool>`, and the platform injects the `VETTA_MCP_TOKEN` bearer. The
  agency-blueprint's `server/mcp.ts` is a hand-written JSON-RPC example (initialize, tools/list,
  tools/call). It runs on Node.
- **Model proxy for raw LLM calls** (no tools, no state). It's Anthropic- and OpenAI-compatible:
  ```bash
  export ANTHROPIC_BASE_URL=https://api.vetta.sh/v1/proxy/anthropic
  export ANTHROPIC_AUTH_TOKEN=sk_live_...
  ```
  OpenAI format: `https://api.vetta.sh/v1/proxy/openai/v1`. Billing is the same as sessions.

---

## 6. Code examples (verbatim)

### 6.1 Minimal: create an agent, run a session, stream (https://usenaive.ai/docs/index.md)
```typescript TypeScript
import { createClient } from "@usenaive-sdk/managed-agents";

const naive = createClient({ baseUrl: "https://api.vetta.sh", apiKey: process.env.NAIVE_API_KEY! });

const agent = await naive.agents.create({
  name: "triage",
  model: "z-ai/glm-5.3-flash",
  system: "You are a concise triage agent.",
  budget: { cap_micro_usd: 50_000_000, max_task_micro_usd: 5_000_000, period: "month" },
});

const session = await naive.sessions.create({
  agent_id: agent.id,
  message: "Summarize what changed in main overnight.",
});

for await (const event of naive.stream(session.id)) {
  console.log(event.type);
}
```

### 6.2 The same arc over HTTP, which is what FastAPI will use (https://usenaive.ai/docs/quickstart.md)
```bash
export NAIVE_API_KEY="sk_..."
export BASE="https://api.vetta.sh"   

AGENT=$(curl -sS "$BASE/v1/agents" \
  -H "Authorization: Bearer $NAIVE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "nightly-triage",
    "model": "z-ai/glm-5.3-flash",
    "window": "immediate",
    "system": "You are a concise triage agent.",
    "budget": { "cap_micro_usd": 50000000, "max_task_micro_usd": 5000000, "period": "month" }
  }' | jq -r .id)

SESSION=$(curl -sS "$BASE/v1/sessions" \
  -H "Authorization: Bearer $NAIVE_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"agent_id\": \"$AGENT\", \"message\": \"Summarize what you can see in your sandbox.\"}" \
  | jq -r .id)

curl -sN "$BASE/v1/sessions/$SESSION/stream" -H "Authorization: Bearer $NAIVE_API_KEY"
```
Expected stream (from the quickstart):
```json
{"seq":4,"type":"tool.started","data":{"name":"write","args":{"path":"~/notes.md","content":"hello from naive"}}}
{"seq":5,"type":"tool.completed","data":{"name":"write","output":"Successfully wrote 16 bytes","is_error":false}}
{"seq":13,"type":"message.completed","data":{"role":"assistant","content":"Done — notes.md contains exactly: hello from naive."}}
{"seq":14,"type":"session.usage","data":{"consumed_micro_usd":2023,"active_seconds":7,"tool_calls":2}}
{"seq":15,"type":"session.idle","data":{"stop_reason":"end_turn"}}
```

### 6.3 Multi-agent: a minimal team (https://usenaive.ai/docs/team/overview.md)
```bash CLI
naive agent create --name research-lead --model z-ai/glm-5.3-flash --harness pi --budget-usd 100 --max-task-usd 10 --budget-period month

naive team set agt_01j8rq5wn2k8zv4xm7c3h6pftb --member web-researcher@3 --member fact-checker@1 --board
```
```typescript TypeScript
const lead = await naive.agents.create({
  name: "research-lead",
  model: "z-ai/glm-5.3-flash",
  harness: "pi",
  budget: { cap_micro_usd: 100_000_000, max_task_micro_usd: 10_000_000, period: "month" },
});

await naive.agents.update(lead.id, {
  expected_version: lead.current_version,
  multiagent: {
    type: "coordinator",
    agents: [
      { type: "agent", id: "agt_9f2c…", version: 3 },
      { type: "agent", id: "agt_4a71…", version: 1 },
    ],
    board: true,
  },
});
```

### 6.4 *OUR SKETCH, not from the docs and untested*: FastAPI calling the documented REST routes
Only routes and fields documented above are used. SSE parsing follows the documented format
(`id:` = seq, `data:` = event JSON). Needs `httpx`.
```python
import json, os, httpx

BASE = "https://api.vetta.sh/v1"
H = {"Authorization": f"Bearer {os.environ['NAIVE_API_KEY']}", "Vetta-Version": "2026-09-30"}

def create_session(agent_id: str, message: str) -> dict:
    r = httpx.post(f"{BASE}/sessions", headers=H, json={"agent_id": agent_id, "message": message})
    r.raise_for_status(); return r.json()

def events(session_id: str, after_seq: int = 0) -> dict:
    r = httpx.get(f"{BASE}/sessions/{session_id}/events", headers=H,
                  params={"after_seq": after_seq, "include_accepted": "true"})
    r.raise_for_status(); return r.json()          # {data, has_more, next_cursor, ...}

def stream(session_id: str, after_seq: int = 0):
    with httpx.stream("GET", f"{BASE}/sessions/{session_id}/stream", headers=H,
                      params={"after_seq": after_seq}, timeout=None) as r:
        for line in r.iter_lines():
            if line.startswith("data:"):
                yield json.loads(line[5:].strip())

def approve(session_id: str, tool_call_id: str, allow: bool, reason: str | None = None):
    body = {"tool_call_id": tool_call_id, "decision": "allow" if allow else "deny"}
    if reason: body["reason"] = reason
    return httpx.post(f"{BASE}/sessions/{session_id}/tool_confirmations", headers=H, json=body).json()

def digest() -> dict:
    return httpx.get(f"{BASE}/digest", headers=H).json()
```

---

## 7. Limits, pricing, gotchas, open questions

**Limits** (https://usenaive.ai/docs/api/overview.md and others)
- 6,000 requests/min per org and 3,000 per key. **50 concurrently `running` sessions**; extra sessions
  queue. A 429 carries `Retry-After`.
- Idempotency: use the `Idempotency-Key` header on POSTs (retained 24h; the same key with a different
  body returns 409).
- Lists are paged with `limit` + `after` and return `{data, has_more, next_cursor}`. The SDK has no
  auto-paging (https://usenaive.ai/docs/sdk/pagination.md).
- Cron runs at most every 5 minutes. Browser sessions are capped at 360 min, phones at 60 min per
  agent, voice calls at 10 min.
- Handoffs: depth 4, 8 per session. Delegation is one level only.
- Errors look like `{ error: { type, code, message, request_id, param } }`.

**Pricing** (https://usenaive.ai/docs/platform/pricing.md)
- Plan: $20/mo Standard (credit included, trial gives $10) or $200/mo Pro. No seat fees.
- Model tokens: a 5-tier ledger (input, cache_write, cache_read, output, reasoning) at the window
  tariff (`immediate` > `priority` > `loose`). There's no per-model price table, because prices come
  from the live catalogue.
- Computer: billed per second while running, nothing while paused (the docs cite about $0.0666/hr for
  an agent's computer). Browser: $0.10/hr plus $0.01 per model-driven action. Email: $0.0008 per send.
  Media: the actual job cost. Web search: $0.00166. Web fetch: $0.00083.

**Gotchas**
1. No Python SDK. Write a thin httpx client and use the TS SDK only in Next.js server code.
2. Plan plus credits must be active before *anything* works. Do this tonight, because checkout needs a
   person.
3. Webhook, MCP and on_idle URLs must be **public HTTPS**. On Windows, run `cloudflared` or `ngrok` for
   local FastAPI.
4. Media and web spend is booked after the fact, so budgets can be overshot. Set conservative caps and
   watch `/v1/credits/spend`.
5. `ask` gates and the "read people's data" flag are the main ways autonomy stalls. A pending approval
   also pauses that agent's cron.
6. `claude_code`/`hermes` harnesses lose the platform built-ins. Use `pi` unless we need Claude Code
   inside the box.
7. Sessions can't be deleted, so test agents stay forever in the org's history. Use a separate org for
   experiments, or name agents clearly.
8. Order events by `seq`, not `created_at`. `message.completed` also fires for *our* messages
   (`role: "user"`).
9. `GET /v1/models` is paged and has several hundred entries. Don't hard-code a model id you haven't
   checked.
10. Doc inconsistencies we found:
    - Concept pages show object-style calls (`session.send(...)`, `session.stream()`,
      `session.confirm(...)`, `for await (... naive.sessions.list(...))`). The generated SDK
      reference documents `client.sessions.send(id, {...})`, `client.sessions.confirmTool`,
      `client.stream(id)` and one-page lists. **Trust the SDK reference.**
    - Session create is `agent` (name or id) on the concept page and `agent_id` in the SDK and REST
      examples.
    - The CLI stream resume flag is written both as `--from-seq` and `--after-seq`.
    - Deployment prompt field is `prompt` vs `input_template` (§3.7).
    - skill.md says teams, memory, outcomes and the wallet are "coming soon". The docs say teams are
      available and have an Outcomes page. The wallet is confirmed not available.

**Open questions (ask Naive staff at kickoff)**
- How are the hackathon "$1k+ Naïve credits" applied? Do we still need to pay $20 or start a trial?
- Which frontier models are in the catalogue (Claude? GPT?), and does `claude-sonnet-5` from skill.md
  exist?
- Can a hosted `fullstack` app run Python, or is it Node-only?
- Is there a supported way to take customer payments (Stripe) through Naive, or do we bring our own?
- When do client-executed custom tools ship? Are MCP-over-vault bearer setups the recommended path for
  hackathon backends?
- Does "Best use of naïve" favour blueprints/Studio (`naive up`) or the raw Managed Agents API?
- Does the Studio show a team's board, approvals and per-agent activity well enough to demo directly,
  or should we build our own "company ops" view from the digest and events API?
