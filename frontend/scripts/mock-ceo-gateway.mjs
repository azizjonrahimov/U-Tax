// A stand-in gateway for /ceo voice mode QA (contract v1.8 §5b) until backend task 18 lands.
// Dev only: never deployed. Every request is logged to stdout as one JSON line, so a QA run
// can prove what the console sent (e.g. that no /api/ceo/confirm followed a model "confirmed").
//
//   PORT=8020 ADMIN_TOKEN=admin-test MOCK_VOICE=0 node scripts/mock-ceo-gateway.mjs
//
// MOCK_VOICE=1 mints a fake Realtime session (the browser's WebRTC must be stubbed by the test);
// otherwise /api/voice/session answers 503 voice_unavailable and the console falls back.
// THEATER_TOKEN (default theater-test) may read state, events and the stream, as the projector does.
// Dev-only extras: POST /api/dev/approval {summary, case_id} and POST /api/dev/block {case_id, reason}.

import { createServer } from "node:http";

const PORT = Number(process.env.PORT || 8020);
const ADMIN = process.env.ADMIN_TOKEN || "admin-test";
const THEATER = process.env.THEATER_TOKEN || "theater-test";
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
  if (has(text, "everything") && (has(text, "stop") || has(text, "pause"))) return tool("pause", { action_type: "everything" }, "command");
  if (has(text, "stop posting")) return tool("pause", { action_type: "social_post" }, "command");
  if (has(text, "approve")) return tool("approve", { approval: "latest" }, "command");
  if (has(text, "deny")) return tool("deny", { approval: "latest" }, "command");
  if (has(text, "money")) return tool("get_money", {}, "command");
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
  } else summary = `${p.name.replace(/_/g, " ")} done.`;
  emit("ceo.confirmed", { proposal_id: p.id, via: body.via, heard }, { actor: "ceo" });
  return [200, { kind: "done", summary }];
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
    console.log(JSON.stringify({ t: Date.now(), method: req.method, path: url.pathname, body, status }));
    res.writeHead(status, { ...cors, "Content-Type": "application/json" }).end(JSON.stringify(json));
  };
  if (url.pathname === "/api/health") return out(200, { status: "ok", version: "mock-ceo" });
  // The theater token reads (state, events, stream), so the projector can be checked against this mock.
  if (req.method === "GET" && url.searchParams.get("token") === THEATER && req.headers.authorization === undefined) {
    if (url.pathname === "/api/state") return out(200, { ...state(), firm: { agents: 7, clients: cases.length }, touches: touchCounts() });
    if (url.pathname === "/api/events/stream") {
      let after = Number(url.searchParams.get("after_id") || 0);
      res.writeHead(200, { ...cors, "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
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
  if (req.headers.authorization !== `Bearer ${ADMIN}` && !(req.method === "GET" && url.pathname === "/api/events" && url.searchParams.get("token") === THEATER)) {
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
  if (req.method === "POST" && p === "/api/dev/block") {
    const c = cases.find((x) => x.case_id === body.case_id);
    if (c) c.status = "BLOCKED";
    emit("case.blocked", { reason: String(body.reason || "QA rejected the figure twice"), needs: "human" }, { case_id: body.case_id });
    return out(200, { ok: true });
  }
  return out(404, { error: { code: "not_found", message: `${req.method} ${p}` } });
});

server.listen(PORT, "127.0.0.1", () => console.log(JSON.stringify({ listening: PORT, voice: VOICE })));
