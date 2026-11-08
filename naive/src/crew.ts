// The U-Tax crew: who each agent is and exactly which gateway tools it may call.
// Tool names and the Agent column come from contracts/api.md v1.4 §6. This file is the one place the
// agent → tool mapping lives; naive.config.ts and the prompts both follow it.

/** The MCP server name. Naïve registers its tools as `utax.<tool>`. */
export const MCP_SERVER = "utax";

/** Every tool the gateway serves at /mcp/<actor> (contract v1.4 §6). */
export const GATEWAY_TOOLS = [
  "list_open_cases",
  "get_case",
  "extract_facts",
  "ask_client",
  "mark_not_eligible",
  "law_search",
  "compute_tax",
  "qa_check",
  "send_memo",
  "book_call",
  "phone_check",
  "post_social",
  "draft_dm",
  "get_candidate",
  "hr_record",
  "flag_case",
  "firm_brief",
  "buy_tool",
  // v1.11 (design D31, the live CEO orders)
  "make_ad",
  "source_candidates",
  "hr_outreach",
  "follow_up",
  "list_candidates",
  // v1.15 (design D35): the xlsx workpaper, docx memo and books entry, built and verified by the gateway
  "prepare_documents",
  // v1.19 (design D38): the agent market. Marketing posts a job; the gateway checks the work and pays
  "hire_contractor",
  "get_job",
] as const;

export type GatewayTool = (typeof GATEWAY_TOOLS)[number];

/**
 * The CEO tool registry (contract §5b), served at /mcp/ceo to the ceo-assistant only (v1.14 §5a-ter,
 * design D34). Start tools run directly there, with no proposal. `get_case` shares its name with the
 * §6 tool, but on /mcp/ceo it is the §5b one.
 */
export const CEO_TOOLS = [
  "get_brief",
  "list_approvals",
  "get_case",
  "get_money",
  "get_trust",
  "get_hiring",
  "recent_activity",
  "pause",
  "approve",
  "deny",
  "set_trust",
  "unblock_case",
  "decide_candidate",
  "order",
] as const;

export type CeoTool = (typeof CEO_TOOLS)[number];

export type AgentKey = "cos" | "intake" | "tax" | "qa" | "acct" | "mkt" | "hr" | "ceo";

export interface Seat {
  /** Naïve agent name (the key `naive up` upserts by). */
  name: string;
  /** Actor code on gateway events (contract v1 §2). The gateway derives it from the agent's token. */
  actor: AgentKey;
  /** Env var holding this agent's own gateway tool token (contract v1.3 §1), sealed in its own vault. */
  tokenEnv: string;
  /** Prompt file under prompts/. */
  prompt: string;
  description: string;
  /** Least privilege: the gateway tools this agent is offered. Nothing else from the gateway. */
  tools: readonly (GatewayTool | CeoTool)[];
}

export const CREW: Record<AgentKey, Seat> = {
  cos: {
    name: "chief-of-staff",
    actor: "cos",
    tokenEnv: "UTAX_TOOL_TOKEN_COS",
    prompt: "chief-of-staff.md",
    description: "Runs the firm for the human CEO: takes orders, assigns work on the board, writes the morning brief.",
    tools: ["list_open_cases", "get_case", "firm_brief"],
  },
  intake: {
    name: "intake",
    actor: "intake",
    tokenEnv: "UTAX_TOOL_TOKEN_INTAKE",
    prompt: "intake.md",
    description: "Reads each client email, quotes the five facts verbatim, re-asks for what is missing, books the review call, sends templated follow-ups.",
    tools: ["get_case", "extract_facts", "ask_client", "mark_not_eligible", "book_call", "follow_up"],
  },
  tax: {
    name: "tax",
    actor: "tax",
    tokenEnv: "UTAX_TOOL_TOKEN_TAX",
    prompt: "tax.md",
    description: "Looks up Delaware franchise-tax law and runs the tested engine on the extracted facts.",
    tools: ["get_case", "law_search", "compute_tax"],
  },
  qa: {
    name: "qa",
    actor: "qa",
    tokenEnv: "UTAX_TOOL_TOKEN_QA",
    prompt: "qa.md",
    description: "Re-runs the engine, re-matches every quote against the email, and checks the memo renders on a phone.",
    tools: ["get_case", "qa_check", "phone_check", "flag_case"],
  },
  acct: {
    name: "accounting",
    actor: "acct",
    tokenEnv: "UTAX_TOOL_TOKEN_ACCT",
    prompt: "accounting.md",
    description: "Prepares the case's documents (workpaper, memo, books entry; built by the gateway from the engine) and sends the QA-passed memo with them. Only after QA.",
    tools: ["get_case", "prepare_documents", "send_memo"],
  },
  mkt: {
    name: "marketing",
    actor: "mkt",
    tokenEnv: "UTAX_TOOL_TOKEN_MKT",
    prompt: "marketing.md",
    description: "Finds clients: branded ads and public posts on the firm's channels, LinkedIn DM drafts the human sends, allowlisted data calls on public startup lists, and share cards from outside contractor agents (the gateway pays them).",
    tools: ["post_social", "draft_dm", "buy_tool", "make_ad", "hire_contractor", "get_job"],
  },
  hr: {
    name: "hr",
    actor: "hr",
    tokenEnv: "UTAX_TOOL_TOKEN_HR",
    prompt: "hr.md",
    description: "Runs the paid gig hire: finds public leads, writes to candidates who gave the firm their address, screens and shortlists. The CEO hires or declines.",
    tools: ["get_candidate", "hr_record", "source_candidates", "hr_outreach", "list_candidates"],
  },
  // v1.14 (design D34): the CEO's own assistant, not on the team or the board. It acts only through
  // the CEO tools on /mcp/ceo, which the gateway runs directly (no proposal) and can mostly undo.
  ceo: {
    name: "ceo-assistant",
    actor: "ceo",
    tokenEnv: "UTAX_TOOL_TOKEN_CEO",
    prompt: "ceo-assistant.md",
    description: "The CEO's assistant: talks with the CEO, answers from the firm's records and acts through the CEO tools.",
    tools: CEO_TOOLS,
  },
};

export const MEMBERS: readonly AgentKey[] = ["intake", "tax", "qa", "acct", "mkt", "hr"];
