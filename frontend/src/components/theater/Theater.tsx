"use client";

// The Agent Theater player: plays the cursor script step by step. Each step
// moves its agent's named cursor to the target element, then applies the step
// to the screen. Every frame comes from a logged event (premise 7).

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { parseJsonl, type Agent, type UTaxEvent } from "@/theater/events";
import { formatClock } from "@/theater/format";
import { humanTouches, railFromEvents } from "@/theater/rail";
import { applyStep, emptyScreen, type ScreenState } from "@/theater/screen";
import { buildScript, type CursorStep, type Pane } from "@/theater/script";
import { MOVE_MS, movesCursor, stepHoldMs } from "@/theater/timing";
import { CalendarPane, MailPane, PhonePane, plural, Rail, SearchPane, SheetPane, SocialPane, type Typed } from "./Panes";
import "./theater.css";

/** Agents in desk order: id, cursor label, color token (theater.css owns the hexes). */
const AGENTS: [Agent, string, string][] = [
  ["cos", "Chief of Staff", "var(--a-cos)"],
  ["intake", "Intake", "var(--a-intake)"],
  ["tax", "Tax", "var(--a-tax)"],
  ["qa", "QA", "var(--a-qa)"],
  ["acct", "Accounting", "var(--a-acct)"],
  ["mkt", "Marketing", "var(--a-mkt)"],
  ["hr", "HR", "var(--a-hr)"],
  ["ceo", "You (CEO)", "var(--a-ceo)"],
];
const SPEEDS = [1, 4, 16] as const;
const FIXTURE_URL = "/fixtures/events.sample.jsonl";

/** Rail targets without their own element land on the nearest rail card. */
const RAIL_FALLBACK: Record<string, string> = {
  "rail.voice": "rail.touches",
  "rail.brief": "rail.touches",
  "rail.notify": "rail.touches",
  "rail.crm": "rail.cases",
};

/** A target the cursor can actually reach: rendered and visible (stage mode hides the rail cards). */
function visible(el: Element | null): Element | null {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? el : null;
}

function locate(target: string, pane: Pane): Element | null {
  const q = (t: string) => visible(document.querySelector(`[data-target="${CSS.escape(t)}"]`));
  return (
    q(target) ??
    (RAIL_FALLBACK[target] ? q(RAIL_FALLBACK[target]) : null) ??
    q(target.split(".").slice(0, 2).join(".")) ??
    q(pane) ??
    (pane === "rail" ? visible(document.querySelector(".stagebox")) : null)
  );
}

type Pos = { x: number; y: number };

/** Desk positions along the bottom, for a viewport size "WxH". */
function parkedAt(size: string): Record<string, Pos> {
  const [W, H] = size.split("x").map(Number);
  // Desks sit just above the footer, whose height scales with the projector size.
  const footerTop = document.querySelector(".theater footer")?.getBoundingClientRect().top ?? H - 74;
  const y0 = Math.max(0, footerTop - 46);
  return Object.fromEntries(AGENTS.map(([id], i) => [id, { x: W * 0.06 + i * W * 0.1, y: y0 + (i % 2) * 14 }]));
}

const subscribeResize = (cb: () => void) => {
  window.addEventListener("resize", cb);
  return () => window.removeEventListener("resize", cb);
};
const windowSize = () => `${window.innerWidth}x${window.innerHeight}`;
const noSize = () => null;

const hasSpeech = () => typeof window !== "undefined" && "speechSynthesis" in window;
const cancelSpeech = () => {
  if (hasSpeech()) window.speechSynthesis.cancel();
};

/**
 * Speak a brief or a CEO order. Fixture mode uses browser speech only, so the
 * room never hears a gateway brief that isn't in this log (controller ruling);
 * live mode (slice 3) plays GET /api/brief/audio. Customer /talk text is never spoken.
 */
function speakText(text: string) {
  if (!hasSpeech()) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

const shouldSpeak = (step: CursorStep) => step.target === "rail.brief" || step.meta?.speaker === "ceo";

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));

function captionFor(step: CursorStep): { label: string; text: string } | null {
  if (step.action !== "say" || !step.text) return null;
  if (step.target === "rail.brief") return { label: "Morning brief · Chief of Staff", text: step.text };
  const speaker = step.meta?.speaker === "customer" ? "Customer · Talk to U-Tax" : "CEO order";
  return { label: speaker, text: step.text };
}

export function Theater() {
  const [events, setEvents] = useState<UTaxEvent[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [screen, setScreen] = useState<ScreenState>(emptyScreen);
  const [typing, setTyping] = useState<{ target: string; shown: number } | null>(null);
  const [caption, setCaption] = useState<{ label: string; text: string } | null>(null);
  const size = useSyncExternalStore(subscribeResize, windowSize, noSize);
  const desks = useMemo(() => (size ? parkedAt(size) : null), [size]);
  const [speed, setSpeed] = useState<number>(1);
  const [stage, setStage] = useState(false);
  /**
   * Cursors that have moved this run, measured for one layout (viewport size +
   * stage mode). After a resize or stage toggle the old coordinates are wrong,
   * so cursors fall back to their desks until they move again.
   */
  const layout = `${size}|${stage}`;
  const layoutRef = useRef(layout);
  const [moves, setMoves] = useState<{ layout: string; pos: Record<string, Pos> }>({ layout: "", pos: {} });
  const [sound, setSound] = useState(false);
  const [run, setRun] = useState(0);
  const [progress, setProgress] = useState(0);
  const [playing, setPlaying] = useState(false);
  const speedRef = useRef(1);
  const soundRef = useRef(false);

  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);
  useEffect(() => {
    layoutRef.current = layout;
  }, [layout]);
  useEffect(() => {
    soundRef.current = sound;
    if (!sound && typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
  }, [sound]);

  // Load the fixture log.
  useEffect(() => {
    let alive = true;
    fetch(FIXTURE_URL, { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        if (!alive) return;
        const { events: parsed, bad } = parseJsonl(text);
        if (bad.length) console.warn(`[theater] ${bad.length} unreadable log line(s)`, bad);
        setEvents(parsed);
      })
      .catch((e: unknown) => alive && setLoadError(e instanceof Error ? e.message : String(e)));
    return () => {
      alive = false;
    };
  }, []);

  const script = useMemo(() => (events ? buildScript(events) : null), [events]);
  // Rail numbers and the clock come only from events the script accepted
  // (deduplicated, valid), so they can never disagree with what the screen showed.
  const accepted = useMemo(() => script?.accepted ?? [], [script]);
  const byId = useMemo(() => new Map(accepted.map((e) => [e.id, e])), [accepted]);
  const numbers = useMemo(
    () => railFromEvents(accepted.filter((e) => e.id <= screen.lastEventId)),
    [accepted, screen.lastEventId],
  );

  // S toggles stage mode.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) return;
      if (e.key === "s" || e.key === "S") setStage((v) => !v);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // The player.
  useEffect(() => {
    if (!script) return;
    let alive = true;
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms / speedRef.current));
    // Wait for layout after a state change. rAF pauses in hidden tabs, so a
    // timeout backs it up (browsers still throttle timers in long-hidden tabs;
    // the projector tab is expected to stay visible).
    const frame = () =>
      new Promise<void>((r) => {
        let done = false;
        const go = () => {
          if (!done) {
            done = true;
            r();
          }
        };
        requestAnimationFrame(go);
        setTimeout(go, 50);
      });
    const moveTo = async (agent: Agent, target: string, pane: Pane) => {
      await frame();
      const el = locate(target, pane);
      if (!el || !alive) return;
      const r = el.getBoundingClientRect();
      let x = r.left + Math.min(r.width * 0.5, 160);
      let y = r.top + Math.min(r.height * 0.5, 40);
      // Text clipped by its pane (overflow hidden) is still "there": keep the cursor inside the pane.
      const box = el.closest(".pane")?.getBoundingClientRect();
      if (box) {
        x = clamp(x, box.left + 8, box.right - 24);
        y = clamp(y, box.top + 8, box.bottom - 24);
      }
      const at = layoutRef.current;
      setMoves((c) => ({ layout: at, pos: c.layout === at ? { ...c.pos, [agent]: { x, y } } : { [agent]: { x, y } } }));
      await sleep(MOVE_MS);
    };

    (async () => {
      let state = emptyScreen();
      setScreen(state);
      setTyping(null);
      setCaption(null);
      setProgress(0);
      setPlaying(true);
      setMoves({ layout: "", pos: {} });
      await sleep(400);
      const steps = script.steps;
      for (let i = 0; i < steps.length && alive; i++) {
        const step = steps[i];
        // One bad step must never freeze the projector: log it and keep playing.
        try {
          if (movesCursor(step)) {
            if (step.action === "drag" && step.from) await moveTo(step.agent!, step.from, step.pane);
            await moveTo(step.agent!, step.target, step.pane);
          }
          if (!alive) return;
          // A new scene: idle cursors go back to their desks instead of floating over moved panes.
          if (step.action === "focus") setMoves({ layout: "", pos: {} });
          const hold = stepHoldMs(step);
          state = applyStep(state, step);
          if (step.action === "type" && step.text) {
            setTyping({ target: step.target, shown: 0 });
            setScreen(state);
            const ticks = Math.max(1, Math.round(hold / 33));
            for (let t = 1; t <= ticks; t++) {
              await sleep(hold / ticks);
              if (!alive) return;
              setTyping({ target: step.target, shown: Math.ceil((step.text.length * t) / ticks) });
            }
            setTyping(null);
          } else {
            setScreen(state);
            const cap = captionFor(step);
            if (cap) {
              setCaption(cap);
              if (soundRef.current && shouldSpeak(step)) speakText(step.text ?? "");
            }
            await sleep(hold);
            if (!alive) return;
            if (cap) setCaption(null);
          }
        } catch (err) {
          console.error(`[theater] step ${step.key} (${step.action} ${step.target}) failed`, err);
          if (!alive) return;
        }
        setProgress((i + 1) / steps.length);
      }
      if (alive) setPlaying(false);
    })();

    return () => {
      alive = false;
      cancelSpeech();
    };
  }, [script, run]);

  const typed: Typed = useCallback(
    (target, full) => (typing && typing.target === target && full !== undefined ? full.slice(0, typing.shown) : full),
    [typing],
  );

  const current = byId.get(screen.lastEventId);
  const clock = formatClock(current?.ts, true) || "--:--:--";
  const skippedCount = script?.skipped.length ?? 0;
  const empty = script !== null && script.steps.length === 0;

  return (
    <div className={`theater${stage ? " stage" : ""}`} style={{ "--t": 1 / speed } as CSSProperties}>
      <header>
        <span className="logo">U</span>
        <span className="title">U-Tax</span>
        <span className="sub">the firm, live · every move rendered from the action log</span>
        <div className="pill">
          <span className="source" title={skippedCount ? `${skippedCount} log event(s) skipped as malformed or unknown (see console)` : undefined}>
            FIXTURE · contract sample{skippedCount ? ` · ${skippedCount} skipped` : ""}
          </span>
          <span className="live">
            <span className={`dot${playing ? "" : " idle"}`} />
            {loadError ? "NO LOG" : playing ? "PLAYING LOG" : script ? "END OF LOG" : "OPENING"}
          </span>
          <span className="mono" aria-label="time of the current event">
            {clock}
          </span>
        </div>
      </header>

      <main data-focus={screen.camera && screen.camera !== "rail" ? screen.camera : undefined}>
        <div className="col left">
          <MailPane screen={screen} typed={typed} />
          <div className="low">
            <SocialPane screen={screen} typed={typed} />
            <PhonePane screen={screen} typed={typed} />
          </div>
        </div>
        <div className="col mid">
          <SheetPane screen={screen} />
          <div className="low">
            <SearchPane screen={screen} typed={typed} />
            <CalendarPane screen={screen} />
          </div>
        </div>
        <Rail screen={screen} numbers={numbers} />
      </main>

      <footer>
        <div className="avs" aria-hidden>
          {AGENTS.filter(([id]) => id !== "ceo").map(([id, label, color]) => (
            <span key={id} className="av" style={{ background: color }} title={label} />
          ))}
        </div>
        <span className="mono firmline">
          Firm run by <b>{plural(numbers.firm.agents, "agent")}</b> · <b>{plural(numbers.firm.clients, "client")}</b> ·{" "}
          <b>{plural(humanTouches(numbers), "human touch", "human touches")}</b>
        </span>
        <div className="bar" role="progressbar" aria-label="log progress" aria-valuenow={Math.round(progress * 100)} aria-valuemin={0} aria-valuemax={100}>
          <i style={{ width: `${progress * 100}%` }} />
        </div>
        <span className="hint">S = stage mode</span>
        <div className="ctl" role="group" aria-label="playback">
          {SPEEDS.map((s) => (
            <button key={s} className={speed === s ? "on" : ""} aria-pressed={speed === s} onClick={() => setSpeed(s)}>
              {s}×
            </button>
          ))}
          <button className={sound ? "on" : ""} aria-pressed={sound} onClick={() => setSound((v) => !v)} title="Play spoken briefs and voice">
            {sound ? "Sound on" : "Sound off"}
          </button>
          <button className={stage ? "on" : ""} aria-pressed={stage} onClick={() => setStage((v) => !v)} title="Stage mode (S)">
            Stage
          </button>
          <button onClick={() => setRun((r) => r + 1)} disabled={!script}>
            Replay
          </button>
        </div>
      </footer>

      {/* Always mounted, so screen readers announce each caption as it changes. */}
      <div className="sr-only" role="status" aria-live="polite">
        {caption ? `${caption.label}: ${caption.text}` : ""}
      </div>
      {caption && (
        <div className="caption" aria-hidden>
          <small>{caption.label}</small>
          {caption.text}
        </div>
      )}

      {(loadError || !script || empty) && (
        <div className="overlay" role="status">
          <div>
            {loadError ? "Couldn’t load the action log" : !script ? "Firm opening…" : "No clients yet"}
            <small>{loadError ? loadError : !script ? "Agents at their desks" : "Marketing is posting"}</small>
          </div>
        </div>
      )}

      {AGENTS.map(([id, label, color]) => {
        const p = (moves.layout === layout ? moves.pos[id] : undefined) ?? desks?.[id];
        if (!p) return null;
        return (
          <div key={id} className="cur" style={{ transform: `translate(${p.x}px, ${p.y}px)` }} aria-hidden>
            <svg width="18" height="20" viewBox="0 0 18 20">
              <path d="M1 1 L16 9 L9 11 L6 18 Z" style={{ fill: color, stroke: "var(--ink)" }} strokeWidth="1" />
            </svg>
            <span style={{ background: color }}>{label}</span>
          </div>
        );
      })}
    </div>
  );
}
