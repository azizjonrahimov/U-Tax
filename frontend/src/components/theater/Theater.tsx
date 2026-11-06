"use client";

// The Agent Theater player. A source (fixture file, full-log replay, or the
// live gateway stream) feeds cursor steps into a queue; the player drains it,
// moving each agent's named cursor to its target and applying the step to the
// screen. Every frame comes from a logged event (premise 7).

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore, type CSSProperties } from "react";
import { API_URL } from "@/lib/api";
import { parseJsonl, type Agent, type UTaxEvent } from "@/theater/events";
import { formatClock } from "@/theater/format";
import {
  backlogSpeed,
  briefAudioUrl,
  fetchAllEvents,
  numbersFromState,
  parseSource,
  reconnectDelayMs,
  seedFromState,
  stateUrl,
  streamUrl,
  type Endpoints,
  type Source,
} from "@/theater/live";
import { humanTouches, railFromEvents, type FirmNumbers } from "@/theater/rail";
import { applyStep, emptyScreen, type ScreenState } from "@/theater/screen";
import { appendEvents, startScript, type CursorStep, type Pane, type ScriptCursor } from "@/theater/script";
import { MOVE_MS, movesCursor, stepHoldMs } from "@/theater/timing";
import { CalendarPane, MailPane, PhonePane, plural, Rail, SearchPane, SheetPane, SocialPane, type Typed } from "./Panes";
import { PaneBoundary } from "./PaneBoundary";
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
const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const GATEWAY: Endpoints = { base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" };
/** /api/state refresh after events land (live rail numbers), at most this often. */
const STATE_REFRESH_MS = 1200;

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
const subscribeNothing = () => () => {};
const locationSearch = () => window.location.search;
const noSearch = () => "";

const hasSpeech = () => typeof window !== "undefined" && "speechSynthesis" in window;
const cancelSpeech = () => {
  if (hasSpeech()) window.speechSynthesis.cancel();
};

function speakText(text: string) {
  if (!hasSpeech()) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}

/** Briefs and CEO orders are spoken; customer /talk text never is. */
const shouldSpeak = (step: CursorStep) => step.target === "rail.brief" || step.meta?.speaker === "ceo";

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), Math.max(lo, hi));

function captionFor(step: CursorStep): { label: string; text: string } | null {
  if (step.action !== "say" || !step.text) return null;
  if (step.target === "rail.brief") return { label: "Morning brief · Chief of Staff", text: step.text };
  const speaker = step.meta?.speaker === "customer" ? "Customer · Talk to U-Tax" : "CEO order";
  return { label: speaker, text: step.text };
}

type Status = "loading" | "playing" | "live" | "waiting" | "reconnecting" | "ended" | "error";

/** The queue a source fills and the player drains. Mutable on purpose: it lives in a ref. */
interface Feed {
  steps: CursorStep[];
  accepted: UTaxEvent[];
  skipped: number;
  ended: boolean;
  wake: (() => void) | null;
}
const newFeed = (): Feed => ({ steps: [], accepted: [], skipped: 0, ended: false, wake: null });

/** The ending of a replay: cursors swarm, then line up under "Clients saved" while it counts up. */
const SWARM_ROUNDS = 3;
const swarmPos = (i: number, round: number, W: number, H: number): Pos => {
  // Deterministic scatter (no Math.random): a golden-angle walk over the stage.
  const a = (i * 7 + round * 3) * 2.399963;
  return { x: W * (0.5 + 0.32 * Math.cos(a)), y: H * (0.45 + 0.28 * Math.sin(a * 1.3)) };
};

export function Theater() {
  const search = useSyncExternalStore(subscribeNothing, locationSearch, noSearch);
  const [override, setOverride] = useState<Source | null>(null);
  const source: Source = override ?? parseSource(search, HAS_GATEWAY);

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
  const [status, setStatus] = useState<Status>("loading");
  const [loadError, setLoadError] = useState<string | null>(null);
  const [feedVersion, setFeedVersion] = useState(0);
  const [liveNumbers, setLiveNumbers] = useState<FirmNumbers | null>(null);
  /** The last event whose steps have all played: log-derived rail numbers move when its animation is done. */
  const [doneId, setDoneId] = useState(0);
  const [finale, setFinale] = useState<{ banner: string; saved: number } | null>(null);
  const speedRef = useRef(1);
  const soundRef = useRef(false);
  const feedRef = useRef<Feed>(newFeed());
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const stopAudio = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    cancelSpeech();
  }, []);

  /** Live briefs play the gateway's audio, falling back to browser speech; fixture and replay speak locally. */
  const speak = useCallback(
    (step: CursorStep) => {
      const text = step.text ?? "";
      if (source === "live" && step.target === "rail.brief") {
        stopAudio();
        const audio = new Audio(briefAudioUrl(GATEWAY, step.eventId));
        audioRef.current = audio;
        let fellBack = false;
        const fallback = () => {
          if (fellBack || audioRef.current !== audio || !soundRef.current) return;
          fellBack = true;
          speakText(text);
        };
        audio.addEventListener("error", fallback, { once: true });
        void audio.play()?.catch(fallback);
      } else {
        speakText(text);
      }
    },
    [source, stopAudio],
  );

  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);
  useEffect(() => {
    layoutRef.current = layout;
  }, [layout]);
  useEffect(() => {
    soundRef.current = sound;
    if (!sound) stopAudio();
  }, [sound, stopAudio]);

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

  // ---------------------------------------------------------------- sources

  useEffect(() => {
    const feed = newFeed();
    feedRef.current = feed;
    let alive = true;
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    let cursor: ScriptCursor = startScript();

    const push = (events: UTaxEvent[]) => {
      if (!alive || events.length === 0) return;
      const res = appendEvents(cursor, events);
      cursor = res.cursor;
      feed.steps.push(...res.steps);
      feed.accepted.push(...res.accepted);
      feed.skipped += res.skipped.length;
      setFeedVersion((v) => v + 1);
      feed.wake?.();
    };
    const end = () => {
      feed.ended = true;
      feed.wake?.();
      setFeedVersion((v) => v + 1);
    };
    const getJson = async (url: string) => {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json();
    };

    (async () => {
      setStatus("loading");
      setLoadError(null);
      setLiveNumbers(null);
      try {
        if (source === "fixture") {
          const r = await fetch(FIXTURE_URL, { cache: "no-store" });
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const { events, bad } = parseJsonl(await r.text());
          if (bad.length) console.warn(`[theater] ${bad.length} unreadable log line(s)`, bad);
          push(events);
          end();
          return;
        }
        if (source === "replay") {
          push(await fetchAllEvents(GATEWAY, getJson));
          end();
          return;
        }
      } catch (e) {
        if (alive) {
          setLoadError(e instanceof Error ? e.message : String(e));
          setStatus("error");
        }
        return;
      }

      // ---- live: /api/state seeds the script and the rail, then the SSE stream.
      let attempt = 0;
      const refreshState = async () => {
        try {
          const s = await getJson(stateUrl(GATEWAY));
          if (!alive) return;
          const n = numbersFromState(s);
          if (n) setLiveNumbers(n);
          const { lastId } = seedFromState(s);
          // The gateway's store went backwards (a reset or a new data dir): ids we hold
          // no longer mean the same thing, so start over instead of silently dropping events.
          if (lastId < cursor.lastId) {
            console.warn(`[theater] gateway log went back to id ${lastId} (we were at ${cursor.lastId}); resyncing`);
            setRun((r) => r + 1);
          }
        } catch {
          // The stream reports connection problems; a missed refresh only delays the rail numbers.
        }
      };
      const scheduleRefresh = () => {
        if (refresh) return;
        refresh = setTimeout(() => {
          refresh = undefined;
          void refreshState();
        }, STATE_REFRESH_MS);
      };

      const connect = () => {
        if (!alive) return;
        es?.close();
        // Resume after the last id we applied; the browser's own reconnects send Last-Event-ID.
        es = new EventSource(streamUrl(GATEWAY, cursor.lastId));
        es.onopen = () => {
          attempt = 0;
          if (alive) setStatus("live");
        };
        es.onmessage = (msg) => {
          try {
            const ev = JSON.parse(msg.data) as UTaxEvent;
            push([ev]);
            scheduleRefresh();
          } catch {
            console.warn("[theater] unreadable stream message", msg.data);
          }
        };
        es.onerror = () => {
          if (!alive) return;
          setStatus("reconnecting");
          if (es?.readyState === EventSource.CLOSED) {
            // The browser gave up (HTTP error or the server ended the stream): retry ourselves.
            retry = setTimeout(connect, reconnectDelayMs(attempt++));
          }
        };
      };

      while (alive) {
        try {
          const s = await getJson(stateUrl(GATEWAY));
          if (!alive) return;
          const seed = seedFromState(s);
          cursor = startScript(seed);
          const n = numbersFromState(s);
          if (n) setLiveNumbers(n);
          setStatus("waiting");
          connect();
          return;
        } catch (e) {
          if (!alive) return;
          setLoadError(e instanceof Error ? e.message : String(e));
          setStatus("reconnecting");
          await new Promise((r) => (retry = setTimeout(r, reconnectDelayMs(attempt++))));
        }
      }
    })();

    return () => {
      alive = false;
      es?.close();
      clearTimeout(retry);
      clearTimeout(refresh);
      feed.ended = true;
      feed.wake?.();
    };
  }, [source, run]);

  // ---------------------------------------------------------------- player

  useEffect(() => {
    let alive = true;
    const feed = feedRef.current;
    let i = 0;
    const effSpeed = () => Math.max(speedRef.current, source === "live" ? backlogSpeed(feed.steps.length - i) : 1);
    const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms / effSpeed()));
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
    const place = (agent: string, x: number, y: number) => {
      const at = layoutRef.current;
      setMoves((c) => ({ layout: at, pos: c.layout === at ? { ...c.pos, [agent]: { x, y } } : { [agent]: { x, y } } }));
    };
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
      place(agent, x, y);
      await sleep(MOVE_MS);
    };
    const nextStep = async (): Promise<CursorStep | null> => {
      while (alive) {
        if (i < feed.steps.length) return feed.steps[i];
        if (feed.ended) return null;
        if (source === "live") setStatus((s) => (s === "live" || s === "playing" ? "live" : s));
        await new Promise<void>((r) => (feed.wake = r));
        feed.wake = null;
      }
      return null;
    };

    // The finale is a fixed-length ending: it runs in real time whatever the playback speed.
    const realSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
    const finaleRun = async () => {
      // Replay ending (design doc): cursors swarm, then fly into "Clients saved $X", which counts up.
      const total = railFromEvents(feed.accepted).money.tax_saved_cents;
      const clients = railFromEvents(feed.accepted).firm.clients;
      const touches = humanTouches(railFromEvents(feed.accepted));
      setFinale({ banner: `${plural(clients, "client")} · 7 agents · ${plural(touches, "human touch", "human touches")}`, saved: 0 });
      const W = window.innerWidth;
      const H = window.innerHeight;
      for (let round = 0; round < SWARM_ROUNDS && alive; round++) {
        AGENTS.forEach(([id], k) => {
          const p = swarmPos(k, round, W, H);
          place(id, p.x, p.y);
        });
        await realSleep(MOVE_MS);
      }
      if (!alive) return;
      const money = visible(document.querySelector(".stagebox .g")) ?? visible(document.querySelector('[data-target="rail.money"] .big'));
      if (money) {
        // Line up across the whole card (the number itself is narrow and right-aligned).
        const r = (money.closest(".pane, .stagebox") ?? money).getBoundingClientRect();
        const step = Math.min(34, (r.width - 40) / AGENTS.length);
        AGENTS.forEach(([id], k) => place(id, r.left + 12 + step * k, r.bottom - 22));
      }
      await realSleep(MOVE_MS);
      const steps = 30;
      for (let k = 1; k <= steps && alive; k++) {
        setFinale((f) => (f ? { ...f, saved: Math.round((total * k) / steps) } : f));
        await realSleep(40);
      }
      if (!alive) return;
      await realSleep(2400);
      if (alive) setFinale((f) => (f ? { ...f, banner: "" } : f));
    };

    (async () => {
      let state = emptyScreen();
      setScreen(state);
      setTyping(null);
      setCaption(null);
      setProgress(0);
      setDoneId(0);
      setFinale(null);
      setMoves({ layout: "", pos: {} });
      await sleep(400);
      for (;;) {
        const step = await nextStep();
        if (!alive) return;
        if (!step) break;
        if (source !== "live") setStatus("playing");
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
              if (soundRef.current && shouldSpeak(step)) speak(step);
            }
            await sleep(hold);
            if (!alive) return;
            if (cap) setCaption(null);
          }
        } catch (err) {
          console.error(`[theater] step ${step.key} (${step.action} ${step.target}) failed`, err);
          if (!alive) return;
        }
        i += 1;
        const next = feed.steps[i];
        if (!next || next.eventId !== step.eventId) setDoneId(step.eventId);
        setProgress(i / Math.max(1, feed.steps.length));
      }
      if (!alive) return;
      setStatus("ended");
      if (source === "replay") await finaleRun();
    })();

    return () => {
      alive = false;
      stopAudio();
    };
  }, [source, run, speak, stopAudio]);

  // ---------------------------------------------------------------- derived view

  const feed = feedRef.current;
  // Rail numbers: the gateway's projections in live mode; otherwise only events the
  // script accepted (deduplicated, valid), counted once their animation has played.
  const logNumbers = useMemo(
    () => railFromEvents(feed.accepted.filter((e) => e.id <= doneId)),
    // feedVersion: the feed is a mutable ref, so its growth is tracked by version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feed, feedVersion, doneId],
  );
  const baseNumbers = source === "live" && liveNumbers ? liveNumbers : logNumbers;
  const numbers = finale ? { ...baseNumbers, money: { ...baseNumbers.money, tax_saved_cents: finale.saved } } : baseNumbers;
  const byId = useMemo(
    () => new Map(feed.accepted.map((e) => [e.id, e])),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feed, feedVersion],
  );

  const typed: Typed = useCallback(
    (target, full) => (typing && typing.target === target && full !== undefined ? full.slice(0, typing.shown) : full),
    [typing],
  );

  const current = byId.get(screen.lastEventId);
  const clock = formatClock(current?.ts, true) || "--:--:--";
  const skippedCount = feed.skipped;
  const loaded = feed.ended || source === "live";
  const empty = source !== "live" && feed.ended && feed.steps.length === 0;

  const sourceLabel = source === "live" ? "LIVE · gateway" : source === "replay" ? "REPLAY · full log" : "FIXTURE · contract sample";
  const statusLabel: Record<Status, string> = {
    loading: "OPENING",
    playing: source === "replay" ? "REPLAYING" : "PLAYING LOG",
    live: "LIVE",
    waiting: "LIVE · waiting",
    reconnecting: "RECONNECTING…",
    ended: "END OF LOG",
    error: "NO LOG",
  };
  const blinking = status === "live" || status === "playing";

  const overlay =
    status === "error"
      ? { title: "Couldn’t load the action log", sub: loadError ?? "" }
      : source === "live" && status === "reconnecting" && !liveNumbers
        ? { title: "Gateway unreachable", sub: "Retrying…" }
        : !loaded || status === "loading"
          ? { title: "Firm opening…", sub: "Agents at their desks" }
          : empty
            ? { title: "No clients yet", sub: "Marketing is posting" }
            : null;

  return (
    <div className={`theater${stage ? " stage" : ""}`} style={{ "--t": 1 / speed } as CSSProperties}>
      <header>
        <span className="logo">U</span>
        <span className="title">U-Tax</span>
        <span className="sub">the firm, live · every move rendered from the action log</span>
        <div className="pill">
          <span
            className={`source${source === "live" ? " gateway" : ""}`}
            title={skippedCount ? `${skippedCount} log event(s) skipped as malformed or unknown (see console)` : undefined}
          >
            {sourceLabel}
            {skippedCount ? ` · ${skippedCount} skipped` : ""}
          </span>
          <span className="live">
            <span className={`dot${blinking ? "" : " idle"}`} />
            {statusLabel[status]}
          </span>
          <span className="mono" aria-label="time of the current event">
            {clock}
          </span>
        </div>
      </header>

      <PaneBoundary resetKey={screen.lastEventId}>
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
      </PaneBoundary>

      <footer>
        <div className="avs" aria-hidden>
          {AGENTS.filter(([id]) => id !== "ceo").map(([id, label, color]) => (
            <span key={id} className="av" style={{ background: color }} title={label} />
          ))}
        </div>
        <span className="mono firmline">
          Firm run by <b>{plural(numbers.firm.agents, "agent")}</b> · <b>{plural(numbers.firm.clients, "client")}</b> ·{" "}
          <b>{plural(humanTouches(numbers), "human touch", "human touches")}</b>
          {source === "replay" ? " · Replay" : ""}
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
          <button className={sound ? "on" : ""} aria-pressed={sound} onClick={() => setSound((v) => !v)} title="Play spoken briefs and CEO orders">
            {sound ? "Sound on" : "Sound off"}
          </button>
          <button className={stage ? "on" : ""} aria-pressed={stage} onClick={() => setStage((v) => !v)} title="Stage mode (S)">
            Stage
          </button>
          <button
            onClick={() => (HAS_GATEWAY && source !== "replay" ? setOverride("replay") : setRun((r) => r + 1))}
            disabled={status === "loading"}
            title={HAS_GATEWAY ? "Replay the whole night from the gateway log" : "Play the log again"}
          >
            Replay
          </button>
          {HAS_GATEWAY && source !== "live" && (
            <button onClick={() => setOverride("live")} title="Follow the firm live">
              Live
            </button>
          )}
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

      {finale?.banner && (
        <div className="banner" role="status">
          Night shift replayed
          <small>{finale.banner}</small>
        </div>
      )}

      {overlay && (
        <div className="overlay" role="status">
          <div>
            {overlay.title}
            <small>{overlay.sub}</small>
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
