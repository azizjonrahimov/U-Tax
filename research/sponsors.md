# Sponsor building blocks: Naïve x YC Autonomous Company Hackathon (Nov 7-8, 2026)

Researched 2026-11-06 with WebFetch and WebSearch. Every claim has a source URL. Anything I could not confirm from a page I read directly is marked **UNVERIFIED**. Search-engine snippets of pages I could not open (X/Twitter returned 402/451) are also marked UNVERIFIED.

---

## 0. TL;DR: which blocks matter for "how much runs without humans"

| Job in the company | Best block | Why |
|---|---|---|
| Brain, orchestration, budgets, audit | **Naïve Managed Agents** (required) | Teams, cron deployments, policies, audit log, spend ledger. The judges are the Naïve team. |
| Find customers (lead data) | **Apify** (or **Monid**, which proxies Apify and 90+ other providers on one balance) | One call returns structured leads (Google Maps contacts, social, etc.). |
| Talk to customers | **AgentMail** | Real inboxes over API, `message.received` webhooks, reply threading. Free tier covers a demo. |
| Get paid | **Stripe** payment links / Checkout plus the `checkout.session.completed` webhook | The agent creates its own product, price and link with no human involved. |
| Operate real-world apps | **Mobilerun** | Cloud Android/iOS agent from one REST call. It has its own side prize. |

Naïve has **native equivalents** for email, phone/SMS, mobile devices, a data catalogue, a CRM, wallets/cards and hosted apps (section 1). A sponsor is worth adding only where it beats the native primitive, or where it unlocks a sponsor prize.

---

## 1. Naïve (the required platform): what the SDK gives you

- **What it is:** a managed agent platform. You get agents (versioned config plus budget), sessions (runs), Linux "computers", skills, files, teams/coordinator, cron deployments, webhooks, identity (email, phone, domains, vault, cards, wallet, KYC/LLC formation), a CRM, hosted apps, a DB and storage, media generation, a data catalogue and cloud phones. Source: https://usenaive.ai/docs/llms.txt
- **SDK language: TypeScript only.** Package `@usenaive-sdk/managed-agents` (ESM-only). CLI: `npm i -g @usenaive-sdk/cli`. **There is no Python SDK**, so a Python/FastAPI backend has to call REST directly. Sources: https://usenaive.ai/docs/getting-started/introduction , https://usenaive.ai/docs/llms.txt
- **REST:** base `https://api.vetta.sh/v1` (vetta.sh docs now 301 to usenaive.ai), Bearer auth. Key calls: `POST /v1/agents`, `POST /v1/sessions`, `GET /v1/sessions/{id}/stream`. Source: https://usenaive.ai/docs/getting-started/introduction
- **Auth:** `NAIVE_API_KEY` env var (overrides the stored profile). `naive login --api-key sk_live_...`. Source: https://usenaive.ai/skill.md
- **Pricing:** Standard $20/mo with $20 credit and a 7-day trial (card required). Pro $200/mo with $200 credit. Prepaid USD balance. Source: https://usenaive.ai/docs/getting-started/introduction
- **Blueprints:** `naive catalog`, `naive template agency`, `naive up --dry-run` then `naive up`. Published blueprints: `agency` (templates `blank`, `seo-geo`) and `media` (`faceless`, `clipping`). Source: https://usenaive.ai/skill.md
- **Homepage templates** include "Get Paid (Stripe-based sales page)", AI Automation Agency, SEO/GEO Agency, Newsletter, Recruiting Agency, Paid Ads Agency and others. The homepage names Stripe, HubSpot, Vercel, GitHub, Gmail, Notion, Slack and Shopify as integrations. Source: https://usenaive.ai/
- **Native primitives that overlap with sponsors:**
  - Email inboxes on verified domains, inbound mail wakes the agent, `email.send` asks by default. Underlying provider not named. Source: https://usenaive.ai/docs/identity/email.md
  - Cloud phones: Android $0.05/min, iOS $0.08/min, idle timeout 10 min by default, 9 actions. Provider not named. Source: https://usenaive.ai/docs/computer/mobile.md
  - Data catalogue: about 1,900 read-only endpoints from 51 unnamed sources in 18 domains (people, companies, social, jobs, reviews...). Median $0.0015/call, capped at 25 results per run. Source: https://usenaive.ai/docs/capabilities/data.md
  - Built-in tools include `web_search`, `web_fetch`, `data_run`, `crm.read/write`, `mobile`, `browser`, `ask_operator`, `send_to_agent` and `board_write`. Source: https://usenaive.ai/docs/capabilities/tools.md
- **Autonomy-relevant controls:**
  - Tool policies are `allow` / `ask` / `deny`. **`ask` pauses the run and needs a human.**
  - `email.send`, `social.post`, `generate_video` and every MCP tool default to `ask`, and a `deny` default does not override them.
  - `ask_operator`, `request_tools` and `propose_plan` can never be `allow`.
  - So to score high on autonomy, explicitly set the tools you need to `"permission": "allow"` and rely on budgets for safety.
  - Source: https://usenaive.ai/docs/capabilities/tools.md
- There are also `api/reviews` ("recording human approvals and edits"), audit logs and outcomes (grade sessions against a rubric). These could be used to *prove* zero human touches in the demo. Source: https://usenaive.ai/docs/llms.txt
- **How to attach a sponsor to a Naïve agent:**
  1. Declare an MCP server on the agent with `{ "type": "url", "name": "...", "url": "https://..." }`. It must be public https. Limits are 20 servers per agent and 100 tools per server.
  2. Store the credential in a **vault** as `static_bearer` or `mcp_oauth`, keyed to the exact MCP URL. Inline `Authorization` headers are rejected, and `env_var` credentials return 501.
  3. Third-party OAuth apps ("over a thousand", provider unnamed) connect via a Connect Link: `naive identity connections apps --search stripe`.
  - Sources: https://usenaive.ai/docs/capabilities/tools.md , https://usenaive.ai/docs/identity/vault.md , https://usenaive.ai/docs/identity/connections.md

### Does any sponsor integrate directly with the Naïve SDK?
- **Mobilerun: yes, documented.** "Mobilerun connects to Naïve with a Mobilerun API key." The flow is: Naïve SDK agent logic, then a Mobilerun cloud device for the mobile steps, then results back to Naïve. Source: https://mobilerun.ai/blog/posts/autonomous-company-hackathon-naive-mobilerun . The exact mechanism is not stated (UNVERIFIED). The most likely path is the Mobilerun MCP (`https://api.mobilerun.ai/v1/mcp`, Bearer) stored as a vault `static_bearer`.
- **Stripe, HubSpot, Vercel:** named as integrations on the Naïve homepage (https://usenaive.ai/). They are not in the docs' tool list. Whether they are available as Connections is UNVERIFIED; check with `naive identity connections apps --search <name>`.
- **Stripe, Apify, Mobilerun, Monid, HubSpot, AgentMail** all have remote MCP servers that fit Naïve's "public https MCP + vault credential" model (details per sponsor below). One possible snag: **AgentMail's API-key MCP auth uses an `x-api-key` header, not Bearer** (https://agentmail.to/docs/integrations/mcp). Whether Naïve's `static_bearer` can supply that header is UNVERIFIED; `mcp_oauth` may be needed instead.
- **None of the sponsors are named as the underlying provider** of Naïve's native email, mobile or data primitives. The docs do not say, so this is UNVERIFIED either way.

---

## 2. AgentMail (deep dive): email inboxes for agents

- **What an agent can do:**
  - Create its own inboxes and send/receive/reply over the API.
  - Get real-time `message.received` events and threads with quote-stripped reply text (`extracted_text`).
  - Use labels to track state, and handle attachments (PDF/DOCX text extraction).
  - Use calendar and allow/block lists.
  - Sources: https://agentmail.to/docs/quickstart , https://agentmail.to/docs/integrations/mcp
- **Python:** `pip install agentmail python-dotenv`. The package is `agentmail`. Env var `AGENTMAIL_API_KEY`. Source: https://agentmail.to/docs/quickstart
  ```python
  from agentmail import AgentMail
  client = AgentMail(api_key=os.getenv("AGENTMAIL_API_KEY"))
  inbox = client.inboxes.create()                       # domain optional
  client.inboxes.messages.send(inbox.inbox_id, to="lead@x.com", subject="Hi", text="...")
  msgs = client.inboxes.messages.list(inbox.inbox_id, limit=10).messages  # msg.extracted_text
  client.inboxes.messages.reply(inbox_id=..., message_id=..., text="...")
  client.inboxes.messages.update(inbox_id=..., message_id=..., add_labels=["replied"], remove_labels=["unreplied"])
  client.webhooks.create(url="https://you/inbound", event_types=["message.received"])
  ```
  Sources: https://agentmail.to/docs/quickstart , https://agentmail.to/docs/sending-receiving-email , https://agentmail.to/docs/webhooks-overview
- **Webhooks:**
  - About 20 event types (`message.received/sent/delivered/bounced/opened`, `domain.verified`, `calendar.event.*`).
  - The payload carries `event_id` for dedupe. Bodies over 64 KiB are replaced by a `body_url` that expires in 1 h.
  - Verify with Svix using `webhook.secret` (needs the raw body).
  - **WebSockets** are an alternative when you have no public URL.
  - Source: https://agentmail.to/docs/webhooks-overview
- **MCP:** `https://mcp.agentmail.to/mcp` with 47 tools. OAuth, or API key in the `x-api-key` header. Source: https://agentmail.to/docs/integrations/mcp
- **Framework integrations:** OpenAI Agents SDK, LangChain, Vercel AI SDK, Claude Agent SDK, Google ADK. Source: https://www.agentmail.to/docs/llms.txt
- **Pricing:** Source: https://www.agentmail.to/pricing
  - **Free $0:** 3 inboxes, 100 emails/day (3,000/mo), 1 custom domain, no card.
  - **Developer $20:** 10 inboxes, 1,000/day.
  - **Startup $200:** 150 inboxes, 15k/day. Early-stage startups can apply for a free month.
  - Hackathon credits: not listed on the pricing page. The user's brief says all participants get $1k+ in sponsor credits (UNVERIFIED per sponsor).
- **Also provisionable via Stripe Projects** (`stripe projects add agentmail/...`). Source: https://docs.stripe.com/projects.md
- **Setup time:** about 10 min to first send. Add about 30 min for a custom domain (DNS).
- **Gotchas:**
  - 429 responses carry `Retry-After`, so use backoff.
  - Agent sign-up is idempotent, and calling it again **rotates the key**.
  - Send both HTML and text for deliverability.
  - Sources: https://agentmail.to/docs/quickstart , https://agentmail.to/docs/sending-receiving-email
  - Cold outreach and warmup rules are not documented on the pages read (UNVERIFIED). Expect deliverability trouble from a fresh domain blasting cold leads, so keep demo volume small.

## 3. Stripe (deep dive): agent-friendly payments

- **What an agent can do:**
  - Create products, prices and **payment links** (`buy.stripe.com/...`) and Checkout Sessions.
  - Create invoices and subscriptions.
  - Read charges and payouts, and run analytics.
  - Prepare a Stripe Atlas incorporation.
  - Accept payments *from* other agents (machine payments).
  - Source: https://docs.stripe.com/mcp.md
- **Agent toolkit status:** `stripe-agent-toolkit` (PyPI), `@stripe/agent-toolkit` and `@stripe/mcp` are **deprecated** ("no further updates, including security fixes"). Use the hosted MCP server or agent plugins instead. Source: https://github.com/stripe/ai
- **MCP:** `https://mcp.stripe.com`, authenticated by OAuth or an **agent API key** as Bearer. Source: https://docs.stripe.com/mcp.md
  - Tools: `stripe_api_search/details/read/write` (generic over a large API list including payment links, prices, products, checkout sessions, invoices, subscriptions, refunds, webhook endpoints), `stripe_analytics`, `atlas_company_formation`, `search_stripe_documentation`.
  - **Deadline:** from December 2026, MCP rejects full secret keys and untagged restricted keys. Use agent-tagged keys.
- **Agent API keys:**
  - These are restricted keys tagged "Authorizing agent access to your account".
  - They are **automatically subject to approval rules**: payouts, refunds and account config changes need a human reviewer by default.
  - Refund via MCP is marked "Human confirmation", and the approval expires in 24 h.
  - Keep refunds/payouts out of the demo loop, or they count as human touches.
  - Sources: https://docs.stripe.com/keys.md , https://docs.stripe.com/mcp.md
- **Python (classic SDK):** `pip install stripe`. Env var `STRIPE_SECRET_KEY` (convention). Sandbox keys are `sk_test_` / `rk_test_`. Source: https://docs.stripe.com/keys.md . The payment-link create call is `POST /v1/payment_links` with `line_items[0][price]` and `line_items[0][quantity]`. Optional params: `after_completion`, `metadata` (copied to the Checkout Session), `restrictions`, `submit_type`. Source: https://docs.stripe.com/api/payment-link/create.md
  ```python
  # UNVERIFIED in this session (SDK syntax from memory; REST shape above is verified)
  import stripe; stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
  price = stripe.Price.create(currency="usd", unit_amount=4900, product_data={"name": "Audit"})
  link  = stripe.PaymentLink.create(line_items=[{"price": price.id, "quantity": 1}],
                                    metadata={"lead_id": "123"})
  # fulfil on webhook event checkout.session.completed (UNVERIFIED this session)
  ```
- **Machine payments (agents paying you):**
  - Uses MPP (cards via Shared Payment Tokens, min $0.50) and x402 (USDC on Base, min $0.01).
  - Stablecoins need Dashboard approval and are not available in New York.
  - Starter code: https://github.com/stripe-samples/machine-payments . Source: https://docs.stripe.com/payments/machine.md
- **Stripe Projects:** `stripe plugin install projects` lets an agent provision **AgentMail, OpenRouter, Render, Vercel**, Supabase, Neon and 60+ others, with credentials synced to `.env`. It has `--json --no-interactive --accept-tos` flags. This is a nice "the agent signs up for its own tools" demo moment. Source: https://docs.stripe.com/projects.md
- **Free tier:** the sandbox is free (test cards). Live mode charges standard processing fees (not re-verified). Hackathon credits: UNVERIFIED.
- **Setup:** about 15 min in a sandbox. Live mode needs account activation (KYC), so **demo in a sandbox** unless you are already activated.

## 4. Mobilerun (deep dive): AI agent driving real phones (side prize: Best Mobile Automation)

- **What an agent can do:**
  - Give a natural-language goal; the Mobilerun agent taps, swipes and types on a cloud Android or connected iPhone.
  - Return structured output via `outputSchema`.
  - Use phone numbers/SMS, mailboxes with **OTP lookup**, proxies, stored credentials, workflows/triggers and webhooks.
  - Sources: https://docs.mobilerun.ai/ , https://docs.mobilerun.ai/llms.txt
- **Cloud REST:** base `https://api.mobilerun.ai/v1`, `Authorization: Bearer $MOBILERUN_API_KEY` (key prefix `dr_sk_`). Source: https://docs.mobilerun.ai/cloud/quickstart
  ```python
  devs = requests.get(f"{API}/devices", headers=H, params={"state": "ready"}).json()["items"]
  task = requests.post(f"{API}/tasks", headers=H, json={"deviceId": devs[0]["id"],
          "task": "Open Instagram and DM @shop ...", "llmModel": "mobilerun/mobile-agent-fast",
          "maxSteps": 50}).json()
  st = requests.get(f"{API}/tasks/{task['id']}/status", headers=H).json()  # completed|failed|cancelled; .message/.output
  ```
  `GET /tasks/{id}/trajectory` returns the step-by-step record with screenshots. Source: https://docs.mobilerun.ai/cloud/quickstart
- **Python SDK:**
  - The cloud SDK is `pip install mobilerun-sdk`. The import is `from mobilerun_sdk import Mobilerun` in v3.1.0; older v2 used `from mobilerun import Mobilerun`. Source: search results for https://pypi.org/project/mobilerun-sdk/3.1.0/ (the page itself failed to render, so call names are UNVERIFIED).
  - The local framework is `uv pip install mobilerun` (`MobileAgent`, `MobileConfig`). It needs **Python 3.11-3.13** (3.14 silently installs an old 0.3.9), adb, and your own LLM key. Source: https://docs.mobilerun.ai/framework/quickstart
- **MCP:** `https://api.mobilerun.ai/v1/mcp` (Bearer API key) or `https://cloud.mobilerun.ai/api/mcp` (OAuth). It has 39 tools, including `run_task`, `get_task`, `list_devices`, `create_device` and `device_action`. Source: https://docs.mobilerun.ai/mcp-server
- **Webhooks:** signed callbacks when tasks, workflows, numbers or mailboxes change state. Source: https://docs.mobilerun.ai/llms.txt
- **Pricing:** Sources: https://docs.mobilerun.ai/credits , https://docs.mobilerun.ai/device-types
  - 1 credit = $0.01, about 0.5 credit per agent step. $5 buys 500 credits.
  - Personal Phone (bring your own) costs $5/mo with 250 credits.
  - Cloud Android costs $50/mo (EU-hosted).
- **Hackathon offer:** Source: https://mobilerun.ai/blog/posts/autonomous-company-hackathon-naive-mobilerun
  - **72 h of Android + iOS device access**, up to **6 seats per team**.
  - **Cloud iPhones first-come-first-served**, so grab them early.
  - Claim via the event QR code.
- **Conflict:** the device-types doc lists *no* cloud iPhone (only a BYO iPhone), but the hackathon post promises cloud iPhones. Sources: https://docs.mobilerun.ai/device-types vs the blog post above.
- **Setup:** about 15 min (claim the device, get the key, run the quickstart).
- **Gotchas:**
  - You need a device in the `ready` state, and the sample's `items[0]` crashes if there is none.
  - Omitting `llmModel` uses the account default.
  - Judges want a working end-to-end workflow, not an agent describing what it would do.
  - Sources: https://docs.mobilerun.ai/cloud/quickstart , https://mobilerun.ai/blog/posts/autonomous-company-hackathon-naive-mobilerun

## 5. Apify (deep dive): scrapers/Actors for lead generation and market data

- **What an agent can do:** run any Apify Store Actor (Google Maps leads with emails, social scrapers, web crawlers, RAG browser) and read structured dataset items. Source: https://docs.apify.com/platform/integrations/mcp
- **Python:** `pip install apify-client` (Python 3.11+). Get the token at console.apify.com/settings/integrations. `APIFY_TOKEN` is the env var used by the MCP/CLI. Sources: https://docs.apify.com/api/client/python/docs/overview , https://docs.apify.com/platform/integrations/mcp
  ```python
  from apify_client import ApifyClient
  c = ApifyClient(os.environ["APIFY_TOKEN"])
  run = c.actor("lukaskrivka/google-maps-with-contact-details").call(run_input={...})  # None => failed
  items = c.dataset(run["defaultDatasetId"]).list_items().items
  ```
  The call/list pattern comes from https://docs.apify.com/api/client/python/docs/overview . The actor ID comes from https://apify.com/lukaskrivka/google-maps-with-contact-details/api/client/python . The exact `run_input` schema differs per actor; check the actor page.
- **MCP:** `https://mcp.apify.com` (OAuth or `Bearer APIFY_TOKEN`). Local: `npx -y @apify/actors-mcp-server`. Source: https://docs.apify.com/platform/integrations/mcp
  - Tools: `search-actors`, `fetch-actor-details` (shows pricing), `call-actor`, `apify/rag-web-browser`, `get-dataset-items`.
  - `call-actor` returns storage IDs, not results.
  - Cap spend with `maxTotalChargeUsd` in `callOptions`. The rate limit is 30 req/s.
- **Pricing:** Source: https://apify.com/pricing
  - **Free plan: $5/month platform credit**, no card. Runs are blocked once it is used up.
  - Compute is $0.20/CU, and the plan allows 5 concurrent runs.
  - Starter costs $19/mo.
  - Hackathon credits: none listed (UNVERIFIED).
- **Setup:** about 10 min.
- **Gotchas:**
  - Some Google Maps actors need **residential proxies, which require a paid plan** (Google blocks datacenter IPs). Source: https://apify.com/clever_fashion/helloworldimersiversity
  - Actor runs take tens of seconds to minutes, so run them async.

## 6. Monid (deep dive): pay-per-call tool registry for agents

- **What an agent can do:** discover, inspect and run 2,800+ tools from 90+ providers (Exa, Apollo, Firecrawl, ElevenLabs, **Apify Actors**) from one prepaid balance, with no per-provider signup. Sources: https://monid.ai/ , https://monid.ai/skill.md
- **REST:** base `https://api.monid.ai`, `Authorization: Bearer monid_live_...`. Calls are `POST /v1/discover {"query":...}`, `POST /v1/inspect {"provider","endpoint"}` and `POST /v1/run {"provider","endpoint","input"}`. It also supports USDC pay-per-run via **x402**. No Python SDK is documented. Source: https://monid.ai/docs/guide/quickstart-api.md
- **CLI:** `npm install -g @monid-ai/cli@latest` (`monid discover -q "twitter posts"`, `monid run -p apify -e /apidojo/tweet-scraper -i '{...}'`, `monid runs get -r <id>`). Keys are stored via `monid keys add`; no env var is documented. Source: https://monid.ai/skill.md
- **MCP:** `https://mcp.monid.ai/v1` (Streamable HTTP, OAuth). Source: https://monid.ai/docs/guide/quickstart-mcp.md
- **Pricing:** Sources: https://monid.ai/ , https://monid.ai/skill.md
  - **$1 free credit**, plus signup code **`MONID-SIGNUP`**.
  - Minimum top-up is $5. Per-endpoint price cards; for example $0.0013/call.
  - Provider errors on COMPLETED runs are not billed.
- **Setup:** about 10 min.
- **Gotchas:** Source: https://monid.ai/skill.md
  - `COMPLETED` can still contain a provider 4xx/5xx.
  - `BLOCKED` is terminal (top up first).
  - Run statuses are uppercase.
  - Check `monid runs list` after network failures to avoid paying twice.
  - **On Windows Git Bash, prefix with `MSYS_NO_PATHCONV=1`**, or `-e /path` values get mangled. This applies to our machine.
- **Overlap:** Naïve's native data catalogue (`data_search/describe/run`, about 1,900 endpoints) has the same discover/describe/run shape. Whether Naïve's is powered by Monid is not stated (UNVERIFIED). Prefer the native one unless Monid has a needed provider.

---

## 7. Other sponsors (shorter)

### OpenAI
- **Agent use:** LLM brains, handoffs, tracing.
- **Package:** `pip install openai-agents`. Env var `OPENAI_API_KEY`.
- **Key calls:** `Agent(name=..., instructions=...)`, `Runner.run_sync(agent, "...")`. Function tools are auto-schema'd, and remote MCP tools are supported. Source: https://openai.github.io/openai-agents-python/
- **Note:** Naïve has its own model router/proxy (Anthropic/OpenAI-compatible), so you may not need a separate key inside Naïve. Source: https://usenaive.ai/docs/llms.txt
- **Credits:** UNVERIFIED.
- **Gotcha:** the Agents SDK uses the Responses API by default.

### OpenRouter
- **Agent use:** one key for many models, automatic fallbacks.
- **Base URL:** `https://openrouter.ai/api/v1`. Env var `OPENROUTER_API_KEY`.
- **Usage:** use the OpenAI SDK with `base_url`, or the native `pip install openrouter`. Source: https://openrouter.ai/docs/quickstart
- **Free models:** `:free` variants are limited to **50 req/day without credits and 1,000/day after buying 10 credits**. The `openrouter/free` router auto-picks a free model. Source: https://openrouter.ai/docs/faq
- **Stripe Projects:** provisionable there. Source: https://docs.stripe.com/projects.md
- **Gotcha:** `~openai/...-latest` aliases drift, so pin slugs. Source: https://openrouter.ai/docs/quickstart

### HubSpot
- **Agent use:** a CRM of record (contacts, companies, deals, tickets) and pipeline stages.
- **Package:** `pip install hubspot-api-client` (v13.0.0, Nov 2 2026). Source: https://pypi.org/project/hubspot-api-client/
  ```python
  from hubspot import HubSpot
  from hubspot.crm.contacts import SimplePublicObjectInputForCreate
  api = HubSpot(access_token=TOKEN)
  api.crm.contacts.basic_api.create(simple_public_object_input_for_create=SimplePublicObjectInputForCreate(properties={"email": "a@b.com"}))
  api.crm.contacts.search_api.do_search(...)
  ```
- **MCP:** remote `https://mcp.hubspot.com` (OAuth 2.1 + PKCE). It can read/write contacts, companies, deals and tickets. Source: https://developers.hubspot.com/docs/build-with-ai/remote-mcp-server
- **API versioning:** date-based paths, e.g. `/crm/objects/2026-09/contacts`. Source: https://developers.hubspot.com/docs/api/overview
- **Token:** a private-app token env var name is not official (UNVERIFIED).
- **Overlap:** Naïve has a native CRM (`crm.read/write`, `api/crm`). Use HubSpot only if a sponsor-visible CRM dashboard helps the demo.

### Vercel
- **Agent use:** host the customer-facing site/app, AI Gateway, Sandbox.
- **AI Gateway:** no markup. A free tier covers a model subset with lower rate limits (429). The free credit stops once you buy credits. Source: https://vercel.com/docs/ai-gateway/pricing
- **Gotcha: Hobby cron jobs run at most once per day, ±59 min.** Use Naïve deployments (cron) for agent schedules instead. Source: https://vercel.com/docs/cron-jobs/usage-and-pricing
- **Sandbox Hobby limits:** 5 CPU-h/mo, 45 min max duration. Source: https://vercel.com/docs/vercel-sandbox/pricing (via search snippet, UNVERIFIED). A Python SDK (`vercel.sandbox`) is UNVERIFIED (third-party).
- **Stripe Projects:** provisionable there. Source: https://docs.stripe.com/projects.md

### Render
- **Agent use:** host the FastAPI backend and webhook receivers, and run Workflows (durable tasks, Python SDK, **public beta** since about Apr 2026). Source: https://releases.sh/release/rel_I8pkxnvzfOoe4yVff58yk (search snippet)
- **MCP server:** `RENDER_API_KEY` (broad, full-account scope). Source: search result for https://lobehub.com/mcp/render-oss-render-mcp-server (UNVERIFIED details)
- **Free tier gotchas:** Source: https://render.com/docs/free
  - Web services **spin down after 15 min idle**, and a cold start takes about 1 min. This is risky for Stripe/AgentMail webhooks during the demo; keep the service warm or use a paid instance.
  - 750 h/mo.
  - Free Postgres **expires after 30 days**.
  - Ephemeral disk.
  - Outbound SMTP ports 25/465/587 are blocked.
- **Stripe Projects:** provisionable there. Source: https://docs.stripe.com/projects.md

### Luxor (Tenki Cloud)
- **What it is:** Luxor Technology (bitcoin/AI compute) owns **Tenki Cloud**: agent sandboxes (Linux VMs, snapshots/forks, exposed preview ports), CI runners and a code reviewer. Sources: https://www.luxor.tech/ai , https://tenki.cloud/products/sandbox.md
- **SDK:**
  - TS: `npm install @tenkicloud/sandbox`. Source: https://tenki.cloud/products/sandbox.md
  - Python: **conflicting package names**, `pip install tenki` (product page snippet) vs `tenki-sandbox` (PyPI). Sources: search results for https://tenki.cloud/products/sandbox.md and https://pypi.org/project/tenki-sandbox/
  - The env var is `TENKI_API_KEY` per AG2 docs (UNVERIFIED). Source: https://docs.ag2.ai/docs/user-guide/extensions/tenki/
- **Pricing:** per second, $0.0504/core-h, $0.0162/GiB-h RAM, first 5 GiB disk free. Starter allows 5 active sessions. Source: https://tenki.cloud/products/sandbox.md
- **Relevance:** low. Naïve already gives each session a computer.
- **Sponsor-list note:** a search snippet of the sponsor list said "Tenki", which likely refers to Luxor (UNVERIFIED).

---

## 8. Public info about the hackathon and Naïve

- **Event page:** https://events.ycombinator.com/naive-autonomous-companies-hackathon returned only its title, "Build an agent-run company that runs itself". Rules and judging were not readable.
- **Naïve's announcement:** "world's first Autonomous Company Hackathon @ycombinator, **$100,000+ in prizes. Winners get a guaranteed YC interview.** Mac Minis, iPhone 18 + much more... Pick a business. Build the agent team that runs it with the Naïve SDK. **Show it operating at demo time.**" This is a search snippet of https://x.com/usenaive/status/2114715211244376391 ; the page itself returned 402, so it is UNVERIFIED.
- **Sponsor list per search snippets:** "OpenAI, OpenRouter, Supabase, Neon, Vercel, Stripe, Tenki, Monid, Apify, AgentMail". This differs from our brief, which adds HubSpot, Luxor, Mobilerun and Render. Sources: snippets of the X posts above and https://x.com/mobilerun_ai/status/2118443925186752884 (UNVERIFIED).
- **Event basics:** Nov 7-8 at YC SF. Pick any business (media, agencies, commerce, software). Agents must do real work at demo time. Source: https://agihunt.info/en/p/1a164e396c3f19901fa2e7784ee (dated Oct 23, 2026; cites https://x.com/ycombinator/status/2113336355329921355)
- **Mobilerun track:** "Best Mobile Automation" in its blog, "Mobile Side Track" on X. Emphasis on a working end-to-end workflow, not an agent describing what it would do. Source: https://mobilerun.ai/blog/posts/autonomous-company-hackathon-naive-mobilerun
- **Naïve's 30-day Autonomous Company Tournament:** started Nov 1. **"Revenue is the only score"**, measured as verified revenue during the round. Prizes are a Lamborghini Aventador S for 1st, $50k for 2nd, $25k for 3rd. Standings are still pending, so there are no winners yet. This is a strong hint that Naïve's judges value real money moving autonomously. A hackathon company could plausibly be entered in the tournament too (UNVERIFIED). Source: https://usenaive.ai/tournament
- **Naïve company facts:** YC S25, founders Sean Dorje and Dennis Zax (sold ezML), $28.5M Series A led by Nexus (Aug 6, 2026). Thesis: maximize useful work per token. Sources: https://siliconangle.com/2026/08/06/naive-bags-28-5m-funding-automate-creation-day-day-running-almost-business/ (via search snippet), https://www.ycombinator.com/launches/Pje-naive-hire-autonomous-employees-that-actually-run-your-company
- **YC launch pitch:** "AI employees" with their own identity, bank account, email, credentials and compute. They "sign up for tools and pay for services", "run without a human in the loop", and use recursive learning (outputs feed patterns back week over week). Source: https://www.ycombinator.com/launches/Pje-naive-hire-autonomous-employees-that-actually-run-your-company
- **Naïve benchmarks listed:** Business Bench, TheAgentCompany, Long Horizon Terminal Bench, Clipping/Short Form Bench. Source: https://usenaive.ai/
- **Past Naïve hackathons and winners:** none found. Similar events:
  - Nevermined "Autonomous Business Hackathon" (Mar 5-6, 2026, SF): agents earn revenue and make purchases.
  - Terac "Zero Human Company Hackathon" (Aug 15, 2026, SF), with Stripe and Render among sponsors and OpenAI/DeepMind/YC-founder judges. Source: https://terac.com/blog/zero-human-company-hackathon
  - Neither published judging rubrics or winners on pages I could read.
  - An analogous rubric (AI Tinkerers "One-Person Unicorn") scores 1-5 on Execution & Functionality, AI & Agents Usage, Scalability (One-Person Model), and Problem Clarity & Market Impact. Source: https://aitinkerers.org/hackathons/h_1aZT6GQ8LvU (via search snippet).

### Judging takeaways (inferred from the above; not an official rubric)
1. The demo must show agents **actually operating live**: real emails sent and received, a real link paid, a real phone app driven. Not slides or "would do". (Naïve X post snippet, Mobilerun blog)
2. **Count the human touches and drive them to zero.** Set Naïve `ask` defaults (`email.send`, MCP tools) to `allow` under budgets. Avoid Stripe approval-rule actions (refunds/payouts). Show the audit log / reviews as proof. (Naïve tools docs, Stripe keys docs)
3. **Revenue is Naïve's favourite metric** (Tournament). A closed loop of lead, outreach, reply, payment link, paid, and fulfilment, even in a Stripe sandbox, matches that narrative best.
4. **Use the Naïve-native primitives prominently** (teams/coordinator, deployments, budgets, identity). The judges are the platform team. Add sponsors where they clearly beat the native primitive or unlock a side prize (Mobilerun).
