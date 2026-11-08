// D37 (contract v1.18): what each department is making right now, drawn live in its window. A pure
// fold over:
// - `agent.activity` (admin and session readers only; never the public theater): thinking, a tool
//   started or finished (its name, `utax.`-prefixed), and `writing`: the agent's own message so far
//   (its narration of the work, masked, ≤ 600 chars). The gateway writes every email from a
//   template, so narration is shown as the agent's notes, never as an email.
// - the domain events that carry the finished artifact: for admin readers `payload.sent` (the email
//   as it actually left) and `payload.cells` (each workbook sheet as written); public readers get
//   the masked public fields. The post as published, the tax engine's numbers, the invite.
// Everything renders as text. A case flagged as a possible injection never shows its mail, its
// cells or (as far as the desk can tell) its agent's notes.

import { formatCents } from "@/theater/format";
import type { UTaxEvent } from "@/theater/events";
import { SEATS, type Seat } from "./workspace";

/** "failed": the gateway logged it as failed (a post that didn't go out): never shown as done. */
export type ArtifactState = "drafting" | "done" | "failed";

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
export type DocName = "workpaper" | "memo" | "books";
/** One workbook: its cells as written (admin readers), else one line per build step. */
export interface Book {
  file: string;
  /** sheet -> cell -> value */
  cells: Record<string, Record<string, string>>;
  sheets: string[];
  lines: { tab: string; text: string }[];
  verified: boolean | null;
  /** doc.created came: `file` is the real file the gateway built (it can be downloaded). */
  built?: true;
}
export interface Memo {
  file: string;
  sections: string[];
  verified: boolean | null;
  built?: true;
}
/** The case's documents as they are built; `units` is the order things appeared, for the playback. */
export interface DocsArt {
  kind: "docs";
  workpaper: Book | null;
  memo: Memo | null;
  books: Book | null;
  units: { doc: DocName; sheet?: string; cell?: string }[];
  state: ArtifactState;
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
export type Artifact = (MailArt | PostArt | DocsArt | CalcArt | InviteArt) & {
  /** Changes when a new artifact starts (the window re-mounts and plays from the start). */
  key: string;
  ts: string;
  caseId: string | null;
};

/** What the agent is doing this moment (agent.activity), shown while it is recent. */
export interface Activity {
  ts: string;
  kind: "thinking" | "tool" | "writing";
  tool: string | null;
}

export interface Desk {
  art: Artifact | null;
  act: Activity | null;
  /** The agent's own words so far (its notes on the work), shown while recent. */
  notes: { text: string; ts: string } | null;
}

export interface Studio {
  desks: Record<Seat, Desk>;
  /** The files a case's memo attaches (the workpaper and the memo, latest version). */
  files: Record<string, Partial<Record<"workpaper" | "memo", string>>>;
  /**
   * D35: the files the gateway actually serves, per case: it lists a version's workpaper and memo
   * only once all three documents (workpaper, memo, books) passed their checks; an earlier
   * version stays served if a newer one fails. Never for a flagged case.
   */
  served: Record<string, string[]>;
  /** The current build's checks, per case (reset when a new build starts). */
  checks: Record<string, Partial<Record<DocName, boolean>>>;
  /** The desk a live artifact-opening event moved last (auto-follow), and that event's key. */
  lastSeat: Seat | null;
  lastKey: string;
}

export const emptyStudio = (): Studio => ({
  desks: Object.fromEntries(SEATS.map((s): [Seat, Desk] => [s, { art: null, act: null, notes: null }])) as unknown as Record<Seat, Desk>,
  files: {},
  served: {},
  checks: {},
  lastSeat: null,
  lastKey: "",
});

/** Event types the office handles itself (the theater's script doesn't know them). */
export const STUDIO_ONLY: ReadonlySet<string> = new Set(["agent.activity", "doc.step", "doc.created", "doc.verified"]);

/** Activity and notes count as current for this long after their log time. */
export const ACTIVITY_MS = 20_000;
/** An artifact stays in the window for this long after its last change (the step page takes over after). */
export const ARTIFACT_MS = 5 * 60_000;
/** A composer opened by a tool that never produced anything closes after this long. */
export const DRAFT_MS = 2 * 60_000;

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
      .replace(/\n{3,}/g, "\n\n")
      .trim(),
    max,
  );

const ALIAS: Readonly<Record<string, Seat>> = {
  chief: "cos", cos: "cos", intake: "intake", tax: "tax", qa: "qa", acct: "acct", accounting: "acct", mkt: "mkt", marketing: "mkt", hr: "hr",
};
/** The seat an activity belongs to: the event's actor, else the last seat word in `agent` ("u-tax-marketing"). */
export function seatOf(ev: UTaxEvent): Seat | null {
  if ((SEATS as readonly string[]).includes(ev.actor)) return ev.actor as Seat;
  const agent = isObj(ev.payload) ? str(ev.payload, "agent").toLowerCase() : "";
  let seat: Seat | null = null;
  for (const part of agent.split(/[^a-z]+/)) if (Object.hasOwn(ALIAS, part)) seat = ALIAS[part];
  return seat;
}

/** A tool's own name: the MCP server prefix ("utax.") off. */
export const bareTool = (t: string) => t.replace(/^[a-z0-9_-]+\./i, "");

/** Which artifact a tool makes, so the window opens the right app the moment the tool starts. */
function artFor(tool: string, base: { key: string; ts: string; caseId: string | null }): Artifact | null {
  if (/^(ask_client|send_memo|follow_up|hr_outreach)$/.test(tool))
    return { ...base, kind: "mail", app: tool === "hr_outreach" ? "AgentMail · hiring" : "AgentMail · compose", to: "", subject: "", body: "", attachments: [], state: "drafting" };
  if (tool === "post_social") return { ...base, kind: "post", channel: "", text: "", image: null, state: "drafting" };
  if (tool === "prepare_documents") return { ...base, kind: "docs", workpaper: null, memo: null, books: null, units: [], state: "drafting" };
  if (tool === "compute_tax") return { ...base, kind: "calc", lines: [], state: "drafting" };
  if (tool === "book_call") return { ...base, kind: "invite", with: "", minutes: 0, state: "drafting" };
  return null;
}

export interface StudioCtx {
  /** A case flagged as a possible injection: its mail and cells are never shown. */
  flagged: (caseId: string | null) => boolean;
  /** Whether this desk's newest step is about a flagged case (its agent's notes are then hidden). */
  seatFlagged: (seat: Seat) => boolean;
  /** The masked name for a case (never a raw id). */
  name: (caseId: string | null) => string;
  /** An image URL the feed allows, else undefined. */
  image: (url: unknown) => string | undefined;
}

const HIDDEN = "Hidden: this client's mail was flagged by QA.";
const MAX_TEXT = 6000;
const MAX_UNITS = 400;

/** A finished artifact ends the agent's tool step (the next activity, if any, says what follows). */
const settled = (act: Activity | null): Activity | null => (act && act.kind === "tool" ? null : act);

function put(st: Studio, seat: Seat, desk: Partial<Desk>, live?: string): Studio {
  const next = { ...st, desks: { ...st.desks, [seat]: { ...st.desks[seat], ...desk } } };
  return live ? { ...next, lastSeat: seat, lastKey: live } : next;
}

const emptyBook = (file: string): Book => ({ file, cells: {}, sheets: [], lines: [], verified: null });

// At least the cents; more digits when the gateway sends them (the assumed par value capital, at full precision).
const USD = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 20 });
const INT = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
/**
 * A workbook cell as Excel shows it (v1.18.2 `fmt`): "usd" → $#,##0.00, "int" → 10,000,000,
 * "par" as is at full precision (0.00001), "text" or anything else as written.
 */
export function cellText(value: unknown, fmt: string): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    // Float noise off (1234.5600000000002), never "-$0.00"; the gateway's real digits stay (APVC).
    if (fmt === "usd") {
      const v = Number(value.toFixed(10));
      return USD.format(v === 0 ? 0 : v);
    }
    if (fmt === "int") return INT.format(value);
    if (fmt === "par") return value.toLocaleString("en-US", { useGrouping: false, maximumSignificantDigits: 21 });
    return String(value);
  }
  return typeof value === "string" ? value : "";
}

/** An artifact a tool opened that never got any content (the tool needed approval, was refused or failed). */
export function empty(a: Artifact | null): boolean {
  if (!a || a.state !== "drafting") return false;
  switch (a.kind) {
    case "mail":
      return !a.body;
    case "post":
      return !a.text;
    case "docs":
      return a.units.length === 0;
    case "calc":
      return a.lines.length === 0;
    case "invite":
      return !a.with;
  }
}

/** Each case's documents: what the Accounting window attaches and the CEO's case list offers (F1). */
export type CaseDocs = Pick<Studio, "files" | "served" | "checks">;
export const emptyCaseDocs = (): CaseDocs => ({ files: {}, served: {}, checks: {} });

/**
 * One doc.* event into a case's documents (one rule for every reader): a build starts its checks
 * over; the latest workpaper and memo are the case's files (the books stay in the firm); a
 * version is served once all three of its documents passed, never for a flagged case (`hide`).
 */
export function stepCaseDocs<T extends CaseDocs>(st: T, ev: UTaxEvent, hide: boolean): T {
  const cid = ev.case_id;
  if (!cid) return st;
  const p: P = isObj(ev.payload) ? ev.payload : {};
  const doc = str(p, "doc");
  if (ev.type === "doc.step") return doc === "workpaper" && num(p, "step") === 1 ? { ...st, checks: { ...st.checks, [cid]: {} } } : st;
  if (ev.type === "doc.created") {
    const file = line(str(p, "file_name"), 100);
    if (!file || (doc !== "workpaper" && doc !== "memo")) return st;
    const files = { ...st.files, [cid]: { ...st.files[cid], [doc]: file } };
    const ids = Object.keys(files);
    if (ids.length > 50) delete files[ids[0]];
    return { ...st, files };
  }
  if (ev.type === "doc.verified" && (doc === "workpaper" || doc === "memo" || doc === "books")) {
    const checks = { ...(st.checks[cid] ?? {}), [doc]: p.ok === true };
    const f = st.files[cid];
    const next = { ...st, checks: { ...st.checks, [cid]: checks } };
    return !hide && checks.workpaper && checks.memo && checks.books && f?.workpaper && f.memo ? { ...next, served: { ...next.served, [cid]: [f.workpaper, f.memo] } } : next;
  }
  return st;
}

/** Fold one event into the studio. Unknown or malformed events change nothing. */
export function applyStudio(st0: Studio, ev: UTaxEvent, ctx: StudioCtx): Studio {
  const p: P = isObj(ev.payload) ? ev.payload : {};
  const key = `${ev.id}`;
  const base = { key, ts: ev.ts, caseId: ev.case_id };
  const hide = ctx.flagged(ev.case_id);
  const st = stepCaseDocs(st0, ev, hide);

  if (ev.type === "agent.activity") {
    const seat = seatOf(ev);
    if (!seat) return st;
    const desk = st.desks[seat];
    const kind = str(p, "kind");
    const tool = bareTool(line(str(p, "tool"), 60)).slice(0, 40) || null;
    if (kind === "thinking") return put(st, seat, { act: { ts: ev.ts, kind: "thinking", tool: null } });
    if (kind === "tool_started" && tool) {
      let art = artFor(tool, base);
      // The relay can log tool_started after the tool's own results (a stream opened late, or the
      // gateway lock): a finished artifact of that kind from just now stays, and nothing moves.
      const prior = desk.art;
      if (art && prior && prior.kind === art.kind && prior.state !== "drafting" && Date.parse(ev.ts) - Date.parse(prior.ts) < 15_000) art = null;
      // Only a tool that makes something moves auto-follow (board reads and chatter don't).
      return put(st, seat, { act: { ts: ev.ts, kind: "tool", tool }, ...(art ? { art } : {}) }, art ? key : undefined);
    }
    if (kind === "writing") {
      const text = typeof p.text === "string" ? block(p.text, 600) : "";
      const notes = text && !ctx.seatFlagged(seat) ? { text, ts: ev.ts } : desk.notes;
      return put(st, seat, { act: { ts: ev.ts, kind: "writing", tool: null }, notes });
    }
    if (kind === "tool_done") return put(st, seat, { act: { ts: ev.ts, kind: "thinking", tool: null }, art: empty(desk.art) ? null : desk.art });
    if (kind === "idle") {
      // A composer that never got anything closes; a finished artifact (or documents still building) stays.
      return put(st, seat, { act: null, art: empty(desk.art) || (desk.art?.state === "drafting" && desk.art.kind !== "docs") ? null : desk.art });
    }
    return st;
  }

  if (ev.type === "case.flagged") {
    if (!ev.case_id || p.kind !== "possible_injection") return st;
    // Everything already on a desk from that case goes, and agents' notes too (they can't be tied to a case).
    let next = st;
    for (const seat of SEATS) {
      const d = next.desks[seat];
      const art = d.art && (d.art.caseId === ev.case_id || (d.art.kind === "mail" && d.art.caseId === null)) ? null : d.art;
      if (art !== d.art || d.notes) next = put(next, seat, { art, notes: null });
    }
    return next;
  }

  const seat = (SEATS as readonly string[]).includes(ev.actor) ? (ev.actor as Seat) : null;
  if (!seat) return st;
  const who = ctx.name(ev.case_id);
  const prev = st.desks[seat].art;
  const act = settled(st.desks[seat].act);

  switch (ev.type) {
    case "client.asked":
    case "memo.sent":
    case "client.followed_up":
    case "hr.outreach": {
      // v1.18: admin readers get `sent` (the email as it actually left); public readers the masked fields.
      const sent: P = isObj(p.sent) ? p.sent : {};
      const subject = line(str(sent, "subject") || str(p, "subject"), 200);
      const body = block(str(sent, "body") || str(p, "body"), MAX_TEXT);
      if (!body && !hide) return put(st, seat, { act, art: prev && prev.kind === "mail" && prev.state === "drafting" ? null : prev }); // nothing to show: the step page says it
      const to = line(str(sent, "to_masked"), 80) || (ev.type === "hr.outreach" ? line(str(p, "label"), 40) || "a candidate" : line(str(p, "to_masked"), 80) || who);
      const fallbackSubject =
        ev.type === "memo.sent" ? `Your tax estimate, ${who}` : ev.type === "client.asked" ? "A few details for your estimate" : ev.type === "hr.outreach" ? "A tax-season gig, and a call" : "Following up";
      const files = ev.type === "memo.sent" && ev.case_id ? st.files[ev.case_id] : undefined;
      const attachments = files ? [files.workpaper, files.memo].filter((f): f is string => !!f) : [];
      // The composer the tool opened becomes this mail (no re-mount: it types the real text in).
      const k = prev?.kind === "mail" && prev.state === "drafting" ? prev.key : key;
      const art: Artifact = {
        ...base,
        key: k,
        kind: "mail",
        app: ev.type === "hr.outreach" ? "AgentMail · hiring" : "AgentMail · compose",
        to: hide ? "Hidden" : to,
        subject: hide ? "Hidden" : subject || fallbackSubject,
        body: hide ? HIDDEN : body,
        attachments: hide ? [] : attachments,
        state: ev.status === "ok" ? "done" : "failed",
      };
      return put(st, seat, { art, act });
    }
    case "post.published": {
      const text = block(str(p, "text"), 600);
      if (!text) return st;
      const k = prev?.kind === "post" && prev.state === "drafting" ? prev.key : key;
      const state = ev.status === "ok" ? "done" : "failed";
      return put(st, seat, { art: { ...base, key: k, kind: "post", channel: line(str(p, "channel"), 20).toLowerCase(), text, image: ctx.image(p.image_url) ?? null, state }, act });
    }
    case "tax.computed": {
      const auth = num(p, "authorized_method_cents");
      const par = num(p, "assumed_par_method_cents");
      const total = num(p, "total_cents");
      if (auth === null || par === null || total === null || ![auth, par, total].every((c) => Number.isSafeInteger(c) && c >= 0)) return st;
      const chosen = str(p, "chosen");
      return put(st, seat, {
        art: {
          ...base,
          kind: "calc",
          lines: [
            { label: "Authorized Shares Method", value: formatCents(auth), chosen: chosen === "authorized" },
            { label: "Assumed Par Value Method", value: formatCents(par), chosen: chosen !== "authorized" },
            { label: `Total due for ${who}`, value: formatCents(total) },
          ],
          state: "done",
        },
        act,
      });
    }
    case "call.booked": {
      const minutes = num(p, "minutes") ?? 0;
      const w = p.kind === "candidate" ? line(str(p, "invitee_masked"), 40) || "a candidate" : who;
      return put(st, seat, { art: { ...base, kind: "invite", with: w, minutes, state: "done" }, act });
    }
    case "doc.step": {
      const doc = str(p, "doc");
      if (doc !== "workpaper" && doc !== "memo" && doc !== "books") return st;
      const detail = line(str(p, "detail"), 200);
      const fresh = doc === "workpaper" && num(p, "step") === 1;
      const reuse = prev?.kind === "docs" && (prev.caseId === ev.case_id || (prev.caseId === null && prev.units.length === 0)) && !(fresh && prev.units.length > 0);
      const cur: Artifact & DocsArt = reuse && prev?.kind === "docs" ? { ...prev, caseId: ev.case_id } : { ...base, kind: "docs", workpaper: null, memo: null, books: null, units: [], state: "drafting" };
      const units = [...cur.units];
      let next: Artifact & DocsArt = { ...cur, ts: ev.ts };
      if (doc === "memo") {
        const m = cur.memo ?? { file: "Memo.docx", sections: [], verified: null };
        const title = detail.replace(/^Memo:\s*/i, "") || `Part ${num(p, "step") ?? m.sections.length + 1}`;
        next = { ...next, memo: { ...m, sections: [...m.sections, title].slice(-12), verified: null } };
        units.push({ doc });
      } else {
        const b = cur[doc] ?? emptyBook(doc === "books" ? "U-Tax_Books.xlsx" : "Workpaper.xlsx");
        // v1.18: admin readers get the sheet as written (each step resends its sheet); never for a flagged case.
        const cells = !hide && Array.isArray(p.cells) ? p.cells.filter(isObj).slice(0, 400) : [];
        if (cells.length) {
          const all = { ...b.cells };
          const sheets = [...b.sheets];
          for (const c of cells) {
            const sheet = line(str(c, "sheet"), 40) || "Sheet1";
            const ref = str(c, "cell").toUpperCase();
            if (!/^[A-Z]{1,2}[0-9]{1,4}$/.test(ref)) continue;
            const value = line(cellText(c.value, str(c, "fmt")), 80);
            if (!sheets.includes(sheet)) sheets.push(sheet);
            const had = all[sheet]?.[ref];
            all[sheet] = { ...(all[sheet] ?? {}), [ref]: value };
            if (had !== value) units.push({ doc, sheet, cell: ref });
          }
          next = { ...next, [doc]: { ...b, cells: all, sheets, verified: null } };
        } else {
          const tab = doc === "books" ? "Books" : detail.split(":")[0].trim() || "Workpaper";
          const sheets = b.sheets.includes(tab) ? b.sheets : [...b.sheets, tab];
          next = { ...next, [doc]: { ...b, sheets, verified: null, lines: [...b.lines, { tab, text: detail.split(":").slice(1).join(":").trim() || detail }].slice(-40) } };
          units.push({ doc, sheet: tab });
        }
      }
      next = { ...next, units: units.slice(-MAX_UNITS), state: "drafting" };
      return put(st, seat, { art: next, act });
    }
    case "doc.created": {
      const doc = str(p, "doc");
      const file = line(str(p, "file_name"), 100);
      const cid = ev.case_id;
      let art = prev;
      if (prev?.kind === "docs" && prev.caseId === cid && file && (doc === "workpaper" || doc === "memo" || doc === "books")) {
        const d = prev[doc];
        art = d ? { ...prev, [doc]: { ...d, file, built: true } } : prev;
      }
      return put(st, seat, { art });
    }
    case "doc.verified": {
      const doc = str(p, "doc");
      if (doc !== "workpaper" && doc !== "memo" && doc !== "books") return st;
      if (prev?.kind !== "docs" || prev.caseId !== ev.case_id) return st;
      const d = prev[doc];
      if (!d) return st;
      const next = { ...prev, [doc]: { ...d, verified: p.ok === true } };
      const all = (["workpaper", "memo", "books"] as const).every((k) => !next[k] || next[k]?.verified !== null);
      return put(st, seat, { art: { ...next, state: all ? "done" : "drafting" }, act });
    }
  }
  return st;
}

/** Whether a desk's activity or notes are still current at `now` (no idle ever came, or it's history). */
export const current = (ts: string | undefined, now: number, ms = ACTIVITY_MS) => {
  const at = Date.parse(ts ?? "");
  return Number.isFinite(at) && now - at < ms && at - now < ms;
};
