// Tool policy for every U-Tax agent (design decision E5, U-TAX-CLAUDE.md rule 12).
//
// - Deny by default: an agent sees nothing it is not handed here.
// - The gateway tools are `allow`. Trust (ASK → AUTO), caps, the QA gate and idempotency
//   live in the gateway's perform(), which answers `needs_approval` when a human must say
//   yes. A Naïve `ask` gate on top would stall the firm on approvals the gateway already
//   enforces, and a pending approval also skips scheduled fires.
// - Naïve's docs say ask-by-default tools still resolve to `ask` under a deny default unless
//   named (https://usenaive.ai/docs/capabilities/tools.md#permission-policies); the local engine's
//   permissionOf resolves them to deny. The server decides, so each one is denied by name.
// - Gateway tools outside an agent's seat are denied by name too (MCP tools default to `ask`).
// - Native email, social and phone tools are denied by name too, so nothing can reach a
//   client without going through perform().

import { CEO_TOOLS, CREW, GATEWAY_TOOLS, MCP_SERVER, type AgentKey } from "./crew.js";

type Permission = "allow" | "deny" | "ask";
interface ToolConfig {
  enabled: boolean;
  permission: Permission;
}
export interface Toolset {
  default_config: { permission: Permission };
  configs: Record<string, ToolConfig>;
}

const ALLOW: ToolConfig = { enabled: true, permission: "allow" };
const DENY: ToolConfig = { enabled: false, permission: "deny" };

/** Ask-by-default built-ins, verbatim from the tools doc. Each must be denied by name. */
export const ASK_BY_DEFAULT = [
  "social.post",
  "email.send",
  "legal.verify",
  "legal.resend_link",
  "legal.form",
  "legal.submit",
  "wallet.pay",
  "wallet.transfer",
  "card.issue",
  "card.credentials",
  "card.cancel",
  "trading.place_order",
  "trading.cancel_order",
  "trading.close_position",
  "company.set_timezone",
  "company.set_logo",
  "company.set_brand",
  "apps.request_access",
  "mobile",
  "data_run",
  "generate_video",
  "edit_video",
  "create_character",
  "generate_image",
  "generate_3d_model",
  "build_media_skill",
  // In the blueprint engine's ASK_BY_DEFAULT_TOOLS / ALWAYS_ASKS (dist/index.js) but not in the doc's list.
  "list_as_template",
  "pay_out_earnings",
  "payments_request",
  "vault.request_secret",
  "operator.request",
] as const;

/**
 * Escalation tools: never `allow`, and an injected agent could use them to ask a person for
 * new tools (request_tools → email.send) or stall the firm on a question. The CEO talks to the
 * firm through the gateway, so every agent is denied all three.
 */
export const ESCALATION = ["ask_operator", "request_tools", "propose_plan"] as const;

/** Native identity tools that would bypass the gateway (E5). Denied by name, never offered. */
export const NATIVE_BYPASS = [
  "email.inboxes",
  "email.read",
  "phone.numbers",
  "phone.read",
  "phone.send",
  "social.accounts",
  "social.status",
  "social.post_metrics",
  "social.cancel",
  "connections.search",
  "connections.connect",
  "connections.status",
  "crm.read",
  "crm.write",
] as const;

/** The team board. Members are seated on the coordinator's board. */
export const BOARD = ["board_read", "board_write"] as const;
/** Delegation, coordinator only. */
export const COORDINATOR = ["send_to_agent", "wait_for_agents", "list_agents"] as const;

export function toolsetFor(agent: AgentKey): Toolset {
  const configs: Record<string, ToolConfig> = {};
  for (const name of [...ASK_BY_DEFAULT, ...NATIVE_BYPASS, ...ESCALATION]) configs[name] = DENY;
  // Members never delegate onward; the coordinator holds the roster.
  for (const name of COORDINATOR) configs[name] = agent === "cos" ? ALLOW : DENY;
  // The CEO assistant is not on the board (v1.14): it acts through the CEO tools only.
  for (const name of BOARD) configs[name] = agent === "ceo" ? DENY : ALLOW;
  for (const tool of [...GATEWAY_TOOLS, ...CEO_TOOLS]) configs[`${MCP_SERVER}.${tool}`] = DENY;
  for (const tool of CREW[agent].tools) configs[`${MCP_SERVER}.${tool}`] = ALLOW;
  return { default_config: { permission: "deny" }, configs };
}
