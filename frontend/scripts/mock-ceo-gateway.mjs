// A stand-in gateway for /ceo voice mode QA (contract v1.8 §5b) until backend task 18 lands.
// Dev only: never deployed. Every request is logged to stdout as one JSON line, so a QA run
// can prove what the console sent (e.g. that no /api/ceo/confirm followed a model "confirmed").
//
//   PORT=8020 ADMIN_TOKEN=admin-test MOCK_VOICE=0 node scripts/mock-ceo-gateway.mjs
// D34 (v1.14 §5a-ter): POST /api/ceo/talk {text, conversation_id?} acts directly (no card) and
// answers {conversation_id, say, actions:[{tool, summary, undo_id?}]}; text containing "slow" answers
// {pending: true} first (GET /api/ceo/talk/{id} has it ~3 s later); stops run at once with an undo.
// POST /api/ceo/undo {undo_id} (once). MOCK_TALK=503 answers assistant_unavailable (the console falls back).
// Task 36/39: POST /api/stream-ticket (Bearer) → {ticket, expires_at}, 60 s single use, for
// GET /api/events/stream?ticket=; a session is never accepted from a URL.
//
// MOCK_VOICE=1 mints a fake Realtime session (the browser's WebRTC must be stubbed by the test);
// otherwise /api/voice/session answers 503 voice_unavailable and the console falls back.
// THEATER_TOKEN (default theater-test) may read state, events and the stream, as the projector does.
// Dev-only extras: POST /api/dev/approval {summary, case_id}, POST /api/dev/block {case_id, reason},
// POST /api/dev/naive-confirm {agent, tool, decision}.
// D31 (v1.11.1): a confirmed CEO order (or POST /api/dev/order {text}) emits the CEO's voice.command, then
// plays each named department's steps with order_id ("advertise" → Marketing, "hire" → HR, "follow up" →
// Intake), MOCK_PACE ms apart (default 1600). The ad image is served at GET /assets/ad_<n>.svg.
// D32 (v1.12): every department action is preceded by tool.called {tool, status}; POST /api/signup
// {name, company, email, request_id?} (public) opens a case like an inbound email and plays the case
// flow (Intake, Tax, QA, Accounting) with tool calls.
// D33 (v1.13 §5a-bis): POST /api/auth/login {email, password} (CEO_EMAIL default ceo@u-tax.test,
// CEO_PASSWORD default utax-demo) answers {session, expires_at}; the session works like the admin
// token, as a Bearer only. POST /api/auth/logout revokes it (always 200).

import { createServer } from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.PORT || 8020);
const ADMIN = process.env.ADMIN_TOKEN || "admin-test";
const THEATER = process.env.THEATER_TOKEN || "theater-test";
const CEO_EMAIL = (process.env.CEO_EMAIL || "ceo@u-tax.test").toLowerCase();
const CEO_PASSWORD = process.env.CEO_PASSWORD || "utax-demo";
const SESSION_MS = 12 * 3600 * 1000;
/** session -> expiry (ms) */
const sessions = new Map();
const loginTries = [];
/** Task 36: stream ticket -> expiry (ms); single use. */
const tickets = new Map();
const redeemTicket = (t) => {
  const ok = typeof t === "string" && (tickets.get(t) ?? 0) > Date.now();
  tickets.delete(t);
  return ok;
};
const validSession = (t) => typeof t === "string" && (sessions.get(t) ?? 0) > Date.now();
const bearerOf = (req) => /^Bearer (.+)$/.exec(req.headers.authorization || "")?.[1] ?? null;
/** The CEO: the admin token or a live session as a Bearer, or a live session as ?token= on a read. */
const isAdmin = (req) => {
  const b = bearerOf(req);
  // As the gateway (task 39): the Authorization bearer only, never a session in a URL.
  return b === ADMIN || validSession(b);
};
const VOICE = process.env.MOCK_VOICE === "1";
const TTL_MS = Number(process.env.PROPOSAL_TTL_MS || 60_000);
const TYPES = ["email", "social_post", "calendar_invite", "voice_screen"];
const CONFIRM = ["confirm", "yes", "do it", "approve it", "go ahead"];
const CANCEL = ["no", "cancel", "stop", "wait"];

const events = [];
const trust = Object.fromEntries(TYPES.map((t) => [t, "ASK"]));
const beforePause = {};
const cases = [
  { case_id: "case_7", masked_name: "Team #7", status: "READY", flagged: false },
  { case_id: "case_9", masked_name: "Team #9", status: "INTAKE_PENDING", flagged: false },
];
const pending = new Map();
const proposals = new Map();
let nextApproval = 1;
let nextProposal = 1;

function emit(type, payload, extra = {}) {
  const e = { id: events.length + 1, ts: new Date().toISOString(), type, actor: "system", case_id: null, provider: null, status: "ok", payload, tokens_in: null, tokens_out: null, cost_usd: null, ...extra };
  events.push(e);
  return e;
}
function requestApproval(summary, caseId, actionType = "email") {
  const id = `apr_${nextApproval++}`;
  pending.set(id, { approval_id: id, action_type: actionType, summary, case_id: caseId });
  emit("approval.requested", { approval_id: id, action_type: actionType, summary }, { case_id: caseId });
  return id;
}
requestApproval("memo for Team #7, $450", "case_7");

const words = (t) => ` ${String(t).toLowerCase().replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim()} `;
const has = (t, w) => words(t).includes(` ${w} `);

function pause(actionType, channel) {
  const types = actionType === "everything" || actionType === "*" ? TYPES : [actionType];
  for (const t of types) {
    if (trust[t] === "PAUSED") continue;
    beforePause[t] = trust[t];
    emit("trust.changed", { action_type: t, from: trust[t], to: "PAUSED", reason: "ceo" });
    trust[t] = "PAUSED";
  }
  emit("touch.operating", { note: `pause ${actionType}`, approval_id: null, channel }, { actor: "ceo" });
  // v1.8.4/v1.8.5: a pause from any path cancels every open proposal (a later confirm answers 409 superseded).
  for (const p of proposals.values()) {
    if (p.used) continue;
    p.used = true;
    p.cancelled = "superseded";
    emit("ceo.cancelled", { proposal_id: p.id, via: "click", heard_len: 0, reason: "superseded" }, { actor: "ceo" });
  }
  return actionType === "everything" || actionType === "*" ? "Paused everything: emails, social posts, calendar invites and phone screens." : `Paused ${actionType.replace(/_/g, " ")}.`;
}

function read(name, args) {
  switch (name) {
    case "get_brief":
      return { say: `Overnight: ${pending.size} approval${pending.size === 1 ? "" : "s"} waiting, ${cases.filter((c) => c.status === "BLOCKED").length} blocked.` };
    case "list_approvals":
      return [...pending.values()].map((a) => ({ approval_id: a.approval_id, action_type: a.action_type, summary: a.summary }));
    case "get_money":
      return { revenue_usd: "1350", spent_usd: "3.42", tax_saved_usd: "2100" };
    case "get_trust":
      return { ...trust };
    case "get_case": {
      const c = findCase(args.case);
      return c ? { case: c.masked_name, status: c.status } : null;
    }
    default:
      return { say: "Nothing new." };
  }
}
function findCase(ref) {
  const n = String(ref ?? "").match(/\d+/)?.[0];
  return cases.find((c) => c.case_id === `case_${n}`);
}

function propose(name, args, source) {
  let readback;
  const pin = { name, args };
  if (name === "approve" || name === "deny") {
    const a = args.approval === "latest" || !args.approval ? [...pending.values()].at(-1) : pending.get(args.approval) ?? [...pending.values()].find((x) => x.case_id === findCase(args.approval)?.case_id);
    if (!a) return args.approval === "latest" || !args.approval ? [409, { error: { code: "nothing_pending", message: "Nothing is waiting on you." } }] : [404, { error: { code: "not_found", message: `No approval matches. Open: ${[...pending.keys()].join(", ") || "none"}.` } }];
    pin.approval_id = a.approval_id;
    readback = `${name === "approve" ? "Approve" : "Deny"} the ${a.summary}. Say confirm.`;
  } else if (name === "set_trust") readback = `Set ${String(args.action_type).replace(/_/g, " ")} to ${args.state}. Say confirm.`;
  else if (name === "order") readback = `Tell the Chief of Staff: ${String(args.text).slice(0, 200)}. Say confirm.`;
  else readback = `${name.replace(/_/g, " ")} ${JSON.stringify(args)}. Say confirm.`;
  const id = `prop_${nextProposal++}`;
  const expires = new Date(Date.now() + TTL_MS).toISOString();
  proposals.set(id, { ...pin, id, expires, used: false, readback });
  emit("ceo.proposed", { proposal_id: id, tool: name, args, readback, expires_at: expires, source }, { actor: "ceo" });
  return [200, { kind: "proposal", proposal_id: id, readback, expires_at: expires }];
}

const KIND = { get_brief: "read", list_approvals: "read", get_case: "read", get_money: "read", get_trust: "read", get_hiring: "read", recent_activity: "read", pause: "stop", approve: "start", deny: "start", set_trust: "start", unblock_case: "start", decide_candidate: "start", order: "start" };

function tool(name, args, source) {
  const kind = Object.hasOwn(KIND, name) ? KIND[name] : null;
  if (!kind) return [400, { error: { code: "unknown_tool", message: `${name} is not a CEO tool` } }];
  if (typeof args !== "object" || args === null) return [400, { error: { code: "bad_args", message: "args must be an object" } }];
  if (kind === "read") return [200, { kind: "read", data: read(name, args) }];
  if (kind === "stop") return [200, { kind: "done", summary: pause(String(args.action_type || "everything"), "voice") }];
  return propose(name, args, source);
}

function command(text) {
  // v1.8.4: confirm and cancel words (and "approve it" with an approve open) point at the newest open proposal.
  const open = [...proposals.values()].filter((p) => !p.used && Date.now() < Date.parse(p.expires)).at(-1);
  const w = words(text).trim();
  if (open && (/^(?:ok |okay )?(?:confirm|yes|do it|go ahead)$/.test(w) || (w === "approve it" && open.name === "approve"))) {
    return [200, { kind: "confirm", proposal_id: open.id, readback: open.readback }];
  }
  if (open && /^(?:no|cancel|cancel it|never mind)$/.test(w)) return [200, { kind: "cancel", proposal_id: open.id, readback: open.readback }];
  // v1.13.1 (the gateway's order): stop commands run at once, even with a card open (a pause
  // cancels it); then, while a card is open, only confirm or cancel act; greetings are never orders.
  if (has(text, "everything") && (has(text, "stop") || has(text, "pause"))) return tool("pause", { action_type: "everything" }, "command");
  if (has(text, "stop posting")) return tool("pause", { action_type: "social_post" }, "command");
  if (open) return [200, { kind: "say", say: "Say confirm or cancel." }];
  if (/^(?:(?:hey|hi|hello|test|testing|okay)\s*)+$/.test(w) || /can you hear me|are you there|is this working/.test(w)) return [200, { kind: "say", say: "I'm listening. Tell me an order." }];
  if (has(text, "approve")) return tool("approve", { approval: "latest" }, "command");
  if (has(text, "deny")) return tool("deny", { approval: "latest" }, "command");
  if (has(text, "money")) return tool("get_money", {}, "command");
  // D31: an order for the firm ("advertise our firm on LinkedIn and X", "hire 2 people", "follow up with the customer").
  if (/^(?:tell the chief of staff to |please )?(advertis|hire|recruit|follow up|post)/.test(w)) return tool("order", { text: text.trim().slice(0, 200) }, "command");
  if (has(text, "overnight") || has(text, "brief") || has(text, "happened")) return tool("get_brief", {}, "command");
  return [200, { kind: "unknown", say: "Try: what happened overnight? · approve the memo for Team 7 · stop posting · stop everything." }];
}

function confirm(body) {
  const p = proposals.get(body.proposal_id);
  if (!p) return [404, { error: { code: "not_found", message: "No such proposal." } }];
  if (p.cancelled) return [409, { error: { code: p.cancelled, message: "That proposal was cancelled." } }];
  if (p.used) return [409, { error: { code: "used", message: "Already decided." } }];
  const heard = String(body.heard ?? "").slice(0, 200);
  const cancel = body.decision === "cancel" || (body.via === "voice" && CANCEL.some((w) => has(heard, w)));
  if (cancel) {
    p.used = true;
    emit("ceo.cancelled", { proposal_id: p.id, via: body.via, heard, reason: body.via === "voice" ? "spoken" : "clicked" }, { actor: "ceo" });
    return [200, { kind: "done", summary: "Cancelled." }];
  }
  if (Date.now() >= Date.parse(p.expires)) return [409, { error: { code: "expired", message: "That proposal expired." } }];
  if (body.via === "voice" && !CONFIRM.some((w) => has(heard, w))) return [400, { error: { code: "not_a_confirm", message: "That wasn't a confirm." } }];
  p.used = true;
  let summary = "Done.";
  if (p.name === "approve" || p.name === "deny") {
    const a = pending.get(p.approval_id);
    if (!a) return [409, { error: { code: "superseded", message: `${p.approval_id} is out of date`, approval_id: p.approval_id, superseded_by: null } }];
    pending.delete(p.approval_id);
    emit("touch.training", { note: `${p.name === "approve" ? "allow" : "deny"}: ${a.summary}`, approval_id: a.approval_id, channel: body.via }, { actor: "ceo", case_id: a.case_id });
    summary = `${p.name === "approve" ? "Approved" : "Denied"} the ${a.summary}.`;
  } else if (p.name === "set_trust") {
    const t = String(p.args.action_type);
    const to = p.args.state === "RESUME" ? beforePause[t] ?? "ASK" : p.args.state;
    emit("trust.changed", { action_type: t, from: trust[t], to, reason: "ceo" });
    trust[t] = to;
    summary = `${t.replace(/_/g, " ")} is now ${to}.`;
  } else if (p.name === "order") {
    order(String(p.args.text), body.via);
    summary = "The Chief of Staff has it.";
  } else summary = `${p.name.replace(/_/g, " ")} done.`;
  emit("ceo.confirmed", { proposal_id: p.id, via: body.via, heard }, { actor: "ceo" });
  return [200, { kind: "done", summary }];
}

// ------------------------------------------------------------------ D31: the live CEO orders
const PACE = Number(process.env.MOCK_PACE || 1600);
let ads = 0;
let candidates = 0;

/** The CEO's order on the log, then the firm acting on it, one step every PACE ms. */
// D34: the conversation's threads and undos.
let talkDownDev = false; // POST /api/dev/talk-down {down} (dev only): the assistant goes away mid-run
const talks = new Map(); // conversation_id -> { pending: null | { ready, reply } , last }
const undos = new Map(); // undo_id -> { kind, ... , used }
let undoSeq = 0;
function newUndo(rec) {
  const id = `undo_${++undoSeq}`;
  undos.set(id, { ...rec, used: false });
  return id;
}
function talkTurn(text) {
  const w = words(text).trim();
  if (has(text, "everything") && (has(text, "stop") || has(text, "pause"))) {
    const before = { ...trust };
    const summary = pause("everything", "voice");
    return { say: summary, actions: [{ tool: "pause", summary, undo_id: newUndo({ kind: "pause", before }) }] };
  }
  if (has(text, "stop posting") || has(text, "pause posting")) {
    const before = { ...trust };
    const summary = pause("social_post", "voice");
    return { say: summary, actions: [{ tool: "pause", summary, undo_id: newUndo({ kind: "pause", before }) }] };
  }
  if (/^(?:(?:hey|hi|hello|test|testing|okay)\s*)+$/.test(w) || /can you hear me|are you there/.test(w)) return { say: "I'm here. What should the firm do?", actions: [] };
  if (has(text, "money") || has(text, "overnight") || has(text, "how are we")) return { say: `${cases.length} clients so far, ${pending.size} approvals waiting on you.`, actions: [] };
  if (has(text, "approve")) return { say: "Approving needs your click on the card, so I left it for you.", actions: [] };
  const id = order(text.slice(0, 200), "voice");
  const summary = `Told the Chief of Staff: ${text.slice(0, 120)}`;
  return { say: `On it. ${summary}.`, actions: [{ tool: "order", summary, undo_id: newUndo({ kind: "order", order_id: id }) }] };
}
function talk(body) {
  if (process.env.MOCK_TALK === "503" || talkDownDev) return [503, { error: { code: "assistant_unavailable", message: "The assistant can't be reached right now. Stops still work: say stop." } }];
  const text = typeof body.text === "string" ? body.text.trim() : "";
  if (!text) return [400, { error: { code: "bad_input", message: "text is required" } }];
  let cid = typeof body.conversation_id === "string" && talks.has(body.conversation_id) ? body.conversation_id : null;
  if (!cid) {
    cid = `conv_${randomUUID().slice(0, 8)}`;
    talks.set(cid, { pending: null, last: null });
  }
  const conv = talks.get(cid);
  emit("ceo.said", { conversation_id: cid, text_len: text.length }, { actor: "ceo" });
  if (/slow/i.test(text)) {
    conv.pending = { ready: Date.now() + 3000, text: text.replace(/slow(ly)?/gi, "").trim() || "hire 1 person" };
    return [200, { conversation_id: cid, say: "Still working on it…", pending: true }];
  }
  const out = { conversation_id: cid, ...talkTurn(text) };
  emit("ceo.replied", { conversation_id: cid, actions: out.actions }, { actor: "ceo_assistant" });
  conv.last = out;
  return [200, out];
}
function talkCheck(cid) {
  const conv = talks.get(cid);
  if (!conv) return [404, { error: { code: "not_found", message: "no such conversation" } }];
  if (conv.pending) {
    if (Date.now() < conv.pending.ready) return [200, { conversation_id: cid, say: "Still working on it…", pending: true }];
    const out = { conversation_id: cid, ...talkTurn(conv.pending.text) };
    conv.pending = null;
    conv.last = out;
    emit("ceo.replied", { conversation_id: cid, actions: out.actions }, { actor: "ceo_assistant" });
    return [200, out];
  }
  return [200, conv.last ?? { conversation_id: cid, say: "", actions: [] }];
}
function undo(body) {
  const rec = undos.get(String(body.undo_id ?? ""));
  if (!rec) return [404, { error: { code: "not_found", message: "no such undo (each undo works once)" } }];
  if (rec.used) return [409, { error: { code: "used", message: "already undone" } }];
  rec.used = true;
  if (rec.kind === "pause") {
    const back = [];
    for (const t of TYPES) {
      if (trust[t] === "PAUSED" && rec.before[t] !== "PAUSED") {
        emit("trust.changed", { action_type: t, from: "PAUSED", to: rec.before[t], reason: "ceo" });
        trust[t] = rec.before[t];
        back.push(t);
      }
    }
    return [200, { kind: "done", summary: back.length ? `Resumed ${back.join(", ")}.` : "Nothing was paused any more." }];
  }
  emit("touch.operating", { note: `cancel order ${rec.order_id}`, approval_id: null, channel: "voice" }, { actor: "ceo" });
  return [200, { kind: "done", summary: "Cancelled that order." }];
}

function order(text, via) {
  const o = emit("voice.command", { text, channel: via === "voice" ? "voice" : "typed", speaker: "ceo", case_id: null, masked_name: null }, { actor: "ceo" });
  const ref = `order_${o.id}`;
  const steps = [];
  const at = (fn) => steps.push(fn);
  const handoff = (seat, tool) => {
    at(() => emit("naive.confirm", { session_id: `ses_${events.length}`, agent: "cos", tool: "send_to_agent", decision: "allow", read_people: false }));
    at(() => emit("tool.called", { tool: "send_to_agent", status: "done", order_id: ref }, { actor: "cos" }));
    at(() => emit("naive.confirm", { session_id: `ses_${events.length}`, agent: seat, tool, decision: "allow", read_people: false }));
    at(() => emit("tool.called", { tool, status: "done", order_id: ref }, { actor: seat }));
  };
  const call = (seat, tool, status = "done") => at(() => emit("tool.called", { tool, status, order_id: ref }, { actor: seat }));
  const w = words(text);
  if (/advertis|\bads?\b|post|linkedin|market/.test(w)) {
    handoff("mkt", "make_ad");
    at(() => {
      const n = ++ads;
      emit("ad.created", { kind: "client", headline: "Your Delaware franchise tax, done right", image_url: `http://127.0.0.1:${PORT}/assets/ad_${n}.svg`, order_id: ref }, { actor: "mkt" });
    });
    for (const channel of ["x", "linkedin"]) {
      call("mkt", "post_social");
      at(() =>
        emit("post.published", { channel, text: "Delaware sent you an $85,165 franchise tax bill? Most startups owe $450. U-Tax checks yours free.", url: `https://${channel}.com/utax/status/${events.length}`, image_url: `http://127.0.0.1:${PORT}/assets/ad_${ads}.svg`, order_id: ref }, { actor: "mkt" }),
      );
    }
  }
  if (/\bhir|recruit|candidate|people/.test(w)) {
    const want = Math.min(5, Number((/\b([1-5])\b/.exec(w) || [])[1] || 2));
    handoff("hr", "source_candidates");
    at(() => emit("tool.bought", { tool: "monid.search", provider: "monid", order_id: ref }, { actor: "hr", cost_usd: "0.05" }));
    at(() => emit("hr.sourced", { count: 3, source: "monid", order_id: ref }, { actor: "hr" }));
    call("hr", "list_candidates");
    for (let i = 0; i < want; i++) {
      const label = `Candidate ${String.fromCharCode(65 + (candidates++ % 26))}`;
      const id = `cand_${candidates}`;
      const start = new Date(Date.now() + 86400000 + i * 3600000).toISOString();
      call("hr", "hr_outreach");
      at(() => emit("hr.outreach", { candidate_id: id, label, slot: start, order_id: ref }, { actor: "hr" }));
      at(() => emit("call.booked", { start, minutes: 20, invitee_masked: label, kind: "candidate", order_id: ref }, { actor: "hr" }));
    }
  }
  if (/follow|customer|client|remind/.test(w)) {
    handoff("intake", "follow_up");
    at(() => emit("client.followed_up", { kind: "memo_check", order_id: ref }, { actor: "intake", case_id: "case_7" }));
  }
  steps.forEach((fn, i) => setTimeout(fn, PACE * (i + 1)));
  return o.id;
}

let signups = 0;
/** §5a POST /api/signup: a case opened like an inbound email, then the crew works it, step by step. */
function signup(name, company, email) {
  const n = 40 + ++signups;
  const caseId = `case_${n}`;
  const masked = `Team #${n}`;
  const [user, host] = email.split("@");
  const from = `${user.slice(0, 1)}***@${host}`;
  cases.push({ case_id: caseId, masked_name: masked, status: "INTAKE_PENDING", flagged: false });
  emit("email.received", { masked_name: masked, from_masked: from, subject: "Free checkup sign-up", body: "(sign-up form: the welcome email asks for the numbers)", source: "signup" }, { actor: "intake", case_id: caseId });
  const steps = [];
  const at = (fn) => steps.push(fn);
  const call = (seat, tool, status = "done") => at(() => emit("tool.called", { tool, status }, { actor: seat, case_id: caseId }));
  call("cos", "list_open_cases");
  at(() => emit("naive.confirm", { session_id: `ses_${events.length}`, agent: "cos", tool: "send_to_agent", decision: "allow", read_people: false }));
  call("cos", "send_to_agent");
  call("intake", "get_case");
  call("intake", "extract_facts");
  at(() => emit("facts.extracted", { facts: { incorporated_delaware: true, authorized_shares: 10000000, issued_shares: 8000000, stated_par: "0.00001", gross_assets_cents: 50000000, foreign_owner_25: false }, quotes: {}, assumed: [] }, { actor: "intake", case_id: caseId }));
  call("tax", "law_search");
  at(() => emit("law.searched", { query: "Delaware franchise tax assumed par value capital method", results: [{ cite: "8 Del. C. § 503", snippet: "assumed par value capital method" }] }, { actor: "tax", case_id: caseId }));
  call("tax", "compute_tax");
  at(() => emit("tax.computed", { authorized_method_cents: 8516500, assumed_par_method_cents: 40000, chosen: "assumed_par", annual_report_fee_cents: 5000, fee_source: "snapshot", total_cents: 45000, due_date: "2027-03-01", form5472_flag: false, rnd_flag: false, engine_version: "mock" }, { actor: "tax", case_id: caseId }));
  call("qa", "qa_check");
  at(() => emit("qa.passed", { checks: [{ field: "total", ok: true, note: "engine re-run matches" }, { field: "assumed_par_method", ok: true, note: "matches" }] }, { actor: "qa", case_id: caseId }));
  call("acct", "send_memo", "needs_approval");
  at(() => requestApproval(`memo for ${masked}`, caseId));
  steps.forEach((fn, i) => setTimeout(fn, PACE * (i + 1)));
  return { case_id: caseId, masked_name: masked };
}

/** The ad as the gateway would render it (an SVG stand-in for its PNG): firm name, headline, the real example. */
function adSvg(n) {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="628" viewBox="0 0 1200 628">
<rect width="1200" height="628" fill="#07070b"/><rect x="40" y="40" width="1120" height="548" rx="28" fill="#0f0f16" stroke="#ff4d3d" stroke-width="4"/>
<text x="90" y="150" fill="#ff4d3d" font-family="Inter,Arial" font-size="56" font-weight="800">U-Tax</text>
<text x="90" y="260" fill="#ecebf5" font-family="Inter,Arial" font-size="58" font-weight="800">Your Delaware franchise tax,</text>
<text x="90" y="330" fill="#ecebf5" font-family="Inter,Arial" font-size="58" font-weight="800">done right.</text>
<text x="90" y="470" fill="#a3a2ba" font-family="Inter,Arial" font-size="64" text-decoration="line-through">$85,165</text>
<text x="400" y="470" fill="#3ddc84" font-family="Inter,Arial" font-size="72" font-weight="800">→ $450</text>
<text x="1110" y="560" fill="#85849f" font-family="Inter,Arial" font-size="24" text-anchor="end">ad ${n} · mock</text></svg>`;
}

/** A silent 8 kHz mono 8-bit WAV of `ms` milliseconds (a playable stand-in for the TTS audio). */
function silentWav(ms) {
  const n = Math.round(8 * ms);
  const b = Buffer.alloc(44 + n, 128);
  b.write("RIFF", 0);
  b.writeUInt32LE(36 + n, 4);
  b.write("WAVEfmt ", 8);
  b.writeUInt32LE(16, 16);
  b.writeUInt16LE(1, 20);
  b.writeUInt16LE(1, 22);
  b.writeUInt32LE(8000, 24);
  b.writeUInt32LE(8000, 28);
  b.writeUInt16LE(1, 32);
  b.writeUInt16LE(8, 34);
  b.write("data", 36);
  b.writeUInt32LE(n, 40);
  return b;
}

function touchCounts() {
  const n = (t) => events.filter((e) => e.type === t).length;
  return { training: n("touch.training"), operating: n("touch.operating"), engineering: n("touch.engineering") };
}

function state() {
  return {
    last_event_id: events.length,
    cases,
    trust,
    hiring: { candidates: [] },
    queue: { waiting: 0, blocked: cases.filter((c) => c.status === "BLOCKED").length },
  };
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const cors = { "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "Authorization, Content-Type", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" };
  if (req.method === "OPTIONS") return res.writeHead(204, cors).end();
  let raw = "";
  for await (const chunk of req) raw += chunk;
  let body = {};
  try {
    body = raw ? JSON.parse(raw) : {};
  } catch {
    body = null;
  }
  const out = (status, json) => {
    // A password never reaches the log, whatever the path.
    const logged = body && typeof body === "object" && "password" in body ? { ...body, password: "[redacted]" } : body;
    console.log(JSON.stringify({ t: Date.now(), method: req.method, path: url.pathname, body: logged, status }));
    res.writeHead(status, { ...cors, "Content-Type": "application/json" }).end(JSON.stringify(json));
  };
  if (url.pathname === "/api/health") return out(200, { status: "ok", version: "mock-ceo" });
  // D33: the CEO sign-in. Never logs the password (the line says only whether each field matched).
  if (req.method === "POST" && url.pathname === "/api/auth/login") {
    const now = Date.now();
    while (loginTries.length && now - loginTries[0] > 15 * 60_000) loginTries.shift();
    const email = body && typeof body.email === "string" ? body.email.trim().toLowerCase() : "";
    const ok = email === CEO_EMAIL && body && body.password === CEO_PASSWORD;
    const reply = (status, json) => {
      console.log(JSON.stringify({ t: now, method: "POST", path: url.pathname, body: { email_ok: email === CEO_EMAIL, ok }, status }));
      res.writeHead(status, { ...cors, "Content-Type": "application/json" }).end(JSON.stringify(json));
    };
    if (loginTries.length >= 10) return reply(429, { error: { code: "rate_limited", message: "too many sign-in attempts" } });
    if (!ok) {
      loginTries.push(now);
      return reply(401, { error: { code: "bad_login", message: "wrong email or password" } });
    }
    const session = `sess.${randomUUID()}`;
    sessions.set(session, now + SESSION_MS);
    return reply(200, { session, expires_at: new Date(now + SESSION_MS).toISOString() });
  }
  if (req.method === "POST" && url.pathname === "/api/stream-ticket") {
    if (!isAdmin(req)) return out(401, { error: { code: "unauthorized", message: "admin token or a CEO session required" } });
    const ticket = `tk.${randomUUID()}`;
    tickets.set(ticket, Date.now() + 60_000);
    return out(200, { ticket, expires_at: new Date(Date.now() + 60_000).toISOString() });
  }
  if (req.method === "POST" && url.pathname === "/api/auth/logout") {
    const b = bearerOf(req);
    sessions.delete(b); // as the gateway: always 200, known session or not
    return out(200, { ok: true });
  }
  const ad = req.method === "GET" && /^\/assets\/ad_([0-9]{1,6})\.svg$/.exec(url.pathname);
  if (ad) return res.writeHead(200, { ...cors, "Content-Type": "image/svg+xml", "Cache-Control": "no-store" }).end(adSvg(ad[1]));
  // §5a: the client sign-up is public (the /talk per-IP limits apply on the real gateway).
  if (req.method === "POST" && url.pathname === "/api/signup") {
    const f = (k) => (body && typeof body[k] === "string" ? body[k].trim().slice(0, 200) : "");
    const name = f("name"), company = f("company"), email = f("email");
    if (!name || !company || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return out(400, { error: { code: "bad_input", message: "name, company and a valid email are required" } });
    if (process.env.MOCK_SIGNUP === "429") return out(429, { error: { code: "at_capacity", message: "U-Tax is at capacity today. Email checkup@u-tax.app and we'll get to you tomorrow." } });
    signup(name, company, email);
    return out(200, { ok: true }); // v1.12.2: never says who is already a client
  }
  // The customer mint (/talk) is public, like the gateway's; with no voice provider it is 503 (D30).
  if (req.method === "POST" && url.pathname === "/api/voice/session" && (!body || body.mode !== "ceo")) {
    return out(503, { error: { code: "voice_unavailable", message: "no Realtime provider" } });
  }
  // v1.10 §5c: POST /api/tts {text, purpose}. "ceo" needs the admin token, "talk" is public.
  // MOCK_TTS=503 answers voice_unavailable; otherwise a short silent WAV sized to the text.
  if (req.method === "POST" && url.pathname === "/api/tts") {
    const purpose = body && body.purpose;
    if (purpose !== "ceo" && purpose !== "talk") return out(400, { error: { code: "bad_input", message: "purpose" } });
    if (purpose === "ceo" && !isAdmin(req)) return out(401, { error: { code: "unauthorized", message: "admin token required" } });
    let text = "";
    if (purpose === "ceo") text = typeof body.text === "string" ? body.text : "";
    else {
      // v1.10.2: registry keys only (the lane's proposed keys), numeric args only, never free text.
      const keys = { "question.1": [], "question.2": [], "question.3": [], "question.4": [], "question.5": [], "estimate.authorized": ["total_cents"], "estimate.assumed_par": ["total_cents"], "memo.on_its_way": [] };
      const args = body.args && typeof body.args === "object" ? body.args : {};
      const want = keys[body.phrase];
      const ok = body.text === undefined && want && Object.keys(args).every((k) => want.includes(k) && Number.isInteger(args[k]) && String(args[k]).length <= 12) && want.every((k) => k in args);
      if (!ok) return out(400, { error: { code: "bad_input", message: "unknown phrase, extra or non-numeric args, or free text" } });
      text = `${body.phrase} ${Object.values(args).join(" ")}`;
    }
    if (!text || text.length > 1500) return out(400, { error: { code: "bad_input", message: "text" } });
    if (process.env.MOCK_TTS === "503") return out(503, { error: { code: "voice_unavailable", message: "no voice key" } });
    console.log(JSON.stringify({ t: Date.now(), method: "POST", path: "/api/tts", body: { purpose, chars: text.length, auth: Boolean(req.headers.authorization) }, status: 200 }));
    return res.writeHead(200, { ...cors, "Content-Type": "audio/wav" }).end(silentWav(Math.min(4000, 300 + text.length * 25)));
  }
  // The theater token reads (state, events, stream), so the projector can be checked against this mock.
  const readToken = url.searchParams.get("token");
  // Theater reads: the theater token as ?token=, or the CEO (Bearer, or a session as ?token=) with the full view.
  const ticket = url.pathname === "/api/events/stream" ? url.searchParams.get("ticket") : null;
  if (ticket !== null && !redeemTicket(ticket)) return out(401, { error: { code: "unauthorized", message: "stream ticket expired or used; get a new one from POST /api/stream-ticket" } });
  const ceoRead = ticket !== null || isAdmin(req);
  if (req.method === "GET" && (ceoRead || (readToken === THEATER && req.headers.authorization === undefined))) {
    if (url.pathname === "/api/state" && !ceoRead) return out(200, { ...state(), firm: { agents: 7, clients: cases.length }, touches: touchCounts() });
    if (url.pathname === "/api/events/stream") {
      let after = Number(url.searchParams.get("after_id") || 0);
      const reader = ticket !== null ? "ticket" : bearerOf(req) ? "bearer" : validSession(readToken) ? "SESSION-IN-URL" : "theater";
      console.log(JSON.stringify({ t: Date.now(), method: "GET", path: "/api/events/stream", body: { reader, token_in_url: Boolean(readToken), after }, status: 200 }));
      res.writeHead(200, { ...cors, "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
      res.write(": open\n\n"); // as the gateway's heartbeat: the browser sees the stream open at once
      const pump = () => {
        for (const e of events.filter((x) => x.id > after)) {
          res.write(`id: ${e.id}\ndata: ${JSON.stringify(e)}\n\n`);
          after = e.id;
        }
      };
      pump();
      const t = setInterval(pump, 300);
      req.on("close", () => clearInterval(t));
      return;
    }
  }
  if (!isAdmin(req) && !(req.method === "GET" && url.pathname === "/api/events" && readToken === THEATER)) {
    return out(401, { error: { code: "unauthorized", message: "admin token required" } });
  }
  if (body === null) return out(400, { error: { code: "bad_json", message: "bad JSON" } });
  const p = url.pathname;
  if (req.method === "GET" && p === "/api/state") return out(200, state());
  if (req.method === "GET" && p === "/api/events") {
    const after = Number(url.searchParams.get("after_id") || 0);
    const limit = Number(url.searchParams.get("limit") || 500);
    const data = events.filter((e) => e.id > after).slice(0, limit);
    return out(200, { data, next_after_id: data.at(-1)?.id ?? after, has_more: events.filter((e) => e.id > after).length > limit });
  }
  if (req.method === "GET" && p === "/api/ceo/brief") {
    const e = emit("brief.generated", { text: `Good evening. ${pending.size} approval waiting: memo for Team #7, $450.`, counts: {} }, { actor: "cos" });
    return out(200, { brief_event_id: e.id, text: e.payload.text, counts: {}, pending_approvals: [...pending.values()], last_event_id: e.id });
  }
  if (req.method === "GET" && p === "/api/brief/audio") return out(503, { error: { code: "voice_unavailable", message: "no key" } });
  if (req.method === "POST" && p === "/api/voice/session") {
    if (!VOICE) return out(503, { error: { code: "voice_unavailable", message: "no OPENAI_API_KEY" } });
    return out(200, { client_secret: "ek_mock", model: "gpt-realtime", expires_at: new Date(Date.now() + 600_000).toISOString(), realtime_url: "https://api.openai.com/v1/realtime/calls" });
  }
  if (req.method === "POST" && p === "/api/ceo/tool") return out(...tool(String(body.name ?? ""), body.args, "realtime"));
  if (req.method === "POST" && p === "/api/ceo/command") return out(...command(String(body.text ?? "")));
  if (req.method === "POST" && p === "/api/ceo/talk") return out(...talk(body));
  if (req.method === "GET" && p.startsWith("/api/ceo/talk/")) return out(...talkCheck(decodeURIComponent(p.slice("/api/ceo/talk/".length))));
  if (req.method === "POST" && p === "/api/ceo/undo") return out(...undo(body));
  if (req.method === "POST" && p === "/api/dev/talk-down") {
    talkDownDev = body.down !== false;
    return out(200, { down: talkDownDev });
  }
  if (req.method === "POST" && p === "/api/ceo/confirm") return out(...confirm(body));
  if (req.method === "POST" && p === "/api/trust") {
    if (body.state === "PAUSED") return out(200, { ok: true, summary: pause(body.action_type, "click") });
    if (body.action_type === "*") return out(400, { error: { code: "bad_args", message: "only PAUSED with *" } });
    const to = body.state === "RESUME" ? beforePause[body.action_type] ?? "ASK" : body.state;
    emit("trust.changed", { action_type: body.action_type, from: trust[body.action_type], to, reason: "ceo" });
    trust[body.action_type] = to;
    return out(200, { ok: true });
  }
  if (req.method === "POST" && p === "/api/approve") {
    const a = pending.get(body.approval_id);
    if (!a) return out(404, { error: { code: "not_found", message: "no such approval" } });
    pending.delete(a.approval_id);
    emit("touch.training", { note: body.decision, approval_id: a.approval_id, channel: "click" }, { actor: "ceo", case_id: a.case_id });
    return out(200, { ok: true });
  }
  if (req.method === "POST" && p === "/api/dev/approval") return out(200, { approval_id: requestApproval(String(body.summary || "memo"), body.case_id ?? null) });
  if (req.method === "POST" && p === "/api/dev/naive-confirm") {
    // v1.9 §6b-bis as the gateway writes it, so the theater's rail line can be checked.
    const e = emit("naive.confirm", { session_id: `ses_${events.length}`, agent: String(body.agent || "cos"), tool: String(body.tool || "board_write"), decision: body.decision === "deny" ? "deny" : "allow", read_people: false });
    return out(200, { id: e.id });
  }
  if (req.method === "POST" && p === "/api/dev/order") return out(200, { id: order(String(body?.text || "Advertise our firm on LinkedIn and X").slice(0, 200), "typed") });
  if (req.method === "POST" && p === "/api/dev/block") {
    const c = cases.find((x) => x.case_id === body.case_id);
    if (c) c.status = "BLOCKED";
    emit("case.blocked", { reason: String(body.reason || "QA rejected the figure twice"), needs: "human" }, { case_id: body.case_id });
    return out(200, { ok: true });
  }
  return out(404, { error: { code: "not_found", message: `${req.method} ${p}` } });
});

server.listen(PORT, "127.0.0.1", () => console.log(JSON.stringify({ listening: PORT, voice: VOICE })));
