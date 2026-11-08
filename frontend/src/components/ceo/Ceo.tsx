"use client";

// The CEO console (/ceo, contract §5 and §8). The CEO signs in on /login with email and password
// (D33: a session kept only in this tab, sent only as a Bearer header), hears and reads the brief,
// and makes the calls only a human makes: approvals, standing orders, hiring, blocked cases.

import Link from "next/link";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { ApiError, API_URL } from "@/lib/api";
import type { UTaxEvent } from "@/theater/events";
import { briefAudioUrl, fetchAllEvents, type Endpoints } from "@/theater/live";
import {
  ACTION_LABEL,
  ACTION_TYPES,
  approveReleases,
  briefParagraphs,
  briefSince,
  canDecide,
  ceoError,
  consoleFromState,
  emptyLog,
  foldLog,
  LAST_SEEN_KEY,
  logStillMatches,
  logWasReset,
  MAX_ORDER_CHARS,
  orderBody,
  outdatedApprovals,
  parseBrief,
  readableSummary,
  readLastSeen,
  STAGE_LABEL,
  UNBLOCK,
  type ActionType,
  type Approval,
  type Brief,
  type Candidate,
  type CaseRow,
  type Console,
  type TrustState,
  type LogView,
} from "@/ceo/ceo";
import { announceAgentAudio, CeoVoice, type VoiceSlots } from "./CeoVoice";
import { logout, useSessionToken, writeSession } from "@/ceo/session";
import { foldOrders, taskChip, type OrderCard, type TaskStatus } from "@/ceo/orders";
import { CaseList } from "./CaseList";
import { Payments } from "./Payments";
import { useConsoleLock } from "@/ceo/popout";
import { current, emptyCaseDocs, stepCaseDocs, type CaseDocs } from "@/office/artifact";
import { SEAT_NAME, SEATS } from "@/office/workspace";
import { API, authJson, newerApproval, REQUEST_MS } from "./gateway";
import "./ceo.css";
import { Emblem } from "@/components/ui/Emblem";
import "./ceo-page.css";
import { DeptAvatar } from "@/components/ui/DeptAvatar";
import { Switch } from "@/components/ui/Switch";
import { EMPTY_ART } from "@/components/ui/art";
import { AGENT_COUNT } from "@/theater/rail";
import { formatCents, formatClock, formatInt, formatUsd } from "@/theater/format";

/** D40: the department that does each kind of action (its avatar on approvals and standing orders). */
const ACTION_SEAT: Readonly<Record<string, string>> = { email: "acct", social_post: "mkt", calendar_invite: "intake", voice_screen: "hr" };

// ---------------------------------------------------------------- F45: the /ceo page's live figures
// Only what the console already fetches (the brief's counts, /api/state money, the admin log); a
// figure the gateway didn't send is hidden, never shown as zero or as a sample.

const isRec = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const nat = (v: unknown) => (typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null);
const text = (v: unknown) => (typeof v === "string" ? v : "");
const DECIMAL = /^\d{1,12}(\.\d{1,18})?$/;

interface BriefCounts {
  clients: number | null;
  sent: number | null;
}
/** GET /api/ceo/brief's `counts` ({clients, sent, blocked}). */
function countsOf(raw: unknown): BriefCounts | null {
  if (!isRec(raw) || !isRec(raw.counts)) return null;
  return { clients: nat(raw.counts.clients), sent: nat(raw.counts.sent) };
}

interface Money {
  savedCents: number | null;
  spentUsd: string | null;
  byProvider: { name: string; usd: string; share: number }[];
}
/** /api/state `money`: tax saved, estimated spend, and spend by provider (for the stacked bar). */
function moneyOf(raw: unknown): Money | null {
  if (!isRec(raw) || !isRec(raw.money)) return null;
  const m = raw.money;
  const rows = isRec(m.spent_by_provider)
    ? Object.entries(m.spent_by_provider).filter((e): e is [string, string] => typeof e[1] === "string" && DECIMAL.test(e[1]) && Number(e[1]) > 0)
    : [];
  const total = rows.reduce((s, [, v]) => s + Number(v), 0);
  return {
    savedCents: nat(m.tax_saved_cents),
    spentUsd: typeof m.spent_usd === "string" && DECIMAL.test(m.spent_usd) ? m.spent_usd : null,
    byProvider: total > 0 ? rows.sort((a, b) => Number(b[1]) - Number(a[1])).map(([name, usd]) => ({ name, usd, share: Number(usd) / total })) : [],
  };
}
/** Bar colours for the spend legend: department colours, never lime (lime is for actions). */
const SPEND_COLORS = ["var(--intake)", "var(--mkt)", "var(--acct)", "var(--tax)", "var(--hr)", "var(--faint)"];

interface Activity {
  id: number;
  seat: string;
  text: string;
  amt: string | null;
  at: string;
  tx: string | null;
}
const SEAT_SET: ReadonlySet<string> = new Set(SEATS);
const ACTIVITY_MAX = 8;
const CHANNEL_NAME: Readonly<Record<string, string>> = { x: "X", twitter: "X", linkedin: "LinkedIn" };
const shortTx = (h: string) => (h.length > 14 ? `${h.slice(0, 6)}…${h.slice(-4)}` : h || null);

/** One line of the live activity feed, from an admin-log event (null: not a line the CEO needs). */
function activityOf(ev: UTaxEvent, name: (id: string | null) => string): Activity | null {
  if (ev.status === "failed" || ev.status === "skipped") return null;
  const p = isRec(ev.payload) ? ev.payload : {};
  const cents = (v: unknown) => (nat(v) !== null ? formatCents(v as number) : null);
  const line = (fallbackSeat: string, t: string, amt: string | null = null, tx: string | null = null): Activity => ({
    id: ev.id,
    seat: SEAT_SET.has(ev.actor) ? ev.actor : fallbackSeat,
    text: t,
    amt,
    at: formatClock(ev.ts),
    tx,
  });
  const who = name(ev.case_id);
  switch (ev.type) {
    case "memo.sent":
      return line("acct", `Sent ${who} their memo`);
    case "tax.computed":
      return line("tax", `Computed ${who}'s franchise tax`, cents(p.total_cents));
    case "qa.passed":
      return line("qa", `Checked ${who}: every number passed`);
    case "qa.failed":
      return line("qa", `Sent ${who}'s memo back for a fix`);
    case "client.asked":
      return line("intake", `Asked ${who} for the missing numbers`);
    case "call.booked":
      return line("intake", `Booked a review call with ${who}`);
    case "case.blocked":
      return line("cos", `${who} needs a human`);
    case "post.published": {
      const ch = text(p.channel);
      return line("mkt", `Posted to ${CHANNEL_NAME[ch.toLowerCase()] ?? (ch || "social")}`);
    }
    case "payment.sent": {
      // Testnet tokens: shown with their token name, never as dollars.
      const amt = DECIMAL.test(text(p.amount_usd)) && text(p.token) ? `${text(p.amount_usd)} ${text(p.token)}` : null;
      return line("acct", `Paid ${text(p.contractor) || "a contractor"}`, amt, shortTx(text(p.tx_hash)));
    }
    case "payment.received":
      return line("acct", `${who} paid`, text(p.currency).toLowerCase() === "usd" ? cents(p.amount_cents) : null);
    case "job.verified":
      return line("qa", "Checked the contractor's work");
    case "hr.applicant":
      return line("hr", `New applicant: ${text(p.name_masked) || "a candidate"}`);
    case "hr.shortlisted":
      return line("hr", `Shortlisted ${text(p.name_masked) || "a candidate"}`);
    case "hr.hired":
      return line("hr", `Hired ${text(p.name_masked) || "a candidate"}`);
    default:
      return null;
  }
}
/** The newest lines first; an event already folded (the log read re-reads the last one) never repeats. */
function foldActivity(prev: readonly Activity[], events: readonly UTaxEvent[], name: (id: string | null) => string): Activity[] {
  const seen = prev.length ? prev[0].id : 0;
  const fresh: Activity[] = [];
  for (const ev of events) {
    if (!isRec(ev) || typeof ev.id !== "number" || ev.id <= seen) continue;
    const a = activityOf(ev, name);
    if (a) fresh.unshift(a);
  }
  return fresh.length ? [...fresh, ...prev].slice(0, ACTIVITY_MAX) : (prev as Activity[]);
}

/** The hiring board's columns (contract stages); declined candidates are listed under the board. */
const KAN_COLS = [
  { stage: "applicant", title: "applied", empty: "—" },
  { stage: "screened", title: "screened", empty: "HR calls next" },
  { stage: "shortlisted", title: "shortlisted", empty: "—" },
  { stage: "hired", title: "hired", empty: "your yes" },
] as const;
/** The channel chip on an approval card (from its kind; the approval carries no channel). */
const ACTION_CHANNEL: Readonly<Record<string, string>> = { email: "email", social_post: "social", calendar_invite: "calendar", voice_screen: "phone" };
const seatName = (seat: string) => (SEAT_SET.has(seat) ? SEAT_NAME[seat as keyof typeof SEAT_NAME] : "U-Tax");

/** D41 F47: each task status, in words (the stepper's chip and its screen-reader line). */
const TASK_WORDS: Readonly<Record<TaskStatus, string>> = {
  queued: "queued",
  running: "running now",
  done: "done",
  waiting_on_you: "waiting on you",
  blocked: "blocked",
  failed: "failed",
  timeout: "timed out",
  cancelled: "cancelled",
};

type RailItem = "brief" | "approvals" | "cases" | "hiring" | "standing" | "money";

const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
/** Endpoints for the shared URL builders: the token never goes in a URL here (it rides the header). */
const NO_QUERY_TOKEN: Endpoints = { base: API_URL, token: "" };
const POLL_MS = 4000;
/** Data older than this is marked stale in the header. */
const STALE_MS = 15000;

const hasSpeech = () => typeof window !== "undefined" && "speechSynthesis" in window;
/** The brief, read by the browser. Voice mode counts it as agent audio (and mutes its mic) while it plays. */
let sayTurn = 0;
let sayTimer: ReturnType<typeof setTimeout> | undefined;
function say(text: string) {
  if (!hasSpeech()) return;
  window.speechSynthesis.cancel();
  clearTimeout(sayTimer);
  const turn = ++sayTurn; // only this playback's end (or stall) may say the brief stopped
  const u = new SpeechSynthesisUtterance(text);
  announceAgentAudio("brief", true);
  u.onend = u.onerror = () => {
    if (turn !== sayTurn) return;
    clearTimeout(sayTimer);
    announceAgentAudio("brief", false);
  };
  // A stalled utterance must not keep the voice mic muted forever: silence it, then say it stopped.
  sayTimer = setTimeout(() => {
    if (turn !== sayTurn) return;
    if (window.speechSynthesis.speaking) window.speechSynthesis.cancel();
    announceAgentAudio("brief", false);
  }, Math.min(120_000, 3000 + text.split(/\s+/).length * 450));
  window.speechSynthesis.speak(u);
}
const hush = () => {
  if (hasSpeech()) window.speechSynthesis.cancel();
};

/** The rows a confirm can belong to, from fresh data. */
function rowKeys(st: Console | null, log: LogView): Set<string> {
  const keys = new Set<string>([...log.pending.keys()].map((id) => `ap:${id}`));
  for (const c of st?.cases ?? []) if (c.status === "BLOCKED") keys.add(`ub:${c.caseId}`);
  for (const c of st?.candidates ?? []) if (canDecide(c.stage)) keys.add(`hr:${c.candidateId}`);
  for (const t of ACTION_TYPES) keys.add(`tr:${t}`);
  return keys;
}

/** Where the next login brief starts (only the session that showed this brief writes it). */
function rememberSeen(b: Brief) {
  if (b.lastEventId === null) return;
  try {
    localStorage.setItem(LAST_SEEN_KEY, String(b.lastEventId));
  } catch {
    /* fine */
  }
}

const errorOf = (e: unknown) => (e instanceof ApiError ? ceoError(e.status, e.code, e.message) : "The gateway sent something unexpected. Try again in a moment.");

interface Confirm {
  key: string;
  prompt: string;
  label: string;
  run: () => void;
  /** An Approve confirm: its prompt is recomputed on every render, and it disarms if its row goes outdated. */
  approval?: Approval;
}

/**
 * The CEO console. `variant="panel"`: inside /office (the office has its own header and
 * sign-out). Otherwise the standalone /ceo page. One console is live at a time across every
 * window (src/ceo/popout.ts): a /ceo window takes it over when it opens, the panel waits for it.
 * A console that isn't live renders a notice only (no polling, no ear, no voice).
 */
export function Ceo({ variant = "page", onElsewhere }: { variant?: "page" | "panel"; onElsewhere?: (elsewhere: boolean) => void } = {}) {
  const token = useSessionToken();
  const { state, take, everElsewhere } = useConsoleLock(!!token, variant === "page");
  useEffect(() => {
    onElsewhere?.(!!token && state === "elsewhere");
  }, [onElsewhere, state, token]);
  // No Web Locks: only the /ceo page runs a console (two could never be fenced from each other).
  if (token && variant === "panel" && state === "unsupported") {
    return (
      <div className="ceo in-panel">
        <main className="elsewhere">
          <p>This browser can&rsquo;t share the console between windows, so it runs on its own page.</p>
          <a className="secondary signin-link" href="/ceo" target="_blank" rel="opener">
            Open the CEO console
          </a>
        </main>
      </div>
    );
  }
  if (token && (state === "waiting" || state === "elsewhere")) {
    return (
      <div className={`ceo${variant === "panel" ? " in-panel" : ""}`}>
        <main className="elsewhere">
          {state === "waiting" ? (
            <p className="dim">Opening the console…</p>
          ) : (
            <>
              <p>The console is open in another window, so it listens and speaks there.</p>
              <button className="secondary" onClick={take}>
                Use it here
              </button>
            </>
          )}
        </main>
      </div>
    );
  }
  // The brief reads itself only when a /ceo window opens (not the panel, not a tab getting the console back later).
  return <CeoConsole variant={variant} autoplay={variant === "page" && !everElsewhere} />;
}

function CeoConsole({ variant, autoplay }: { variant: "page" | "panel"; autoplay: boolean }) {
  const token = useSessionToken();
  /** Why the console signed out (a 401: the session expired or was revoked), shown on the sign-in link. */
  const [signInError, setSignInError] = useState<string | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [view, setView] = useState<Console | null>(null);
  const [log, setLog] = useState<LogView>(emptyLog);
  /** The poll's own error: cleared by the next good poll. */
  const [error, setError] = useState<string | null>(null);
  /** The last action's error: stays until the next action (a refresh never hides it). */
  const [actionError, setActionError] = useState<string | null>(null);
  const [briefError, setBriefError] = useState<string | null>(null);
  /** Approvals the gateway answered 409 superseded for (no event marks them; ids reset with the log). */
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set());
  /** The newer approval to decide after a 409 superseded. */
  const [highlight, setHighlight] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  /** D37: the CEO's orders, each with its live steps (folded from the same admin log the console reads). */
  const [orders, setOrders] = useState<OrderCard[]>([]);
  const ordersRef = useRef<OrderCard[]>([]);
  /** F1: each case's verified documents (folded from the same log), reachable after the desk moves on. */
  const [served, setServed] = useState<CaseDocs["served"]>({});
  const caseDocsRef = useRef<CaseDocs>(emptyCaseDocs());
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [announce, setAnnounce] = useState("");
  const [order, setOrder] = useState("");
  const [orderSent, setOrderSent] = useState(false);
  /** F45 (the /ceo page): the brief's counts, /api/state money and the latest activity, read from what the console already fetches. */
  const [briefCounts, setBriefCounts] = useState<BriefCounts | null>(null);
  const [money, setMoney] = useState<Money | null>(null);
  const [activity, setActivity] = useState<Activity[]>([]);
  const activityRef = useRef<Activity[]>([]);
  const [railOn, setRailOn] = useState<RailItem>("brief");
  /** F45: where the voice card puts STOP (the rail), the top bar's mic and its alerts (top of main). */
  const [stopSlot, setStopSlot] = useState<HTMLElement | null>(null);
  const [micSlot, setMicSlot] = useState<HTMLElement | null>(null);
  const [alertsSlot, setAlertsSlot] = useState<HTMLElement | null>(null);
  const slots = useMemo<VoiceSlots>(() => ({ stop: stopSlot, mic: micSlot, alerts: alertsSlot }), [stopSlot, micSlot, alertsSlot]);
  /** Bumped on every sign-in and sign-out: work from an older session never writes state. */
  const session = useRef(0);
  const logRef = useRef<LogView>(emptyLog());
  const briefRef = useRef<Brief | null>(null);
  /** Refreshes run one at a time, in order (the poll and the one after an action never overlap). */
  const chain = useRef<Promise<void>>(Promise.resolve());
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrl = useRef<string | null>(null);
  const audioSeq = useRef(0);
  /** Voice mode's ear on the log: each refresh hands it the new events (hands-free speaks approvals and blocks). */
  const voiceFeed = useRef<((events: readonly UTaxEvent[], reset: boolean) => void) | null>(null);

  const stopAudio = useCallback(() => {
    audioSeq.current += 1;
    audioRef.current?.pause();
    audioRef.current = null;
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    audioUrl.current = null;
    hush();
    sayTurn += 1; // a stopped brief's own end and stall timer are void
    clearTimeout(sayTimer);
    announceAgentAudio("brief", false);
  }, []);

  const signOut = useCallback(
    (why: string | null) => {
      session.current += 1;
      stopAudio();
      ordersRef.current = [];
      setOrders([]);
      caseDocsRef.current = emptyCaseDocs();
      setServed({});
      activityRef.current = [];
      setActivity([]);
      setMoney(null);
      setBriefCounts(null);
      if (why === null) void logout(); // the CEO's own Sign out revokes the session
      else writeSession(null); // a 401: it is already dead at the gateway
      briefRef.current = null;
      logRef.current = emptyLog();
      setBrief(null);
      setView(null);
      setLog(logRef.current);
      setError(null);
      setActionError(null);
      setBriefError(null);
      setDismissed(new Set());
      setHighlight(null);
      setNotice(null);
      setUpdatedAt(null);
      setBusy(null);
      setConfirm(null);
      setAnnounce("");
      setOrder("");
      setOrderSent(false);
      setSignInError(why);
    },
    [stopAudio],
  );

  /** A 401 means the session expired or was revoked: back to sign-in. Anything else shows in the banner. */
  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) return signOut(errorOf(e));
      setError(errorOf(e));
    },
    [signOut],
  );

  /** The brief's audio from the gateway (fetched with the header), else browser speech. Newest call wins. */
  const playBrief = useCallback(
    async (b: Brief, tok: string) => {
      stopAudio();
      const mine = audioSeq.current;
      const current = () => mine === audioSeq.current;
      try {
        if (b.briefEventId === null) throw new Error("no brief event");
        const r = await fetch(briefAudioUrl(NO_QUERY_TOKEN, b.briefEventId), {
          headers: { Authorization: `Bearer ${tok}` },
          signal: AbortSignal.timeout(REQUEST_MS),
        });
        if (!r.ok) throw new Error(`audio ${r.status}`);
        const blob = await r.blob();
        if (!current()) return;
        audioUrl.current = URL.createObjectURL(blob);
        const a = new Audio(audioUrl.current);
        audioRef.current = a;
        a.onended = a.onpause = a.onerror = () => announceAgentAudio("brief", false);
        announceAgentAudio("brief", true);
        await a.play();
      } catch {
        if (current()) say(b.text); // no voice key (503), or autoplay blocked: the browser reads it
      }
    },
    [stopAudio],
  );

  /** One refresh: admin /api/state, then the admin log after what's already folded (from 0 after a reset). */
  const doRefresh = useCallback(
    async (mine: number, tok: string) => {
      if (mine !== session.current) return;
      try {
        const rawState = await authJson<unknown>(tok, `${API}/state`);
        const st = consoleFromState(rawState);
        if (mine !== session.current) return;
        setMoney(moneyOf(rawState));
        let reset = false;
        const restart = () => {
          reset = true;
          // Ids are reused after a reset: the old rows go off screen right away, before the reload
          // (also when the reload fails: the next poll starts from 0 without knowing it reset).
          logRef.current = emptyLog();
          setLog(logRef.current);
          ordersRef.current = [];
          setOrders([]);
          caseDocsRef.current = emptyCaseDocs();
          setServed({});
          activityRef.current = [];
          setActivity([]);
          setView(null);
          setConfirm(null);
          setNotice("The gateway's log restarted, so the console reloaded it. Check the lists before acting.");
          setDismissed(new Set()); // approval ids start over with the new log
          setHighlight(null);
        };
        if (st !== null && logWasReset(st.lastEventId, logRef.current.lastId)) restart();
        const getJson = (url: string) => authJson<unknown>(tok, url);
        // One read: from the event we stopped at (it must still be the same one), then everything after it.
        const from = logRef.current.lastId;
        let { events } = await fetchAllEvents(NO_QUERY_TOKEN, getJson, Math.max(0, from - 1), 200);
        if (mine !== session.current) return;
        if (from > 0 && !logStillMatches(logRef.current, { data: events })) {
          restart(); // a reset that already regrew past our id
          ({ events } = await fetchAllEvents(NO_QUERY_TOKEN, getJson, 0, 200));
          if (mine !== session.current) return;
        }
        logRef.current = foldLog(logRef.current, events as UTaxEvent[]);
        {
          const names = new Map(st?.cases.map((c) => [c.caseId, c.name]) ?? []);
          const fl = new Set(st?.cases.filter((c) => c.flagged).map((c) => c.caseId) ?? []);
          const nameOf = (id: string | null) => (id !== null && fl.has(id) ? "a flagged client" : id !== null ? (names.get(id) ?? "a client") : "a client");
          ordersRef.current = foldOrders(reset ? [] : ordersRef.current, events as UTaxEvent[], {
            name: nameOf,
            flagged: (id) => id !== null && fl.has(id),
            image: () => undefined,
          });
          setOrders(ordersRef.current);
          activityRef.current = foldActivity(reset ? [] : activityRef.current, events as UTaxEvent[], nameOf);
          setActivity(activityRef.current);
          caseDocsRef.current = (events as unknown[]).filter((e): e is UTaxEvent => typeof e === "object" && e !== null).reduce((d, ev) => stepCaseDocs(d, ev, ev.case_id !== null && fl.has(ev.case_id)), reset ? emptyCaseDocs() : caseDocsRef.current);
          setServed(caseDocsRef.current.served);
        }
        setLog(logRef.current);
        voiceFeed.current?.(events as UTaxEvent[], reset);
        if (st) setView(st);
        // A confirm whose row is gone (decided elsewhere, unblocked, reloaded) must not come back armed.
        setConfirm((c) => (c && !rowKeys(st, logRef.current).has(c.key) ? null : c));
        setError(null);
        setUpdatedAt(Date.now());
      } catch (e) {
        if (mine === session.current) fail(e);
      }
    },
    [fail],
  );
  const refreshNow = useCallback(
    (mine: number, tok: string) => {
      chain.current = chain.current.then(() => doRefresh(mine, tok));
      return chain.current;
    },
    [doRefresh],
  );

  /** The login brief: since what this device last saw (from the start if the gateway's log is behind it). */
  const loadBrief = useCallback(
    async (tok: string, stateLastId: number | null): Promise<Brief & { counts: BriefCounts | null }> => {
      let lastSeen = 0;
      try {
        lastSeen = readLastSeen(localStorage.getItem(LAST_SEEN_KEY));
      } catch {
        /* no storage: the brief covers everything */
      }
      const raw = await authJson<unknown>(tok, `${API}/ceo/brief?since_id=${briefSince(lastSeen, stateLastId)}`);
      const b = parseBrief(raw);
      if (!b) throw new Error("malformed brief");
      return { ...b, counts: countsOf(raw) };
    },
    [],
  );

  // Signed in: the brief (if this tab hasn't one yet), then a refresh every few seconds, one at a time.
  useEffect(() => {
    if (!token) return;
    session.current += 1;
    const mine = session.current;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let briefing = false;
    const briefOnce = async () => {
      if (briefing) return;
      briefing = true;
      try {
        const st = consoleFromState(await authJson<unknown>(token, `${API}/state`));
        const b = await loadBrief(token, st?.lastEventId ?? null);
        if (mine !== session.current) return;
        briefRef.current = b;
        setBrief(b);
        setBriefCounts(b.counts);
        setBriefError(null);
        if (autoplay) {
          rememberSeen(b);
          if (document.visibilityState === "visible") void playBrief(b, token); // the panel shows the brief; its Play button reads it
        }
      } catch (e) {
        if (mine !== session.current) return;
        if (e instanceof ApiError && e.status === 401) fail(e);
        else setBriefError(`${errorOf(e)} Retrying.`);
      } finally {
        briefing = false;
      }
    };
    const tick = async () => {
      if (!briefRef.current && mine === session.current) void briefOnce(); // retried each poll until it lands, never blocking the lists
      await refreshNow(mine, token);
      if (mine === session.current) timer = setTimeout(() => void tick(), POLL_MS);
    };
    void tick();
    const clock = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      session.current += 1;
      clearTimeout(timer);
      clearInterval(clock);
    };
  }, [token, refreshNow, loadBrief, playBrief, fail, autoplay]);

  useEffect(() => () => stopAudio(), [stopAudio]);

  // After a 409 superseded, bring the newer approval into view.
  useEffect(() => {
    if (highlight) document.getElementById(`row-${highlight}`)?.scrollIntoView({ block: "center" });
  }, [highlight]);

  /** One action at a time; the lists refresh right after, in order. */
  const act = async (key: string, path: string, body: unknown, done: string) => {
    if (busy || !token) return;
    const mine = session.current;
    setBusy(key);
    setConfirm(null);
    setActionError(null);
    setHighlight(null);
    try {
      await authJson(token, `${API}${path}`, { method: "POST", body: JSON.stringify(body) });
      if (mine !== session.current) return;
      setAnnounce(done);
      if (path === "/voice") {
        setOrder("");
        setOrderSent(true);
      }
    } catch (e) {
      if (mine === session.current) {
        const id = (body as { approval_id?: unknown }).approval_id;
        if (e instanceof ApiError && e.status === 401) signOut(errorOf(e));
        else if (e instanceof ApiError && e.code === "superseded" && typeof id === "string") {
          // v1.7.8: drop the stale entry and point at the newer one (the gateway writes no event for it).
          const by = newerApproval(e);
          setDismissed((d) => new Set(d).add(id));
          setHighlight(by);
          setActionError(
            by
              ? "That approval was out of date. Decide the newer one, highlighted below."
              : "That approval was out of date: the case changed, and the crew will ask again if it still applies.",
          );
        } else setActionError(errorOf(e));
      }
    } finally {
      if (mine === session.current) {
        await refreshNow(mine, token);
        setBusy(null);
      }
    }
  };

  const ask = (c: Confirm) => setConfirm(c);

  /** Voice mode changed something: refresh the lists now, in order with the poll. */
  const refreshAfterVoice = useCallback(() => {
    if (token) void refreshNow(session.current, token);
  }, [token, refreshNow]);
  const voiceSignOut = useCallback((why: string) => signOut(why), [signOut]);
  const caseNames = useMemo(() => new Map(view?.cases.map((c) => [c.caseId, c.name]) ?? []), [view]);
  const flaggedCases = useMemo(() => new Set(view?.cases.filter((c) => c.flagged).map((c) => c.caseId) ?? []), [view]);

  // ---------------------------------------------------------------- views

  if (!token) {
    return (
      <div className="ceo">
        <header>
          <span className="logo">U</span>
          <span className="title">U-Tax · CEO console</span>
        </header>
        <main className="signin">
          <h1>Signed out</h1>
          {signInError && (
            <p className="error" role="alert">
              {signInError}
            </p>
          )}
          <p className="dim">Sign in with your email and password to run the firm from here.</p>
          <p>
            <Link className="primary signin-link" href="/login?next=/ceo">
              Sign in
            </Link>
          </p>
          {!HAS_GATEWAY && <p className="dim">No gateway is configured in this build.</p>}
        </main>
      </div>
    );
  }

  const loading = view === null;
  const blocked = view?.cases.filter((c) => c.status === "BLOCKED") ?? [];
  const pending = [...log.pending.values()].filter((a) => !dismissed.has(a.approvalId));
  const outdated = outdatedApprovals(pending);
  const candidates = view?.candidates ?? [];
  const toDecide = candidates.filter((c) => canDecide(c.stage)).length;
  const names = caseNames;
  const stale = updatedAt !== null && now - updatedAt > STALE_MS;
  const working = (key: string) => busy === key;

  /** The Approve prompt from what is waiting right now (never a count from when the confirm opened). */
  const approvePrompt = (a: Approval) => {
    const label = ACTION_LABEL[a.actionType as ActionType] ?? a.actionType;
    const rel = approveReleases(pending, a, view?.trust[a.actionType as ActionType], outdated);
    if (!rel) return "Approve it?";
    const kind = label.toLowerCase();
    const one = rel.others === 1;
    return (
      `Approve it? ${label} then run on their own, and the ${one ? "1 other waiting " + kind.replace(/s$/, "") + " goes" : `${rel.others} other waiting ${kind} go`} out now` +
      (rel.outdated ? `, including ${rel.outdated} outdated. Deny the outdated ones first if their figures are wrong.` : ".")
    );
  };
  // An Approve confirm disarms when its approval is decided or goes outdated (a newer version arrived).
  const live = confirm && (!confirm.approval || (log.pending.has(confirm.approval.approvalId) && !outdated.has(confirm.approval.approvalId))) ? confirm : null;
  const confirmRow = (key: string) =>
    live?.key === key ? (
      <div className="confirm" role="group" aria-label="Confirm">
        <span>{live.approval ? approvePrompt(live.approval) : live.prompt}</span>
        <button className="danger" autoFocus onClick={() => live.run()} disabled={!!busy}>
          {live.label}
        </button>
        <button className="secondary" onClick={() => setConfirm(null)}>
          Cancel
        </button>
      </div>
    ) : null;

  // ---------------------------------------------------------------- actions
  // Shared by the panel's lists and the /ceo page: F45 moved the page's markup, not its actions.
  const approveClick = (a: Approval, key: string, what: string) => {
    const approve = () => void act(key, "/approve", { approval_id: a.approvalId, decision: "allow", note: "" }, `Approved: ${what}`);
    if (!approveReleases(pending, a, view?.trust[a.actionType as ActionType], outdated)) return approve();
    ask({ key, prompt: "", label: "Approve and let them run", run: approve, approval: a });
  };
  const denyClick = (a: Approval, key: string, what: string) =>
    ask({
      key,
      prompt: "Deny it? The firm won't do this.",
      label: "Deny",
      run: () => void act(key, "/approve", { approval_id: a.approvalId, decision: "deny", note: "" }, `Denied: ${what}`),
    });
  /** The click handler for one Unblock button (Not eligible asks first). */
  const unblockClick = (c: CaseRow, u: (typeof UNBLOCK)[number]) => {
    const key = `ub:${c.caseId}`;
    const go = () => void act(key, `/cases/${encodeURIComponent(c.caseId)}/unblock`, { to: u.to, note: "" }, `${c.name}: ${u.label}`);
    return u.to === "NOT_ELIGIBLE"
      ? () => ask({ key, prompt: `Close ${c.name} as not eligible? It sends the not-eligible note.`, label: "Close as not eligible", run: go })
      : go;
  };
  const decide = (c: Candidate, decision: "hire" | "decline") => {
    const key = `hr:${c.candidateId}`;
    ask({
      key,
      prompt: decision === "hire" ? `Hire ${c.name}? It sends the offer email.` : `Decline ${c.name}? It sends the decline email.`,
      label: decision === "hire" ? "Hire" : "Decline",
      run: () => void act(key, `/candidates/${encodeURIComponent(c.candidateId)}/decision`, { decision }, `${c.name}: ${decision === "hire" ? "hired" : "declined"}`),
    });
  };
  /** D40: one switch, the same two handlers (on → Pause; off → the Resume confirm). */
  const trustToggle = (t: ActionType, state: TrustState) => {
    const key = `tr:${t}`;
    return state !== "PAUSED"
      ? void act(key, "/trust", { action_type: t, state: "PAUSED" }, `${ACTION_LABEL[t]} paused`)
      : ask({
          key,
          prompt: `Resume ${ACTION_LABEL[t].toLowerCase()}? It goes back to how it was before the pause, and anything you approved while it was paused goes out now.`,
          label: "Resume",
          run: () => void act(key, "/trust", { action_type: t, state: "RESUME" }, `${ACTION_LABEL[t]} resumed`),
        });
  };
  const submitOrder = (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const body = orderBody(order);
    if (body.text) void act("order", "/voice", body, "Order sent to the Chief of Staff");
  };
  const updatedText =
    updatedAt === null ? "Loading…" : stale ? "Not updating · retrying" : `Updated ${new Date(updatedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}`;

  if (variant === "page") {
    // ---------------------------------------------------------------- F45: the /ceo page, ported from
    // docs/designs/inside/ceo-console-prototype.html (DOM and classes as the prototype; data and handlers as before).
    const waiting = pending.length - outdated.size;
    const allPaused = view !== null && ACTION_TYPES.every((t) => view.trust[t] === "PAUSED");
    const hour = now ? new Date(now).getHours() : null;
    const greeting = hour === null ? "Hello" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
    const kpis = [
      briefCounts?.clients != null ? { label: "clients served", value: formatInt(briefCounts.clients), lime: false } : null,
      briefCounts?.sent != null ? { label: "memos sent", value: formatInt(briefCounts.sent), lime: false } : null,
      money?.savedCents != null ? { label: "clients saved", value: formatCents(money.savedCents), lime: true } : null,
      money?.spentUsd != null ? { label: "spent (est.)", value: formatUsd(money.spentUsd), lime: false } : null,
    ].filter((k) => k !== null);
    const spend = money && money.byProvider.length > 0 ? money : null;
    const declined = candidates.filter((c) => c.stage === "declined");
    const column = (stage: string) =>
      candidates.filter((c) => (stage === "applicant" ? c.stage !== "declined" && !KAN_COLS.some((k) => k.stage === c.stage && k.stage !== "applicant") : c.stage === stage));
    const navTo = (item: RailItem) => () => setRailOn(item);
    const allClear = EMPTY_ART["all-clear"];
    return (
      <div className="cx app stage-glow">
        <header className="top">
          <div className="brand">
            <span className="mark emblem-mark" aria-hidden>
              <Emblem size={30} />
            </span>
            <span>U-Tax</span>
            <span className="chip">ceo</span>
          </div>
          <form onSubmit={submitOrder} aria-labelledby="or-h">
            <span id="or-h" className="sr-only">
              Order to the Chief of Staff
            </span>
            <label className="cmd">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true" focusable="false">
                <path d="M4 12h16M14 6l6 6-6 6" />
              </svg>
              <input
                id="order"
                value={order}
                maxLength={MAX_ORDER_CHARS}
                autoComplete="off"
                aria-label="Your order to the Chief of Staff"
                placeholder="Order the Chief of Staff… e.g. Prioritize the Team #12 re-ask."
                onChange={(e) => {
                  setOrder(e.target.value);
                  setOrderSent(false);
                }}
              />
              {orderSent ? (
                <span className="ok" role="status">
                  Sent to the Chief of Staff.
                </span>
              ) : (
                <span className="kbd" aria-hidden="true">
                  ↵ send
                </span>
              )}
              {order.trim() && (
                <button className="btn btn-lime btn-sm" type="submit" disabled={!!busy}>
                  {working("order") ? "Sending…" : "Send order"}
                </button>
              )}
              <span className="slot" ref={setMicSlot} />
            </label>
          </form>
          <span className="status">
            <span className="dot" aria-hidden="true" />
            {loading ? "connecting" : allPaused ? "everything paused" : "firm open"} · {AGENT_COUNT} agents
          </span>
          <div className="me">
            <span className={stale ? "updated stale" : "updated"} role="status">
              {updatedText}
            </span>
            <DeptAvatar seat="ceo" size={32} />
            <button className="btn btn-ghost btn-sm" onClick={() => signOut(null)}>
              Sign out
            </button>
          </div>
        </header>

        <nav className="rail" aria-label="Console">
          <a className={railOn === "brief" ? "nav on" : "nav"} href="#brief" onClick={navTo("brief")}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M4 5h16v10H8l-4 4z" />
            </svg>
            <span className="lbl">Brief</span>
          </a>
          <a className={railOn === "approvals" ? "nav on" : "nav"} href="#approvals" onClick={navTo("approvals")}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M5 12l4 4 10-10" />
            </svg>
            <span className="lbl">Approvals</span>
            {!loading && <span className={waiting > 0 ? "n hot" : "n"}>{waiting}</span>}
          </a>
          <a className={railOn === "cases" ? "nav on" : "nav"} href="#cases-h" onClick={navTo("cases")}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <rect x="4" y="4" width="16" height="16" rx="3" />
              <path d="M8 9h8M8 13h8M8 17h5" />
            </svg>
            <span className="lbl">Cases</span>
            {view && <span className="n">{view.cases.length}</span>}
          </a>
          <a className={railOn === "hiring" ? "nav on" : "nav"} href="#hiring" onClick={navTo("hiring")}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <circle cx="9" cy="9" r="3" />
              <path d="M4 19a5 5 0 0110 0M16 7a3 3 0 010 6M17 19a5 5 0 00-2-4" />
            </svg>
            <span className="lbl">Hiring</span>
            {view && <span className="n">{candidates.length}</span>}
          </a>
          <a className={railOn === "standing" ? "nav on" : "nav"} href="#standing" onClick={navTo("standing")}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <path d="M12 4v4M12 16v4M4 12h4M16 12h4" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            <span className="lbl">Standing orders</span>
          </a>
          {spend && (
            <a className={railOn === "money" ? "nav on" : "nav"} href="#money" onClick={navTo("money")}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <path d="M12 3v18M16 7c0-1.7-1.8-3-4-3s-4 1.3-4 3 1.8 2.6 4 3 4 1.3 4 3-1.8 3-4 3-4-1.3-4-3" />
              </svg>
              <span className="lbl">Money</span>
            </a>
          )}
          <h6>the firm</h6>
          <Link className="nav" href="/office">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
              <rect x="3" y="4" width="18" height="13" rx="2" />
              <path d="M8 21h8" />
            </svg>
            <span className="lbl">Office, live</span>
            <span className="n" aria-hidden="true">
              ↗
            </span>
          </Link>
          {/* STOP EVERYTHING: the voice card's own button (same handler, one click, Esc), drawn here. */}
          <span className="slot" ref={setStopSlot} />
        </nav>

        <main>
          <p className="sr-only" role="status" aria-live="polite">
            {announce}
          </p>
          {actionError && (
            <p className="error banner" role="alert">
              {actionError}{" "}
              <button className="ghost inline" onClick={() => setActionError(null)}>
                Dismiss
              </button>
            </p>
          )}
          {error && (
            <p className="error banner" role="alert">
              {error}
            </p>
          )}
          {notice && (
            <p className="notice" role="status">
              {notice}{" "}
              <button className="ghost inline" onClick={() => setNotice(null)}>
                Dismiss
              </button>
            </p>
          )}
          <div className="voice-alerts" ref={setAlertsSlot} />

          <section className="hero">
            <div className="card glass brief" id="brief" role="region" aria-labelledby="brief-h">
              <div className="kicker">your brief</div>
              <h1 id="brief-h" className="dot-display">
                {greeting}. The firm kept <em>working.</em>
              </h1>
              {brief ? (
                <>
                  <div className="brief-text">
                    {briefParagraphs(readableSummary(brief.text)).map((p, i) => (
                      <p key={i}>{p}</p>
                    ))}
                  </div>
                  {kpis.length > 0 && (
                    <div className="kpis">
                      {kpis.map((k) => (
                        <div key={k.label} className={k.lime ? "kpi lime" : "kpi"}>
                          <b>{k.value}</b>
                          <span>{k.label}</span>
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="row">
                    <button className="btn btn-lime" onClick={() => token && void playBrief(brief, token)}>
                      <span className="wave" aria-hidden="true">
                        <i />
                        <i />
                        <i />
                        <i />
                        <i />
                      </span>
                      Play the brief
                    </button>
                    <Link className="btn btn-ghost" href="/office">
                      Open the office ↗
                    </Link>
                  </div>
                </>
              ) : briefError ? (
                <p className="error" role="status">
                  {briefError}
                </p>
              ) : (
                <p className="dim">Loading the brief…</p>
              )}
            </div>
            <div className="card glass voice">
              <CeoVoice key={token} token={token} refresh={refreshAfterVoice} signOut={voiceSignOut} feedRef={voiceFeed} names={caseNames} flagged={flaggedCases} skin="page" slots={slots} />
            </div>
          </section>

          {orders.length > 0 && (
            <section aria-labelledby="ord-h" className="orders-live">
              <div className="sh">
                <h2 id="ord-h" className="dot-display">
                  Your orders, <em>live</em>
                </h2>
              </div>
              <ol>
                {orders.map((o) => (
                  <li key={o.id} className="card glass order-card">
                    <div className="order-head">
                      <p className="order-text">
                        <q>{o.text}</q>
                      </p>
                      {taskChip(o.tasks) && <span className="task-chip">{taskChip(o.tasks)}</span>}
                    </div>
                    {o.tasks && (
                      <ol className="order-tasks" aria-label="Tasks">
                        {o.tasks.map((t) => (
                          <li key={t.no} className={`tk ${t.status}`}>
                            <span className="tk-badge" aria-hidden="true">
                              {t.status === "done" ? "✓" : t.status === "failed" || t.status === "timeout" ? "✕" : t.status === "queued" ? "○" : t.no}
                            </span>
                            {t.status === "running" && <span className="tk-dot" aria-hidden="true" />}
                            <span className="tk-text" title={t.text}>
                              {t.text}
                            </span>
                            {(t.status === "waiting_on_you" || t.status === "blocked") && <span className="tk-wait">{TASK_WORDS[t.status]}</span>}
                            {t.seat && <DeptAvatar seat={t.seat} size={22} className="tk-av" title={SEAT_NAME[t.seat]} />}
                            <span className="sr-only">
                              Task {t.no}: {TASK_WORDS[t.status]}
                            </span>
                          </li>
                        ))}
                      </ol>
                    )}
                    {o.live && current(o.live.ts, now) && <p className="order-live">{o.live.text}</p>}
                    {o.steps.length === 0 ? (
                      <p className="dim">Waiting for the Chief of Staff to pick it up…</p>
                    ) : (
                      <ol className="order-steps">
                        {o.steps.map((st) => (
                          <li key={st.key} className={st.status}>
                            <DeptAvatar seat={st.seat ?? "cos"} size={20} className="step-av" />
                            <span>
                              <span className="who">{st.seat ? SEAT_NAME[st.seat] : "U-Tax"}</span> {st.text}
                            </span>
                          </li>
                        ))}
                      </ol>
                    )}
                  </li>
                ))}
              </ol>
            </section>
          )}

          <section id="approvals" aria-labelledby="ap-h">
            <div className="sh">
              <h2 id="ap-h" className="dot-display">
                Waiting on <em>you</em>
              </h2>
              <span className="count">{waiting}</span>
              <p>The first approval of a kind lets U-Tax do that kind on its own afterwards.</p>
            </div>
            {pending.length === 0 ? (
              <p className="empty">{loading ? "Loading…" : "Nothing needs your sign-off."}</p>
            ) : (
              <div className="appr">
                {pending.map((a) => {
                  const key = `ap:${a.approvalId}`;
                  const old = outdated.has(a.approvalId);
                  const what = readableSummary(a.summary || a.approvalId);
                  const caseName = a.caseId ? names.get(a.caseId) : undefined;
                  const seat = Object.hasOwn(ACTION_SEAT, a.actionType) ? ACTION_SEAT[a.actionType] : "cos";
                  const label = ACTION_LABEL[a.actionType as ActionType] ?? a.actionType;
                  const tr = view?.trust[a.actionType as ActionType];
                  const why = tr === "ASK" ? `first ${label.toLowerCase().replace(/s$/, "")} → then automatic` : tr === "AUTO" ? "runs on its own" : tr === "PAUSED" ? "paused" : "";
                  return (
                    <article
                      key={a.approvalId}
                      id={`row-${a.approvalId}`}
                      className={["card glass ap", old && "outdated", highlight === a.approvalId && "highlight"].filter(Boolean).join(" ")}
                    >
                      <div className="ap-h">
                        <DeptAvatar seat={seat} size={36} />
                        <div>
                          <b>{seatName(seat)}</b>
                          <span>
                            {label}
                            {old ? " · outdated: a newer version is here" : ""}
                          </span>
                        </div>
                        <span className="ch">{ACTION_CHANNEL[a.actionType] ?? a.actionType}</span>
                      </div>
                      <div className="post">
                        <p id={`w-${key}`}>
                          {what}
                          {caseName && !what.includes(caseName) ? <small>{caseName}</small> : null}
                        </p>
                      </div>
                      <div className="ap-f">
                        <span className="why">{why}</span>
                        <button className="btn btn-ghost btn-sm" aria-describedby={`w-${key}`} disabled={!!busy} onClick={() => denyClick(a, key, what)}>
                          Deny
                        </button>
                        {!old && (
                          <button className="btn btn-lime btn-sm" aria-describedby={`w-${key}`} disabled={!!busy} onClick={() => approveClick(a, key, what)}>
                            {working(key) ? "Working…" : "Approve"}
                          </button>
                        )}
                      </div>
                      {confirmRow(key)}
                    </article>
                  );
                })}
              </div>
            )}
          </section>

          <section className="split">
            <div id="hiring" role="region" aria-labelledby="hr-h">
              <div className="sh">
                <h2 id="hr-h" className="dot-display">Hiring</h2>
                <span className={toDecide ? "count" : "count dim"}>{toDecide ? `${toDecide} to decide` : candidates.length}</span>
                <p>HR runs it; you make the call.</p>
              </div>
              {candidates.length === 0 ? (
                <p className="empty">{loading ? "Loading…" : "No applicants yet."}</p>
              ) : (
                <>
                  <div className="card glass kan">
                    {KAN_COLS.map((col) => {
                      const inCol = column(col.stage);
                      return (
                        <div key={col.stage} className="col">
                          <h4>
                            {col.title} <span>{inCol.length}</span>
                          </h4>
                          {inCol.length === 0 ? (
                            <div className="ghostcol">{col.empty}</div>
                          ) : (
                            inCol.map((c, i) => {
                              const key = `hr:${c.candidateId}`;
                              return (
                                <div key={c.candidateId} className="cand">
                                  <div className="who" id={`w-${key}`}>
                                    <span className={i % 2 ? "ini b" : "ini"} aria-hidden="true">
                                      {(c.name.trim()[0] ?? "?").toUpperCase()}
                                    </span>
                                    {c.name}
                                  </div>
                                  <span className="tagc">{STAGE_LABEL[c.stage] ?? c.stage}</span>
                                  {c.credentials ? <small>{c.credentials}</small> : null}
                                  {canDecide(c.stage) && (
                                    <div className="btns">
                                      <button className="btn btn-lime btn-sm" aria-describedby={`w-${key}`} disabled={!!busy} onClick={() => decide(c, "hire")}>
                                        {working(key) ? "Working…" : "Hire"}
                                      </button>
                                      <button className="btn btn-ghost btn-sm" aria-describedby={`w-${key}`} disabled={!!busy} onClick={() => decide(c, "decline")}>
                                        Decline
                                      </button>
                                    </div>
                                  )}
                                  {confirmRow(key)}
                                </div>
                              );
                            })
                          )}
                        </div>
                      );
                    })}
                  </div>
                  {declined.length > 0 && <p className="hint">Declined: {declined.map((c) => c.name).join(", ")}</p>}
                </>
              )}
            </div>
            <div id="standing" role="region" aria-labelledby="tr-h">
              <div className="sh">
                <h2 id="tr-h" className="dot-display">Standing orders</h2>
                <p>A kind that asks you first runs on its own after you approve one. Switch a kind off to pause it; on puts it back as it was.</p>
              </div>
              {loading ? (
                <p className="empty">Loading…</p>
              ) : (
                <div className="card glass orders">
                  {ACTION_TYPES.map((t) => {
                    const key = `tr:${t}`;
                    const state = view.trust[t] ?? "ASK";
                    return [
                      <div key={t} className="ord">
                        <DeptAvatar seat={ACTION_SEAT[t] ?? "cos"} size={30} />
                        <div className="ord-txt" id={`w-${key}`}>
                          <b>{ACTION_LABEL[t]}</b>
                          <span>{state === "PAUSED" ? "paused · switch on to resume" : state === "AUTO" ? "runs without asking" : "needs your first OK"}</span>
                        </div>
                        <span className={state === "AUTO" ? "st auto" : state === "PAUSED" ? "st paused" : "st ask"}>
                          {state === "PAUSED" ? "paused" : state === "AUTO" ? "runs on its own" : "asks first"}
                        </span>
                        {working(key) && <span className="dim">Working…</span>}
                        <Switch className="sw" on={state !== "PAUSED"} label={`${ACTION_LABEL[t]} on (off pauses them)`} disabled={!!busy} onChange={() => trustToggle(t, state)} />
                      </div>,
                      <Fragment key={`${t}-confirm`}>{confirmRow(key)}</Fragment>,
                    ];
                  })}
                </div>
              )}
            </div>
          </section>

          {view && token && <CaseList cases={view.cases} served={served} token={token} onExpired={signOut} />}
        </main>

        <aside className="side" aria-label="Live activity">
          <section aria-labelledby="bl-h">
            <h5 id="bl-h">
              blocked cases <span>{loading ? "…" : blocked.length}</span>
            </h5>
            {loading ? (
              <p className="empty">Loading…</p>
            ) : blocked.length === 0 ? (
              <div className="clear">
                <svg viewBox={allClear.vb} aria-hidden="true" focusable="false" dangerouslySetInnerHTML={{ __html: allClear.svg }} />
                <p>No case is waiting on a human. QA re-checks every number before anything leaves.</p>
              </div>
            ) : (
              <ul className="blk">
                {blocked.map((c) => {
                  const key = `ub:${c.caseId}`;
                  return (
                    <li key={c.caseId} className="card glass">
                      <div id={`w-${key}`}>
                        <b>{c.name}</b>
                        <span className="dim">
                          {" "}
                          · {c.flagged ? "flagged by QA · " : ""}
                          {log.blockedReason.get(c.caseId) ?? "needs a human"}
                        </span>
                      </div>
                      <div className="btns">
                        {UNBLOCK.map((u) => (
                          <button
                            key={u.to}
                            className={u.to === "READY" ? "btn btn-lime btn-sm" : u.to === "NOT_ELIGIBLE" ? "danger-outline" : "btn btn-ghost btn-sm"}
                            aria-describedby={`w-${key}`}
                            disabled={!!busy}
                            onClick={unblockClick(c, u)}
                          >
                            {working(key) && u.to === "READY" ? "Working…" : u.label}
                          </button>
                        ))}
                      </div>
                      <p className="hint">Retry runs it again with its facts · Re-ask emails the client for them · Not eligible closes it.</p>
                      {confirmRow(key)}
                    </li>
                  );
                })}
              </ul>
            )}
          </section>
          <section aria-labelledby="act-h">
            <h5 id="act-h">
              live activity <span>{activity.length ? "latest" : ""}</span>
            </h5>
            {activity.length === 0 ? (
              <p className="hint">{loading ? "Loading…" : "Quiet so far."}</p>
            ) : (
              <div className="feed">
                {activity.map((a) => (
                  <div key={a.id} className="fe">
                    <DeptAvatar seat={a.seat} size={28} />
                    <div>
                      <p>
                        {a.text}
                        {a.amt ? (
                          <>
                            {" "}
                            <span className="amt">{a.amt}</span>
                          </>
                        ) : null}
                      </p>
                      <small>
                        {a.at}
                        {a.tx ? ` · tx ${a.tx}` : ""}
                      </small>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
          {spend && (
            <section id="money" aria-labelledby="money-h">
              <h5 id="money-h">
                where the money went <span>{spend.spentUsd ? formatUsd(spend.spentUsd) : ""}</span>
              </h5>
              <div className="card glass spend">
                <div className="bar" aria-hidden="true">
                  {spend.byProvider.map((p, i) => (
                    <i key={p.name} style={{ width: `${(p.share * 100).toFixed(1)}%`, background: SPEND_COLORS[i % SPEND_COLORS.length] }} />
                  ))}
                </div>
                <div className="legend">
                  {spend.byProvider.map((p, i) => (
                    <span key={p.name} style={{ "--c": SPEND_COLORS[i % SPEND_COLORS.length] } as CSSProperties} title={formatUsd(p.usd)}>
                      {p.name} {Math.round(p.share * 100)}%
                    </span>
                  ))}
                </div>
              </div>
            </section>
          )}
          {/* D41 F50.3: the last payments, under the money card (hidden when the ledger can't be read). */}
          <Payments token={token} tick={activity[0]?.id ?? 0} />
        </aside>
      </div>
    );
  }

  return (
    // The docked panel inside /office (the /ceo page returned above). Unchanged by F45.
    // In the panel the root takes focus on a click inside, so Esc (STOP EVERYTHING) still works there.
    <div className="ceo in-panel" tabIndex={-1}>
      <header>
        <span className="title">CEO console</span>
        <span className={`updated${stale ? " stale" : ""}`}>{updatedText}</span>
      </header>
      <main>
        <p className="sr-only" role="status" aria-live="polite">
          {announce}
        </p>
        <CeoVoice key={token} token={token} refresh={refreshAfterVoice} signOut={voiceSignOut} feedRef={voiceFeed} names={caseNames} flagged={flaggedCases} escInside={variant === "panel"} />
        {actionError && (
          <p className="error banner" role="alert">
            {actionError}{" "}
            <button className="ghost inline" onClick={() => setActionError(null)}>
              Dismiss
            </button>
          </p>
        )}
        {error && (
          <p className="error banner" role="alert">
            {error}
          </p>
        )}
        {notice && (
          <p className="notice" role="status">
            {notice}{" "}
            <button className="ghost inline" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </p>
        )}

        <section className="brief" aria-labelledby="brief-h">
          <h1 id="brief-h">Your brief</h1>
          {brief ? (
            <>
              <div className="brief-text">
                {briefParagraphs(readableSummary(brief.text)).map((p, i) => (
                  <p key={i}>{p}</p>
                ))}
              </div>
              <button className="secondary" onClick={() => token && void playBrief(brief, token)}>
                Play the brief
              </button>
            </>
          ) : briefError ? (
            <p className="error" role="status">
              {briefError}
            </p>
          ) : (
            <p className="dim">Loading the brief…</p>
          )}
        </section>

        <section aria-labelledby="ap-h">
          <h2 id="ap-h">
            Waiting on you <span className="count">{pending.length - outdated.size}</span>
          </h2>
          <p className="hint">The first approval of a kind lets U-Tax do that kind on its own afterwards.</p>
          {pending.length === 0 ? (
            <p className="empty">{loading ? "Loading…" : "Nothing needs your sign-off."}</p>
          ) : (
            <ul className="items">
              {pending.map((a) => {
                const key = `ap:${a.approvalId}`;
                const old = outdated.has(a.approvalId);
                const what = readableSummary(a.summary || a.approvalId);
                const caseName = a.caseId ? names.get(a.caseId) : undefined;
                return (
                  <li
                    key={a.approvalId}
                    id={`row-${a.approvalId}`}
                    className={[old && "outdated", highlight === a.approvalId && "highlight"].filter(Boolean).join(" ") || undefined}
                  >
                    <div className="line">
                      <DeptAvatar seat={Object.hasOwn(ACTION_SEAT, a.actionType) ? ACTION_SEAT[a.actionType] : "cos"} size={32} className="row-av" />
                      <div className="what" id={`w-${key}`}>
                        <small>
                          {ACTION_LABEL[a.actionType as ActionType] ?? a.actionType}
                          {old ? " · outdated: a newer version is below" : ""}
                        </small>
                        {what}
                        {caseName && !what.includes(caseName) ? <span className="dim"> · {caseName}</span> : null}
                      </div>
                      <div className="btns">
                        {!old && (
                          <button
                            className="primary"
                            aria-describedby={`w-${key}`}
                            disabled={!!busy}
                            onClick={() => {
                              const approve = () => void act(key, "/approve", { approval_id: a.approvalId, decision: "allow", note: "" }, `Approved: ${what}`);
                              if (!approveReleases(pending, a, view?.trust[a.actionType as ActionType], outdated)) return approve();
                              ask({ key, prompt: "", label: "Approve and let them run", run: approve, approval: a });
                            }}
                          >
                            {working(key) ? "Working…" : "Approve"}
                          </button>
                        )}
                        <button
                          className="secondary"
                          aria-describedby={`w-${key}`}
                          disabled={!!busy}
                          onClick={() =>
                            ask({
                              key,
                              prompt: "Deny it? The firm won't do this.",
                              label: "Deny",
                              run: () => void act(key, "/approve", { approval_id: a.approvalId, decision: "deny", note: "" }, `Denied: ${what}`),
                            })
                          }
                        >
                          Deny
                        </button>
                      </div>
                    </div>
                    {confirmRow(key)}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section aria-labelledby="bl-h">
          <h2 id="bl-h">
            Blocked cases <span className="count">{blocked.length}</span>
          </h2>
          {blocked.length === 0 ? (
            <p className="empty">{loading ? "Loading…" : "No case is waiting on a human."}</p>
          ) : (
            <ul className="items">
              {blocked.map((c) => {
                const key = `ub:${c.caseId}`;
                return (
                  <li key={c.caseId}>
                    <div className="line">
                      <div className="what" id={`w-${key}`}>
                        <small>{c.flagged ? "Blocked · flagged by QA" : "Blocked"}</small>
                        {c.name}
                        <span className="dim"> · {log.blockedReason.get(c.caseId) ?? "needs a human"}</span>
                      </div>
                      <div className="btns">
                        {UNBLOCK.map((u) => {
                          const go = () => void act(key, `/cases/${encodeURIComponent(c.caseId)}/unblock`, { to: u.to, note: "" }, `${c.name}: ${u.label}`);
                          return (
                            <button
                              key={u.to}
                              className={u.to === "READY" ? "primary" : u.to === "NOT_ELIGIBLE" ? "danger-outline" : "secondary"}
                              aria-describedby={`w-${key}`}
                              disabled={!!busy}
                              onClick={
                                u.to === "NOT_ELIGIBLE"
                                  ? () => ask({ key, prompt: `Close ${c.name} as not eligible? It sends the not-eligible note.`, label: "Close as not eligible", run: go })
                                  : go
                              }
                            >
                              {working(key) && u.to === "READY" ? "Working…" : u.label}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                    <p className="hint row-hint">Retry runs it again with its facts · Re-ask emails the client for them · Not eligible closes it.</p>
                    {confirmRow(key)}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section aria-labelledby="hr-h">
          <h2 id="hr-h">
            Hiring <span className="count">{toDecide ? `${toDecide} to decide` : candidates.length}</span>
          </h2>
          {candidates.length === 0 ? (
            <p className="empty">{loading ? "Loading…" : "No applicants yet."}</p>
          ) : (
            <ul className="items">
              {candidates.map((c) => {
                const key = `hr:${c.candidateId}`;
                const decide = (decision: "hire" | "decline") =>
                  ask({
                    key,
                    prompt: decision === "hire" ? `Hire ${c.name}? It sends the offer email.` : `Decline ${c.name}? It sends the decline email.`,
                    label: decision === "hire" ? "Hire" : "Decline",
                    run: () => void act(key, `/candidates/${encodeURIComponent(c.candidateId)}/decision`, { decision }, `${c.name}: ${decision === "hire" ? "hired" : "declined"}`),
                  });
                return (
                  <li key={c.candidateId}>
                    <div className="line">
                      <div className="what" id={`w-${key}`}>
                        <small>{STAGE_LABEL[c.stage] ?? c.stage}</small>
                        {c.name}
                        {c.credentials ? <span className="dim"> · {c.credentials}</span> : null}
                      </div>
                      {canDecide(c.stage) && (
                        <div className="btns">
                          <button className="primary" aria-describedby={`w-${key}`} disabled={!!busy} onClick={() => decide("hire")}>
                            {working(key) ? "Working…" : "Hire"}
                          </button>
                          <button className="secondary" aria-describedby={`w-${key}`} disabled={!!busy} onClick={() => decide("decline")}>
                            Decline
                          </button>
                        </div>
                      )}
                    </div>
                    {confirmRow(key)}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <section aria-labelledby="tr-h">
          <h2 id="tr-h">Standing orders</h2>
          <p className="hint">A kind that asks you first runs on its own after you approve one. Switch a kind off to pause it; on puts it back as it was.</p>
          {loading ? (
            <p className="empty">Loading…</p>
          ) : (
            <ul className="items">
              {ACTION_TYPES.map((t) => {
                const key = `tr:${t}`;
                const state = view.trust[t] ?? "ASK";
                return (
                  <li key={t}>
                    <div className="line">
                      <DeptAvatar seat={ACTION_SEAT[t] ?? "cos"} size={32} className="row-av" />
                      <div className="what" id={`w-${key}`}>
                        <small className={`st ${state.toLowerCase()}`}>
                          {state === "PAUSED" ? "paused" : state === "AUTO" ? "runs on its own" : "asks you first"}
                        </small>
                        {ACTION_LABEL[t]}
                      </div>
                      <div className="btns sw">
                        {working(key) && <span className="dim">Working…</span>}
                        {/* D40: one switch, the same two handlers (on → Pause; off → the Resume confirm). */}
                        <Switch
                          on={state !== "PAUSED"}
                          label={`${ACTION_LABEL[t]} on (off pauses them)`}
                          disabled={!!busy}
                          onChange={() =>
                            state !== "PAUSED"
                              ? void act(key, "/trust", { action_type: t, state: "PAUSED" }, `${ACTION_LABEL[t]} paused`)
                              : ask({
                                  key,
                                  prompt: `Resume ${ACTION_LABEL[t].toLowerCase()}? It goes back to how it was before the pause, and anything you approved while it was paused goes out now.`,
                                  label: "Resume",
                                  run: () => void act(key, "/trust", { action_type: t, state: "RESUME" }, `${ACTION_LABEL[t]} resumed`),
                                })
                          }
                        />
                      </div>
                    </div>
                    {confirmRow(key)}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {orders.length > 0 && (
          <section aria-labelledby="ord-h" className="orders">
            <h2 id="ord-h">Your orders, live</h2>
            <ol>
              {orders.map((o) => (
                <li key={o.id} className="order-card">
                  <p className="order-text">
                    <q>{o.text}</q>
                  </p>
                  {o.live && current(o.live.ts, now) && (
                    <p className="order-live">
                      {o.live.text}
                    </p>
                  )}
                  {o.steps.length === 0 ? (
                    <p className="dim">Waiting for the Chief of Staff to pick it up…</p>
                  ) : (
                    <ol className="order-steps">
                      {o.steps.map((st) => (
                        <li key={st.key} className={st.status}>
                          <DeptAvatar seat={st.seat ?? "cos"} size={20} className="step-av" />
                          <span className="who">{st.seat ? SEAT_NAME[st.seat] : "U-Tax"}</span> {st.text}
                        </li>
                      ))}
                    </ol>
                  )}
                </li>
              ))}
            </ol>
          </section>
        )}

        <section aria-labelledby="or-h">
          <h2 id="or-h">Order to the Chief of Staff</h2>
          <form
            className="order"
            onSubmit={(e) => {
              e.preventDefault();
              const body = orderBody(order);
              if (body.text) void act("order", "/voice", body, "Order sent to the Chief of Staff");
            }}
          >
            <label htmlFor="order" className="sr-only">
              Your order
            </label>
            <textarea
              id="order"
              value={order}
              maxLength={MAX_ORDER_CHARS}
              rows={2}
              placeholder="e.g. Prioritize the Team #12 re-ask."
              onChange={(e) => {
                setOrder(e.target.value);
                setOrderSent(false);
              }}
            />
            <div className="row end">
              {orderSent && (
                <span className="ok" role="status">
                  Sent to the Chief of Staff.
                </span>
              )}
              <button className="primary" type="submit" disabled={!order.trim() || !!busy}>
                {working("order") ? "Sending…" : "Send order"}
              </button>
            </div>
          </form>
        </section>

        {view && token && <CaseList cases={view.cases} served={served} token={token} onExpired={signOut} />}
      </main>
    </div>
  );
}
