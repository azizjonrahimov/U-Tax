// D34 (contract v1.14 §5a-ter): the CEO talks to the assistant in plain words. Each utterance goes
// to POST /api/ceo/talk; the assistant acts directly (no confirm card) and answers what it did,
// with an undo for the actions that have one. Pure parsing here; CeoVoice does the talking.

export interface TalkAction {
  tool: string;
  summary: string;
  undoId: string | null;
}

export interface TalkReply {
  conversationId: string | null;
  say: string;
  actions: TalkAction[];
  /** The assistant is still working: poll GET /api/ceo/talk/{conversationId}. */
  pending: boolean;
}

/** How often a pending turn is checked, and for how long at most (the gateway waits 25 s per call). */
export const TALK_POLL_MS = 1500;
export const TALK_GIVE_UP_MS = 90_000;
/** The longest text one turn carries (the gateway's own cap is 2000). */
export const TALK_TEXT_MAX = 2000;

const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const clip = (t: string, max: number) => (Array.from(t).length > max ? `${Array.from(t).slice(0, max - 1).join("")}…` : t);
/** Display text from the wire: plain text only, one line, bounded. */
const text = (v: unknown, max: number) => (typeof v === "string" ? clip(v.replace(/\s+/g, " ").trim(), max) : "");

/** A talk reply from the wire, or null when it isn't one. */
export function parseTalkReply(raw: unknown): TalkReply | null {
  if (!isObj(raw)) return null;
  const cid = typeof raw.conversation_id === "string" && raw.conversation_id ? raw.conversation_id.slice(0, 80) : null;
  const pending = raw.pending === true;
  const actions: TalkAction[] = [];
  if (Array.isArray(raw.actions)) {
    for (const a of raw.actions.slice(0, 12)) {
      if (!isObj(a)) continue;
      const summary = text(a.summary, 300);
      if (!summary) continue;
      const undo = typeof a.undo_id === "string" && /^[\w.:-]{1,40}$/.test(a.undo_id) ? a.undo_id : null;
      actions.push({ tool: text(a.tool, 40) || "action", summary, undoId: undo });
    }
  }
  const say = text(raw.say, 1500);
  if (!say && !actions.length && !pending) return null;
  return { conversationId: cid, say: say || (pending ? "Still working on it…" : ""), actions, pending };
}

/** What the CEO hears for a reply: the assistant's words, else its actions. */
export function replyLine(r: TalkReply): string {
  if (r.say) return r.say;
  return r.actions.map((a) => a.summary).join(" ") || "Done.";
}

/** The undo answer ({kind: "done", summary}), in the CEO's words. */
export function undoLine(raw: unknown): string {
  return isObj(raw) && typeof raw.summary === "string" && raw.summary.trim() ? text(raw.summary, 300) : "Undone.";
}

/** How long the console uses the command path after /api/ceo/talk answered 503, before trying talk again. */
export const TALK_RETRY_MS = 60_000;
/** After TALK_GIVE_UP_MS a pending turn is still checked, slowly, for this long (its actions still get chips). */
export const TALK_SLOW_POLL_MS = 10_000;
export const TALK_LATE_MS = 5 * 60_000;

/**
 * What is read aloud: whole sentences up to about `max` characters (the ear is closed while the
 * console speaks, so a long answer would block the CEO). The transcript keeps the full text.
 */
export function spokenPart(say: string, max = 300): string {
  if (say.length <= max) return say;
  const sentences = say.match(/[^.!?]+[.!?]+(?:\s+|$)/g) ?? [];
  let out = "";
  for (const s of sentences) {
    if ((out + s).length > max) break;
    out += s;
  }
  out = out.trim();
  return out || `${say.slice(0, max - 1).trimEnd()}…`;
}
