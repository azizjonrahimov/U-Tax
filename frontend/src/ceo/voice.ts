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
import { ECHO_TAIL_MS } from "@/lib/tts";

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

// Contract v1.8.4/v1.8.5's spoken grammar, identical to the gateway's (backend app/ceo_tools.py
// heard_confirms / heard_cancels), so the console never sends a confirm the gateway would refuse.
const FILLER = "(?:(?:ok|okay|alright|so|um|uh|yes|yeah|please) )*";
const CONFIRM_RE = new RegExp(`^${FILLER}(?:confirm|yes|confirm it|do it|go ahead|yes please)(?: please)?$`);
const APPROVE_IT_RE = new RegExp(`^${FILLER}approve it(?: please)?$`);
/** v1.8.4's cancel words, exactly ("I can't confirm that" is no cancel and no confirm: not_a_confirm). */
const CANCEL_RE = /\b(no|nope|cancel|stop|wait|hold on|never ?mind|don'?t|do not|not|never)\b/;
/** The readback's own words and the model asking are never the CEO confirming. */
const ECHO_RE = /\bsay confirm\b|\b(?:shall|should) i\b|\bdo you want\b|\bwould you like\b|\bwant me to\b/;
/** Only a cancel and nothing else: anything more ("stop everything", "stop posting") is also a command. */
const BARE_CANCEL_RE = /^(?:(?:ok|okay|oh|please|no) )*(?:no|nope|cancel|cancel it|cancel that|stop|stop it|wait|hold on|never ?mind)(?: please| wait| thanks)?$/;

export interface ConfirmContext {
  /** The proposal's tool: "approve it" confirms only an approve. */
  tool?: string | null;
  /** Its readback: a piece of it heard back (an order that says "go ahead") never confirms. */
  readback?: string;
}

/**
 * v1.8.5: a question mark of any script, a ';', or a letter outside a-z: the grammar can't read
 * it, so it never confirms (a "no" in another script would otherwise be stripped away).
 */
const unreadable = (s: string) => Array.from(s).some((ch) => "?¿？؟;".includes(ch) || (ch.charCodeAt(0) > 127 && /\p{L}/u.test(ch)));

const readbackBody = (readback: string | undefined) => speech((readback ?? "").replace(/\s*say confirm\.?\s*$/i, ""));

/**
 * The CEO's words → confirm, cancel, "blocked" (a confirm phrase the rules refuse: a question,
 * an echo, the readback's own words, unreadable text), or nothing. A cancel word always wins.
 */
export function classifyConfirm(text: string, ctx: ConfirmContext = {}): "confirm" | "cancel" | "blocked" | null {
  const clipped = clipHeard(text);
  const w = speech(clipped);
  if (!w) return null;
  if (CANCEL_RE.test(w)) return "cancel";
  const phrase = CONFIRM_RE.test(w) || (ctx.tool === "approve" && APPROVE_IT_RE.test(w));
  const body = readbackBody(ctx.readback);
  if (unreadable(clipped) || ECHO_RE.test(w) || (body && ` ${body} `.includes(` ${w} `))) return phrase || /\bconfirm\b/.test(w) ? "blocked" : null;
  return phrase ? "confirm" : null;
}

/**
 * The CEO's words → confirm, cancel, or nothing (contract v1.8.4 + v1.8.5, the gateway's grammar).
 * A confirm must be the whole utterance (fillers allowed), never a question, an echo, a piece of
 * the readback, or text the grammar can't read.
 */
export function matchConfirm(text: string, ctx: ConfirmContext = {}): "confirm" | "cancel" | null {
  const c = classifyConfirm(text, ctx);
  return c === "blocked" ? null : c;
}

/** "approve it" (the v1.8.4 command form that confirms an approve proposal). */
export const isApproveIt = (text: string) => APPROVE_IT_RE.test(speech(clipHeard(text)));

/**
 * Did the agent's reply carry the whole readback? Most of its words (numbers aside: speech
 * writes "$450" as words), and no question after it. Otherwise the CEO hasn't heard what
 * they'd confirm, and only a click or typing can.
 */
export function readbackCovered(said: string, readback: string): boolean {
  const want = readbackBody(readback).split(" ").filter(Boolean);
  if (!want.length) return true;
  const got = speech(said).split(" ").filter(Boolean);
  // In order: the longest common subsequence of the readback's words and the reply's.
  const lcs = Array.from({ length: want.length + 1 }, () => new Array<number>(got.length + 1).fill(0));
  for (let i = want.length - 1; i >= 0; i--)
    for (let j = got.length - 1; j >= 0; j--) lcs[i][j] = want[i] === got[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
  const matched = new Set<number>(); // indexes of `want` in that subsequence
  for (let i = 0, j = 0; i < want.length && j < got.length; ) {
    if (want[i] === got[j]) {
      matched.add(i);
      i += 1;
      j += 1;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) i += 1;
    else j += 1;
  }
  // The words that decide what runs must all be there, in place: the verb, every figure and
  // target (digits, a candidate letter), and every status word ("READY", "AUTO").
  const caps = new Set(Array.from(readback.matchAll(/\b[A-Z][A-Z_]+\b/g), (m) => m[0].toLowerCase().split("_")).flat());
  const key = (w: string, i: number) => i === 0 || /\d/.test(w) || /^[a-z]$/.test(w) || caps.has(w);
  if (want.some((w, i) => key(w, i) && !matched.has(i))) return false;
  // A negation the readback doesn't have turns it around ("I will not approve…").
  const added = got.filter((w) => CANCEL_RE.test(w) && !want.includes(w));
  if (added.length) return false;
  const tail = said.trim().split(/(?<=[.!])\s+/).at(-1) ?? "";
  return matched.size / want.length >= 0.9 && !/[?¿？]\s*$/.test(tail);
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
  /** v1.13: a line to say and nothing else (a greeting, or anything but confirm/cancel while a card is open). */
  | { kind: "say"; say: string }
  /** /api/ceo/command matched a confirm or cancel word to the gateway's newest open proposal. */
  | { kind: "pending"; decision: "confirm" | "cancel"; proposalId: string; readback: string };

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown) => (typeof v === "string" ? v : "");

/** Words as heard: lower case, no punctuation, single spaces. */
const plainWords = (t: string) => t.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, " ").replace(/\s+/g, " ").trim();

/** Words compared as heard: case, punctuation and spacing don't make a line "new". */
export const sameWords = (a: string, b: string) => plainWords(a) === plainWords(b);

/**
 * D33 voice loop guard: the browser's recognizer can deliver one utterance twice (Chrome's
 * continuous mode, and across a restart). A final identical to the last utterance within this
 * window (counted from the later of that utterance and the ear's current session start) is the
 * same utterance, never a second order. A person repeating themselves takes longer than this.
 */
export const REPEAT_HEARD_MS = 2000;
/** A reply to the same command, identical and this recent after it finished, is not said again. */
export const REPEAT_SAID_MS = 3000;
/** A final that repeats words the console said this recently is the room's echo of it. */
export const ECHO_WINDOW_MS = 8000;

/** Fillers and address words before a command ("okay, stop everything", "U-Tax, pause everything"). */
const STOP_FILLER = String.raw`(?:(?:ok|okay|hey|so|um|uh|please|u\s*tax)\s+)*`;
const STOP_EVERYTHING_RE = new RegExp(String.raw`^${STOP_FILLER}(?:stop|pause|halt)\s+(?:everything|all(?:\s+of\s+it)?|the\s+firm)(?:\s+(?:now|please|right\s+now))?$`, "u");
const STOP_COMMAND_RE = new RegExp(String.raw`^${STOP_FILLER}(?:stop|pause|halt)\b`, "u");

/**
 * The whole utterance is "stop everything" (fillers allowed): the console's STOP button path,
 * never a command. "Stop all posts" or "pause everything except payroll" are not this.
 */
export const isStopEverything = (text: string) => STOP_EVERYTHING_RE.test(plainWords(text));
/** A stop-kind command ("stop posting", "pause emails"): v1.13.1 runs it at once and it cancels the open card. */
export const isStopCommand = (text: string) => STOP_COMMAND_RE.test(plainWords(text));

/** Whether `text` (heard or to be said at `now`) is a new line after `last`. */
export function isFresh(last: { text: string; at: number } | null, text: string, now: number, windowMs: number): boolean {
  return !last || now - last.at > windowMs || !sameWords(last.text, text);
}

/** What the ear remembers across recognizer sessions: the last utterance and the console's recent lines. */
export interface EarMemory {
  last: { text: string; at: number } | null;
  said: { text: string; at: number }[];
}
/** One recognizer session: when it started, and the transcript handled at each result index. */
export interface EarSession {
  start: number;
  handled: Map<number, string>;
}
export const newEarMemory = (): EarMemory => ({ last: null, said: [] });
export const newEarSession = (now: number): EarSession => ({ start: now, handled: new Map() });

/** Remember a line the console started saying (for the self-echo check); keeps the last few. */
export function rememberSaid(memory: EarMemory, text: string, now: number) {
  memory.said = [...memory.said.filter((l) => now - l.at <= ECHO_WINDOW_MS), { text, at: now }].slice(-6);
}

export type EarDrop = "repeat" | "echo";

/**
 * Which finals of one recognizer result event are new utterances. A final is dropped when it is:
 * - the same transcript at an index this session already handled (a re-delivery);
 * - the last utterance again within REPEAT_HEARD_MS of the later of that utterance and this
 *   session's start (a re-delivery across a restart);
 * - the console's own recent line (its words, at least three, run inside a line said in the last
 *   ECHO_WINDOW_MS, or match a short line whole).
 * Mutates `session` and `memory`; returns what to act on and what was dropped.
 */
export function earFinals(
  results: readonly { index: number; text: string; isFinal: boolean }[],
  session: EarSession,
  memory: EarMemory,
  now: number,
): { take: string[]; dropped: { text: string; why: EarDrop }[] } {
  const take: string[] = [];
  const dropped: { text: string; why: EarDrop }[] = [];
  for (const r of results) {
    const text = r.text.trim();
    if (!r.isFinal || !text) continue;
    if (session.handled.get(r.index) === plainWords(text)) continue;
    session.handled.set(r.index, plainWords(text));
    const last = memory.last;
    if (last && sameWords(last.text, text) && now - Math.max(last.at, session.start) <= REPEAT_HEARD_MS) {
      dropped.push({ text, why: "repeat" });
      continue;
    }
    const heard = plainWords(text);
    const echo = memory.said.some((l) => {
      if (now - l.at > ECHO_WINDOW_MS) return false;
      const said = plainWords(l.text);
      return heard === said || (heard.split(" ").length >= 3 && ` ${said} `.includes(` ${heard} `));
    });
    if (echo) {
      dropped.push({ text, why: "echo" });
      continue;
    }
    memory.last = { text, at: now };
    take.push(text);
  }
  return { take, dropped };
}

/**
 * The reply-line guard: a reply to the same command (same key), identical, within REPEAT_SAID_MS
 * of the last one finishing, is not said again. Lines without a key (readbacks, alerts, notices)
 * are always said: two alerts can share words, and a readback must really play to arm a confirm.
 */
export function shouldSay(last: { key: string; text: string; at: number } | null, key: string | undefined, text: string, now: number): boolean {
  return !key || !last || last.key !== key || !sameWords(last.text, text) || now - last.at > REPEAT_SAID_MS;
}

/** A proposal's lifetime (contract: 60 s), and the fallback when the wire's expiry can't be used. */
export const PROPOSAL_TTL_MS = 60_000;

/** The local card closes this much before the gateway's 60 s, for the round trip. */
const EXPIRY_MARGIN_MS = 2000;

/**
 * Local expiry. Every proposal lives 60 s from when the gateway made it, which is when its
 * answer arrives here, so the console counts 60 s from receipt (less a margin) on its own clock.
 * The wire's `expires_at` is on the gateway's clock: a client clock that is off, even by a few
 * seconds, must not shorten the card. The gateway's 409 expired stays the authority.
 */
export function localExpiry(_expiresAt: unknown, now: number): number {
  return now + PROPOSAL_TTL_MS - EXPIRY_MARGIN_MS;
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
    case "say":
      return { kind: "say", say: str(raw.say) || "I'm listening." };
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
    case "say":
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
    // An activity line: "case blocked, Team #7" (ids and timestamps are for the model, not the ear).
    if (typeof data.type === "string" && depth > 0) {
      const what = data.type.replace(/[._]/g, " ");
      const who = typeof data.case === "string" ? data.case : typeof data.masked_name === "string" ? data.masked_name : "";
      return who ? `${what}, ${who}` : what;
    }
    return Object.entries(data)
      .filter(([k]) => !/^(id|ts|last_event_id)$|_id$/.test(k))
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
  /** A confirm phrase not taken (heard during agent audio, before the readback, or blocked by the rules): say it again, or click. */
  | { type: "ignored"; text: string }
  /** The agent's reply didn't carry the whole readback: this card can be confirmed by click or typing only, until it is read in full. */
  | { type: "unread" }
  /** The CEO's own words, for the transcript. */
  | { type: "heard"; text: string }
  /** The agent's words, for the transcript (never matched against the grammar). */
  | { type: "agent"; text: string }
  | { type: "state"; state: "listening" | "thinking" | "speaking" | "idle" }
  /** A response started or ended (the console sends one response.create at a time). */
  | { type: "response"; active: boolean }
  | { type: "error"; message: string; code: string };

/** Speech that starts this soon after agent audio stops may be the room's echo of it (one value for every page: §5c). */
export { ECHO_TAIL_MS };
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
  /** What that response said (its transcript): it must carry the readback. */
  replyText: string | null;
  /** When its audio stopped playing (ms). */
  replyEndedAt: number | null;
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
  };
}

export const emptyVoice = (): VoiceState => ({
  started: new Map(),
  calls: new Set(),
  proposal: null,
  audio: { playing: new Map(), windows: [] },
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
      replyText: null,
      replyEndedAt: null,
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
  onScreen?: string,
): { state: VoiceState; proposal: OpenProposal } | null {
  const p = s.proposal;
  if (!p) return null;
  if (claimed !== undefined) return p.proposalId === claimed && p.settling ? { state: s, proposal: p } : null;
  // A click acts on the card it was made on: never on one that replaced it under the pointer.
  if (onScreen !== undefined && p.proposalId !== onScreen) return null;
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
  const d = classifyConfirm(text, p);
  if (!d) return null;
  if (d === "blocked") return { type: "ignored", text };
  if (d === "confirm") {
    if (now >= p.expiresAt) return null;
    if (p.heardAt === null || speechAt <= p.heardAt + ECHO_TAIL_MS || duringAgentAudio(s, speechAt)) return { type: "ignored", text };
  }
  return { type: "confirm", proposalId: p.proposalId, decision: d, heard: clipHeard(text) };
}

/**
 * Words typed, or heard by the browser (which never listens while it speaks): the grammar, no
 * audio timing. `spoken`: heard by the browser, so like any spoken confirm it needs the readback
 * to have been heard first (typing doesn't: the card is on screen).
 */
export function decideTyped(s: VoiceState, text: string, now: number, spoken = false): VoiceEffect | null {
  const p = s.proposal;
  if (!p || p.settling) return null;
  const d = classifyConfirm(text, p);
  if (!d) return null;
  // Browser-heard words are speech like any other: not before the readback was heard, not over agent audio.
  if (d === "blocked" || (d === "confirm" && spoken && (p.heardAt === null || duringAgentAudio(s, now)))) return { type: "ignored", text };
  if (d === "confirm" && now >= p.expiresAt) return null;
  return { type: "confirm", proposalId: p.proposalId, decision: d, heard: clipHeard(text) };
}

const bounded = <K, V>(m: Map<K, V>) => {
  if (m.size > MAX_ITEMS) m.delete(m.keys().next().value as K);
  return m;
};

const responseId = (r: unknown) => (isObj(r) ? str(r.id) : "");

/** What a finished response said: a message (and its words), audio, or only function calls. */
function spoke(r: unknown): { message: boolean; audio: boolean; text: string } {
  const out = isObj(r) && Array.isArray(r.output) ? r.output : [];
  let message = false;
  let audio = false;
  const text: string[] = [];
  for (const item of out) {
    if (!isObj(item) || item.type !== "message") continue;
    message = true;
    for (const c of Array.isArray(item.content) ? item.content : []) {
      if (!isObj(c)) continue;
      if (/audio/.test(str(c.type))) audio = true;
      text.push(str(c.text) || str(c.transcript));
    }
  }
  return { message, audio, text: text.join(" ").trim() };
}

/** The reply is no readback after all (cut off, failed, only calls, or not the whole readback): wait for the next one. */
const resetReply = (p: OpenProposal): OpenProposal => ({ ...p, replyId: null, replyAudio: false, replyText: null, replyEndedAt: null });

/**
 * Arm the card when the reading-back reply both finished playing and carried the readback. A
 * reply that didn't carry it leaves the card unarmed (click or typing only) and says so.
 */
function tryArm(s: VoiceState): { state: VoiceState; unread: boolean } {
  const p = s.proposal;
  if (!p || p.heardAt !== null || p.replyText === null || p.replyEndedAt === null) return { state: s, unread: false };
  if (readbackCovered(p.replyText, p.readback)) return { state: { ...s, proposal: { ...p, heardAt: p.replyEndedAt } }, unread: false };
  return { state: { ...s, proposal: resetReply(p) }, unread: true };
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
      // For the reading-back reply they are what the CEO heard: they must carry the readback.
      const text = str(raw.transcript).trim();
      let st = markAudio(s);
      const q = st.proposal;
      let unread = false;
      if (q && waiting && q.replyId && (!rid || rid === q.replyId)) {
        st = { ...st, proposal: { ...q, replyText: `${q.replyText ?? ""} ${text}`.trim() } };
        ({ state: st, unread } = tryArm(st));
      }
      const effects: VoiceEffect[] = text ? [{ type: "agent", text }] : [];
      if (unread) effects.push({ type: "unread" });
      return { state: st, effects };
    }
    case "response.audio_transcript.delta":
    case "response.output_audio_transcript.delta":
    case "response.audio.done":
    case "response.output_audio.done":
      return { state: markAudio(s), effects: [] };
    case "input_audio_buffer.speech_stopped":
      return { state: s, effects: [{ type: "state", state: "thinking" }] };
    case "output_audio_buffer.started": {
      const st = markAudio(agentAudio(s, "model", true, now));
      return { state: st, effects: [{ type: "state", state: "speaking" }] };
    }
    case "response.audio.delta":
    case "response.output_audio.delta":
      // Audio is being made; over WebRTC only output_audio_buffer.* say when it plays in the room.
      return { state: markAudio(s), effects: [{ type: "state", state: "speaking" }] };
    case "output_audio_buffer.stopped": {
      // The model's audio finished playing in the room.
      let st = agentAudio(s, "model", false, now);
      const q = st.proposal;
      let unread = false;
      if (q && waiting && q.replyId && q.replyAudio && (!rid || rid === q.replyId)) {
        st = { ...st, proposal: { ...q, replyEndedAt: now } };
        ({ state: st, unread } = tryArm(st));
      }
      return { state: st, effects: unread ? [{ type: "state", state: "idle" }, { type: "unread" }] : [{ type: "state", state: "idle" }] };
    }
    case "output_audio_buffer.cleared": {
      // Cut off (a barge-in, a cough, or the echo tripping the VAD): the CEO did not hear the
      // whole readback, so this reply never arms the card. The next full reading can.
      let st = agentAudio(s, "model", false, now);
      const q = st.proposal;
      if (q && waiting && q.replyId && (!rid || rid === q.replyId)) st = { ...st, proposal: resetReply(q) };
      return { state: st, effects: [{ type: "state", state: "idle" }] };
    }
    case "response.done": {
      const r = raw.response;
      const id = responseId(r);
      let st = s;
      const q = st.proposal;
      const effects: VoiceEffect[] = [{ type: "state", state: "idle" }, { type: "response", active: false }];
      if (q && q.heardAt === null && q.replyId && q.replyId !== "local" && q.replyId === id) {
        const said = spoke(r);
        const status = isObj(r) ? str(r.status) : "";
        if (status && status !== "completed") st = { ...st, proposal: resetReply(q) }; // cancelled or failed: not heard
        // An audio reply is heard only when its audio stops playing (output_audio_buffer.stopped).
        else if (said.audio || q.replyAudio) st = { ...st, proposal: { ...q, replyAudio: true } };
        else if (said.message) {
          // A text-only readback: heard now, if it carried the readback.
          const r2 = tryArm({ ...st, proposal: { ...q, replyText: said.text, replyEndedAt: now } });
          st = r2.state;
          if (r2.unread) effects.push({ type: "unread" });
        } else st = { ...st, proposal: resetReply(q) }; // only function calls: the readback is still to come
      }
      return { state: st, effects };
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
