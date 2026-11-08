"use client";

// D32 /office: the firm's app shell. A sidebar lists every department in the firm's order; each
// opens a live workspace (a browser window an agent drives, its step list and its KPIs), all drawn
// from the event log. Auto-follow opens whichever department acted last; any click takes over.

import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { Theater } from "@/components/theater/Theater";
import { DeptAvatar } from "@/components/ui/DeptAvatar";
import { Emblem } from "@/components/ui/Emblem";
import { Switch } from "@/components/ui/Switch";
import { formatCents, formatClock } from "@/theater/format";
import type { FirmNumbers } from "@/theater/rail";
import { useFirmFeed, type DocsBase, type FeedStatus } from "@/office/feed";
import { logout, useSessionToken } from "@/ceo/session";
import { canPopOut, popOut } from "@/ceo/popout";
import { Ceo } from "@/components/ceo/Ceo";
import { followDelay, followTarget, followWhileTouring, initialTab, initialView, TOUR_DWELL_MS, type DeskTab, type View } from "@/office/follow";
import type { Thread } from "@/office/mail";
import { Mail } from "./Mail";
import { Payments } from "./Payments";
import { FormView } from "./FormView";
import { ExcelView } from "./ExcelView";
import { EmptyState } from "@/components/ui/EmptyState";
import type { Docs } from "@/office/forms";
import { activeTask, taskLabel, tourTarget, type TourOrder, type TourTask } from "@/office/tour";
import { TaskBanner } from "./TaskBanner";
import { ARTIFACT_MS, current, DRAFT_MS, type Desk } from "@/office/artifact";
import { ActivityPill, AgentNotes, ArtifactView } from "./Artifact";
import { SEAT_NAME, SEAT_ROLE, SEATS, type Seat, type Workspace, type WsStep } from "@/office/workspace";
import "./office.css";
import "./office-page.css"; // D41 F46: after office.css and the theater CSS, so the prototype rules win ties

/** A desk counts as working while its last step is this recent (wall clock, live feed). */
const WORKING_MS = 20_000;

const seatColor = (seat: Seat) => `var(--seat-${seat})`;


export function Office() {
  const session = useSessionToken();
  const { office, numbers, status, live, studio, tour, mail, payTick, docs, docsLive, docsBase } = useFirmFeed(session);
  const theater = useMemo(() => <Theater />, []);
  /** D33: the CEO console as a panel on the right (signed in). Nothing mounts until the viewer's layout is read. */
  const [panelReady, setPanelReady] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelW, setPanelW] = useState(PANEL_W);
  const [view, setView] = useState<View>("overview");
  const [follow, setFollow] = useState(true);
  const lastSwitch = useRef(0);
  /** D41 F48: the desk the tour is "clicking" (the sidebar press), the task that just ended, the order-done toast. */
  const [pressing, setPressing] = useState<Seat | null>(null);
  const [ended, setEnded] = useState<{ order: TourOrder; task: TourTask } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  /** D41 F49: the Intake desk's view (?tab=inbox opens the Inbox). */
  const [intakeTab, setIntakeTab] = useState<DeskTab>("workspace");
  /** D42 T3: when the viewer last picked a desk tab (a form or sheet event after it switches the tab again). */
  const [pickedAt, setPickedAt] = useState(0);
  const pickTab = useCallback((t: DeskTab) => {
    setIntakeTab(t);
    setPickedAt(Date.now());
  }, []);
  const followRef = useRef(follow);
  const seenEnd = useRef(new Set<string>());
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
      setIntakeTab(initialTab(window.location.search));
      const saved = readPanel();
      // First visit: open on a wide screen only (the theater needs the room on a laptop).
      setPanelOpen(saved ? saved.open : false); // D41 F46: closed unless the CEO opened it
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
  const running = activeTask(tour);
  useEffect(() => {
    const target = followWhileTouring(activeTask(tour) !== null, followTarget(follow, live, office, Date.now()));
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
  }, [follow, live, office, tour]);

  useEffect(() => {
    followRef.current = follow;
  }, [follow]);

  // D41 F48: the task tour. While a CEO task runs, the office shows the department doing it: the
  // sidebar row is pressed, then the desk opens (follow stays on; ?d= is left alone).
  useEffect(() => {
    if (!follow) return;
    const target = tourTarget(tour);
    if (!target || target === view) return;
    let press: ReturnType<typeof setTimeout> | undefined;
    const t = setTimeout(() => {
      setPressing(target);
      press = setTimeout(() => {
        setPressing(null);
        setView(target);
        lastSwitch.current = Date.now();
      }, 650);
    }, followDelay(lastSwitch.current, Date.now(), TOUR_DWELL_MS));
    return () => {
      clearTimeout(t);
      if (press) clearTimeout(press);
    };
  }, [follow, tour, view]);

  // A task that just ended shows "✓ Task 2 done" (or its status) for 2.5 s; an order that just
  // finished shows "All 3 tasks done ✓" for 4 s, then the Overview. History never replays these.
  useEffect(() => {
    const fresh = (ts?: string) => {
      const at = ts ? Date.parse(ts) : NaN;
      return Number.isFinite(at) && Math.abs(Date.now() - at) < 15_000;
    };
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (const order of tour) {
      for (const task of order.tasks) {
        const k = `${order.id}:${task.no}`;
        if (!task.endedAt || seenEnd.current.has(k)) continue;
        seenEnd.current.add(k);
        if (!fresh(task.endedAt)) continue;
        timers.push(setTimeout(() => setEnded({ order, task }), 0));
        timers.push(setTimeout(() => setEnded((e) => (e && e.order.id === order.id && e.task.no === task.no ? null : e)), 2500));
      }
      const k = `${order.id}:done`;
      if (!order.done || seenEnd.current.has(k)) continue;
      seenEnd.current.add(k);
      if (!fresh(order.doneAt)) continue;
      const done = order.tasks.filter((t) => t.status === "done").length;
      const text = done === order.tasks.length ? `All ${order.tasks.length} tasks done ✓` : `${done} of ${order.tasks.length} tasks done`;
      timers.push(setTimeout(() => setToast(text), 0));
      timers.push(
        setTimeout(() => {
          setToast(null);
          if (followRef.current) setView("overview");
        }, 4000),
      );
    }
    return () => timers.forEach(clearTimeout);
  }, [tour]);

  /** The tour opens Intake's Inbox while its running task's newest work is an email (one at or after the task started). */
  let newestMail = "";
  for (const th of mail) {
    const ts = th.messages.at(-1)?.ts ?? "";
    if (ts > newestMail) newestMail = ts;
  }
  const tourInbox = follow && running?.task.seat === "intake" && !!running.task.startedAt && newestMail >= running.task.startedAt;
  // The Accounting desk is on the tour for a payment (money events are shown there): open its Payments.
  const tourPayments = follow && running?.task.seat === "acct";
  // D42 T3: the form filling or the sheet building live (the last 90 s) puts its desk on that tab.
  const autoDoc = docsLive && docsLive.at > pickedAt && now - docsLive.at < 90_000 ? docsLive.kind : null;
  const docTab: DeskTab | null = autoDoc === "form" && view === "tax" ? "form" : autoDoc === "excel" && view === "acct" ? "excel" : null;

  /** The task a desk's banner shows: the running one at its desk (and at the CoS), else the one that just ended there. */
  const bannerFor = (seat: Seat): { order: TourOrder; task: TourTask } | null => {
    if (running && (running.task.seat === seat || seat === "cos")) return running;
    if (ended && (ended.task.seat === seat || seat === "cos")) return ended;
    return null;
  };

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

  // D40: each department's activity bar is its share of the busiest desk's steps.
  const maxSteps = Math.max(1, ...SEATS.map((s) => office.desks[s].steps.length));

  return (
    <div className={session ? "office app stage-glow with-panel" : "office app stage-glow"} style={SEAT_VARS}>
      {/* D41 F46: the prototype's sidebar DOM (office-prototype.html), classes as is; .ox scopes office-page.css to it. */}
      <div className="ox" style={{ display: "contents" }}>
        <nav className="side" aria-label="Departments">
          <Link href="/" className="brand" aria-label="U-Tax home">
            <span className="mark emblem-mark" aria-hidden>
              <Emblem size={30} />
            </span>
            U-Tax <small>office</small>
          </Link>
          <button className={view === "overview" ? "ov on" : "ov"} aria-current={view === "overview" ? "page" : undefined} onClick={() => open("overview")}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true" focusable="false">
              <rect x="3" y="4" width="18" height="13" rx="2" />
              <path d="M8 21h8" />
            </svg>
            Overview<span>theater</span>
          </button>
          <h6 aria-hidden>departments · steps</h6>
          {SEATS.map((seat) => {
            const desk = office.desks[seat];
            const busy = working(seat);
            return (
              <button
                key={seat}
                className={["dept", view === seat ? "on" : "", busy ? "busy" : "", pressing === seat ? "tour-press" : "", running && tourTarget(tour) === seat ? "tour-on" : ""].join(" ").trim()}
                aria-current={view === seat ? "page" : undefined}
                onClick={() => open(seat)}
                style={{ "--c": seatColor(seat) } as CSSProperties}
              >
                {/* key: a new step re-runs the one-time pulse */}
                <DeptAvatar key={desk.steps[0]?.key ?? "idle"} seat={seat} size={30} className={busy ? "pulse" : undefined} />
                <div>
                  <b>{SEAT_NAME[seat]}</b>
                  <i style={{ "--w": `${Math.round((desk.steps.length / maxSteps) * 100)}%`, "--c": seatColor(seat) } as CSSProperties} aria-hidden />
                </div>
                <em>{busy ? "working" : desk.steps.length || "idle"}</em>
              </button>
            );
          })}
          <div className="bottom">
            <FeedDot status={status} />
            <div className="follow-sw">
              <Switch on={follow} onChange={() => setFollow((f) => !f)} label="Follow live (open whichever department acts)" />
              <span aria-hidden>{follow && running ? `Touring ${taskLabel(running.order, running.task).toLowerCase()}` : follow ? "Following live" : "Follow live"}</span>
            </div>
            <Link href="/ceo" className="sbtn">
              CEO console ↗
            </Link>
            {session ? (
              <button className="sbtn" onClick={() => void logout()}>
                Sign out
              </button>
            ) : (
              <Link href="/login?next=/office" className="sbtn">
                CEO sign-in
              </Link>
            )}
          </div>
        </nav>
      </div>

      {/* The Overview's theater brings its own <main>; a department view is one. */}
      <div className="office-main">
        {view === "overview" ? (
          <div className="overview">
            {theater}
          </div>
        ) : (
          <Department key={view} desk={office.desks[view]} studio={studio.desks[view]} numbers={numbers} working={working(view) || current(studio.desks[view].act?.ts, now)} following={follow} now={now} session={session} served={studio.served} banner={bannerFor(view)} tab={tourInbox ? "inbox" : (docTab ?? (tourPayments ? "payments" : intakeTab))} onTab={pickTab} mail={mail} payTick={payTick} docs={docs} docsBase={docsBase} />
        )}
        {toast && (
          <div className="tour-toast" role="status">
            {toast}
          </div>
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
    <span className={`livepill feed ${status}`} role="status">
      <span className={`dot${status === "live" ? "" : " off"}`} aria-hidden />
      {label}
    </span>
  );
}

// ------------------------------------------------------------------ one department

function Department({ desk, studio, numbers, working, following, now, session, served, banner, tab, onTab, mail, payTick, docs, docsBase }: { desk: Workspace; studio: Desk; numbers: FirmNumbers | null; working: boolean; following: boolean; now: number; session: string | null; served: Record<string, string[]>; banner: { order: TourOrder; task: TourTask } | null; tab: DeskTab; onTab: (t: DeskTab) => void; mail: Thread[]; payTick: number; docs: Docs; docsBase: DocsBase }) {
  const seat = desk.seat;
  const formTab = seat === "tax" && tab === "form";
  const excelTab = seat === "acct" && tab === "excel";
  const nameOf = (caseId: string | null) => (caseId ? (mail.find((t) => t.caseId === caseId)?.name ?? caseId) : "the client");
  const inbox = seat === "intake" && tab === "inbox";
  const payments = seat === "acct" && tab === "payments";
  const step = desk.steps[0];
  return (
    <main className={banner ? "of-dept touring" : "of-dept"} style={{ "--c": seatColor(seat) } as CSSProperties}>
      {banner && <TaskBanner order={banner.order} task={banner.task} />}
      <header className="dept-head">
        <div>
          <h1 className="dot-display">
            <span className="dot" aria-hidden /> {SEAT_NAME[seat]}
          </h1>
          <p>{SEAT_ROLE[seat]}</p>
        </div>
        <span className={`dept-state${working ? " on" : ""}`}>{working ? "Working now" : step ? `Last step ${formatClock(step.ts)}` : "No steps yet"}</span>
        {following && <span className="dept-follow">Auto-following the firm</span>}
        {seat === "intake" && (
          <div className="seg desk-tabs" role="group" aria-label="Intake view">
            <button className={tab !== "inbox" ? "on" : ""} aria-pressed={tab !== "inbox"} onClick={() => onTab("workspace")}>
              Workspace
            </button>
            <button className={tab === "inbox" ? "on" : ""} aria-pressed={tab === "inbox"} onClick={() => onTab("inbox")}>
              Inbox
            </button>
          </div>
        )}
        {seat === "acct" && (
          <div className="seg desk-tabs" role="group" aria-label="Accounting view">
            <button className={tab !== "payments" && tab !== "excel" ? "on" : ""} aria-pressed={tab !== "payments" && tab !== "excel"} onClick={() => onTab("workspace")}>
              Workspace
            </button>
            <button className={tab === "payments" ? "on" : ""} aria-pressed={tab === "payments"} onClick={() => onTab("payments")}>
              Payments
            </button>
            <button className={tab === "excel" ? "on" : ""} aria-pressed={tab === "excel"} onClick={() => onTab("excel")}>
              Excel
            </button>
          </div>
        )}
        {seat === "tax" && (
          <div className="seg desk-tabs" role="group" aria-label="Tax view">
            <button className={tab !== "form" ? "on" : ""} aria-pressed={tab !== "form"} onClick={() => onTab("workspace")}>
              Workspace
            </button>
            <button className={tab === "form" ? "on" : ""} aria-pressed={tab === "form"} onClick={() => onTab("form")}>
              Form
            </button>
          </div>
        )}
      </header>
      {inbox ? (
        <Mail threads={mail} />
      ) : payments ? (
        <Payments session={session} tick={payTick} />
      ) : formTab ? (
        docs.form ? (
          <FormView key={`${docs.form.caseId}:${docs.form.doc}`} form={docs.form} client={nameOf(docs.form.caseId)} base={docsBase.form === `${docs.form.caseId}:${docs.form.doc}` ? docsBase.formN : 0} session={session} />
        ) : (
          <div className="doc-empty">
            <EmptyState art="sheet" title="No form yet" line="Tax fills a draft Form 1120 here, line by line, once a case asks for income tax." />
          </div>
        )
      ) : excelTab ? (
        docs.book ? (
          <ExcelView key={`${docs.book.caseId}:${docs.book.book}`} book={docs.book} base={docsBase.book === `${docs.book.caseId}:${docs.book.book}` ? docsBase.bookN : 0} />
        ) : (
          <div className="doc-empty">
            <EmptyState art="sheet" title="No workbook yet" line="Accounting builds the returns workbook here, cell by cell, with every formula." />
          </div>
        )
      ) : (
      <div className="dept-grid">
        <Window seat={seat} step={step} tabs={desk.tabs} trail={desk.steps.slice(1, 6)} studio={studio} now={now} session={session} served={served} />
        <aside className="dept-side">
          <Kpis desk={desk} numbers={numbers} />
          <section className="steps" aria-label={`${SEAT_NAME[seat]} steps`}>
            <h2>Steps</h2>
            {desk.steps.length === 0 ? (
              <p className="empty">Nothing yet. Each step this department takes shows here as it happens.</p>
            ) : (
              <ol>
                {desk.steps.slice(0, 14).map((s, i) => (
                  <li key={s.key} className={`${i === 0 ? "now " : ""}${s.kind} ${s.status}${banner && s.order === banner.order.id && s.task === banner.task.no ? " in-task" : ""}`}>
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
      )}
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

const NONE: readonly string[] = [];

function Window({ seat, step, tabs, trail, studio, now, session, served }: { seat: Seat; step: WsStep | undefined; tabs: string[]; trail: WsStep[]; studio: Desk; now: number; session: string | null; served: Record<string, string[]> }) {
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
    <section className="win glass" aria-label={`${SEAT_NAME[seat]}'s workspace`}>
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
          <ArtifactView key={art.key} art={art} session={session} served={art.caseId ? (served[art.caseId] ?? NONE) : NONE} />
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
  job: "Agent market · hire a contractor",
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
