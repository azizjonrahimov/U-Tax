"use client";

// The CEO console (/ceo, contract §5 and §8). The CEO signs in with the admin token (typed
// here, kept only in this tab, sent only as a Bearer header), hears and reads the login brief,
// and makes the calls only a human makes: approvals, standing orders, hiring, blocked cases.

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ApiError, apiFetch, API_URL } from "@/lib/api";
import type { UTaxEvent } from "@/theater/events";
import { fetchAllEvents } from "@/theater/live";
import {
  ACTION_LABEL,
  ACTION_TYPES,
  canDecide,
  ceoError,
  consoleFromState,
  emptyLog,
  foldLog,
  LAST_SEEN_KEY,
  MAX_ORDER_CHARS,
  orderBody,
  parseBrief,
  readLastSeen,
  seedPending,
  STAGE_LABEL,
  TOKEN_KEY,
  UNBLOCK,
  type ActionType,
  type Brief,
  type Console,
  type LogView,
  type UnblockTo,
} from "@/ceo/ceo";
import "./ceo.css";

const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const POLL_MS = 4000;

// ------------------------------------------------------------------ the token, per tab

const tokenListeners = new Set<() => void>();
const readToken = () => {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
};
function writeToken(t: string | null) {
  try {
    if (t) sessionStorage.setItem(TOKEN_KEY, t);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage off: the token lives until the page closes */
  }
  memoryToken = t;
  tokenListeners.forEach((l) => l());
}
let memoryToken: string | null = null;
const subscribeToken = (l: () => void) => {
  tokenListeners.add(l);
  return () => tokenListeners.delete(l);
};
const tokenSnapshot = () => readToken() ?? memoryToken;
const noToken = () => null;

const hasSpeech = () => typeof window !== "undefined" && "speechSynthesis" in window;
function say(text: string) {
  if (!hasSpeech()) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

const errorOf = (e: unknown) => (e instanceof ApiError ? ceoError(e.status, e.code, e.message) : ceoError(0, "network_error", ""));

export function Ceo() {
  const token = useSyncExternalStore(subscribeToken, tokenSnapshot, noToken);
  const [draftToken, setDraftToken] = useState("");
  const [signInError, setSignInError] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [view, setView] = useState<Console | null>(null);
  const [log, setLog] = useState<LogView>(emptyLog);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [order, setOrder] = useState("");
  const [orderSent, setOrderSent] = useState(false);
  const logRef = useRef<LogView>(emptyLog());
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrl = useRef<string | null>(null);

  const call = useCallback(
    <T,>(path: string, init: RequestInit = {}) => {
      const headers = new Headers(init.headers);
      headers.set("Authorization", `Bearer ${token ?? ""}`);
      return apiFetch<T>(path, { cache: "no-store", ...init, headers });
    },
    [token],
  );

  const signOut = useCallback((why: string | null) => {
    audioRef.current?.pause();
    writeToken(null);
    setBrief(null);
    setView(null);
    logRef.current = emptyLog();
    setLog(logRef.current);
    setSignInError(why);
  }, []);

  /** Any 401 means the token is wrong or rotated: back to sign-in, never a loop of errors. */
  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) return signOut("The admin token was rejected. Sign in again.");
      setError(errorOf(e));
    },
    [signOut],
  );

  /** The brief's audio from the gateway (fetched with the header: the token never goes in a URL), else browser speech. */
  const playBrief = useCallback(
    async (b: Brief) => {
      audioRef.current?.pause();
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
      audioUrl.current = null;
      try {
        if (b.briefEventId === null) throw new Error("no brief event");
        const r = await fetch(`${API_URL}/api/brief/audio?event_id=${b.briefEventId}`, { headers: { Authorization: `Bearer ${token ?? ""}` } });
        if (!r.ok) throw new Error(`audio ${r.status}`);
        audioUrl.current = URL.createObjectURL(await r.blob());
        const a = new Audio(audioUrl.current);
        audioRef.current = a;
        await a.play();
      } catch {
        say(b.text); // no voice key (503), or autoplay blocked: the browser reads it
      }
    },
    [token],
  );

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const s = consoleFromState(await call<unknown>("/state"));
      if (s) setView(s);
      const getJson = (url: string) => call<unknown>(url.slice(`${API_URL}/api`.length));
      const { events } = await fetchAllEvents({ base: API_URL, token: "" }, getJson, logRef.current.lastId, 20);
      if (events.length) {
        logRef.current = foldLog(logRef.current, events as UTaxEvent[]);
        setLog(logRef.current);
      }
    } catch (e) {
      fail(e);
    }
  }, [call, fail, token]);

  // Signed in: the brief once, then the console refreshes every few seconds.
  useEffect(() => {
    if (!token) return;
    let alive = true;
    (async () => {
      try {
        let since = 0;
        try {
          since = readLastSeen(localStorage.getItem(LAST_SEEN_KEY));
        } catch {
          /* no storage: the brief covers everything */
        }
        const b = parseBrief(await call<unknown>(`/ceo/brief?since_id=${since}`));
        if (!alive || !b) return;
        setBrief(b);
        logRef.current = seedPending(logRef.current, b.pending);
        setLog(logRef.current);
        if (b.lastEventId !== null) {
          try {
            localStorage.setItem(LAST_SEEN_KEY, String(b.lastEventId));
          } catch {
            /* fine */
          }
        }
        void playBrief(b);
      } catch (e) {
        if (alive) fail(e);
      }
      if (alive) await refresh();
    })();
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
    // The brief is fetched once per sign-in, not on every new `call`/`refresh` identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  useEffect(
    () => () => {
      audioRef.current?.pause();
      if (audioUrl.current) URL.revokeObjectURL(audioUrl.current);
      if (hasSpeech()) window.speechSynthesis.cancel();
    },
    [],
  );

  const signIn = async () => {
    const t = draftToken.trim();
    if (!t) return;
    setSigningIn(true);
    setSignInError(null);
    try {
      // Check it before keeping it: a wrong token never lands in storage.
      await apiFetch<unknown>("/state", { cache: "no-store", headers: { Authorization: `Bearer ${t}` } });
      setDraftToken("");
      writeToken(t);
    } catch (e) {
      setSignInError(e instanceof ApiError && e.status === 401 ? "That token isn't the admin token." : errorOf(e));
    } finally {
      setSigningIn(false);
    }
  };

  /** One action at a time; the console refreshes right after. */
  const act = async (key: string, path: string, body: unknown) => {
    if (busy) return;
    setBusy(key);
    setError(null);
    try {
      await call(path, { method: "POST", body: JSON.stringify(body) });
      await refresh();
      return true;
    } catch (e) {
      fail(e);
      await refresh();
      return false;
    } finally {
      setBusy(null);
    }
  };

  const sendOrder = async () => {
    const body = orderBody(order);
    if (!body.text) return;
    if (await act("order", "/voice", body)) {
      setOrder("");
      setOrderSent(true);
    }
  };

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
              <input id="token" type="password" autoComplete="current-password" value={draftToken} onChange={(e) => setDraftToken(e.target.value)} />
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

  const blocked = view?.cases.filter((c) => c.status === "BLOCKED") ?? [];
  const pending = [...log.pending.values()];
  const candidates = view?.candidates ?? [];
  const names = new Map(view?.cases.map((c) => [c.caseId, c.name]) ?? []);

  return (
    <div className="ceo">
      <header>
        <span className="logo">U</span>
        <span className="title">U-Tax · CEO console</span>
        <button className="ghost" onClick={() => signOut(null)}>
          Sign out
        </button>
      </header>
      <main>
        {error && (
          <p className="error banner" role="alert">
            {error}
          </p>
        )}

        <section className="brief" aria-labelledby="brief-h">
          <h1 id="brief-h">Your brief</h1>
          {brief ? (
            <>
              <p className="brief-text">{brief.text}</p>
              <button className="secondary" onClick={() => void playBrief(brief)}>
                Play the brief
              </button>
            </>
          ) : (
            <p className="dim">Loading the brief…</p>
          )}
        </section>

        <section aria-labelledby="ap-h">
          <h2 id="ap-h">
            Waiting on you <span className="count">{pending.length}</span>
          </h2>
          {pending.length === 0 ? (
            <p className="empty">Nothing needs your sign-off.</p>
          ) : (
            <ul className="items">
              {pending.map((a) => (
                <li key={a.approvalId}>
                  <div className="what">
                    <small>{ACTION_LABEL[a.actionType as ActionType] ?? a.actionType}</small>
                    {a.summary || a.approvalId}
                    {a.caseId && names.get(a.caseId) && !a.summary.includes(names.get(a.caseId)!) ? <span className="dim"> · {names.get(a.caseId)}</span> : null}
                  </div>
                  <div className="btns">
                    <button className="primary" disabled={!!busy} onClick={() => void act(`ap:${a.approvalId}`, "/approve", { approval_id: a.approvalId, decision: "allow", note: "" })}>
                      Approve
                    </button>
                    <button className="secondary" disabled={!!busy} onClick={() => void act(`ap:${a.approvalId}`, "/approve", { approval_id: a.approvalId, decision: "deny", note: "" })}>
                      Deny
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="bl-h">
          <h2 id="bl-h">
            Blocked cases <span className="count">{blocked.length}</span>
          </h2>
          {blocked.length === 0 ? (
            <p className="empty">No case is waiting on a human.</p>
          ) : (
            <ul className="items">
              {blocked.map((c) => (
                <li key={c.caseId}>
                  <div className="what">
                    <small>{c.flagged ? "Blocked · flagged by QA" : "Blocked"}</small>
                    {c.name}
                    <span className="dim"> · {log.blockedReason.get(c.caseId) ?? "needs a human"}</span>
                  </div>
                  <div className="btns">
                    {UNBLOCK.map((u) => (
                      <button
                        key={u.to}
                        className={u.to === "READY" ? "primary" : "secondary"}
                        title={u.hint}
                        disabled={!!busy}
                        onClick={() => void act(`ub:${c.caseId}`, `/cases/${encodeURIComponent(c.caseId)}/unblock`, { to: u.to satisfies UnblockTo, note: "" })}
                      >
                        {u.label}
                      </button>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="hr-h">
          <h2 id="hr-h">
            Hiring <span className="count">{candidates.filter((c) => canDecide(c.stage)).length}</span>
          </h2>
          {candidates.length === 0 ? (
            <p className="empty">No applicants yet.</p>
          ) : (
            <ul className="items">
              {candidates.map((c) => (
                <li key={c.candidateId}>
                  <div className="what">
                    <small>{STAGE_LABEL[c.stage] ?? c.stage}</small>
                    {c.name}
                    {c.credentials ? <span className="dim"> · {c.credentials}</span> : null}
                  </div>
                  {canDecide(c.stage) && (
                    <div className="btns">
                      <button className="primary" disabled={!!busy} onClick={() => void act(`hr:${c.candidateId}`, `/candidates/${encodeURIComponent(c.candidateId)}/decision`, { decision: "hire" })}>
                        Hire
                      </button>
                      <button className="secondary" disabled={!!busy} onClick={() => void act(`hr:${c.candidateId}`, `/candidates/${encodeURIComponent(c.candidateId)}/decision`, { decision: "decline" })}>
                        Decline
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-labelledby="tr-h">
          <h2 id="tr-h">Standing orders</h2>
          <ul className="items">
            {ACTION_TYPES.map((t) => {
              const state = view?.trust[t] ?? "ASK";
              return (
                <li key={t}>
                  <div className="what">
                    <small className={`st ${state.toLowerCase()}`}>{state === "PAUSED" ? "paused" : state === "AUTO" ? "runs on its own" : "asks you first"}</small>
                    {ACTION_LABEL[t]}
                  </div>
                  <div className="btns">
                    {state === "PAUSED" ? (
                      <button className="primary" disabled={!!busy} onClick={() => void act(`tr:${t}`, "/trust", { action_type: t, state: "AUTO" })}>
                        Resume
                      </button>
                    ) : (
                      <button className="secondary" disabled={!!busy} onClick={() => void act(`tr:${t}`, "/trust", { action_type: t, state: "PAUSED" })}>
                        Pause
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>

        <section aria-labelledby="or-h">
          <h2 id="or-h">Order to the Chief of Staff</h2>
          <form
            className="order"
            onSubmit={(e) => {
              e.preventDefault();
              void sendOrder();
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
                Send order
              </button>
            </div>
          </form>
        </section>
      </main>
    </div>
  );
}
