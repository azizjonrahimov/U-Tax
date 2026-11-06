// "Talk to U-Tax" (/talk): pure logic, so the page stays thin and this is tested without a
// browser, a microphone or a gateway. Contract §5 (POST /api/voice, /api/voice/session),
// §6e (speaker-labelled turns, reply_email from the typed field), §8 (public page).

import type { UTaxEvent } from "@/theater/events";
import { formatCents } from "@/theater/format";

export type Speaker = "customer" | "agent";
export interface Turn {
  speaker: Speaker;
  text: string;
}

/** One turn on screen and in the request. Longer speech is clipped, never dropped. */
export const MAX_TURN_CHARS = 1000;
/** The gateway refuses a customer transcript over 4000 characters (too_long). */
export const MAX_CUSTOMER_CHARS = 4000;
/** The gateway keeps at most 60 turns of context. */
export const MAX_TURNS = 60;

const clipChars = (s: string, n: number) => {
  const cps = Array.from(s);
  return cps.length <= n ? s : `${cps.slice(0, n - 1).join("")}…`;
};

export const customerChars = (turns: readonly Turn[]) =>
  turns.reduce((n, t) => (t.speaker === "customer" ? n + Array.from(t.text).length + 1 : n), 0);

/**
 * Add a turn: trimmed, clipped, empty ones ignored. A customer turn that would push the
 * transcript past the gateway's cap is cut to what still fits (and marked full).
 */
export function addTurn(turns: readonly Turn[], speaker: Speaker, raw: string): { turns: Turn[]; full: boolean } {
  const text = clipChars(raw.replace(/\s+/g, " ").trim(), MAX_TURN_CHARS);
  if (!text) return { turns: [...turns], full: customerChars(turns) >= MAX_CUSTOMER_CHARS };
  let next = text;
  if (speaker === "customer") {
    const room = MAX_CUSTOMER_CHARS - customerChars(turns) - 1;
    if (room <= 0) return { turns: [...turns], full: true };
    next = clipChars(text, room);
  }
  const out = [...turns, { speaker, text: next }];
  return { turns: out.length > MAX_TURNS ? out.slice(out.length - MAX_TURNS) : out, full: customerChars(out) >= MAX_CUSTOMER_CHARS };
}

/** Same rule as the gateway (`^[^@\s]+@[^@\s]+\.[^@\s]+$`), so the button never offers a refused send. */
export const isEmail = (s: string) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(s.trim()) && s.trim().length <= 320;

export interface VoiceBody {
  turns: Turn[];
  channel: "voice" | "typed";
  speaker: "customer";
  reply_email: string;
  request_id: string;
}

/** POST /api/voice body. Agent turns go along as labelled context; only customer words count (gateway). */
export function voiceBody(turns: readonly Turn[], email: string, channel: "voice" | "typed", requestId: string): VoiceBody {
  return { turns: turns.map((t) => ({ ...t })), channel, speaker: "customer", reply_email: email.trim(), request_id: requestId };
}

export const canSend = (turns: readonly Turn[], email: string) => turns.some((t) => t.speaker === "customer") && isEmail(email);

// ------------------------------------------------------------------ typed fallback

/**
 * The typed (and no-key) intake: the voice prompt's questions in its order
 * (naive/prompts/voice-intake.md). Spoken with browser speech when sound is on.
 */
export const TYPED_SCRIPT: readonly string[] = [
  "Hi, I'm U-Tax's AI intake agent. Is your company a Delaware corporation, an Inc. or Corp., not an LLC?",
  "How many shares is the company authorized to issue, and how many are issued?",
  "Do you know the par value per share? If not, that's fine, just say so.",
  "Roughly what will the company's total assets be at the end of the year? And does any foreign person or company own 25% or more?",
  "Thanks. Type the email the estimate should go to in the box below, then press Send to U-Tax.",
];

/** The agent's next line in typed mode: one question per customer answer, then the email prompt. */
export function nextTypedQuestion(turns: readonly Turn[]): string | null {
  const answered = turns.filter((t) => t.speaker === "customer").length;
  const asked = turns.filter((t) => t.speaker === "agent").length;
  if (asked > answered) return null; // still waiting for an answer
  return asked < TYPED_SCRIPT.length ? TYPED_SCRIPT[asked] : null;
}

// ------------------------------------------------------------------ realtime

export interface VoiceSession {
  clientSecret: string;
  model: string;
  /** Where the page posts its SDP offer (contract v1.7.5 `realtime_url`, else the beta default). */
  sdpUrl: string;
}

/**
 * Fallback SDP endpoint for a session minted on POST /v1/realtime/sessions (the beta pair),
 * used only when the gateway sends no `realtime_url`. UNVERIFIED without a key; any failure
 * drops the page to typed chat.
 */
export const REALTIME_SDP_URL = "https://api.openai.com/v1/realtime";

/** The SDP offer carries the caller's audio session and the secret: only ever to OpenAI over https. */
function realtimeUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  try {
    const u = new URL(raw);
    return u.protocol === "https:" && u.hostname === "api.openai.com" && !u.username && !u.password ? u.toString() : null;
  } catch {
    return null;
  }
}

/** POST /api/voice/session → {client_secret, model, expires_at, realtime_url?}; anything else is unusable. */
export function parseVoiceSession(raw: unknown): VoiceSession | null {
  if (typeof raw !== "object" || raw === null) return null;
  const { client_secret, model, realtime_url } = raw as Record<string, unknown>;
  if (typeof client_secret !== "string" || !client_secret || typeof model !== "string" || !/^[\w.-]{1,80}$/.test(model)) return null;
  if (realtime_url !== undefined && realtime_url !== null && realtimeUrl(realtime_url) === null) return null; // set but not OpenAI: refuse
  const base = realtimeUrl(realtime_url) ?? REALTIME_SDP_URL;
  const u = new URL(base);
  if (!u.searchParams.has("model")) u.searchParams.set("model", model);
  return { clientSecret: client_secret, model, sdpUrl: u.toString() };
}

/** The only session field the page sets: transcription of the caller's audio (never instructions or tools). */
export const TRANSCRIPTION_UPDATE = {
  type: "session.update",
  session: { input_audio_transcription: { model: "whisper-1" } },
} as const;

export type RealtimeSignal =
  | { kind: "turn"; speaker: Speaker; text: string }
  | { kind: "state"; state: "listening" | "thinking" | "speaking" | "idle" }
  | { kind: "error"; message: string };

/** Realtime server events (data channel "oai-events") → what the page shows. Beta and GA names. */
export function realtimeSignal(raw: unknown): RealtimeSignal | null {
  if (typeof raw !== "object" || raw === null) return null;
  const e = raw as Record<string, unknown>;
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  switch (e.type) {
    case "conversation.item.input_audio_transcription.completed":
      return str(e.transcript).trim() ? { kind: "turn", speaker: "customer", text: str(e.transcript) } : null;
    case "response.audio_transcript.done":
    case "response.output_audio_transcript.done":
      return str(e.transcript).trim() ? { kind: "turn", speaker: "agent", text: str(e.transcript) } : null;
    case "input_audio_buffer.speech_started":
      return { kind: "state", state: "listening" };
    case "input_audio_buffer.speech_stopped":
      return { kind: "state", state: "thinking" };
    case "response.audio.delta":
    case "response.output_audio.delta":
    case "output_audio_buffer.started":
      return { kind: "state", state: "speaking" };
    case "response.done":
    case "output_audio_buffer.stopped":
      return { kind: "state", state: "idle" };
    case "error": {
      const err = e.error as Record<string, unknown> | undefined;
      return { kind: "error", message: str(err?.message) || "voice error" };
    }
    default:
      return null;
  }
}

// ------------------------------------------------------------------ following the case

export type Stage = "received" | "facts" | "computed" | "checked" | "sent";
export const STAGES: readonly { id: Stage; label: string }[] = [
  { id: "received", label: "Intake has your answers" },
  { id: "facts", label: "Facts checked against what you said" },
  { id: "computed", label: "Tax agent computed both methods" },
  { id: "checked", label: "QA re-checked every number" },
  { id: "sent", label: "Memo sent to your inbox" },
];

export interface CaseProgress {
  caseId: string;
  stage: Stage;
  totalCents: number | null;
  method: "assumed_par" | "authorized" | null;
  /** The firm needs more facts (it emailed a question). */
  askedFor: string[];
  /** Ended without a memo: not a Delaware corporation etc. */
  notEligible: string | null;
  /** Waiting on a human (BLOCKED). */
  blocked: boolean;
  lastId: number;
}

const ORDER: Record<Stage, number> = { received: 0, facts: 1, computed: 2, checked: 3, sent: 4 };

export const emptyProgress = (caseId: string): CaseProgress => ({
  caseId, stage: "received", totalCents: null, method: null, askedFor: [], notEligible: null, blocked: false, lastId: 0,
});

/** Fold this case's events (others ignored) into what the page shows. Forward-only. */
export function followCase(p: CaseProgress, events: readonly UTaxEvent[]): CaseProgress {
  let s = p;
  const up = (to: Stage) => (ORDER[to] > ORDER[s.stage] ? to : s.stage);
  for (const e of events) {
    if (typeof e !== "object" || e === null || e.case_id !== p.caseId || !(e.id > s.lastId)) continue;
    s = { ...s, lastId: e.id };
    if (e.status !== "ok") continue;
    const pl = (e.payload ?? {}) as Record<string, unknown>;
    switch (e.type) {
      case "facts.extracted":
        s = { ...s, stage: up("facts"), askedFor: [] };
        break;
      case "client.asked":
      case "facts.missing":
        s = { ...s, askedFor: Array.isArray(pl.missing) ? pl.missing.filter((m): m is string => typeof m === "string").slice(0, 8) : [] };
        break;
      case "tax.computed":
        if (Number.isSafeInteger(pl.total_cents) && (pl.total_cents as number) >= 0) {
          s = { ...s, stage: up("computed"), totalCents: pl.total_cents as number, method: pl.chosen === "authorized" ? "authorized" : "assumed_par" };
        }
        break;
      case "qa.passed":
        s = { ...s, stage: up("checked") };
        break;
      case "memo.sent":
        s = { ...s, stage: up("sent"), blocked: false };
        break;
      case "case.not_eligible":
        s = { ...s, notEligible: typeof pl.reason === "string" ? pl.reason : "not_eligible" };
        break;
      case "case.blocked":
        s = { ...s, blocked: true };
        break;
      case "case.status":
        if (pl.to !== "BLOCKED" && s.blocked) s = { ...s, blocked: false };
        break;
    }
  }
  return s;
}

const FACT_WORDS: Record<string, string> = {
  incorporated_delaware: "whether it's a Delaware corporation",
  authorized_shares: "authorized shares",
  issued_shares: "issued shares",
  stated_par: "par value",
  gross_assets_cents: "total assets",
  foreign_owner_25: "foreign ownership",
};

/** The line the page shows and speaks for where the case is now. */
export function progressLine(p: CaseProgress): string {
  if (p.notEligible) return "This checkup is for Delaware corporations, so the firm is emailing you a short note on what to do instead.";
  if (p.blocked) return "A human at U-Tax is taking a look. You'll get an email.";
  if (p.askedFor.length && p.stage === "received") {
    const words = p.askedFor.map((f) => FACT_WORDS[f] ?? f.replace(/_/g, " "));
    return `The firm emailed you one question: ${words.join(", ")}.`;
  }
  if (p.totalCents !== null) {
    const how = p.method === "authorized" ? "the authorized shares method" : "the assumed par value method";
    const tail = p.stage === "sent" ? "The memo is in your inbox." : "QA is checking it, then the memo goes out.";
    return `Your estimated Delaware franchise tax is ${formatCents(p.totalCents)}, using ${how}. ${tail}`;
  }
  return STAGES.find((s) => s.id === p.stage)?.label ?? "";
}

// ------------------------------------------------------------------ errors

/** A gateway error → a calm sentence for the caller (no codes, no stack). */
export function friendlyError(status: number, code: string): string {
  if (status === 429 || code === "rate_limited") return "Lots of people are talking to U-Tax right now. Please try again in a minute.";
  if (code === "too_long") return "That's a lot of detail. Please keep it to the five questions.";
  if (code === "bad_input") return "Please check the email address and try again.";
  if (status === 0 || code === "network_error") return "Can't reach U-Tax right now. Check the connection and try again.";
  return "Something went wrong on our side. Please try again.";
}
