// D41 F49 (runbook §D.2): the firm's email, as threads per case, from the log (pure; tests pin it).
// Every message is already masked in its payload; a case flagged as a possible injection never
// shows its message bodies (the theater's rule). Signed in, `payload.sent` is the email as it left.

export type MailDir = "in" | "out";
export interface MailMsg {
  id: number;
  ts: string;
  dir: MailDir;
  subject: string;
  /** ≤ 600 chars, masked by the gateway. */
  body: string;
  /** What kind of mail this is when it isn't a plain message ("asked for the missing numbers"). */
  label?: string;
  provider: string | null;
  status: string;
}
export interface Thread {
  caseId: string;
  name: string;
  flagged: boolean;
  messages: MailMsg[];
}
export interface MailEvent {
  id: number;
  ts: string;
  type: string;
  case_id?: string | null;
  provider?: string | null;
  status?: string;
  payload?: unknown;
}

const BODY_MAX = 600;
export const MAIL_EVENTS: ReadonlySet<string> = new Set(["email.received", "client.asked", "memo.sent", "client.followed_up", "call.booked", "payment.link_created", "payment.received"]);
const FOLLOW_UP: Record<string, string> = {
  memo_check: "follow-up: did the memo arrive?",
  missing_facts: "follow-up: the missing numbers",
  call_reminder: "follow-up: call reminder",
  payment_receipt: "receipt: filing help paid",
};

type P = Record<string, unknown>;
const obj = (v: unknown): P => (typeof v === "object" && v !== null && !Array.isArray(v) ? (v as P) : {});
const str = (v: unknown) => (typeof v === "string" ? v : "");
const cut = (s: string) => (s.length > BODY_MAX ? `${s.slice(0, BODY_MAX - 1)}…` : s);
const dollars = (cents: unknown) => (typeof cents === "number" && Number.isSafeInteger(cents) && cents >= 0 ? `$${(cents / 100).toFixed(cents % 100 ? 2 : 0)}` : "");

/** One log event → the message it is, or null (not mail). */
export function messageOf(ev: MailEvent): MailMsg | null {
  if (!MAIL_EVENTS.has(ev.type)) return null;
  const p = obj(ev.payload);
  const sent = obj(p.sent);
  const base = { id: ev.id, ts: ev.ts, provider: ev.provider ?? null, status: ev.status ?? "ok" };
  const subj = str(sent.subject) || str(p.subject);
  const body = str(sent.body) || str(p.body);
  switch (ev.type) {
    case "email.received":
      return { ...base, dir: "in", subject: str(p.subject), body: cut(str(p.body)) };
    case "client.asked":
      return { ...base, dir: "out", subject: subj || "A few numbers to finish your check", body: cut(body), label: "asked for the missing numbers" };
    case "memo.sent":
      return { ...base, dir: "out", subject: subj || "Your Delaware franchise tax checkup", body: cut(body), label: "the memo" };
    case "client.followed_up": {
      const kind = str(p.kind);
      return { ...base, dir: "out", subject: subj || FOLLOW_UP[kind] || "follow-up", body: cut(body), label: FOLLOW_UP[kind] ?? "follow-up" };
    }
    case "call.booked": {
      const minutes = typeof p.minutes === "number" ? `${p.minutes}-min ` : "";
      return { ...base, dir: "out", subject: "Review call invite", body: cut(`${minutes}review call${str(p.start) ? ` · ${str(p.start)}` : ""}`), label: "invite: review call" };
    }
    case "payment.link_created":
      return { ...base, dir: "out", subject: "Filing help", body: cut(`${dollars(p.amount_cents)} filing-help payment link (Stripe)`.trim()), label: "filing-help link" };
    case "payment.received":
      return { ...base, dir: "in", subject: "Payment received", body: "", label: `paid ${dollars(p.amount_cents)}`.trim() };
  }
  return null;
}

const caseOf = (ev: MailEvent) => (typeof ev.case_id === "string" && ev.case_id ? ev.case_id : str(obj(ev.payload).case_id) || null);

/** Fold events (history on open, then the stream) into threads, newest thread first. Idempotent per event id. */
export function foldMail(prev: readonly Thread[], events: readonly MailEvent[]): Thread[] {
  const byCase = new Map(prev.map((t) => [t.caseId, { ...t, messages: [...t.messages] }]));
  for (const ev of events) {
    const caseId = caseOf(ev);
    if (!caseId) continue;
    if (ev.type === "case.flagged" && str(obj(ev.payload).kind) === "possible_injection") {
      const t = byCase.get(caseId);
      if (t) t.flagged = true;
      else byCase.set(caseId, { caseId, name: caseId, flagged: true, messages: [] });
      continue;
    }
    const m = messageOf(ev);
    if (!m) continue;
    const t = byCase.get(caseId) ?? { caseId, name: caseId, flagged: false, messages: [] };
    const named = str(obj(ev.payload).masked_name);
    if (named && t.name === caseId) t.name = named.slice(0, 40);
    if (!t.messages.some((x) => x.id === m.id)) t.messages = [...t.messages, m].sort((a, b) => a.id - b.id);
    byCase.set(caseId, t);
  }
  return [...byCase.values()].filter((t) => t.messages.length > 0).sort((a, b) => (b.messages.at(-1)?.id ?? 0) - (a.messages.at(-1)?.id ?? 0));
}

/** A thread waits on the firm when its last message came in. */
export const unanswered = (t: Thread) => t.messages.at(-1)?.dir === "in";
