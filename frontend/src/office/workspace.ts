// D32 department workspaces: a pure fold from the event log to what each department's "browser"
// shows. Only events the theater script accepted reach here (validated, contract §2-3, v1.12), and
// every line is built from a fixed template plus masked or numeric fields: no email body, subject,
// quote, candidate name or address is ever shown. Nothing here invents a step.

import type { UTaxEvent } from "@/theater/events";
import { formatCents, formatUsd, normalizeDecimal } from "@/theater/format";

export const SEATS = ["cos", "intake", "tax", "qa", "acct", "mkt", "hr"] as const;
export type Seat = (typeof SEATS)[number];
export const isSeat = (v: unknown): v is Seat => typeof v === "string" && (SEATS as readonly string[]).includes(v);

export const SEAT_NAME: Readonly<Record<Seat, string>> = {
  cos: "Chief of Staff",
  intake: "Intake",
  tax: "Tax",
  qa: "QA",
  acct: "Accounting",
  mkt: "Marketing",
  hr: "HR",
};

/** What each department does, for the sidebar and the empty workspace. */
export const SEAT_ROLE: Readonly<Record<Seat, string>> = {
  cos: "Routes every case and CEO order to the right desk",
  intake: "Reads client email and pulls the facts",
  tax: "Researches the law and computes the tax",
  qa: "Re-checks every number before it leaves",
  acct: "Writes and sends the memo",
  mkt: "Makes ads and posts for the firm",
  hr: "Finds and books people for gigs",
};

export type StepStatus = "done" | "needs_approval" | "refused" | "error" | "other";

export interface WsField {
  label: string;
  value: string;
}

/** One line in a department's step list, and what its window showed for it. */
export interface WsStep {
  key: string;
  eventId: number;
  ts: string;
  seat: Seat;
  /** "tool": a tool.called (the click); "event": what that work produced. */
  kind: "tool" | "event";
  text: string;
  status: StepStatus;
  /** The window's address bar: the tool or site. */
  site: string;
  /** The window body for this step; `focus` is the field the cursor goes to. */
  fields: WsField[];
  focus: number;
  image?: string;
  /** The case this step is about, when it has one. */
  caseId?: string;
  /** An email step: its sender fields are hidden if the case is flagged later. */
  mail?: true;
  /** D41 F48: the CEO order and task this step worked for (contract v1.20 stamps order_id and task_no). */
  order?: string;
  task?: number;
}

export interface Workspace {
  seat: Seat;
  /** Newest first. */
  steps: WsStep[];
  /** Sites this desk used, newest last (the window's tabs). */
  tabs: string[];
  /** Counts for the KPI tiles (since the feed started). */
  counts: Record<string, number>;
}

export interface Office {
  desks: Record<Seat, Workspace>;
  /** The desk that acted last (auto-follow), and the key of that step. */
  lastSeat: Seat | null;
  lastKey: string;
}

const MAX_STEPS = 40;
const MAX_TABS = 4;

export const emptyOffice = (): Office => ({
  desks: Object.fromEntries(SEATS.map((s): [Seat, Workspace] => [s, { seat: s, steps: [], tabs: [], counts: {} }])) as unknown as Record<Seat, Workspace>,
  lastSeat: null,
  lastKey: "",
});

// ------------------------------------------------------------------ tools → sites

/** The address bar for a tool: the site or app an agent would have open. */
const TOOL_SITE: Readonly<Record<string, string>> = {
  get_case: "utax · case file",
  list_open_cases: "utax · open cases",
  firm_brief: "utax · CEO brief",
  send_to_agent: "Naïve · team board",
  board_write: "Naïve · team board",
  board_read: "Naïve · team board",
  extract_facts: "utax · intake sheet",
  ask_client: "AgentMail · compose",
  mark_not_eligible: "utax · case file",
  book_call: "Calendar · invite",
  follow_up: "AgentMail · thread",
  law_search: "Naïve Data · law search",
  compute_tax: "utax · compute_tax",
  qa_check: "utax · QA check",
  flag_case: "utax · QA check",
  phone_check: "Mobilerun · cloud phone",
  send_memo: "AgentMail · compose",
  post_social: "Social · compose",
  draft_dm: "Social · DMs",
  make_ad: "utax · ad studio",
  buy_tool: "Monid · checkout",
  source_candidates: "Monid · public search",
  list_candidates: "utax · hiring board",
  get_candidate: "utax · hiring board",
  hr_record: "utax · hiring board",
  hr_outreach: "AgentMail · hiring",
};

/**
 * What the tool call was, as the click in the browser ("Pressed Post"). The result, when the
 * tool produced one, follows as its own line from the domain event ("Posted to X").
 */
const TOOL_VERB: Readonly<Record<string, string>> = {
  get_case: "Opened the case file",
  list_open_cases: "Opened the list of open cases",
  firm_brief: "Pulled the firm's numbers",
  send_to_agent: "Handed the work to a department",
  board_write: "Wrote on the team board",
  board_read: "Read the team board",
  extract_facts: "Filled in the intake sheet",
  ask_client: "Pressed Send on a question to the client",
  mark_not_eligible: "Marked the case not eligible",
  book_call: "Sent a calendar invite",
  follow_up: "Pressed Send on a follow-up",
  law_search: "Queried the law catalogue",
  compute_tax: "Ran the tax engine",
  qa_check: "Re-ran the engine to check",
  flag_case: "Raised a flag for the CEO",
  phone_check: "Opened the memo on a cloud phone",
  send_memo: "Pressed Send on the memo",
  post_social: "Pressed Post",
  draft_dm: "Wrote a DM draft",
  make_ad: "Rendered the ad in the studio",
  buy_tool: "Paid for a data tool",
  source_candidates: "Ran a public-profile search",
  list_candidates: "Opened the candidate list",
  get_candidate: "Opened a candidate's answers",
  hr_record: "Moved a candidate on the board",
  hr_outreach: "Pressed Send on an invite",
};

/** What a tool waiting on the CEO would do, for "Asked the CEO's OK to …". */
const TOOL_ASK: Readonly<Record<string, string>> = {
  send_memo: "send the memo",
  post_social: "post for the firm",
  hr_outreach: "email a candidate",
  ask_client: "write to the client",
  book_call: "send a calendar invite",
  follow_up: "send a follow-up",
  buy_tool: "buy a data tool",
  draft_dm: "send a DM",
};
const toolAsk = (tool: string) => (Object.hasOwn(TOOL_ASK, tool) ? TOOL_ASK[tool] : `use ${tool}`);
const toolSite = (tool: string) => (Object.hasOwn(TOOL_SITE, tool) ? TOOL_SITE[tool] : `utax · ${tool}`);
const toolVerb = (tool: string) => (Object.hasOwn(TOOL_VERB, tool) ? TOOL_VERB[tool] : `Used ${tool}`);
/** Only safe characters from an agent-supplied tool name (as the theater does). */
const safeTool = (t: unknown) => (typeof t === "string" ? Array.from(t.replace(/[^\w.:-]/g, "")).slice(0, 40).join("") : "") || "a tool";

// ------------------------------------------------------------------ payload reads (already validated by the script)

type P = Record<string, unknown>;
const s = (p: P, k: string): string => (typeof p[k] === "string" ? (p[k] as string) : "");
const n = (p: P, k: string): number | null => (typeof p[k] === "number" && Number.isFinite(p[k]) ? (p[k] as number) : null);
const clip = (t: string, max: number) => (Array.from(t).length > max ? `${Array.from(t).slice(0, max - 1).join("")}…` : t);
const plural = (k: number, one: string, many = `${one}s`) => `${k} ${k === 1 ? one : many}`;
const CHANNEL: Readonly<Record<string, string>> = { x: "X", twitter: "X", linkedin: "LinkedIn", instagram: "Instagram", facebook: "Facebook" };
const channelName = (c: string) => (Object.hasOwn(CHANNEL, c.toLowerCase()) ? CHANNEL[c.toLowerCase()] : clip(c, 20) || "social");
const candidate = (v: string) => (/^Candidate [A-Z]{1,2}$/.test(v) ? v : "a candidate");
const FACT_LABEL: Readonly<Record<string, string>> = {
  incorporated_delaware: "Delaware corporation",
  authorized_shares: "Authorized shares",
  issued_shares: "Issued shares",
  stated_par: "Par value",
  gross_assets_cents: "Gross assets",
  foreign_owner_25: "Foreign owner ≥ 25%",
};
const factValue = (k: string, v: unknown): string | null => {
  if (typeof v === "boolean") return v ? "Yes" : "No";
  if (typeof v === "number" && Number.isFinite(v)) return k.endsWith("_cents") ? formatCents(v) : v.toLocaleString("en-US");
  if (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v)) return `$${v}`;
  return null;
};

/** An email step of a flagged case: nothing from the mail is shown. */
const HIDDEN_MAIL = { text: "Opened a new email (flagged by QA: hidden)", fields: [{ label: "From", value: "Hidden: flagged by QA" }] as WsField[] };

/** A possible-injection flag hides the case's mail on every desk, steps already shown included. */
function hideMail(prev: Office, caseId: string): Office {
  let changed = false;
  const desks = { ...prev.desks };
  for (const seat of SEATS) {
    const w = desks[seat];
    if (!w.steps.some((x) => x.mail && x.caseId === caseId)) continue;
    changed = true;
    desks[seat] = { ...w, steps: w.steps.map((x) => (x.mail && x.caseId === caseId ? { ...x, text: HIDDEN_MAIL.text, fields: HIDDEN_MAIL.fields, focus: 0 } : x)) };
  }
  return changed ? { ...prev, desks } : prev;
}

interface Draft {
  seat: Seat;
  kind: "tool" | "event";
  text: string;
  status?: StepStatus;
  site: string;
  fields?: WsField[];
  focus?: number;
  image?: string;
  count?: string;
  mail?: true;
}

/**
 * One event → at most one workspace step. `name` maps a case id to its masked name (the theater
 * learns it from email.received); `flagged` cases never show their mail fields; `image` passes
 * an image URL only when it already passed the script's URL check (the caller hands those in).
 */
/** v1.20: the order and task an event belongs to, when the gateway stamped them. */
function orderOf(ev: UTaxEvent): { order?: string; task?: number } {
  const p = (ev.payload ?? {}) as P;
  const order = typeof p.order_id === "string" && /^order_\d{1,12}$/.test(p.order_id) ? p.order_id : undefined;
  const task = typeof p.task_no === "number" && Number.isSafeInteger(p.task_no) && p.task_no > 0 ? p.task_no : undefined;
  return { ...(order ? { order } : {}), ...(order && task ? { task } : {}) };
}

export function stepOf(ev: UTaxEvent, name: (caseId: string | null) => string, flagged: (caseId: string | null) => boolean, image: (url: unknown) => string | undefined): Draft | null {
  const p = (ev.payload ?? {}) as P;
  const actor = ev.actor;
  const seat: Seat | null = isSeat(actor) ? actor : null;
  const who = name(ev.case_id);
  switch (ev.type) {
    case "tool.called": {
      if (!seat) return null;
      const tool = safeTool(p.tool);
      const raw = s(p, "status");
      const status: StepStatus = raw === "done" || raw === "needs_approval" || raw === "refused" || raw === "error" ? raw : "other";
      const verb = toolVerb(tool);
      const text =
        status === "needs_approval" ? `Asked the CEO's OK to ${toolAsk(tool)}` : status === "refused" ? `${verb}: refused by the firm's rules` : status === "error" ? `${verb}: failed` : verb;
      return { seat, kind: "tool", text, status, site: toolSite(tool), fields: [{ label: "Tool", value: tool }, { label: "Result", value: STATUS_WORD[status] }], focus: 1, count: "tools" };
    }
    case "naive.confirm": {
      const agent = p.agent;
      if (!isSeat(agent)) return null;
      const tool = safeTool(p.tool);
      const ok = p.decision === "allow";
      return { seat: agent, kind: "tool", text: `Naïve check: ${tool} ${ok ? "allowed" : "denied"}`, status: ok ? "done" : "refused", site: "Naïve · tool check", fields: [{ label: "Tool", value: tool }, { label: "Decision", value: ok ? "Allowed by the firm's rules" : "Denied" }], focus: 1 };
    }
    case "email.received": {
      if (seat !== "intake") return null;
      const hidden = flagged(ev.case_id);
      if (hidden) return { ...HIDDEN_MAIL, seat, kind: "event", site: "AgentMail · inbox", focus: 0, count: "emails", mail: true };
      return { seat, kind: "event", text: `Opened a new email from ${s(p, "masked_name") || who}`, site: "AgentMail · inbox", fields: [{ label: "From", value: clip(s(p, "from_masked"), 60) }, { label: "Client", value: clip(s(p, "masked_name"), 40) }], focus: 0, count: "emails", mail: true };
    }
    case "facts.extracted": {
      if (!seat) return null;
      const facts = (typeof p.facts === "object" && p.facts !== null ? p.facts : {}) as P;
      const fields = Object.keys(FACT_LABEL)
        .map((k) => ({ label: FACT_LABEL[k], value: factValue(k, facts[k]) }))
        .filter((f): f is WsField => f.value !== null);
      return { seat, kind: "event", text: `Pulled ${plural(fields.length, "fact")} for ${who}`, site: "utax · intake sheet", fields, focus: Math.max(0, fields.length - 1), count: "facts" };
    }
    case "facts.missing": {
      if (!seat) return null;
      const missing = Array.isArray(p.missing) ? p.missing.length : 0;
      return { seat, kind: "event", text: `Found ${plural(missing, "fact")} missing for ${who}`, site: "utax · intake sheet", fields: [{ label: "Missing", value: String(missing) }], focus: 0 };
    }
    case "client.asked": {
      if (!seat) return null;
      const missing = Array.isArray(p.missing) ? p.missing.length : 0;
      return { seat, kind: "event", text: `Asked ${who} for ${plural(missing, "missing fact")}`, site: "AgentMail · compose", fields: [{ label: "To", value: who }, { label: "Asking for", value: plural(missing, "fact") }], focus: 1, count: "emails" };
    }
    case "law.searched": {
      if (!seat) return null;
      const results = Array.isArray(p.results) ? p.results.length : 0;
      return { seat, kind: "event", text: `Searched the law: “${clip(s(p, "query"), 60)}”`, site: "Naïve Data · law search", fields: [{ label: "Query", value: clip(s(p, "query"), 80) }, { label: "Results", value: String(results) }], focus: 0, count: "searches" };
    }
    case "tax.computed": {
      if (!seat) return null;
      const auth = n(p, "authorized_method_cents");
      const par = n(p, "assumed_par_method_cents");
      const total = n(p, "total_cents");
      if (auth === null || par === null || total === null) return null;
      const method = s(p, "chosen") === "authorized" ? "authorized shares" : "assumed par value";
      return {
        seat,
        kind: "event",
        text: `Computed ${formatCents(total)} for ${who} (${method} method)`,
        site: "utax · compute_tax",
        fields: [
          { label: "Authorized shares method", value: formatCents(auth) },
          { label: "Assumed par value method", value: formatCents(par) },
          { label: "Total due", value: formatCents(total) },
        ],
        focus: 2,
        count: "estimates",
      };
    }
    case "qa.passed":
    case "qa.failed": {
      if (!seat) return null;
      const checks = Array.isArray(p.checks) ? p.checks : [];
      const bad = checks.filter((c) => typeof c === "object" && c !== null && (c as P).ok === false).length;
      const passed = ev.type === "qa.passed";
      return { seat, kind: "event", text: passed ? `Checked ${plural(checks.length, "figure")} for ${who}: all match` : `Stopped ${who}: ${bad} of ${checks.length} figures don't match`, status: passed ? "done" : "error", site: "utax · QA check", fields: [{ label: "Checked", value: String(checks.length) }, { label: "Mismatches", value: String(bad) }], focus: 1, count: passed ? "passed" : "failed" };
    }
    case "case.flagged": {
      if (!seat) return null;
      return { seat, kind: "event", text: `Flagged ${who}: ${s(p, "kind").replace(/_/g, " ")}`, status: "needs_approval", site: "utax · QA check", fields: [{ label: "Flag", value: s(p, "kind").replace(/_/g, " ") }], focus: 0, count: "flags" };
    }
    case "phone.checked": {
      if (!seat) return null;
      const ok = p.rendered_ok === true;
      return { seat, kind: "event", text: ok ? "Checked the memo on a cloud phone: it renders" : "The memo failed to render on a cloud phone", status: ok ? "done" : "error", site: "Mobilerun · cloud phone", fields: [{ label: "Rendered", value: ok ? "Yes" : "No" }], focus: 0 };
    }
    case "memo.sent": {
      if (!seat) return null;
      return { seat, kind: "event", text: `Sent the memo to ${who}`, site: "AgentMail · compose", fields: [{ label: "To", value: clip(s(p, "to_masked"), 60) }, { label: "Memo", value: clip(s(p, "memo_ref"), 24) || "sent" }], focus: 1, count: "memos" };
    }
    case "call.booked": {
      if (!seat) return null;
      const minutes = n(p, "minutes") ?? 0;
      const with_ = p.kind === "candidate" ? candidate(s(p, "invitee_masked")) : who;
      return { seat, kind: "event", text: `Booked a ${minutes}-min call with ${with_}`, site: "Calendar · invite", fields: [{ label: "With", value: with_ }, { label: "Length", value: `${minutes} min` }], focus: 0, count: "calls" };
    }
    case "client.followed_up": {
      if (!seat) return null;
      const kind = s(p, "kind").replace(/_/g, " ") || "follow-up";
      return { seat, kind: "event", text: `Followed up with ${who} (${kind})`, site: "AgentMail · thread", fields: [{ label: "To", value: who }, { label: "About", value: kind }], focus: 1, count: "follow-ups" };
    }
    case "post.published": {
      if (!seat) return null;
      const ch = channelName(s(p, "channel"));
      return { seat, kind: "event", text: `Posted to ${ch}`, site: `${ch} · compose`, fields: [{ label: "Post", value: clip(s(p, "text"), 160) }], focus: 0, image: image(p.image_url), count: "posts" };
    }
    case "ad.created": {
      if (!seat) return null;
      return { seat, kind: "event", text: p.kind === "hiring" ? "Made a hiring ad image" : "Made an ad image", site: "utax · ad studio", fields: [{ label: "Headline", value: clip(s(p, "headline"), 80) }], focus: 0, image: image(p.image_url), count: "ads" };
    }
    case "dm.drafted": {
      if (!seat) return null;
      return { seat, kind: "event", text: "Drafted a DM for the CEO to send", site: "Social · DMs", fields: [{ label: "To", value: clip(s(p, "recipient_label"), 40) }], focus: 0 };
    }
    case "tool.bought": {
      if (!seat || ev.cost_usd === null || ev.cost_usd === undefined) return null;
      let cost = "";
      try {
        cost = formatUsd(normalizeDecimal(ev.cost_usd));
      } catch {
        return null;
      }
      return { seat, kind: "event", text: `Bought ${safeTool(p.tool)} · ${cost}`, site: "Monid · checkout", fields: [{ label: "Tool", value: safeTool(p.tool) }, { label: "Cost", value: cost }], focus: 1 };
    }
    case "hr.sourced": {
      if (!seat) return null;
      const c = n(p, "count") ?? 0;
      return { seat, kind: "event", text: c ? `Found ${plural(c, "public profile")}` : "Searched public profiles: none found", site: "Monid · public search", fields: [{ label: "Found", value: String(c) }, { label: "Names", value: "kept off screen" }], focus: 0, count: "leads" };
    }
    case "hr.outreach": {
      if (!seat) return null;
      const c = candidate(s(p, "label"));
      return { seat, kind: "event", text: `Emailed ${c} with a call invite`, site: "AgentMail · hiring", fields: [{ label: "To", value: c }, { label: "Invite", value: "call slot attached" }], focus: 1, count: "contacted" };
    }
    case "hr.applicant":
    case "hr.screened":
    case "hr.shortlisted":
    case "hr.hired":
    case "hr.declined":
    case "hr.questionnaire_sent":
    case "hr.decision_mailed": {
      if (!seat) return null;
      const stage = ev.type.slice(3).replace(/_/g, " ");
      return { seat, kind: "event", text: `A candidate moved to: ${stage}`, site: "utax · hiring board", fields: [{ label: "Stage", value: stage }], focus: 0, count: "hiring" };
    }
    case "screen.call": {
      if (!seat) return null;
      const secs = n(p, "seconds") ?? 0;
      return { seat, kind: "event", text: `Screened a candidate by phone (${Math.max(1, Math.round(secs / 60))} min)`, site: "Phone · screening call", fields: [{ label: "Length", value: `${Math.round(secs / 60)} min` }], focus: 0 };
    }
    case "brief.generated": {
      if (seat !== "cos") return null;
      return { seat, kind: "event", text: "Wrote the CEO's brief", site: "utax · CEO brief", fields: [{ label: "Brief", value: "ready for the CEO" }], focus: 0, count: "briefs" };
    }
    case "ceo.notified": {
      if (seat !== "cos") return null;
      return { seat, kind: "event", text: "Told the CEO", site: "AgentMail · CEO update", fields: [{ label: "Channel", value: s(p, "channel") }], focus: 0 };
    }
  }
  return null;
}

const STATUS_WORD: Readonly<Record<StepStatus, string>> = {
  done: "Done",
  needs_approval: "Waiting for the CEO's OK",
  refused: "Refused by the firm's rules",
  error: "Failed",
  other: "Reported",
};

/** Fold one accepted event in. Returns a new Office; never mutates. */
/** What the fold needs from outside: case names, flagged cases, and the image check. */
export interface StepCtx {
  name: (caseId: string | null) => string;
  flagged: (caseId: string | null) => boolean;
  image: (url: unknown) => string | undefined;
}

export function applyEvent(prev: Office, ev: UTaxEvent, ctx: StepCtx): Office {
  if (ev.type === "case.flagged" && ev.case_id && (ev.payload as { kind?: unknown } | null)?.kind === "possible_injection") prev = hideMail(prev, ev.case_id);
  const d = stepOf(ev, ctx.name, ctx.flagged, ctx.image);
  if (!d) return prev;
  const key = `${ev.id}`;
  const desk = prev.desks[d.seat];
  if (desk.steps.some((x) => x.key === key)) return prev; // replay
  const step: WsStep = { key, eventId: ev.id, ts: ev.ts, seat: d.seat, kind: d.kind, text: d.text, status: d.status ?? "done", site: d.site, fields: d.fields ?? [], focus: d.focus ?? 0, ...(d.image ? { image: d.image } : {}), ...(ev.case_id ? { caseId: ev.case_id } : {}), ...(d.mail ? { mail: true as const } : {}), ...orderOf(ev) };
  const tabs = [...desk.tabs.filter((t) => t !== d.site), d.site].slice(-MAX_TABS);
  const counts = d.count ? { ...desk.counts, [d.count]: (desk.counts[d.count] ?? 0) + 1 } : desk.counts;
  const next: Workspace = { ...desk, steps: [step, ...desk.steps].slice(0, MAX_STEPS), tabs, counts };
  return { desks: { ...prev.desks, [d.seat]: next }, lastSeat: d.seat, lastKey: key };
}
