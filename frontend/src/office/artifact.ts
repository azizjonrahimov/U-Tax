// D37 (contract v1.18): what each department is making right now, drawn live in its window. A pure
// fold over two kinds of event:
// - `agent.activity` (admin and session readers only; never the public theater): the agent is
//   thinking, a tool started or finished, or the text it is writing (cumulative: the latest ≤ 600
//   characters of the draft, masked, at most one event a second);
// - the domain events that carry the finished artifact: for admin readers `payload.sent` (the email
//   as it actually left) and `payload.cells` (what each doc.step wrote); the post as published; the
//   tax engine's numbers; the invite. Theater readers get none of that and see the plain steps.
// Everything renders as text. A case flagged as a possible injection never shows its mail.

import { formatCents } from "@/theater/format";
import type { UTaxEvent } from "@/theater/events";
import { SEATS, type Seat } from "./workspace";

export type ArtifactState = "drafting" | "done";

export interface MailArt {
  kind: "mail";
  app: string;
  to: string;
  subject: string;
  body: string;
  attachments: string[];
  state: ArtifactState;
}
export interface PostArt {
  kind: "post";
  channel: string;
  text: string;
  image: string | null;
  state: ArtifactState;
}
export interface SheetArt {
  kind: "sheet";
  file: string;
  /** Sheet tabs in the order they appeared; each row is one cell the gateway wrote. */
  tabs: string[];
  rows: { tab: string; label: string; value: string }[];
  state: ArtifactState;
  verified: boolean | null;
}
export interface DocArt {
  kind: "doc";
  file: string;
  sections: { title: string; text: string }[];
  state: ArtifactState;
  verified: boolean | null;
}
export interface CalcArt {
  kind: "calc";
  lines: { label: string; value: string; chosen?: boolean }[];
  state: ArtifactState;
}
export interface InviteArt {
  kind: "invite";
  with: string;
  minutes: number;
  state: ArtifactState;
}
export type Artifact = (MailArt | PostArt | SheetArt | DocArt | CalcArt | InviteArt) & {
  /** Changes when a new artifact starts (the window re-mounts and types from the start). */
  key: string;
  ts: string;
  caseId: string | null;
};

/** What the agent is doing this moment (from agent.activity). */
export interface Activity {
  key: string;
  ts: string;
  kind: "thinking" | "tool" | "writing" | "idle";
  tool: string | null;
}

export interface Desk {
  art: Artifact | null;
  act: Activity | null;
}

export interface Studio {
  desks: Record<Seat, Desk>;
  /** Files built per case (doc.created), attached to the memo when it is sent. */
  files: Record<string, string[]>;
  /** The desk agent.activity moved last (auto-follow), and that event's key. */
  lastSeat: Seat | null;
  lastKey: string;
}

export const emptyStudio = (): Studio => ({
  desks: Object.fromEntries(SEATS.map((s): [Seat, Desk] => [s, { art: null, act: null }])) as unknown as Record<Seat, Desk>,
  files: {},
  lastSeat: null,
  lastKey: "",
});

/** Event types the office handles itself (the theater's script doesn't know them). */
export const STUDIO_ONLY: ReadonlySet<string> = new Set(["agent.activity", "doc.step", "doc.created", "doc.verified"]);

type P = Record<string, unknown>;
const isObj = (v: unknown): v is P => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (p: P, k: string) => (typeof p[k] === "string" ? (p[k] as string) : "");
const num = (p: P, k: string) => (typeof p[k] === "number" && Number.isFinite(p[k]) ? (p[k] as number) : null);
const clip = (t: string, max: number) => (Array.from(t).length > max ? `${Array.from(t).slice(0, max - 1).join("")}…` : t);
/** One-line display text. */
const line = (t: string, max: number) => clip(t.replace(/\s+/g, " ").trim(), max);
/** Multi-line text (an email or a post): control characters out, blank runs collapsed, bounded. */
const block = (t: string, max: number) =>
  clip(
    t
      .replace(/\r\n?/g, "\n")
      .replace(/[\u0000-\u0008\u000b-\u001f\u007f]/g, "")
      .replace(/\n{3,}/g, "\n\n"),
    max,
  );

const ALIAS: Readonly<Record<string, Seat>> = {
  chief: "cos", cos: "cos", intake: "intake", tax: "tax", qa: "qa", acct: "acct", accounting: "acct", mkt: "mkt", marketing: "mkt", hr: "hr",
};
/** The seat an activity belongs to: the event's actor, else a seat named in `agent` ("utax-intake"). */
export function seatOf(ev: UTaxEvent): Seat | null {
  if ((SEATS as readonly string[]).includes(ev.actor)) return ev.actor as Seat;
  const agent = isObj(ev.payload) ? str(ev.payload, "agent").toLowerCase() : "";
  // The last seat word wins ("u-tax-marketing" is Marketing, not Tax).
  let seat: Seat | null = null;
  for (const part of agent.split(/[^a-z]+/)) if (Object.hasOwn(ALIAS, part)) seat = ALIAS[part];
  return seat;
}

/** Which artifact a tool makes, so the window opens the right app the moment the tool starts. */
function artFor(tool: string, base: { key: string; ts: string; caseId: string | null }): Artifact | null {
  if (/^(ask_client|send_memo|follow_up|hr_outreach|send_email|reply_email)$/.test(tool))
    return { ...base, kind: "mail", app: tool === "hr_outreach" ? "AgentMail · hiring" : "AgentMail · compose", to: "", subject: "", body: "", attachments: [], state: "drafting" };
  if (/^(post_social|make_ad)$/.test(tool)) return { ...base, kind: "post", channel: "", text: "", image: null, state: "drafting" };
  if (tool === "prepare_documents") return { ...base, kind: "sheet", file: "Workpaper.xlsx", tabs: [], rows: [], state: "drafting", verified: null };
  if (tool === "compute_tax") return { ...base, kind: "calc", lines: [], state: "drafting" };
  if (tool === "book_call") return { ...base, kind: "invite", with: "", minutes: 0, state: "drafting" };
  return null;
}

const MAX_TEXT = 6000;
const MAX_ROWS = 60;

/**
 * The text being written replaces the draft (v1.18: each writing event carries the latest ≤ 600
 * characters, so a long draft shows its newest part, led by an ellipsis).
 */
function setDraft(art: Artifact, text: string): Artifact {
  if (art.state !== "drafting") return art;
  const t = block(text, 600);
  if (art.kind === "mail") return { ...art, body: t };
  if (art.kind === "post") return { ...art, text: t };
  return art;
}

export interface StudioCtx {
  /** A case flagged as a possible injection: its mail is never shown. */
  flagged: (caseId: string | null) => boolean;
  /** The masked name for a case (never a raw id). */
  name: (caseId: string | null) => string;
  /** An image URL the feed allows, else undefined. */
  image: (url: unknown) => string | undefined;
}

const HIDDEN = "Hidden: this client's mail was flagged by QA.";

/** A finished artifact ends the agent's writing or tool step (the next activity, if any, says what follows). */
const settled = (act: Activity | null): Activity | null => (act && (act.kind === "writing" || act.kind === "tool") ? null : act);

function put(st: Studio, seat: Seat, desk: Partial<Desk>, live?: string): Studio {
  const next = { ...st, desks: { ...st.desks, [seat]: { ...st.desks[seat], ...desk } } };
  return live ? { ...next, lastSeat: seat, lastKey: live } : next;
}

/** Fold one event into the studio. Unknown or malformed events change nothing. */
export function applyStudio(st: Studio, ev: UTaxEvent, ctx: StudioCtx): Studio {
  const p: P = isObj(ev.payload) ? ev.payload : {};
  const key = `${ev.id}`;
  const base = { key, ts: ev.ts, caseId: ev.case_id };
  const hide = ctx.flagged(ev.case_id);

  if (ev.type === "agent.activity") {
    const seat = seatOf(ev);
    if (!seat) return st;
    const desk = st.desks[seat];
    const kind = str(p, "kind");
    const tool = line(str(p, "tool"), 40) || null;
    if (kind === "thinking") return put(st, seat, { act: { key, ts: ev.ts, kind: "thinking", tool: desk.act?.tool ?? null } }, key);
    if (kind === "tool_started" && tool) {
      const art = artFor(tool, base);
      return put(st, seat, { act: { key, ts: ev.ts, kind: "tool", tool }, ...(art ? { art } : {}) }, key);
    }
    if (kind === "writing") {
      const text = typeof p.text === "string" ? p.text : "";
      let art = desk.art;
      // Text with no artifact open yet: the agent is writing a message (the common case).
      if (!art || art.state !== "drafting") art = artFor("send_email", base);
      if (art && !hide) art = setDraft(art, text);
      return put(st, seat, { act: { key, ts: ev.ts, kind: "writing", tool: desk.act?.tool ?? null }, art }, key);
    }
    if (kind === "tool_done") return put(st, seat, { act: { key, ts: ev.ts, kind: "thinking", tool: null } });
    if (kind === "idle") return put(st, seat, { act: null });
    return st;
  }

  const seat = (SEATS as readonly string[]).includes(ev.actor) ? (ev.actor as Seat) : null;
  if (!seat) return st;
  const who = ctx.name(ev.case_id);

  switch (ev.type) {
    case "client.asked":
    case "memo.sent":
    case "client.followed_up":
    case "hr.outreach": {
      const prev = st.desks[seat].art;
      // v1.18: admin readers get `sent` (the email as it actually left); theater readers don't.
      const sent: P = isObj(p.sent) ? p.sent : {};
      const subject = line(str(sent, "subject"), 200);
      const body = block(str(sent, "body"), MAX_TEXT);
      const to = line(str(sent, "to_masked"), 80) || (ev.type === "hr.outreach" ? line(str(p, "label"), 40) || "a candidate" : line(str(p, "to_masked"), 80) || who);
      const fallbackSubject =
        ev.type === "memo.sent" ? `Your tax estimate, ${who}` : ev.type === "client.asked" ? "A few details for your estimate" : ev.type === "hr.outreach" ? "A tax-season gig, and a call" : "Following up";
      const attachments = ev.type === "memo.sent" && ev.case_id ? (st.files[ev.case_id] ?? []) : [];
      // The draft typed live stays on screen if the event carries no body (a public reader).
      const draft = prev?.kind === "mail" && prev.state === "drafting" ? prev.body : "";
      const art: Artifact = {
        ...base,
        kind: "mail",
        app: ev.type === "hr.outreach" ? "AgentMail · hiring" : "AgentMail · compose",
        to: hide ? "Hidden" : to,
        subject: hide ? "Hidden" : subject || fallbackSubject,
        body: hide ? HIDDEN : body || draft,
        attachments,
        state: "done",
      };
      return put(st, seat, { art, act: settled(st.desks[seat].act) });
    }
    case "post.published": {
      const prev = st.desks[seat].art;
      const text = block(str(p, "text"), 600) || (prev?.kind === "post" ? prev.text : "");
      return put(st, seat, { art: { ...base, kind: "post", channel: line(str(p, "channel"), 20).toLowerCase(), text, image: ctx.image(p.image_url) ?? null, state: "done" }, act: settled(st.desks[seat].act) });
    }
    case "ad.created": {
      return put(st, seat, { art: { ...base, kind: "post", channel: "", text: block(str(p, "headline") || str(p, "text"), 600), image: ctx.image(p.image_url) ?? null, state: "drafting" } });
    }
    case "tax.computed": {
      const auth = num(p, "authorized_method_cents");
      const par = num(p, "assumed_par_method_cents");
      const total = num(p, "total_cents");
      if (![auth, par, total].every((c) => c !== null && Number.isSafeInteger(c) && c >= 0)) return st;
      const chosen = str(p, "chosen");
      return put(st, seat, {
        art: {
          ...base,
          kind: "calc",
          lines: [
            { label: "Authorized Shares Method", value: formatCents(auth as number), chosen: chosen === "authorized" },
            { label: "Assumed Par Value Method", value: formatCents(par as number), chosen: chosen !== "authorized" },
            { label: `Total due for ${who}`, value: formatCents(total as number) },
          ],
          state: "done",
        },
      });
    }
    case "call.booked": {
      const minutes = num(p, "minutes") ?? 0;
      const w = p.kind === "candidate" ? line(str(p, "invitee_masked"), 40) || "a candidate" : who;
      return put(st, seat, { art: { ...base, kind: "invite", with: w, minutes, state: "done" }, act: settled(st.desks[seat].act) });
    }
    case "doc.step": {
      const doc = str(p, "doc");
      const step = line(str(p, "step"), 80);
      const detail = line(str(p, "detail"), 200);
      // v1.18: admin readers get the cells this step wrote.
      const cells = Array.isArray(p.cells)
        ? p.cells.filter(isObj).slice(0, 40).map((c) => ({ tab: line(str(c, "sheet"), 40) || "Sheet1", label: line(str(c, "cell"), 12), value: line(typeof c.value === "number" ? String(c.value) : str(c, "value"), 80) }))
        : [];
      if (!step && !detail && !cells.length) return st;
      const prev = st.desks[seat].art;
      if (doc === "memo") {
        const cur = prev?.kind === "doc" && prev.caseId === ev.case_id && prev.state === "drafting" ? prev : { ...base, kind: "doc" as const, file: "Memo.docx", sections: [], state: "drafting" as const, verified: null };
        return put(st, seat, { art: { ...cur, ts: ev.ts, sections: [...cur.sections, { title: step || "Section", text: detail }].slice(-12) } });
      }
      // The workpaper and the books are spreadsheets: each step is a row on its tab.
      const tab = doc === "books" ? "Books" : step.split(":")[0].trim() || "Workpaper";
      const cur =
        prev?.kind === "sheet" && prev.caseId === ev.case_id && prev.state === "drafting"
          ? prev
          : { ...base, kind: "sheet" as const, file: doc === "books" ? "U-Tax_Books.xlsx" : "Workpaper.xlsx", tabs: [], rows: [], state: "drafting" as const, verified: null };
      const [label, ...rest] = (detail || step).split(":");
      const value = rest.join(":").trim() || detail;
      const added = cells.length ? cells : [{ tab, label: line(label, 60), value: line(value, 80) }];
      const tabs = [...cur.tabs];
      for (const a of added) if (!tabs.includes(a.tab)) tabs.push(a.tab);
      return put(st, seat, { art: { ...cur, ts: ev.ts, tabs, rows: [...cur.rows, ...added].slice(-MAX_ROWS) } });
    }
    case "doc.created": {
      const file = line(str(p, "file_name"), 80);
      const cid = ev.case_id;
      const files = cid && file ? { ...st.files, [cid]: [...(st.files[cid] ?? []).filter((f) => f !== file), file].slice(-6) } : st.files;
      const prev = st.desks[seat].art;
      const art = prev && (prev.kind === "sheet" || prev.kind === "doc") && prev.caseId === cid && file ? { ...prev, file } : prev;
      return { ...put(st, seat, { art }), files };
    }
    case "doc.verified": {
      const prev = st.desks[seat].art;
      if (!prev || (prev.kind !== "sheet" && prev.kind !== "doc") || prev.caseId !== ev.case_id) return st;
      return put(st, seat, { art: { ...prev, state: "done", verified: p.ok === true } });
    }
  }
  return st;
}
