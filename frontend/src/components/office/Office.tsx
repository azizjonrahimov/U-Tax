"use client";

// D32 /office: the firm's app shell. A sidebar lists every department in the firm's order; each
// opens a live workspace (a browser window an agent drives, its step list and its KPIs), all drawn
// from the event log. Auto-follow opens whichever department acted last; any click takes over.

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Theater } from "@/components/theater/Theater";
import { formatCents, formatClock } from "@/theater/format";
import type { FirmNumbers } from "@/theater/rail";
import { useFirmFeed, type FeedStatus } from "@/office/feed";
import { logout, useSessionToken } from "@/ceo/session";
import { canPopOut, popOut } from "@/ceo/popout";
import { Ceo } from "@/components/ceo/Ceo";
import { followDelay, followTarget, initialView, type View } from "@/office/follow";
import { ARTIFACT_MS, current, DRAFT_MS, type Desk } from "@/office/artifact";
import { ActivityPill, AgentNotes, ArtifactView } from "./Artifact";
import { SEAT_NAME, SEAT_ROLE, SEATS, type Seat, type Workspace, type WsStep } from "@/office/workspace";
import "./office.css";

/** A desk counts as working while its last step is this recent (wall clock, live feed). */
const WORKING_MS = 20_000;

const seatColor = (seat: Seat) => `var(--seat-${seat})`;


export function Office() {
  const session = useSessionToken();
  const { office, numbers, status, live, studio } = useFirmFeed(session);
  const theater = useMemo(() => <Theater />, []);
  /** D33: the CEO console as a panel on the right (signed in). Nothing mounts until the viewer's layout is read. */
  const [panelReady, setPanelReady] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelW, setPanelW] = useState(PANEL_W);
  const [view, setView] = useState<View>("overview");
  const [follow, setFollow] = useState(true);
  const lastSwitch = useRef(0);
  /** Wall clock for the "working" dots, refreshed every few seconds (render stays pure). */
  const [now, setNow] = useState(0);

  useEffect(() => {
    // After hydration (the server renders the overview): a shared link to a department opens it and stays there.
    // The Overview gets a full dwell before auto-follow may leave it.
    lastSwitch.current = Date.now();
    const t = setTimeout(() => {
      setNow(Date.now());
      const init = initialView(window.location.search);
      if (!init.follow) setFollow(false);
      if (init.view !== "overview") setView(init.view);
      const saved = readPanel();
      // First visit: open on a wide screen only (the theater needs the room on a laptop).
      setPanelOpen(saved ? saved.open : window.innerWidth >= 1600);
      if (saved) setPanelW(saved.width);
      setPanelReady(true);
    }, 0);
    const tick = setInterval(() => setNow(Date.now()), 4000);
    return () => {
      clearTimeout(t);
      clearInterval(tick);
    };
  }, []);

  // Auto-follow: open the department that just acted, no faster than one every DWELL_MS. Only a
  // real, recent agent step counts: the log folded on open (the initial replay) never moves the view.
  useEffect(() => {
    const target = followTarget(follow, live, office, Date.now());
    if (!target) return;
    const wait = followDelay(lastSwitch.current, Date.now());
    const t = setTimeout(() => {
      setView((v) => {
        if (v === target) return v;
        lastSwitch.current = Date.now();
        return target;
      });
    }, wait);
    return () => clearTimeout(t);
  }, [follow, live, office]);

  const open = useCallback((v: View) => {
    setFollow(false);
    setView(v);
    const url = new URL(window.location.href);
    if (v === "overview") url.searchParams.delete("d");
    else url.searchParams.set("d", v);
    window.history.replaceState(null, "", url);
  }, []);

  // A desk is working while its newest step is recent (the step's own log time).
  const working = (seat: Seat) => {
    const ts = office.desks[seat].steps[0]?.ts;
    const at = ts ? Date.parse(ts) : NaN;
    return Number.isFinite(at) && now - at < WORKING_MS && now - at > -WORKING_MS;
  };

  return (
    <div className={`office${session ? " with-panel" : ""}`} style={SEAT_VARS}>
      <nav className="side" aria-label="Departments">
        <Link href="/" className="side-brand" aria-label="U-Tax home">
          <span className="mark" aria-hidden>
            U
          </span>
          <span>
            U-Tax <small>office</small>
          </span>
        </Link>
        <ul>
          <li>
            <button className={`side-item${view === "overview" ? " on" : ""}`} aria-current={view === "overview" ? "page" : undefined} onClick={() => open("overview")}>
              <span className="dot all" aria-hidden />
              <span className="side-name">Overview</span>
              <span className="side-meta">theater</span>
            </button>
          </li>
          {SEATS.map((seat) => {
            const desk = office.desks[seat];
            const busy = working(seat);
            return (
              <li key={seat}>
                <button
                  className={`side-item${view === seat ? " on" : ""}${busy ? " busy" : ""}`}
                  aria-current={view === seat ? "page" : undefined}
                  onClick={() => open(seat)}
                  style={{ "--c": seatColor(seat) } as CSSProperties}
                >
                  {/* key: a new step re-runs the one-time pulse */}
                  <span key={desk.steps[0]?.key ?? "idle"} className={`dot${busy ? " pulse" : ""}`} aria-hidden />
                  <span className="side-name">{SEAT_NAME[seat]}</span>
                  <span className="side-meta">{busy ? "working" : desk.steps.length ? `${desk.steps.length} steps` : "idle"}</span>
                </button>
              </li>
            );
          })}
          <li className="side-sep">
            <Link href="/ceo" className="side-item">
              <span className="dot ceo-dot" aria-hidden />
              <span className="side-name">CEO console</span>
              <span className="side-meta">↗</span>
            </Link>
          </li>
        </ul>
        <div className="side-foot">
          <FeedDot status={status} />
          <button className={`follow${follow ? " on" : ""}`} aria-pressed={follow} onClick={() => setFollow((f) => !f)}>
            {follow ? "Following live" : "Follow live"}
          </button>
          {session ? (
            <button className="side-out" onClick={() => void logout()}>
              Sign out
            </button>
          ) : (
            <Link href="/login?next=/office" className="side-out">
              CEO sign-in
            </Link>
          )}
        </div>
      </nav>

      {/* The Overview's theater brings its own <main>; a department view is one. */}
      <div className="office-main">
        {view === "overview" ? (
          <div className="overview">
            {theater}
          </div>
        ) : (
          <Department key={view} desk={office.desks[view]} studio={studio.desks[view]} numbers={numbers} working={working(view) || current(studio.desks[view].act?.ts, now)} following={follow} now={now} session={session} />
        )}
      </div>
      {session && panelReady && (
        <CeoPanel
          open={panelOpen}
          width={panelW}
          onOpen={(o) => {
            setPanelOpen(o);
            savePanel({ open: o, width: panelW });
          }}
          onWidth={(w, done) => {
            setPanelW(w);
            if (done) savePanel({ open: panelOpen, width: w });
          }}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ D33: the CEO console panel

const PANEL_W = 460;
const PANEL_MIN = 360;
const PANEL_MAX = 780;
const PANEL_KEY = "utax.office.ceoPanel";
const clampW = (w: number) => Math.round(Math.min(PANEL_MAX, Math.max(PANEL_MIN, w)));
/** The viewer's own layout (open or hidden, width): a convenience, so storage may be off. */
function readPanel(): { open: boolean; width: number } | null {
  try {
    const v = JSON.parse(localStorage.getItem(PANEL_KEY) ?? "null") as { open?: unknown; width?: unknown } | null;
    if (!v) return null;
    return { open: v.open !== false, width: typeof v.width === "number" && Number.isFinite(v.width) ? clampW(v.width) : PANEL_W };
  } catch {
    return null;
  }
}
function savePanel(v: { open: boolean; width: number }) {
  try {
    localStorage.setItem(PANEL_KEY, JSON.stringify(v));
  } catch {
    /* storage off: the layout resets next time */
  }
}

function CeoPanel({ open, width, onOpen, onWidth }: { open: boolean; width: number; onOpen: (open: boolean) => void; onWidth: (w: number, done: boolean) => void }) {
  /** The console is live in another window (a pop-out, a /ceo tab): the panel shrinks to a notice. */
  const [elsewhere, setElsewhere] = useState(false);
  const [blocked, setBlocked] = useState(false);
  // One element for the life of the panel: resizing or the office's clock never re-renders the console.
  const console_ = useMemo(() => <Ceo variant="panel" onElsewhere={setElsewhere} />, []);
  if (!open) {
    return (
      <aside className="ceo-panel closed" aria-label="CEO console (hidden)">
        <button className="ceo-tab" onClick={() => onOpen(true)}>
          <span aria-hidden>‹</span> CEO console
        </button>
      </aside>
    );
  }
  // Drag the left edge to resize (or use the arrow keys on it).
  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    const x0 = e.clientX;
    const w0 = width;
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    let w = w0;
    const move = (m: PointerEvent) => {
      w = clampW(w0 + (x0 - m.clientX));
      onWidth(w, false);
    };
    const up = () => {
      el.removeEventListener("pointermove", move);
      el.removeEventListener("pointerup", up);
      el.removeEventListener("pointercancel", up);
      onWidth(w, true);
    };
    el.addEventListener("pointermove", move);
    el.addEventListener("pointerup", up);
    el.addEventListener("pointercancel", up);
  };
  return (
    <aside className={`ceo-panel${elsewhere ? " elsewhere" : ""}`} style={elsewhere ? undefined : { width }} aria-label="CEO console">
      {!elsewhere && (
        <div
          className="ceo-grip"
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize the CEO console"
          aria-valuemin={PANEL_MIN}
          aria-valuemax={PANEL_MAX}
          aria-valuenow={width}
          tabIndex={0}
          onPointerDown={drag}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
              e.preventDefault();
              onWidth(clampW(width + (e.key === "ArrowLeft" ? 24 : -24)), true);
            }
          }}
        />
      )}
      <div className="ceo-bar">
        {canPopOut() && (
          <button className="ghost" onClick={() => setBlocked(!popOut())} title="Open the console in its own window (or bring it to the front)">
            {elsewhere ? "Show the window" : "Pop out"} <span aria-hidden>↗</span>
          </button>
        )}
        <button className="ghost" onClick={() => onOpen(false)} title="Hide the console">
          Hide <span aria-hidden>›</span>
        </button>
      </div>
      {blocked && (
        <p className="popped-note" role="alert">
          Your browser blocked the window. Allow pop-ups for this site, then press Pop out again.
        </p>
      )}
      <div className="ceo-body">{console_}</div>
    </aside>
  );
}

const SEAT_VARS = {
  "--seat-cos": "#ff4d3d",
  "--seat-intake": "#4da3ff",
  "--seat-tax": "#ffb547",
  "--seat-qa": "#3ddc84",
  "--seat-acct": "#c77dff",
  "--seat-mkt": "#ff6fb5",
  "--seat-hr": "#2ee6d6",
} as CSSProperties;

function FeedDot({ status }: { status: FeedStatus }) {
  const label = { connecting: "Connecting…", live: "Live", reconnecting: "Reconnecting…", offline: "No gateway" }[status];
  return (
    <span className={`feed ${status}`} role="status">
      <span className="dot" aria-hidden />
      {label}
    </span>
  );
}

// ------------------------------------------------------------------ one department

function Department({ desk, studio, numbers, working, following, now, session }: { desk: Workspace; studio: Desk; numbers: FirmNumbers | null; working: boolean; following: boolean; now: number; session: string | null }) {
  const seat = desk.seat;
  const step = desk.steps[0];
  return (
    <main className="of-dept" style={{ "--c": seatColor(seat) } as CSSProperties}>
      <header className="dept-head">
        <div>
          <h1>
            <span className="dot" aria-hidden /> {SEAT_NAME[seat]}
          </h1>
          <p>{SEAT_ROLE[seat]}</p>
        </div>
        <span className={`dept-state${working ? " on" : ""}`}>{working ? "Working now" : step ? `Last step ${formatClock(step.ts)}` : "No steps yet"}</span>
        {following && <span className="dept-follow">Auto-following the firm</span>}
      </header>
      <div className="dept-grid">
        <Window seat={seat} step={step} tabs={desk.tabs} trail={desk.steps.slice(1, 6)} studio={studio} now={now} session={session} />
        <aside className="dept-side">
          <Kpis desk={desk} numbers={numbers} />
          <section className="steps" aria-label={`${SEAT_NAME[seat]} steps`}>
            <h2>Steps</h2>
            {desk.steps.length === 0 ? (
              <p className="empty">Nothing yet. Each step this department takes shows here as it happens.</p>
            ) : (
              <ol>
                {desk.steps.slice(0, 14).map((s, i) => (
                  <li key={s.key} className={`${i === 0 ? "now " : ""}${s.kind} ${s.status}`}>
                    <span className="st" aria-hidden>
                      {STATUS_ICON[s.status]}
                    </span>
                    <span className="tx">{s.text}</span>
                    <time className="mono">{formatClock(s.ts, true)}</time>
                  </li>
                ))}
              </ol>
            )}
          </section>
        </aside>
      </div>
    </main>
  );
}

const STATUS_ICON: Readonly<Record<WsStep["status"], string>> = { done: "✓", needs_approval: "⏳", refused: "⛔", error: "✕", other: "•" };

/** The department's browser: tabs, address bar, the page, and the agent's cursor driving it. */
/**
 * D37: the window shows the artifact while it is the desk's latest work: not after a newer
 * non-tool step (a tool.called line belongs to the artifact it made), and not once it is old
 * (a composer that never got anything closes sooner). `now` is the office's ticking clock.
 */
function showsArtifact(studio: Desk, step: WsStep | undefined, now: number): boolean {
  const art = studio.art;
  if (!art) return false;
  const a = Date.parse(art.ts);
  if (!Number.isFinite(a)) return false;
  if (now && now - a > (art.state === "drafting" && (art.kind !== "docs" || art.units.length === 0) ? DRAFT_MS : ARTIFACT_MS)) return false;
  if (!step) return true;
  const s = Date.parse(step.ts);
  return !Number.isFinite(s) || (step.kind === "tool" && step.status === "done") || a >= s - 2000;
}

function Window({ seat, step, tabs, trail, studio, now, session }: { seat: Seat; step: WsStep | undefined; tabs: string[]; trail: WsStep[]; studio: Desk; now: number; session: string | null }) {
  const body = useRef<HTMLDivElement>(null);
  const [cursor, setCursor] = useState<{ x: number; y: number; press: number }>({ x: 24, y: 24, press: 0 });
  const [typed, setTyped] = useState<number>(Infinity);
  const reduce = useRef(false);

  useEffect(() => {
    reduce.current = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }, []);

  const art = showsArtifact(studio, step, now) ? studio.art : null;
  const pageStep = art ? undefined : step;
  /** The last step the cursor played: the same step coming back on screen isn't replayed as new. */
  const played = useRef<string | null>(null);

  // A new step: the cursor travels to the field it touched, presses, and types the value in.
  useLayoutEffect(() => {
    const step = pageStep;
    if (!step || !body.current) return;
    if (played.current === step.key) return;
    played.current = step.key;
    const target = body.current.querySelector<HTMLElement>('[data-focus="1"]');
    const box = body.current.getBoundingClientRect();
    const r = target?.getBoundingClientRect();
    const x = r ? r.left - box.left + Math.min(r.width - 8, 140) : 24;
    const y = r ? r.top - box.top + r.height / 2 : 24;
    const full = step.fields[step.focus]?.value.length ?? 0;
    if (reduce.current) {
      setCursor((c) => ({ x, y, press: c.press + 1 }));
      setTyped(Infinity);
      return;
    }
    setTyped(0);
    setCursor((c) => ({ ...c, x, y }));
    const press = setTimeout(() => setCursor((c) => ({ ...c, press: c.press + 1 })), 620);
    let shown = 0;
    let typing: ReturnType<typeof setInterval> | undefined;
    const start = setTimeout(() => {
      typing = setInterval(() => {
        shown += Math.max(1, Math.ceil(full / 40));
        setTyped(shown);
        if (shown >= full) clearInterval(typing);
      }, 28);
    }, 760);
    return () => {
      clearTimeout(press);
      clearTimeout(start);
      clearInterval(typing);
    };
  }, [pageStep]);

  const site = art ? ART_SITE[art.kind] : (step?.site ?? "about:blank");
  return (
    <section className="win" aria-label={`${SEAT_NAME[seat]}'s workspace`}>
      <div className="win-bar">
        <span className="lights" aria-hidden>
          <i />
          <i />
          <i />
        </span>
        {/* The tabs are a picture of the agent's browser (the address bar names the current one). */}
        <div className="tabs" aria-hidden>
          {(tabs.length ? tabs : ["New tab"]).map((t) => (
            <span key={t} className={`tab${t === site ? " on" : ""}`}>
              {t}
            </span>
          ))}
        </div>
      </div>
      <div className="win-addr">
        <span className="lock" aria-hidden>
          ⌁
        </span>
        <span className="mono addr">{site}</span>
        <ActivityPill act={studio.act} now={now} />
        {!art && step && <span className={`badge ${step.status}`}>{STATUS_WORD[step.status]}</span>}
      </div>
      {/* the page loads (a short bar) whenever a new step arrives */}
      <div key={art?.key ?? step?.key ?? "none"} className="win-load" aria-hidden />
      <div className="win-body" ref={body}>
        {art ? (
          <ArtifactView key={art.key} art={art} session={session} />
        ) : !step ? (
          <div className="win-empty">
            <p>Waiting for {SEAT_NAME[seat]}&rsquo;s next step.</p>
            <p className="faint">Every step here comes from the firm&rsquo;s log as it happens. Nothing is staged.</p>
          </div>
        ) : (
          <div key={step.key} className="page">
            <h2>{step.text}</h2>
            <dl>
              {step.fields.map((f, i) => (
                <div key={f.label} className={`row${i === step.focus ? " focus" : ""}`} data-focus={i === step.focus ? "1" : undefined}>
                  <dt>{f.label}</dt>
                  <dd className={i === step.focus ? "mono typing" : "mono"}>{i === step.focus && Number.isFinite(typed) ? f.value.slice(0, typed) : f.value}</dd>
                </div>
              ))}
            </dl>
            {step.image && (
              // eslint-disable-next-line @next/next/no-img-element -- the gateway's own ad image, checked by the feed
              <img className="shot" src={step.image} alt="The image this step produced" referrerPolicy="no-referrer" onError={(e) => (e.currentTarget.hidden = true)} />
            )}
          </div>
        )}
        {trail.length > 0 && (
          // The tab's history: the pages this department went through just before, newest first.
          <ol className="trail" aria-label="Earlier in this workspace">
            {trail.map((t) => (
              <li key={t.key} className={t.status}>
                <span className="mono">{t.site}</span>
                <span>{t.text}</span>
              </li>
            ))}
          </ol>
        )}
        {step && !art && (
          <div className="agent" style={{ transform: `translate(${cursor.x}px, ${cursor.y}px)` }} aria-hidden>
            <svg width="18" height="20" viewBox="0 0 18 20">
              <path d="M1 1 L16 9 L9 11 L6 18 Z" />
            </svg>
            <span key={cursor.press} className="ring" />
            <span className="who">{SEAT_NAME[seat]}</span>
          </div>
        )}
      </div>
      <AgentNotes desk={studio} now={now} who={SEAT_NAME[seat]} />
    </section>
  );
}

const ART_SITE: Readonly<Record<NonNullable<Desk["art"]>["kind"], string>> = {
  mail: "AgentMail · compose",
  post: "Social · compose",
  docs: "Excel · Word · case documents",
  calc: "utax · compute_tax",
  invite: "Calendar · invite",
};

const STATUS_WORD: Readonly<Record<WsStep["status"], string>> = {
  done: "Done",
  needs_approval: "Needs your OK",
  refused: "Refused",
  error: "Failed",
  other: "Reported",
};

function Kpis({ desk, numbers }: { desk: Workspace; numbers: FirmNumbers | null }) {
  const c = (k: string) => desk.counts[k] ?? 0;
  const tiles: [string, string][] = (() => {
    switch (desk.seat) {
      case "cos":
        return [["Clients", numbers ? String(numbers.firm.clients) : "—"], ["Waiting on the CEO", numbers ? String(numbers.queue.waiting) : "—"], ["Briefs", String(c("briefs"))]];
      case "intake":
        return [["Emails", String(c("emails"))], ["Facts pulled", String(c("facts"))], ["Follow-ups", String(c("follow-ups"))]];
      case "tax":
        return [["Estimates", String(c("estimates"))], ["Law searches", String(c("searches"))], ["Tax saved", numbers ? formatCents(numbers.money.tax_saved_cents) : "—"]];
      case "qa":
        return [["Passed", String(c("passed"))], ["Stopped", String(c("failed"))], ["Flagged", String(c("flags"))]];
      case "acct":
        return [["Memos sent", String(c("memos"))], ["Clients", numbers ? String(numbers.firm.clients) : "—"], ["Tax saved", numbers ? formatCents(numbers.money.tax_saved_cents) : "—"]];
      case "mkt":
        return [["Posts", String(c("posts"))], ["Ads made", String(c("ads"))], ["Spent (firm)", numbers ? `$${numbers.money.spent_usd}` : "—"]];
      case "hr":
        return [["Leads found", String(c("leads"))], ["Emailed", String(c("contacted"))], ["Applicants", numbers ? String(numbers.hiring.applicants) : "—"]];
    }
  })();
  return (
    <section className="kpis" aria-label="Key numbers">
      {tiles.map(([label, value]) => (
        <div key={label} className="kpi">
          <b className="mono">{value}</b>
          <span>{label}</span>
        </div>
      ))}
      <div className="kpi">
        <b className="mono">{c("tools")}</b>
        <span>Tool calls</span>
      </div>
    </section>
  );
}
