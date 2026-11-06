// U-Tax on Naïve: seven agents, one shared board, one MCP server (the U-Tax gateway).
// Apply with `naive up --dry-run` first, then `naive up`. Env vars: see .env.example.
//
// Design: docs/designs/u-tax.md (E1 one gateway, E5 U-Tax tools only, D17 MCP transport).
// Shapes: contracts/api.md v1.4 §1 (one tool token per agent), §6 (tools, one MCP URL per seat),
// §6b (wake messages),
// §7 (memo template).

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineProject } from "@usenaive-sdk/blueprints";

import { CREW, MCP_SERVER, MEMBERS, type AgentKey } from "./src/crew.js";
import { toolsetFor } from "./src/policy.js";

const HERE = dirname(fileURLToPath(import.meta.url));

/** True for loopback, private, link-local, CGNAT and mDNS/internal hosts Naïve refuses. */
function isPrivateHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/\.$/, "").replace(/^\[|\]$/g, "");
  if (host === "localhost" || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal")) return true;
  if (host.includes(":")) {
    // IPv6: loopback/unspecified/IPv4-compatible (::x), unique-local fc00::/7, link-local fe80::/10,
    // multicast ff00::/8, IPv4-mapped, NAT64 64:ff9b::/96. Public IPv6 literals are rare for a tunnel.
    return host.startsWith("::") || /^f[cdf]/.test(host) || /^fe[89ab]/.test(host) || host.startsWith("64:ff9b:");
  }
  const v4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  if (v4) {
    const [a, b] = [Number(v4[1]), Number(v4[2])];
    return (
      a === 0 || a === 10 || a === 127 || a >= 224 || // this-net, private, loopback, multicast/reserved
      (a === 100 && b >= 64 && b <= 127) || // CGNAT
      (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) ||
      (a === 198 && (b === 18 || b === 19)) // benchmarking
    );
  }
  return !host.includes("."); // a single-label name only resolves on a private network
}

/**
 * The gateway's public MCP base, ending in /mcp (cloudflared tonight, Render Saturday). Each agent
 * calls its own seat under it, `<base>/<actor>` (contract v1.4 §6). Fails closed.
 */
function mcpUrl(): string {
  const raw = (process.env.UTAX_MCP_URL ?? "").trim();
  const hint = "Point it at the gateway's public HTTPS /mcp endpoint, e.g. https://<tunnel>.trycloudflare.com/mcp";
  if (!raw) throw new Error(`UTAX_MCP_URL is not set. ${hint}.`);
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error(`UTAX_MCP_URL is not a URL: ${raw}. ${hint}.`);
  }
  // Naïve refuses these at apply (400 validation_failed); refuse them here, before anything is provisioned.
  if (url.protocol !== "https:" || isPrivateHost(url.hostname)) {
    throw new Error(`UTAX_MCP_URL must be public HTTPS (Naïve refuses http, localhost and private hosts): ${raw}. ${hint}.`);
  }
  // No query or fragment: a token in the URL would be stored in plain text on every agent.
  if (url.search || url.hash) throw new Error(`UTAX_MCP_URL must not carry a query or fragment: ${raw}. ${hint}.`);
  // A bare tunnel host 404s, which Naïve only reports as session.degraded: agents would run with no tools.
  if (!url.pathname.replace(/\/$/, "").endsWith("/mcp")) {
    throw new Error(`UTAX_MCP_URL must end in /mcp (the gateway's MCP endpoint): ${raw}. ${hint}.`);
  }
  return url.toString().replace(/\/$/, "");
}

/**
 * Each agent's own gateway tool token (contract v1.4 §1): the token decides the actor and the
 * tools the gateway lets it call. Sealed into that agent's own vault from env at apply.
 * Refuse empty, short, padded or shared tokens: `naive up` matches a credential by kind/key/url,
 * never the value, so a bad value sealed once stays until it is deleted (README: rotation).
 */
function requireToolTokens(): void {
  const seen = new Map<string, string>();
  for (const seat of Object.values(CREW)) {
    const value = process.env[seat.tokenEnv] ?? "";
    if (value !== value.trim() || /\s/.test(value)) {
      throw new Error(`${seat.tokenEnv} contains whitespace (a CRLF .env?). Every tool call would be refused.`);
    }
    if (value.length < 32) {
      throw new Error(`${seat.tokenEnv} must be set to the backend's value, at least 32 characters (openssl rand -hex 32).`);
    }
    const other = seen.get(value);
    if (other) throw new Error(`${seat.tokenEnv} has the same value as ${other}; each agent needs its own token.`);
    seen.set(value, seat.tokenEnv);
  }
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

// Daily caps (period: day). Fast-path members delegated with wait:true run on the Chief of
// Staff's budget, and every card that reaches done/blocked wakes a CoS digest session, so the
// CoS carries the most. Measure real per-session cost on Saturday and adjust. Ceiling: $13/day.
const BUDGETS: Record<AgentKey, { cap: number; task: number }> = {
  cos: { cap: 5.0, task: 1.0 },
  intake: { cap: 2.0, task: 0.25 },
  tax: { cap: 1.25, task: 0.2 },
  qa: { cap: 1.25, task: 0.2 },
  acct: { cap: 1.0, task: 0.15 },
  mkt: { cap: 1.25, task: 0.25 },
  hr: { cap: 1.25, task: 0.25 },
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

const GATEWAY_MCP_BASE = mcpUrl();
/** One MCP endpoint per seat, so Naïve can never hand one agent's credential to another. */
const seatUrl = (key: AgentKey) => `${GATEWAY_MCP_BASE}/${CREW[key].actor}`;
requireToolTokens();

/** One identity per agent, so its vault (and so its token) reaches that agent's sessions only. */
const identityOf = (key: AgentKey) => `utax-${CREW[key].actor}`;

// Phone: Naïve numbers carry SMS only; `voice` is always false (docs, api/messaging.md, D21).
// Off by default. Set UTAX_PHONE_E164 to attach a number the owner already bought, so an
// inbound text wakes the Chief of Staff. The firm cannot text back (outbound SMS is 501).
// Anyone can text a number, so the CoS prompt treats every text as data, never as an order.
const PHONE_E164 = process.env.UTAX_PHONE_E164?.trim() || undefined;

function agent(key: AgentKey) {
  const seat = CREW[key];
  return {
    name: seat.name,
    description: seat.description,
    model: MODELS[key],
    window: "immediate" as const,
    budget: { cap_micro_usd: usd(BUDGETS[key].cap), max_task_micro_usd: usd(BUDGETS[key].task), period: "day" as const },
    system: system(key),
    identity: identityOf(key), // holds this agent's vault, so its own token is injected
    // X-UTax-Actor is optional and non-secret: the gateway accepts it only when it matches the
    // token's actor (contract v1.4 §6). UNVERIFIED that Naïve forwards non-secret headers.
    mcp_servers: [{ name: MCP_SERVER, url: seatUrl(key), headers: { "X-UTax-Actor": seat.actor } }],
    tools: toolsetFor(key),
    metadata: { utax_actor: seat.actor },
  };
}

const KEYS = Object.keys(CREW) as AgentKey[];

export default defineProject({
  name: "u-tax",
  // The engine adds an `apps` tool (ask) to template crews unless this is false. U-Tax agents build no apps.
  crew_edits_apps: false,
  title: "U-Tax",
  description:
    "An accounting firm run by seven agents and one human CEO. Free Delaware franchise-tax checkups for startups. Educational estimates, not tax advice.",

  identities: KEYS.map((key) => ({
    name: identityOf(key),
    description: `U-Tax ${CREW[key].name}. Holds only this agent's gateway token. No native inbox: mail arrives through the gateway.`,
    ...(key === "cos" && PHONE_E164 ? { phone: { agent: CREW.cos.name, e164: PHONE_E164 } } : {}),
  })),

  vaults: KEYS.map((key) => ({
    name: `${identityOf(key)}-secrets`,
    identity: identityOf(key),
    credentials: [
      {
        kind: "static_bearer" as const,
        key: `U-Tax tool token (${CREW[key].actor})`,
        mcp_server_url: seatUrl(key),
        value: { from_env: CREW[key].tokenEnv },
      },
    ],
  })),

  agents: [
    {
      ...agent("cos"),
      multiagent: {
        type: "coordinator",
        agents: MEMBERS.map((m) => ({ type: "agent" as const, name: CREW[m].name })),
        board: true,
      },
      handoffs: "team",
      // Sent once, as a started session, when `naive up` first creates the agent; written as a
      // CEO order so the CoS treats it as a real trigger. (Not `tasks[]`: seeding tasks makes the
      // engine create an extra org "CEO" orchestrator agent outside this policy.) If that first
      // run is lost (gateway down), the CEO types "open the firm" in /ceo.
      intake: {
        message:
          "CEO: open the firm. Route every open case as your prompt says (board first, no duplicates), then " +
          "one card for marketing: \"Post the free franchise-tax checkup to the event channels\".",
        budget_micro_usd: usd(0.5),
      },
      schedules: [
        {
          // Sunday 8:00 AM Pacific: the overnight brief (D24: also the first login brief and its
          // email). Pause it after the event: `naive deploy pause <dep>`.
          cron: "0 8 * * 0",
          timezone: "America/Los_Angeles",
          summary: "Morning brief for the CEO",
          input:
            "Morning brief. Call utax.firm_brief once. From its result only, write the CEO's brief " +
            "(under 120 words, readable aloud in 45 seconds): clients served, money saved versus spent, human " +
            "touches, anything BLOCKED and what it needs, and the one decision you need from the CEO today. " +
            "Quote numbers exactly as firm_brief returned them; never add or compute any.",
          budget_micro_usd: usd(0.5),
          identity: identityOf("cos"),
        },
      ],
    },
    ...MEMBERS.map((m) => ({ ...agent(m), handoffs: false as const })),
  ],
});
