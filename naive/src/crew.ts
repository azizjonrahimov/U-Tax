// The U-Tax crew: who each agent is and exactly which gateway tools it may call.
// Tool names come from contracts/api.md v1 §6. This file is the one place the
// agent → tool mapping lives; naive.config.ts and the prompts both follow it.

/** The MCP server name. Naïve registers its tools as `utax.<tool>`. */
export const MCP_SERVER = "utax";

/** Every tool the gateway serves at /mcp (contract v1 §6). */
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
  "hr_record",
  "book_cost",
  "firm_brief",
] as const;

export type GatewayTool = (typeof GATEWAY_TOOLS)[number];

export type AgentKey = "cos" | "intake" | "tax" | "qa" | "acct" | "mkt" | "hr";

export interface Seat {
  /** Naïve agent name (the key `naive up` upserts by). */
  name: string;
  /** Actor code on gateway events (contract v1 §2). */
  actor: AgentKey;
  /** Prompt file under prompts/. */
  prompt: string;
  description: string;
  /** Least privilege: the gateway tools this agent is offered. Nothing else from the gateway. */
  tools: readonly GatewayTool[];
}

export const CREW: Record<AgentKey, Seat> = {
  cos: {
    name: "chief-of-staff",
    actor: "cos",
    prompt: "chief-of-staff.md",
    description: "Runs the firm for the human CEO: takes orders, assigns work on the board, writes the morning brief.",
    tools: ["list_open_cases", "get_case", "firm_brief"],
  },
  intake: {
    name: "intake",
    actor: "intake",
    prompt: "intake.md",
    description: "Reads each client email, quotes the five facts verbatim, re-asks for what is missing, books the review call.",
    tools: ["get_case", "extract_facts", "ask_client", "mark_not_eligible", "book_call"],
  },
  tax: {
    name: "tax",
    actor: "tax",
    prompt: "tax.md",
    description: "Looks up Delaware franchise-tax law and runs the tested engine on the extracted facts.",
    tools: ["get_case", "law_search", "compute_tax"],
  },
  qa: {
    name: "qa",
    actor: "qa",
    prompt: "qa.md",
    description: "Re-runs the engine, re-matches every quote against the email, and checks the memo renders on a phone.",
    tools: ["get_case", "qa_check", "phone_check"],
  },
  acct: {
    name: "accounting",
    actor: "acct",
    prompt: "accounting.md",
    description: "Sends the QA-passed memo to the client. Only through the gateway template, only after QA.",
    tools: ["get_case", "send_memo"],
  },
  mkt: {
    name: "marketing",
    actor: "mkt",
    prompt: "marketing.md",
    description: "Finds clients: public posts on the firm's channels and LinkedIn DM drafts the human sends.",
    tools: ["post_social", "draft_dm"],
  },
  hr: {
    name: "hr",
    actor: "hr",
    prompt: "hr.md",
    description: "Runs the one paid gig hire: records applicants, a disclosed AI screen, a shortlist. The human hires.",
    tools: ["hr_record"],
  },
};

export const MEMBERS: readonly AgentKey[] = ["intake", "tax", "qa", "acct", "mkt", "hr"];
