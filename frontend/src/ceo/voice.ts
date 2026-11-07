// The CEO voice console (/ceo voice mode, contract v1.8 §5b and §8, design D28): pure logic,
// tested without a browser, a microphone or a gateway.
//
// Safety rules held here, in code:
// - The model can only reach POST /api/ceo/tool with a tool from the registry below. A "start"
//   tool comes back as a proposal; nothing is carried out.
// - A proposal is confirmed only by the CEO's own words (Realtime's
//   conversation.item.input_audio_transcription.completed for audio the CEO said *after* the
//   card appeared, or browser speech / typing in the fallback), matched by the grammar below,
//   or by a click. Model output never confirms: its transcripts are never matched.

import type { UTaxEvent } from "@/theater/events";

// ------------------------------------------------------------------ the tool registry (§5b)

export type ToolKind = "read" | "stop" | "start";
export const CEO_TOOLS: Readonly<Record<string, ToolKind>> = Object.freeze({
  get_brief: "read",
  list_approvals: "read",
  get_case: "read",
  get_money: "read",
  get_trust: "read",
  get_hiring: "read",
  recent_activity: "read",
  pause: "stop",
  approve: "start",
  deny: "start",
  set_trust: "start",
  unblock_case: "start",
  decide_candidate: "start",
  order: "start",
});
export const isCeoTool = (name: string) => Object.hasOwn(CEO_TOOLS, name);

/** STOP EVERYTHING: the one call the console makes on its own, with no confirm. */
export const STOP_EVERYTHING = { name: "pause", args: { action_type: "everything" } } as const;

// ------------------------------------------------------------------ the confirm grammar

/** The gateway's grammar exactly (it re-checks `heard`); "confirmed" is not in it. */
const CONFIRM_PHRASES = ["confirm", "yes", "do it", "approve it", "go ahead"];
const CANCEL_WORDS = ["no", "cancel", "stop", "wait"];
/** Stricter than the gateway (it fails closed): a negation never confirms ("don't do it"). */
const NEGATIONS = ["not", "don't", "dont", "do not", "never", "nope"];
/** The readback ends "Say confirm.": a mic that picks up the speaker must not confirm itself. */
const ECHOES = ["say confirm", "say yes", "say approve", "say cancel"];

/** `heard` travels to the gateway clipped to this (contract: ≤ 200 chars), and only the clipped text is matched. */
export const MAX_HEARD = 200;

const normalize = (s: string) =>
  ` ${Array.from(s).slice(0, MAX_HEARD).join("").toLowerCase().replace(/[’`]/g, "'").replace(/[^a-z0-9' ]+/g, " ").replace(/\s+/g, " ").trim()} `;
const hasPhrase = (norm: string, phrase: string) => norm.includes(` ${phrase} `);

export const clipHeard = (s: string) => Array.from(s.replace(/\s+/g, " ").trim()).slice(0, MAX_HEARD).join("");

/**
 * The CEO's words → confirm, cancel, or nothing. Whole words, any case. A cancel word or a
 * negation always wins ("yes, wait" cancels). An echo of the readback is nothing.
 */
export function matchConfirm(text: string): "confirm" | "cancel" | null {
  const n = normalize(text);
  if (n.trim() === "") return null;
  if (ECHOES.some((p) => hasPhrase(n, p))) return null;
  if (CANCEL_WORDS.some((w) => hasPhrase(n, w)) || NEGATIONS.some((w) => hasPhrase(n, w))) return "cancel";
  return CONFIRM_PHRASES.some((p) => hasPhrase(n, p)) ? "confirm" : null;
}

// ------------------------------------------------------------------ gateway answers

export interface Proposal {
  proposalId: string;
  readback: string;
  /** ms since epoch; the card counts down to it and a confirm after it is refused locally. */
  expiresAt: number;
}

export type ToolResult =
  | { kind: "read"; data: unknown }
  | { kind: "done"; summary: string }
  | ({ kind: "proposal" } & Proposal)
  | { kind: "unknown"; say: string };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown) => (typeof v === "string" ? v : "");

/** A proposal's lifetime when the gateway's expires_at can't be read (contract: 60 s). */
export const PROPOSAL_TTL_MS = 60_000;

/** POST /api/ceo/tool, /api/ceo/command and /api/ceo/confirm answers; anything else is null. */
export function parseToolResult(raw: unknown, now: number): ToolResult | null {
  if (!isObj(raw)) return null;
  switch (raw.kind) {
    case "read":
      return { kind: "read", data: raw.data ?? null };
    case "done":
      return { kind: "done", summary: str(raw.summary) || "Done." };
    case "unknown":
      return { kind: "unknown", say: str(raw.say) || "I didn't catch a command." };
    case "proposal": {
      const id = str(raw.proposal_id);
      const readback = str(raw.readback);
      if (!id || !readback) return null;
      const t = Date.parse(str(raw.expires_at));
      // Never trust a far-off or past expiry from the wire more than the contract's 60 s.
      const expiresAt = Number.isFinite(t) ? Math.min(t, now + PROPOSAL_TTL_MS) : now + PROPOSAL_TTL_MS;
      return { kind: "proposal", proposalId: id, readback, expiresAt };
    }
    default:
      return null;
  }
}

/** What the model hears back for a function call (a string; the model speaks from it). */
export function toolOutput(r: ToolResult | { kind: "error"; code: string; message: string }): string {
  switch (r.kind) {
    case "read":
      return clipJson({ ok: true, data: r.data });
    case "done":
      return clipJson({ ok: true, done: r.summary });
    case "proposal":
      return clipJson({
        ok: true,
        proposal: r.readback,
        status: "NOT DONE. Read the proposal aloud exactly. It runs only if the CEO says confirm or clicks Confirm; never say it is done.",
      });
    case "unknown":
      return clipJson({ ok: false, say: r.say });
    case "error":
      return clipJson({ ok: false, error: r.code, message: r.message });
  }
}

/** Function outputs stay small: the model needs a summary, not the whole firm. */
const MAX_OUTPUT = 6000;
function clipJson(v: unknown): string {
  const s = JSON.stringify(v);
  return s.length <= MAX_OUTPUT ? s : `${s.slice(0, MAX_OUTPUT)}…(cut)`;
}

/** A read answer as a few spoken sentences (the fallback has no model to phrase it). */
export function describeRead(data: unknown, depth = 0): string {
  if (data === null || data === undefined) return "Nothing to report.";
  if (typeof data === "string") return data;
  if (typeof data === "number" || typeof data === "boolean") return String(data);
  if (Array.isArray(data)) {
    if (!data.length) return "None.";
    if (depth > 0) return `${data.length}`;
    const items = data.slice(0, 5).map((d) => describeRead(d, depth + 1));
    return `${data.length}: ${items.join("; ")}${data.length > 5 ? "; and more" : ""}.`;
  }
  if (isObj(data)) {
    for (const k of ["say", "text", "summary"]) if (typeof data[k] === "string" && data[k]) return data[k] as string;
    if (depth > 1) return "";
    return Object.entries(data)
      .slice(0, 8)
      .map(([k, v]) => {
        const val = describeRead(v, depth + 1);
        return val ? `${k.replace(/_/g, " ")}: ${val}` : "";
      })
      .filter(Boolean)
      .join(depth ? ", " : ". ");
  }
  return "";
}

// ------------------------------------------------------------------ the Realtime session

/** A function call the model asked for (Realtime `response.function_call_arguments.done`). */
export interface FunctionCall {
  callId: string;
  name: string;
  /** Parsed args, or null when the model sent unreadable JSON. */
  args: Record<string, unknown> | null;
}

export type VoiceEffect =
  /** POST /api/ceo/tool {name, args, call_id}. */
  | { type: "tool"; call: FunctionCall }
  /** Answer the model without asking the gateway (not a registry tool, or unreadable args). */
  | { type: "reply"; callId: string; output: string }
  /** POST /api/ceo/confirm with via "voice" and what the CEO said. */
  | { type: "confirm"; proposalId: string; decision: "confirm" | "cancel"; heard: string }
  /** The CEO's own words, for the transcript. */
  | { type: "heard"; text: string }
  /** The agent's words, for the transcript (never matched against the grammar). */
  | { type: "agent"; text: string }
  | { type: "state"; state: "listening" | "thinking" | "speaking" | "idle" }
  | { type: "error"; message: string };

export interface VoiceState {
  /** Counts committed CEO utterances, in order. */
  seq: number;
  /** item_id → its place in that order. */
  committed: ReadonlyMap<string, number>;
  /** call_ids already handled: Realtime can repeat a call in two event shapes. */
  calls: ReadonlySet<string>;
  /** The open confirm card. */
  proposal: (Proposal & { armedAt: number; settling: boolean }) | null;
}

export const emptyVoice = (): VoiceState => ({ seq: 0, committed: new Map(), calls: new Set(), proposal: null });

/** Keep the item map bounded on a long session. */
const MAX_ITEMS = 200;

/** A proposal arrived: the card opens, and only utterances committed from now on can confirm it. */
export function armProposal(s: VoiceState, p: Proposal): VoiceState {
  return { ...s, proposal: { ...p, armedAt: s.seq, settling: false } };
}

/** The card closed (confirmed, cancelled, expired, STOP, or replaced). */
export const clearProposal = (s: VoiceState, proposalId?: string): VoiceState =>
  !s.proposal || (proposalId !== undefined && s.proposal.proposalId !== proposalId) ? s : { ...s, proposal: null };

/** A confirm is on its way to the gateway: no second one from a repeated "yes". */
export const settleProposal = (s: VoiceState): VoiceState => (s.proposal ? { ...s, proposal: { ...s.proposal, settling: true } } : s);

/**
 * Can the CEO's words decide the open card? Only words from after it appeared (a transcript of the
 * command that created it, "approve it", finishing late must not confirm it), before it expires,
 * and only once.
 */
export function decide(s: VoiceState, text: string, utteranceSeq: number | null, now: number): VoiceEffect | null {
  const p = s.proposal;
  if (!p || p.settling) return null;
  if (utteranceSeq === null || utteranceSeq <= p.armedAt) return null;
  const d = matchConfirm(text);
  if (!d) return null;
  if (d === "confirm" && now >= p.expiresAt) return null;
  return { type: "confirm", proposalId: p.proposalId, decision: d, heard: clipHeard(text) };
}

/** Realtime server event (data channel "oai-events") → new state and what the console does. */
export function voiceStep(s: VoiceState, raw: unknown, now: number): { state: VoiceState; effects: VoiceEffect[] } {
  if (!isObj(raw)) return { state: s, effects: [] };
  switch (raw.type) {
    case "input_audio_buffer.committed": {
      const id = str(raw.item_id);
      if (!id || s.committed.has(id)) return { state: s, effects: [] };
      const seq = s.seq + 1;
      const committed = new Map(s.committed).set(id, seq);
      if (committed.size > MAX_ITEMS) committed.delete(committed.keys().next().value as string);
      return { state: { ...s, seq, committed }, effects: [] };
    }
    case "conversation.item.input_audio_transcription.completed": {
      // The CEO's microphone, transcribed: the only Realtime text that can confirm.
      const text = str(raw.transcript).trim();
      if (!text) return { state: s, effects: [] };
      const at = s.committed.get(str(raw.item_id)) ?? null;
      const effects: VoiceEffect[] = [{ type: "heard", text }];
      const d = decide(s, text, at, now);
      if (d) effects.push(d);
      return { state: d ? settleProposal(s) : s, effects };
    }
    case "response.function_call_arguments.done":
      return call(s, str(raw.call_id), str(raw.name), raw.arguments);
    case "response.output_item.done": {
      const item = isObj(raw.item) ? raw.item : {};
      if (item.type !== "function_call") return { state: s, effects: [] };
      return call(s, str(item.call_id), str(item.name), item.arguments);
    }
    case "response.audio_transcript.done":
    case "response.output_audio_transcript.done": {
      // The model's words. Shown, never matched: the model saying "confirmed" does nothing.
      const text = str(raw.transcript).trim();
      return { state: s, effects: text ? [{ type: "agent", text }] : [] };
    }
    case "input_audio_buffer.speech_started":
      return { state: s, effects: [{ type: "state", state: "listening" }] };
    case "input_audio_buffer.speech_stopped":
      return { state: s, effects: [{ type: "state", state: "thinking" }] };
    case "output_audio_buffer.started":
    case "response.audio.delta":
    case "response.output_audio.delta":
      return { state: s, effects: [{ type: "state", state: "speaking" }] };
    case "output_audio_buffer.stopped":
    case "response.done":
      return { state: s, effects: [{ type: "state", state: "idle" }] };
    case "error": {
      const err = isObj(raw.error) ? raw.error : {};
      return { state: s, effects: [{ type: "error", message: str(err.message) || "voice error" }] };
    }
    default:
      return { state: s, effects: [] };
  }
}

function call(s: VoiceState, callId: string, name: string, rawArgs: unknown): { state: VoiceState; effects: VoiceEffect[] } {
  if (!callId || s.calls.has(callId)) return { state: s, effects: [] };
  const calls = new Set(s.calls).add(callId);
  if (calls.size > MAX_ITEMS) calls.delete(calls.values().next().value as string);
  const state = { ...s, calls };
  if (!isCeoTool(name)) {
    // Not in the registry (e.g. a "confirm" the model made up): the gateway is never asked.
    return { state, effects: [{ type: "reply", callId, output: toolOutput({ kind: "error", code: "unknown_tool", message: `${name || "that"} is not a CEO tool` }) }] };
  }
  let args: Record<string, unknown> | null = null;
  try {
    const parsed: unknown = typeof rawArgs === "string" ? (rawArgs.trim() ? JSON.parse(rawArgs) : {}) : rawArgs ?? {};
    args = isObj(parsed) ? parsed : null;
  } catch {
    args = null;
  }
  if (!args) return { state, effects: [{ type: "reply", callId, output: toolOutput({ kind: "error", code: "bad_args", message: "arguments must be a JSON object" }) }] };
  return { state, effects: [{ type: "tool", call: { callId, name, args } }] };
}

/** The client events that hand a function result back to the model and let it speak. */
export const functionOutputEvents = (callId: string, output: string) => [
  { type: "conversation.item.create", item: { type: "function_call_output", call_id: callId, output } },
  { type: "response.create" },
];

/** After a click or a spoken confirm: tell the model what the gateway did, so it can say so. */
export const outcomeEvents = (text: string) => [
  { type: "conversation.item.create", item: { type: "message", role: "system", content: [{ type: "input_text", text }] } },
  { type: "response.create" },
];

// ------------------------------------------------------------------ hands-free announcements

/**
 * New approvals and BLOCKED cases as short spoken lines ("Approval needed: memo for Team #7, $450.
 * Say approve or deny."). Only events after `afterId`, so turning hands-free on never reads the backlog.
 */
export function announcements(events: readonly UTaxEvent[], afterId: number, names: ReadonlyMap<string, string>, flagged: ReadonlySet<string>): string[] {
  const out: string[] = [];
  for (const e of events) {
    if (!isObj(e) || typeof e.id !== "number" || e.id <= afterId || e.status !== "ok") continue;
    const p = isObj(e.payload) ? e.payload : {};
    const who = e.case_id ? names.get(e.case_id) ?? "a case" : null;
    if (e.type === "approval.requested") {
      const what = oneLine(str(p.summary)) || `${str(p.action_type).replace(/_/g, " ") || "an action"}${who ? ` for ${who}` : ""}`;
      out.push(`Approval needed: ${what}. Say approve or deny.`);
    } else if (e.type === "case.blocked") {
      const why = e.case_id && flagged.has(e.case_id) ? "flagged by QA" : oneLine(str(p.reason)) || "it needs a human";
      out.push(`${who ?? "A case"} is blocked: ${why}.`);
    }
  }
  return out;
}

const oneLine = (s: string) => Array.from(s.replace(/\s+/g, " ").trim().replace(/[.\s]+$/, "")).slice(0, 160).join("");

/** Seconds left on a card, for the countdown. */
export const secondsLeft = (expiresAt: number, now: number) => Math.max(0, Math.ceil((expiresAt - now) / 1000));
