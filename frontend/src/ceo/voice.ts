// The CEO voice console (/ceo voice mode, contract v1.8 §5b and §8, design D28): pure logic,
// tested without a browser, a microphone or a gateway.
//
// Safety rules held here, in code (v1.8.1):
// - The model can only reach POST /api/ceo/tool with a tool from the registry below. A "start"
//   tool comes back as a proposal; nothing is carried out.
// - A proposal is confirmed only by the CEO's own words, matched by the same grammar as the
//   gateway (the whole utterance is a short confirm phrase; never a question, never the
//   readback's own words), or by a click. Over Realtime the words must come from
//   conversation.item.input_audio_transcription.completed for speech that STARTED after the
//   readback had played and outside every stretch of agent audio (plus a tail for the room's
//   echo), for as long as the card is open. Model output never confirms: its transcripts are
//   never matched.

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

/** `heard` travels to the gateway clipped to this (contract: ≤ 200 chars), and only the clipped text is matched. */
export const MAX_HEARD = 200;

export const clipHeard = (s: string) => Array.from(s.replace(/\s+/g, " ").trim()).slice(0, MAX_HEARD).join("");

/** Lower case, punctuation gone (an apostrophe kept), one space between words: the gateway's `_speech`. */
const speech = (s: string) =>
  s
    .toLowerCase()
    .replace(/[’`]/g, "'")
    .replace(/[^a-z0-9' ]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

// The gateway's grammar (backend app/ceo_tools.py heard_confirms / heard_cancels), mirrored so the
// console never sends a confirm the gateway would refuse, and stricter where it fails closed.
const FILLER = "(?:(?:ok|okay|alright|so|um|uh|yes|yeah|please) )*";
const CONFIRM_RE = new RegExp(`^${FILLER}(?:confirm|yes|confirm it|do it|go ahead|yes please)(?: please)?$`);
const APPROVE_IT_RE = new RegExp(`^${FILLER}approve it(?: please)?$`);
/** The contract's cancel words plus negations ("can't", "won't": stricter than the gateway). */
const CANCEL_RE = /\b(no|nope|cancel|stop|wait|hold on|hang on|never ?mind|don'?t|do not|not|never|can'?t|cannot|won'?t)\b/;
/** The readback's own words and the model asking are never the CEO confirming. */
const ECHO_RE = /\bsay confirm\b|\b(?:shall|should) i\b|\bdo you want\b|\bwould you like\b|\bwant me to\b/;
/** Only a cancel and nothing else: anything more ("stop everything", "stop posting") is also a command. */
const BARE_CANCEL_RE = /^(?:(?:ok|okay|oh|please|no) )*(?:no|nope|cancel|cancel it|cancel that|stop|stop it|wait|hold on|hang on|never ?mind)(?: please| wait| thanks)?$/;

export interface ConfirmContext {
  /** The proposal's tool: "approve it" confirms only an approve. */
  tool?: string | null;
  /** Its readback: a piece of it heard back (an order that says "go ahead") never confirms. */
  readback?: string;
}

/**
 * The CEO's words → confirm, cancel, or nothing. A cancel word or a negation always wins
 * ("yes, wait" cancels; "I can't confirm that" cancels). A confirm must be the whole utterance
 * (fillers allowed), not a question, not an echo, not a piece of the readback.
 */
export function matchConfirm(text: string, ctx: ConfirmContext = {}): "confirm" | "cancel" | null {
  const clipped = clipHeard(text);
  const w = speech(clipped);
  if (!w) return null;
  if (CANCEL_RE.test(w)) return "cancel";
  if (clipped.includes("?") || ECHO_RE.test(w)) return null;
  const body = speech((ctx.readback ?? "").replace(/\s*say confirm\.?\s*$/i, ""));
  if (body && ` ${body} `.includes(` ${w} `)) return null;
  if (CONFIRM_RE.test(w)) return "confirm";
  if (ctx.tool === "approve" && APPROVE_IT_RE.test(w)) return "confirm";
  return null;
}

/** A cancel with nothing else in it. "Stop everything" cancels the card AND goes on as a command. */
export const isBareCancel = (text: string) => BARE_CANCEL_RE.test(speech(clipHeard(text)));

// ------------------------------------------------------------------ gateway answers

export interface Proposal {
  proposalId: string;
  readback: string;
  /** ms since epoch; the card counts down to it and a confirm after it is refused locally. */
  expiresAt: number;
  /** The start tool behind it, when known (a model function call): "approve it" needs "approve". */
  tool?: string | null;
}

export type ToolResult =
  | { kind: "read"; data: unknown }
  | { kind: "done"; summary: string }
  | ({ kind: "proposal" } & Proposal)
  | { kind: "unknown"; say: string }
  /** /api/ceo/command matched a confirm or cancel word to the gateway's newest open proposal. */
  | { kind: "pending"; decision: "confirm" | "cancel"; proposalId: string; readback: string };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown) => (typeof v === "string" ? v : "");

/** A proposal's lifetime (contract: 60 s), and the fallback when the wire's expiry can't be used. */
export const PROPOSAL_TTL_MS = 60_000;

/**
 * Local expiry from the wire's `expires_at`. The gateway's clock is the authority (it answers
 * 409 expired), so a client clock running ahead must not kill a fresh card: an expiry that is
 * already past, unreadable or further than 60 s out falls back to now + 60 s.
 */
export function localExpiry(expiresAt: unknown, now: number): number {
  const t = Date.parse(str(expiresAt));
  const left = t - now;
  return Number.isFinite(left) && left > 0 && left <= PROPOSAL_TTL_MS ? t : now + PROPOSAL_TTL_MS;
}

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
      return { kind: "proposal", proposalId: id, readback, expiresAt: localExpiry(raw.expires_at, now) };
    }
    case "confirm":
    case "cancel": {
      const id = str(raw.proposal_id);
      return id ? { kind: "pending", decision: raw.kind, proposalId: id, readback: str(raw.readback) } : null;
    }
    default:
      return null;
  }
}

/** What the model hears back for a function call (a string; the model speaks from it). Never a proposal_id. */
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
    case "pending":
      return clipJson({ ok: false, say: "The CEO must confirm on the console." });
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
  /** POST /api/ceo/confirm with via "voice" and what the CEO said. The reducer has claimed the card. */
  | { type: "confirm"; proposalId: string; decision: "confirm" | "cancel"; heard: string }
  /** A confirm phrase heard while the agent was talking (or right after): not taken, say it again. */
  | { type: "ignored"; text: string }
  /** The CEO's own words, for the transcript. */
  | { type: "heard"; text: string }
  /** The agent's words, for the transcript (never matched against the grammar). */
  | { type: "agent"; text: string }
  | { type: "state"; state: "listening" | "thinking" | "speaking" | "idle" }
  /** A response started or ended (the console sends one response.create at a time). */
  | { type: "response"; active: boolean }
  | { type: "error"; message: string; code: string };

/** Speech that starts this soon after agent audio stops may be the room's echo of it. */
export const ECHO_TAIL_MS = 800;
const MAX_WINDOWS = 12;

/** The open confirm card, as the reducer tracks it. */
export interface OpenProposal extends Proposal {
  settling: boolean;
  /** When the card opened (ms). */
  openedAt: number;
  /**
   * When the readback had been heard (ms): its audio stopped, or a text-only reply finished.
   * Null until then: no spoken confirm before the CEO has heard what they confirm.
   */
  heardAt: number | null;
  /** The Realtime response reading it back; "local" when the console reads it with browser speech. */
  replyId: string | null;
  /** That response played audio (then only the end of the audio arms the card). */
  replyAudio: boolean;
}

export interface VoiceState {
  /** item_id → when (ms) the CEO's speech for it started. */
  started: ReadonlyMap<string, number>;
  /** call_ids already handled: Realtime can repeat a call in two event shapes. */
  calls: ReadonlySet<string>;
  proposal: OpenProposal | null;
  /** Agent audio in the room: the model's voice and the browser's own speech. */
  audio: {
    /** Sources playing now ("model", "browser"), with when each started. */
    playing: ReadonlyMap<string, number>;
    /** Recent finished stretches of agent audio, [from, to] in ms. */
    windows: readonly (readonly [number, number])[];
    /** This session reports output_audio_buffer.* (WebRTC): only those end the model's audio. */
    buffered: boolean;
  };
}

export const emptyVoice = (): VoiceState => ({
  started: new Map(),
  calls: new Set(),
  proposal: null,
  audio: { playing: new Map(), windows: [], buffered: false },
});

/** Keep the item maps bounded on a long session. */
const MAX_ITEMS = 200;

/**
 * A proposal arrived: the card opens. `readBy`: who reads it aloud. "model" (a function call:
 * the next agent reply), "browser" (the console speaks it), or "none" (typing with no voice
 * line: nothing to wait for, so typed or browser-heard words decide it at once).
 */
export function armProposal(s: VoiceState, p: Proposal, now: number, readBy: "model" | "browser" | "none" = "model"): VoiceState {
  return {
    ...s,
    proposal: {
      ...p,
      settling: false,
      openedAt: now,
      heardAt: readBy === "none" ? now : null,
      replyId: readBy === "browser" ? "local" : null,
      replyAudio: false,
    },
  };
}

/** The console finished reading the card aloud (or had no voice to read it with). */
export function readbackHeard(s: VoiceState, proposalId: string, now: number): VoiceState {
  const p = s.proposal;
  return p && p.proposalId === proposalId && p.heardAt === null ? { ...s, proposal: { ...p, heardAt: now } } : s;
}

/** The card closed (confirmed, cancelled, expired, STOP, reset, or replaced). */
export const clearProposal = (s: VoiceState, proposalId?: string): VoiceState =>
  !s.proposal || (proposalId !== undefined && s.proposal.proposalId !== proposalId) ? s : { ...s, proposal: null };

/** A confirm is on its way to the gateway: no second one from a repeated "yes" or a double click. */
export const settleProposal = (s: VoiceState): VoiceState => (s.proposal ? { ...s, proposal: { ...s.proposal, settling: true } } : s);

/**
 * The gate every send goes through. `claimed`: the reducer already decided and marked this card
 * settling (a spoken confirm), so it is sent for exactly that id. Otherwise (a click, or typed
 * words) the card is claimed here: never twice, never a confirm after expiry.
 */
export function claimCard(
  s: VoiceState,
  decision: "confirm" | "cancel",
  now: number,
  claimed?: string,
): { state: VoiceState; proposal: OpenProposal } | null {
  const p = s.proposal;
  if (!p) return null;
  if (claimed !== undefined) return p.proposalId === claimed && p.settling ? { state: s, proposal: p } : null;
  if (p.settling || (decision === "confirm" && now >= p.expiresAt)) return null;
  const state = settleProposal(s);
  return { state, proposal: state.proposal as OpenProposal };
}

/** Agent audio started or stopped (the model's voice, or the browser speaking). */
export function agentAudio(s: VoiceState, source: string, on: boolean, now: number): VoiceState {
  const playing = new Map(s.audio.playing);
  if (on) {
    if (playing.has(source)) return s;
    playing.set(source, now);
    return { ...s, audio: { ...s.audio, playing } };
  }
  const from = playing.get(source);
  if (from === undefined) return s;
  playing.delete(source);
  const windows = [...s.audio.windows, [from, now] as const].slice(-MAX_WINDOWS);
  return { ...s, audio: { ...s.audio, playing, windows } };
}

/** Did speech that started at `at` overlap agent audio (or its echo tail)? */
function duringAgentAudio(s: VoiceState, at: number): boolean {
  for (const since of s.audio.playing.values()) if (at >= since) return true;
  return s.audio.windows.some(([from, to]) => at >= from && at <= to + ECHO_TAIL_MS);
}

/**
 * Can the CEO's words decide the open card? A cancel word cancels from any speech that started
 * after the card opened. A confirm needs speech that started after the readback was heard (plus
 * the echo tail), outside every stretch of agent audio, an unexpired card, and only once.
 */
export function decide(s: VoiceState, text: string, speechAt: number | null, now: number): VoiceEffect | null {
  const p = s.proposal;
  if (!p || p.settling || speechAt === null || speechAt < p.openedAt) return null;
  const d = matchConfirm(text, p);
  if (!d) return null;
  if (d === "confirm") {
    if (now >= p.expiresAt) return null;
    if (p.heardAt === null || speechAt <= p.heardAt + ECHO_TAIL_MS || duringAgentAudio(s, speechAt)) return { type: "ignored", text };
  }
  return { type: "confirm", proposalId: p.proposalId, decision: d, heard: clipHeard(text) };
}

/** Words typed, or heard by the browser (which never listens while it speaks): grammar only, no audio timing. */
export function decideTyped(s: VoiceState, text: string, now: number): VoiceEffect | null {
  const p = s.proposal;
  if (!p || p.settling) return null;
  const d = matchConfirm(text, p);
  if (!d || (d === "confirm" && now >= p.expiresAt)) return null;
  return { type: "confirm", proposalId: p.proposalId, decision: d, heard: clipHeard(text) };
}

const bounded = <K, V>(m: Map<K, V>) => {
  if (m.size > MAX_ITEMS) m.delete(m.keys().next().value as K);
  return m;
};

const responseId = (r: unknown) => (isObj(r) ? str(r.id) : "");

/** Did a finished response say anything (a message), as opposed to only calling functions? */
function spoke(r: unknown): { message: boolean; audio: boolean } {
  const out = isObj(r) && Array.isArray(r.output) ? r.output : [];
  let message = false;
  let audio = false;
  for (const item of out) {
    if (!isObj(item) || item.type !== "message") continue;
    message = true;
    for (const c of Array.isArray(item.content) ? item.content : []) if (isObj(c) && /audio/.test(str(c.type))) audio = true;
  }
  return { message, audio };
}

/** The reading-back response's audio ended: from now (plus the echo tail) the CEO's speech can confirm. */
function readbackEnded(s: VoiceState, rid: string, now: number): VoiceState {
  const p = s.proposal;
  if (!p || p.heardAt !== null || !p.replyId || p.replyId === "local" || (rid && rid !== p.replyId) || !p.replyAudio) return s;
  return { ...s, proposal: { ...p, heardAt: now } };
}

/** Realtime server event (data channel "oai-events") → new state and what the console does. */
export function voiceStep(s: VoiceState, raw: unknown, now: number): { state: VoiceState; effects: VoiceEffect[] } {
  if (!isObj(raw)) return { state: s, effects: [] };
  const p = s.proposal;
  const waiting = p !== null && p.heardAt === null && p.replyId !== "local";
  const rid = str(raw.response_id);
  const markAudio = (st: VoiceState) => {
    const q = st.proposal;
    return q && waiting && q.replyId && !q.replyAudio && (!rid || rid === q.replyId) ? { ...st, proposal: { ...q, replyAudio: true } } : st;
  };
  switch (raw.type) {
    case "input_audio_buffer.speech_started": {
      const id = str(raw.item_id);
      const state = id && !s.started.has(id) ? { ...s, started: bounded(new Map(s.started).set(id, now)) } : s;
      return { state, effects: [{ type: "state", state: "listening" }] };
    }
    case "conversation.item.input_audio_transcription.completed": {
      // The CEO's microphone, transcribed: the only Realtime text that can confirm.
      const text = str(raw.transcript).trim();
      if (!text) return { state: s, effects: [] };
      const effects: VoiceEffect[] = [{ type: "heard", text }];
      const d = decide(s, text, s.started.get(str(raw.item_id)) ?? null, now);
      if (d) effects.push(d);
      return { state: d?.type === "confirm" ? settleProposal(s) : s, effects };
    }
    case "response.created": {
      // The first response after the card opened that says something is the one reading it back.
      const id = responseId(raw.response);
      const state = waiting && p && !p.replyId && id ? { ...s, proposal: { ...p, replyId: id } } : s;
      return { state, effects: [{ type: "response", active: true }] };
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
      return { state: markAudio(s), effects: text ? [{ type: "agent", text }] : [] };
    }
    case "response.audio_transcript.delta":
    case "response.output_audio_transcript.delta":
    case "response.audio.done":
    case "response.output_audio.done":
      return { state: markAudio(s), effects: [] };
    case "input_audio_buffer.speech_stopped":
      return { state: s, effects: [{ type: "state", state: "thinking" }] };
    case "output_audio_buffer.started": {
      const st = markAudio(agentAudio({ ...s, audio: { ...s.audio, buffered: true } }, "model", true, now));
      return { state: st, effects: [{ type: "state", state: "speaking" }] };
    }
    case "response.audio.delta":
    case "response.output_audio.delta":
      return { state: markAudio(agentAudio(s, "model", true, now)), effects: [{ type: "state", state: "speaking" }] };
    case "output_audio_buffer.stopped":
    case "output_audio_buffer.cleared": {
      // The model's audio ended in the room (stopped), or the CEO cut it off (cleared, a barge-in:
      // that speech started during the audio, so it still can't confirm).
      const st = readbackEnded(agentAudio(s, "model", false, now), rid, now);
      return { state: st, effects: [{ type: "state", state: "idle" }] };
    }
    case "response.done": {
      const r = raw.response;
      const id = responseId(r);
      let st = s.audio.buffered ? s : agentAudio(s, "model", false, now); // no buffer events: the response's end is the audio's end
      const q = st.proposal;
      if (q && q.heardAt === null && q.replyId && q.replyId !== "local" && q.replyId === id) {
        const said = spoke(r);
        if (said.audio || q.replyAudio) {
          st = { ...st, proposal: { ...q, replyAudio: true } };
          if (!s.audio.buffered) st = readbackEnded(st, id, now);
        } else if (said.message) st = { ...st, proposal: { ...q, heardAt: now } }; // a text-only readback
        else st = { ...st, proposal: { ...q, replyId: null } }; // only function calls: the readback is still to come
      }
      return { state: st, effects: [{ type: "state", state: "idle" }, { type: "response", active: false }] };
    }
    case "error": {
      const err = isObj(raw.error) ? raw.error : {};
      return { state: s, effects: [{ type: "error", message: str(err.message) || "voice error", code: str(err.code) }] };
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

/** A function result for the model. The console asks for a response separately, one at a time. */
export const functionOutputItem = (callId: string, output: string) => ({
  type: "conversation.item.create",
  item: { type: "function_call_output", call_id: callId, output },
});

/**
 * v1.8.1: after a confirm, a cancel or an expiry, tell the model what actually happened (a system
 * item; the console then asks for a response) so the agent says it. Never a proposal_id.
 */
export const actionResultItem = (summary: string) => ({
  type: "conversation.item.create",
  item: { type: "message", role: "system", content: [{ type: "input_text", text: `Action result: ${oneLine(summary)}` }] },
});

// ------------------------------------------------------------------ hands-free announcements

/**
 * New approvals and BLOCKED cases as short spoken lines ("Approval needed: memo for Team #7, $450.
 * Say approve or deny."). Only events after `afterId`, so turning hands-free on never reads the
 * backlog. A flagged case (known already, or flagged in this same batch) is never read out.
 */
export function announcements(events: readonly UTaxEvent[], afterId: number, names: ReadonlyMap<string, string>, flagged: ReadonlySet<string>): string[] {
  const hidden = new Set(flagged);
  for (const e of events) if (isObj(e) && e.type === "case.flagged" && typeof e.case_id === "string") hidden.add(e.case_id);
  const out: string[] = [];
  for (const e of events) {
    if (!isObj(e) || typeof e.id !== "number" || e.id <= afterId || e.status !== "ok") continue;
    const p = isObj(e.payload) ? e.payload : {};
    const who = e.case_id ? names.get(e.case_id) ?? "a case" : null;
    const isHidden = e.case_id !== null && hidden.has(e.case_id);
    if (e.type === "approval.requested") {
      const kind = str(p.action_type).replace(/_/g, " ") || "an action";
      const what = isHidden ? `${kind} for ${who}, flagged by QA, content hidden` : oneLine(str(p.summary)) || `${kind}${who ? ` for ${who}` : ""}`;
      out.push(`Approval needed: ${what}. Say approve or deny.`);
    } else if (e.type === "case.blocked") {
      const why = isHidden ? "flagged by QA" : oneLine(str(p.reason)) || "it needs a human";
      out.push(`${who ?? "A case"} is blocked: ${why}.`);
    }
  }
  return out;
}

const oneLine = (s: string) => Array.from(s.replace(/\s+/g, " ").trim().replace(/[.\s]+$/, "")).slice(0, 160).join("");

/** Seconds left on a card, for the countdown. */
export const secondsLeft = (expiresAt: number, now: number) => Math.max(0, Math.ceil((expiresAt - now) / 1000));
