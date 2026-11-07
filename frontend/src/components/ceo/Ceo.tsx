"use client";

// The CEO console (/ceo, contract §5 and §8). The CEO signs in with the admin token (typed
// here, kept only in this tab, sent only as a Bearer header), hears and reads the login brief,
// and makes the calls only a human makes: approvals, standing orders, hiring, blocked cases.

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
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
  TOKEN_KEY,
  UNBLOCK,
  type ActionType,
  type Approval,
  type Brief,
  type Console,
  type LogView,
} from "@/ceo/ceo";
import "./ceo.css";

const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const API = `${API_URL}/api`;
/** Endpoints for the shared URL builders: the token never goes in a URL here (it rides the header). */
const NO_QUERY_TOKEN: Endpoints = { base: API_URL, token: "" };
const POLL_MS = 4000;
const REQUEST_MS = 15000;
/** Data older than this is marked stale in the header. */
const STALE_MS = 15000;

// ------------------------------------------------------------------ the token, per tab

const tokenListeners = new Set<() => void>();
let memoryToken: string | null = null;
const readToken = () => {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return memoryToken; // storage off: the token lives until the page closes
  }
};
function writeToken(t: string | null) {
  memoryToken = t;
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* memoryToken holds it */
  }
  tokenListeners.forEach((l) => l());
}
const subscribeToken = (l: () => void) => {
  tokenListeners.add(l);
  return () => tokenListeners.delete(l);
};
const noToken = () => null;

const hasSpeech = () => typeof window !== "undefined" && "speechSynthesis" in window;
function say(text: string) {
  if (!hasSpeech()) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
const hush = () => {
  if (hasSpeech()) window.speechSynthesis.cancel();
};

/** An admin request: Bearer header, a timeout, and contract errors as ApiError. */
async function authJson<T>(token: string, url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body != null) headers.set("Content-Type", "application/json");
  let r: Response;
  try {
    r = await fetch(url, { cache: "no-store", ...init, headers, signal: AbortSignal.timeout(REQUEST_MS) });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") throw new ApiError(0, "timeout", "timed out");
    throw new ApiError(0, "network_error", e instanceof Error ? e.message : "network error");
  }
  const body: unknown = await r.json().catch(() => null);
  if (!r.ok) {
    const err = typeof body === "object" && body !== null ? (body as { error?: { code?: unknown; message?: unknown } }).error : undefined;
    throw new ApiError(r.status, typeof err?.code === "string" ? err.code : "http_error", typeof err?.message === "string" ? err.message : `HTTP ${r.status}`);
  }
  return body as T;
}

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

export function Ceo() {
  const token = useSyncExternalStore(subscribeToken, readToken, noToken);
  const [draftToken, setDraftToken] = useState("");
  const [signInError, setSignInError] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [view, setView] = useState<Console | null>(null);
  const [log, setLog] = useState<LogView>(emptyLog);
  /** The poll's own error: cleared by the next good poll. */
  const [error, setError] = useState<string | null>(null);
  /** The last action's error: stays until the next action (a refresh never hides it). */
  const [actionError, setActionError] = useState<string | null>(null);
  const [briefError, setBriefError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [announce, setAnnounce] = useState("");
  const [order, setOrder] = useState("");
  const [orderSent, setOrderSent] = useState(false);
  /** Bumped on every sign-in and sign-out: work from an older session never writes state. */
  const session = useRef(0);
  const logRef = useRef<LogView>(emptyLog());
  const briefRef = useRef<Brief | null>(null);
  /** Refreshes run one at a time, in order (the poll and the one after an action never overlap). */
  const chain = useRef<Promise<void>>(Promise.resolve());
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrl = useRef<string | null>(null);
  const audioSeq = useRef(0);

  const stopAudio = useCallback(() => {
    audioSeq.current += 1;
    audioRef.current?.pause();
    audioRef.current = null;
    if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
    audioUrl.current = null;
    hush();
  }, []);

  const signOut = useCallback(
    (why: string | null) => {
      session.current += 1;
      stopAudio();
      writeToken(null);
      briefRef.current = null;
      logRef.current = emptyLog();
      setBrief(null);
      setView(null);
      setLog(logRef.current);
      setError(null);
      setActionError(null);
      setBriefError(null);
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

  /** A 401 means the token is wrong or rotated: back to sign-in. Anything else shows in the banner. */
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
        const st = consoleFromState(await authJson<unknown>(tok, `${API}/state`));
        if (mine !== session.current) return;
        const restart = () => {
          // Ids are reused after a reset: the old rows go off screen right away, before the reload.
          logRef.current = emptyLog();
          setLog(logRef.current);
          setView(null);
          setConfirm(null);
          setNotice("The gateway's log restarted, so the console reloaded it. Check the lists before acting.");
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
        setLog(logRef.current);
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
    async (tok: string, stateLastId: number | null): Promise<Brief> => {
      let lastSeen = 0;
      try {
        lastSeen = readLastSeen(localStorage.getItem(LAST_SEEN_KEY));
      } catch {
        /* no storage: the brief covers everything */
      }
      const b = parseBrief(await authJson<unknown>(tok, `${API}/ceo/brief?since_id=${briefSince(lastSeen, stateLastId)}`));
      if (!b) throw new Error("malformed brief");
      return b;
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
        rememberSeen(b);
        setBriefError(null);
        void playBrief(b, token);
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
  }, [token, refreshNow, loadBrief, playBrief, fail]);

  useEffect(() => () => stopAudio(), [stopAudio]);

  const signIn = async () => {
    const t = draftToken.trim();
    if (!t || signingIn) return;
    setSigningIn(true);
    setSignInError(null);
    try {
      // The brief needs the admin token, so it is the check: a wrong token never lands in storage.
      const st = consoleFromState(await authJson<unknown>(t, `${API}/state`));
      const b = await loadBrief(t, st?.lastEventId ?? null);
      setDraftToken("");
      briefRef.current = b;
      setBrief(b);
      rememberSeen(b);
      writeToken(t);
      void playBrief(b, t); // inside the click, so the browser lets it play
    } catch (e) {
      setSignInError(e instanceof ApiError && e.status === 401 ? "That token isn't the admin token." : errorOf(e));
    } finally {
      setSigningIn(false);
    }
  };

  /** One action at a time; the lists refresh right after, in order. */
  const act = async (key: string, path: string, body: unknown, done: string) => {
    if (busy || !token) return;
    const mine = session.current;
    setBusy(key);
    setConfirm(null);
    setActionError(null);
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
        if (e instanceof ApiError && e.status === 401) signOut(errorOf(e));
        else setActionError(errorOf(e));
      }
    } finally {
      if (mine === session.current) {
        await refreshNow(mine, token);
        setBusy(null);
      }
    }
  };

  const ask = (c: Confirm) => setConfirm(c);

  // ---------------------------------------------------------------- views

  if (!token) {
    return (
      <div className="ceo">
        <header>
          <span className="logo">U</span>
          <span className="title">U-Tax · CEO console</span>
        </header>
        <main className="signin">
          <h1>Sign in</h1>
          <p className="dim">Paste the admin token. It stays in this browser tab only.</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void signIn();
            }}
          >
            <label htmlFor="token">Admin token</label>
            <div className="row">
              {/* Not a password: no autofill or save prompt (the token must not be synced to a password manager). */}
              <input
                id="token"
                type="password"
                autoComplete="off"
                data-1p-ignore
                data-lpignore="true"
                spellCheck={false}
                value={draftToken}
                onChange={(e) => setDraftToken(e.target.value)}
              />
              <button className="primary" type="submit" disabled={!draftToken.trim() || signingIn || !HAS_GATEWAY}>
                {signingIn ? "Checking…" : "Sign in"}
              </button>
            </div>
            {signInError && (
              <p className="error" role="alert">
                {signInError}
              </p>
            )}
            {!HAS_GATEWAY && <p className="dim">No gateway is configured in this build.</p>}
          </form>
        </main>
      </div>
    );
  }

  const loading = view === null;
  const blocked = view?.cases.filter((c) => c.status === "BLOCKED") ?? [];
  const pending = [...log.pending.values()];
  const outdated = outdatedApprovals(pending);
  const candidates = view?.candidates ?? [];
  const toDecide = candidates.filter((c) => canDecide(c.stage)).length;
  const names = new Map(view?.cases.map((c) => [c.caseId, c.name]) ?? []);
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

  return (
    <div className="ceo">
      <header>
        <span className="logo">U</span>
        <span className="title">U-Tax · CEO console</span>
        <span className={`updated${stale ? " stale" : ""}`} role="status">
          {updatedAt === null ? "Loading…" : stale ? "Not updating · retrying" : `Updated ${new Date(updatedAt).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" })}`}
        </span>
        <button className="ghost" onClick={() => signOut(null)}>
          Sign out
        </button>
      </header>
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
                  <li key={a.approvalId} className={old ? "outdated" : undefined}>
                    <div className="line">
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
          {loading ? (
            <p className="empty">Loading…</p>
          ) : (
            <ul className="items">
              {ACTION_TYPES.map((t) => {
                const key = `tr:${t}`;
                const state = view.trust[t] ?? "ASK";
                const waiting = pending.filter((a) => a.actionType === t && !outdated.has(a.approvalId)).length;
                return (
                  <li key={t}>
                    <div className="line">
                      <div className="what" id={`w-${key}`}>
                        <small className={`st ${state.toLowerCase()}`}>
                          {state === "PAUSED" ? "paused" : state === "AUTO" ? "runs on its own" : "asks you first"}
                        </small>
                        {ACTION_LABEL[t]}
                      </div>
                      <div className="btns">
                        {state === "AUTO" && (
                          <button
                            className="secondary"
                            aria-describedby={`w-${key}`}
                            disabled={!!busy}
                            onClick={() => void act(key, "/trust", { action_type: t, state: "PAUSED" }, `${ACTION_LABEL[t]} paused`)}
                          >
                            {working(key) ? "Working…" : "Pause"}
                          </button>
                        )}
                        {state === "PAUSED" && (
                          <button
                            className="primary"
                            aria-describedby={`w-${key}`}
                            disabled={!!busy}
                            onClick={() =>
                              ask({
                                key,
                                prompt: `Resume ${ACTION_LABEL[t].toLowerCase()}? It runs on its own again${waiting ? `, and the ${waiting} waiting go out now` : ""}.`,
                                label: "Resume",
                                run: () => void act(key, "/trust", { action_type: t, state: "AUTO" }, `${ACTION_LABEL[t]} resumed`),
                              })
                            }
                          >
                            {working(key) ? "Working…" : "Resume…"}
                          </button>
                        )}
                        {state === "ASK" && <span className="dim small">Approve one to let it run on its own</span>}
                      </div>
                    </div>
                    {confirmRow(key)}
                  </li>
                );
              })}
            </ul>
          )}
        </section>

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
      </main>
    </div>
  );
}
