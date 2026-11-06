// U-Tax on Naïve: seven agents, one shared board, one MCP server (the U-Tax gateway).
// Apply with `naive up --dry-run` first, then `naive up`. Env vars: see .env.example.
//
// Design: docs/designs/u-tax.md (E1 one gateway, E5 U-Tax tools only, D17 MCP transport).
// Shapes: contracts/api.md v1 §6 (tools) and §7 (memo template).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineProject } from "@usenaive-sdk/blueprints";

import { CREW, MCP_SERVER, MEMBERS, type AgentKey } from "./src/crew.js";
import { toolsetFor } from "./src/policy.js";

const HERE = dirname(fileURLToPath(import.meta.url));

/** The gateway's public MCP endpoint (cloudflared tonight, Render Saturday). Never localhost. */
function mcpUrl(): string {
  const url = process.env.UTAX_MCP_URL;
  if (!url) {
    throw new Error(
      "UTAX_MCP_URL is not set. Point it at the gateway's public HTTPS /mcp endpoint, " +
        "e.g. https://<tunnel>.trycloudflare.com/mcp (Naïve refuses localhost).",
    );
  }
  return url;
}

/** The agent's system prompt: its own file plus the firm-wide rules every agent carries. */
function system(agent: AgentKey): string {
  const own = readFileSync(join(HERE, "prompts", CREW[agent].prompt), "utf8");
  const firm = readFileSync(join(HERE, "prompts", "_firm-rules.md"), "utf8");
  return `${own.trim()}\n\n${firm.trim()}\n`;
}

const usd = (dollars: number) => Math.round(dollars * 1_000_000); // integer micro-USD

// Models: chosen from the docs' examples. z-ai/glm-5.3-flash is the docs' "routine tool use"
// model; deepseek/deepseek-v4-flash-0731 is their "strict JSON output" pick, used where an
// agent must return exact quoted spans. Both serve the `immediate` window only (docs), so
// every agent runs `immediate`.
const MODEL_TOOL_USE = "z-ai/glm-5.3-flash"; // UNVERIFIED: check GET /v1/models
const MODEL_STRICT = "deepseek/deepseek-v4-flash-0731"; // UNVERIFIED: check GET /v1/models

// Conservative daily caps (period: day). Members delegated with wait:true run on the Chief
// of Staff's budget; board-woken members run on their own. Total ceiling: $10.50/day.
const BUDGETS: Record<AgentKey, { cap: number; task: number }> = {
  cos: { cap: 3.0, task: 0.5 },
  intake: { cap: 2.0, task: 0.25 },
  tax: { cap: 1.25, task: 0.2 },
  qa: { cap: 1.25, task: 0.2 },
  acct: { cap: 1.0, task: 0.15 },
  mkt: { cap: 1.0, task: 0.25 },
  hr: { cap: 1.0, task: 0.25 },
};

const MODELS: Record<AgentKey, string> = {
  cos: MODEL_TOOL_USE, // UNVERIFIED: check GET /v1/models
  intake: MODEL_STRICT, // UNVERIFIED: check GET /v1/models
  tax: MODEL_TOOL_USE, // UNVERIFIED: check GET /v1/models
  qa: MODEL_STRICT, // UNVERIFIED: check GET /v1/models
  acct: MODEL_TOOL_USE, // UNVERIFIED: check GET /v1/models
  mkt: MODEL_TOOL_USE, // UNVERIFIED: check GET /v1/models
  hr: MODEL_TOOL_USE, // UNVERIFIED: check GET /v1/models
};

const IDENTITY = "utax-firm";
const GATEWAY_MCP_URL = mcpUrl();

// Phone: Naïve numbers carry SMS only; `voice` is always false (docs, api/messaging.md).
// Off by default. Set UTAX_PHONE_E164 to attach a number the owner already bought, so an
// inbound text wakes the Chief of Staff. The firm cannot text back (outbound SMS is 501).
const PHONE_E164 = process.env.UTAX_PHONE_E164;

function agent(key: AgentKey) {
  const seat = CREW[key];
  return {
    name: seat.name,
    description: seat.description,
    model: MODELS[key],
    window: "immediate" as const,
    budget: { cap_micro_usd: usd(BUDGETS[key].cap), max_task_micro_usd: usd(BUDGETS[key].task), period: "day" as const },
    system: system(key),
    identity: IDENTITY, // holds the vault, so the gateway bearer is injected into its sessions
    mcp_servers: [{ name: MCP_SERVER, url: GATEWAY_MCP_URL }],
    tools: toolsetFor(key),
    metadata: { utax_actor: seat.actor },
  };
}

export default defineProject({
  name: "u-tax",
  // The engine adds an `apps` tool (ask) to template crews unless this is false. U-Tax agents build no apps.
  crew_edits_apps: false,
  title: "U-Tax",
  description:
    "An accounting firm run by seven agents and one human CEO. Free Delaware franchise-tax checkups for startups. Educational estimates, not tax advice.",

  identities: [
    {
      name: IDENTITY,
      description: "The firm. Holds the gateway credential. No native inbox: client mail arrives through the gateway.",
      ...(PHONE_E164 ? { phone: { agent: CREW.cos.name, e164: PHONE_E164 } } : {}),
    },
  ],

  vaults: [
    {
      name: "utax-secrets",
      identity: IDENTITY,
      credentials: [
        {
          kind: "static_bearer",
          key: "U-Tax gateway tool secret",
          mcp_server_url: GATEWAY_MCP_URL,
          value: { from_env: "UTAX_TOOL_SECRET" },
        },
      ],
    },
  ],

  agents: [
    {
      ...agent("cos"),
      multiagent: {
        type: "coordinator",
        agents: MEMBERS.map((m) => ({ type: "agent" as const, name: CREW[m].name })),
        board: true,
      },
      handoffs: "team",
      schedules: [
        {
          // Sunday 8:00 AM Pacific: the overnight brief (design doc CEO review #4).
          cron: "0 8 * * 0",
          timezone: "America/Los_Angeles",
          summary: "Morning brief for the CEO",
          input:
            "Morning brief. Call utax.firm_brief once. From its result only, write the CEO's spoken brief " +
            "(under 120 words, readable aloud in 45 seconds): clients served, money saved versus spent, human " +
            "touches, anything BLOCKED and what it needs, and the one decision you need from the CEO today. " +
            "Quote numbers exactly as firm_brief returned them; never add or compute any.",
          budget_micro_usd: usd(0.5),
          identity: IDENTITY,
        },
      ],
    },
    ...MEMBERS.map((m) => ({ ...agent(m), handoffs: false as const })),
  ],

  tasks: [
    {
      key: "open-the-firm",
      title: "Open the firm",
      body:
        "Call utax.list_open_cases. For each open case, put one card on the board for the agent whose step " +
        "is next (intake → tax → qa → accounting) and assign it. Then put one card for marketing: \"Post the " +
        "free franchise-tax checkup to the event channels\" and one for hr: \"Open the paid 1-hour gig\".",
      assignee: CREW.cos.name,
    },
  ],
});
