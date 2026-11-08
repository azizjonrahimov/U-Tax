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
  eventsUrl,
  fetchAllEvents,
  HttpError,
  isAuthError,
  liveVerdict,
  numbersFromState,
  parseSource,
  reconnectDelayMs,
  seedFromState,
  stateUrl,
  streamUrl,
  WATCHDOG_MS,
  type Endpoints,
  type Source,
} from "@/theater/live";
import { humanTouches, railFromEvents, type FirmNumbers } from "@/theater/rail";
import { applyStep, emptyScreen, flaggedInLog, seedOrders, seedScreen, visibleOrders, type ScreenState } from "@/theater/screen";
import { appendEvents, buildScript, startScript, type CursorStep, type Pane, type ScriptCursor } from "@/theater/script";
import { MOVE_MS, movesCursor, stepHoldMs } from "@/theater/timing";
import { CalendarPane, MailPane, OrdersStrip, PhonePane, plural, Rail, SearchPane, SheetPane, SocialPane, type Typed } from "./Panes";
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
/** Fixture/replay loads retry this many times before showing an error. */
const LOAD_ATTEMPTS = 3;
/** Live mode keeps this many played steps; older ones are dropped so a long night doesn't grow memory. */
const LIVE_KEEP_PLAYED = 200;
/** Live clock lookup keeps this many recent events. */
const LIVE_KEEP_EVENTS = 1000;
/** Customer captions are skipped when the live backlog is deeper than this (public /talk can't stall the stage). */
const CUSTOMER_CAPTION_BACKLOG = 30;
const TOKEN_REJECTED = "Theater token rejected (check NEXT_PUBLIC_THEATER_TOKEN)";
/** D31: a (re)connecting projector reads this many recent events to rebuild the CEO order cards it missed. */
const ORDER_LOOKBACK = 300;

/** Rail targets without their own element land on the nearest rail card. */
const RAIL_FALLBACK: Record<string, string> = {
  "rail.voice": "rail.touches",
  "rail.brief": "rail.touches",
  "rail.notify": "rail.touches",
  "rail.crm": "rail.cases",
  "rail.naive": "rail.touches",
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
  // D32: inside the office shell the theater doesn't start at the window's left edge.
  const box = document.querySelector(".theater")?.getBoundingClientRect();
  const left = box?.left ?? 0;
  const width = box && box.width > 0 ? box.width : W;
  return Object.fromEntries(AGENTS.map(([id], i) => [id, { x: left + width * 0.06 + i * width * 0.1, y: y0 + (i % 2) * 14 }]));
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
const isCustomerSay = (step: CursorStep) => step.action === "say" && step.meta?.speaker === "customer";

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
  /** Fixture/replay: every accepted event (rail numbers are projected from these). Live: empty. */
  accepted: UTaxEvent[];
  /** id → ts of recent accepted events, for the header clock. */
  times: Map<number, string>;
  maxAcceptedId: number;
  skipped: number;
  ended: boolean;
  /** A screen seed for the player to apply before its next step (live: /api/state cases; replay: flagged cases). */
  seed: ((s: ScreenState) => ScreenState) | null;
  /** Steps dropped from the head (live only), so progress stays meaningful. */
  dropped: number;
  /** Live, after a reload: the gateway's last event, so the clock has a time before the next event plays. */
  clockSeed: number;
  /** Highest event id the player has finished (live clock trimming never drops events still to play). */
  playedId: number;
  wake: (() => void) | null;
}
const newFeed = (): Feed => ({ steps: [], accepted: [], times: new Map(), maxAcceptedId: 0, skipped: 0, ended: false, seed: null, dropped: 0, playedId: 0, clockSeed: 0, wake: null });

/** Chain screen seeds: the feed holds one, and several sources can add to it before the player applies it. */
const addSeed = (feed: Feed, next: (s: ScreenState) => ScreenState) => {
  const prev = feed.seed;
  feed.seed = prev ? (s) => next(prev(s)) : next;
};

/** A cancellable wait: cleanup settles every pending one, so no async closure is left hanging. */
function waiter() {
  const pending = new Set<() => void>();
  const wait = (ms: number) =>
    new Promise<void>((resolve) => {
      const done = () => {
        clearTimeout(t);
        pending.delete(done);
        resolve();
      };
      const t = setTimeout(done, ms);
      pending.add(done);
    });
  const cancelAll = () => [...pending].forEach((d) => d());
  return { wait, cancelAll };
}

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
  /** The speed actually playing (live backlog can raise it); CSS transitions follow it. */
  const [effSpeed, setEffSpeed] = useState<number>(1);
  const [stage, setStage] = useState(false);
  /**
   * Cursors that have moved this run, measured for one layout (viewport size +
   * stage mode). After a resize or stage toggle the old coordinates are wrong,
   * so cursors fall back to their desks until they move again.
   */
  // D31: the orders band moves every pane when it opens or closes, so it is part of the layout too.
  const hasOrders = visibleOrders(screen).length > 0;
  const layout = `${size}|${stage}|${hasOrders}`;
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
  /** Replay ending; `lined` = the cursors have lined up at the money (their name tags step aside). */
  const [finale, setFinale] = useState<{ banner: string; saved: number; lined?: boolean } | null>(null);
  const speedRef = useRef(1);
  const soundRef = useRef(false);
  const feedRef = useRef<Feed>(newFeed());
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const sourceRef = useRef<Source>(source);

  /** Stop every voice: the gateway's brief audio and browser speech. */
  const stopAudio = useCallback(() => {
    audioRef.current?.pause();
    audioRef.current = null;
    cancelSpeech();
  }, []);

  /** Live briefs play the gateway's audio, falling back to browser speech; fixture and replay speak locally. */
  const speak = useCallback(
    (step: CursorStep) => {
      const text = step.text ?? "";
      stopAudio(); // never two voices at once
      if (sourceRef.current === "live" && step.target === "rail.brief") {
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
    [stopAudio],
  );

  useEffect(() => {
    speedRef.current = speed;
  }, [speed]);
  useEffect(() => {
    layoutRef.current = layout;
  }, [layout]);
  useEffect(() => {
    sourceRef.current = source;
  }, [source]);
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
    const live = source === "live";
    let alive = true;
    let es: EventSource | null = null;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    let watchdog: ReturnType<typeof setInterval> | undefined;
    let flush: ReturnType<typeof setTimeout> | undefined;
    let cursor: ScriptCursor = startScript();
    const aborter = new AbortController();
    const { wait, cancelAll } = waiter();

    const push = (events: UTaxEvent[]) => {
      if (!alive || events.length === 0) return;
      const res = appendEvents(cursor, events);
      cursor = res.cursor;
      for (const s of res.steps) feed.steps.push(s);
      for (const e of res.accepted) {
        if (!live) feed.accepted.push(e);
        feed.times.set(e.id, e.ts);
        feed.maxAcceptedId = Math.max(feed.maxAcceptedId, e.id);
      }
      if (live && feed.times.size > LIVE_KEEP_EVENTS) {
        // Drop only events already played, so a big catch-up keeps its clock.
        for (const id of feed.times.keys()) {
          if (feed.times.size <= LIVE_KEEP_EVENTS || id > feed.playedId) break;
          feed.times.delete(id);
        }
      }
      if (live) {
        // A backlog can hold an email and the flag on its case: hide the case before its mail plays.
        const flagged = flaggedInLog(res.accepted);
        if (flagged.length) addSeed(feed, (s) => seedScreen(s, [], flagged));
      }
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
      const r = await fetch(url, { cache: "no-store", signal: aborter.signal });
      if (!r.ok) throw new HttpError(r.status);
      return r.json();
    };
    const fail = (e: unknown) => {
      if (!alive) return;
      setLoadError(isAuthError(e) ? TOKEN_REJECTED : e instanceof Error ? e.message : String(e));
      setStatus("error");
    };

    /** Fixture and replay: load the whole log (retrying a blip), then play it. */
    const loadLog = async (load: () => Promise<UTaxEvent[]>) => {
      for (let attempt = 0; ; attempt++) {
        try {
          const events = await load();
          if (!alive) return;
          // The whole night is known up front: cases flagged later never show their mail at all.
          const flagged = flaggedInLog(events);
          if (flagged.length) addSeed(feed, (s) => seedScreen(s, [], flagged));
          push(events);
          end();
          return;
        } catch (e) {
          if (!alive) return;
          if (isAuthError(e) || attempt + 1 >= LOAD_ATTEMPTS) return fail(e);
          await wait(reconnectDelayMs(attempt));
        }
      }
    };

    (async () => {
      setStatus("loading");
      setLoadError(null);
      setLiveNumbers(null);

      if (source === "fixture") {
        await loadLog(async () => {
          const r = await fetch(FIXTURE_URL, { cache: "no-store", signal: aborter.signal });
          if (!r.ok) throw new HttpError(r.status);
          const { events, bad } = parseJsonl(await r.text());
          if (bad.length) console.warn(`[theater] ${bad.length} unreadable log line(s)`, bad);
          return events;
        });
        return;
      }
      if (source === "replay") {
        await loadLog(async () => {
          const { events, truncated } = await fetchAllEvents(GATEWAY, getJson);
          if (truncated) console.warn(`[theater] replay stopped at ${events.length} events (page cap); the rest of the night is not shown`);
          return events;
        });
        return;
      }

      // ---- live: /api/state seeds the script, the rail and the case cards, then the SSE stream.
      let attempt = 0;
      let messages = 0;
      let stalled = 0;
      let queued: UTaxEvent[] = [];
      /** A rejected token stops live mode for good: no more polls or reconnects that would flap the screen. */
      let stopped = false;
      /** Only the newest /api/state answer counts: an older, slower one must not move the stall count. */
      let pollGen = 0;
      /** One 401/403 can be a token rotation or a deploy blip; two in a row is a wrong token. */
      let authFails = 0;
      const stop = (e: unknown) => {
        stopped = true;
        es?.close();
        clearInterval(watchdog);
        fail(e);
      };

      const poll = async () => {
        if (stopped) return;
        // Compare against where we were when we asked: events that land while the request is
        // in flight move the cursor past the snapshot, and that is not a reset.
        const before = cursor.lastId;
        const msgsBefore = messages;
        messages = 0;
        const gen = ++pollGen;
        let s: unknown;
        try {
          s = await getJson(stateUrl(GATEWAY));
        } catch (e) {
          if (gen !== pollGen) return;
          if (isAuthError(e)) {
            if (++authFails >= 2) stop(e);
            else scheduleRefresh(); // check again soon rather than waiting a whole watchdog tick
          }
          return; // an outage shows through the stream's own errors
        }
        if (!alive || stopped || gen !== pollGen) {
          messages += msgsBefore; // a newer poll owns the window: hand the count back
          return;
        }
        authFails = 0;
        const n = numbersFromState(s);
        if (n) setLiveNumbers(n);
        const v = liveVerdict({ stateLastId: seedFromState(s).lastId, cursorLastId: before, messagesSinceLastPoll: msgsBefore + messages, stalledPolls: stalled });
        stalled = v.stalledPolls;
        if (v.verdict === "resync") {
          console.warn(`[theater] gateway log went back behind id ${cursor.lastId}; resyncing`);
          setRun((r) => r + 1);
        } else if (v.verdict === "reconnect") {
          console.warn("[theater] stream is silent while the gateway moved on; reconnecting");
          setStatus("reconnecting");
          connect();
        }
      };
      const scheduleRefresh = () => {
        if (refresh) return;
        refresh = setTimeout(() => {
          refresh = undefined;
          void poll();
        }, STATE_REFRESH_MS);
      };

      const connect = () => {
        if (!alive || stopped) return;
        es?.close();
        // Resume after the last id we applied; the browser's own reconnects send Last-Event-ID.
        es = new EventSource(streamUrl(GATEWAY, cursor.lastId));
        es.onopen = () => {
          attempt = 0;
          if (!alive) return;
          setStatus("live");
          void poll(); // a reconnect can follow a gateway reset: check right away
        };
        es.onmessage = (msg) => {
          messages += 1;
          try {
            queued.push(JSON.parse(msg.data) as UTaxEvent);
          } catch {
            console.warn("[theater] unreadable stream message", msg.data);
            return;
          }
          // A reconnect replays a burst: apply it as one batch, not one render per event.
          if (!flush) {
            flush = setTimeout(() => {
              flush = undefined;
              const batch = queued;
              queued = [];
              push(batch);
              scheduleRefresh();
            }, 0);
          }
        };
        es.onerror = () => {
          if (!alive || stopped) return;
          setStatus("reconnecting");
          if (es?.readyState === EventSource.CLOSED) {
            // The browser gave up (HTTP error or the server ended the stream): retry ourselves.
            void wait(reconnectDelayMs(attempt++)).then(() => alive && connect());
          }
        };
      };

      while (alive) {
        try {
          const s = await getJson(stateUrl(GATEWAY));
          if (!alive) return;
          const seed = seedFromState(s);
          if (seed.lastId === null) console.warn("[theater] /api/state has no last_event_id; streaming from the start");
          cursor = startScript({ caseNames: seed.caseNames, lastId: seed.lastId ?? 0 });
          // Cards for cases already open, so a reload shows rows that match the rail's numbers.
          addSeed(feed, (sc) => seedScreen(sc, seed.cases));
          feed.wake?.();
          if (seed.lastId) {
            // D31: a reload mid-order would lose the order's card (live starts from now): rebuild the
            // cards from the recent log, orders only, without replaying anything on the panes.
            const upTo = seed.lastId;
            void getJson(eventsUrl(GATEWAY, Math.max(0, upTo - ORDER_LOOKBACK), ORDER_LOOKBACK))
              .then((page) => {
                const data = (page as { data?: unknown }).data;
                if (!alive || !Array.isArray(data)) return;
                const recent = (data as UTaxEvent[]).filter((e) => typeof e?.id === "number" && e.id <= upTo);
                const { steps } = buildScript(recent, { log: () => {}, onGap: () => {} });
                addSeed(feed, (sc) => seedOrders(sc, steps));
                feed.wake?.();
              })
              .catch(() => console.warn("[theater] couldn't read recent orders; the band starts with the next one"));
          }
          if (seed.lastId) {
            // The clock shows the last event's time right away (else "--:--:--" until the next one plays).
            const lastId = seed.lastId;
            void getJson(eventsUrl(GATEWAY, lastId - 1, 1))
              .then((page) => {
                const e = (page as { data?: { id?: unknown; ts?: unknown }[] }).data?.[0];
                if (alive && e?.id === lastId && typeof e.ts === "string") {
                  feed.times.set(lastId, e.ts);
                  feed.clockSeed = lastId;
                  setFeedVersion((v) => v + 1);
                }
              })
              .catch(() => console.warn("[theater] no time for the last event; the clock waits for the next one"));
          }
          const n = numbersFromState(s);
          if (n) setLiveNumbers(n);
          setStatus("waiting");
          connect();
          watchdog = setInterval(() => void poll(), WATCHDOG_MS);
          return;
        } catch (e) {
          if (!alive) return;
          if (isAuthError(e)) return stop(e);
          setLoadError(e instanceof Error ? e.message : String(e));
          setStatus("reconnecting");
          await wait(reconnectDelayMs(attempt++));
        }
      }
    })();

    return () => {
      alive = false;
      es?.close();
      aborter.abort();
      cancelAll();
      clearTimeout(refresh);
      clearTimeout(flush);
      clearInterval(watchdog);
      feed.ended = true;
      feed.wake?.();
    };
  }, [source, run]);

  // ---------------------------------------------------------------- player

  useEffect(() => {
    let alive = true;
    const feed = feedRef.current;
    const live = source === "live";
    let i = 0;
    let lastEff = speedRef.current;
    setEffSpeed(lastEff); // a new run starts at the operator's speed, not the last run's catch-up speed
    const { wait: realSleep, cancelAll } = waiter();
    const effective = () => {
      const v = Math.max(speedRef.current, live ? backlogSpeed(feed.steps.length - i) : 1);
      if (v !== lastEff) {
        lastEff = v;
        setEffSpeed(v);
      }
      return v;
    };
    const sleep = (ms: number) => realSleep(ms / effective());
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

    let state = emptyScreen();
    const applySeed = () => {
      if (!feed.seed) return;
      state = feed.seed(state);
      feed.seed = null;
      setScreen(state);
    };
    const nextStep = async (): Promise<CursorStep | null> => {
      while (alive) {
        applySeed();
        if (i < feed.steps.length) return feed.steps[i];
        if (feed.ended) return null;
        await new Promise<void>((r) => (feed.wake = r));
        feed.wake = null;
      }
      return null;
    };

    const finaleRun = async () => {
      // Replay ending (design doc): cursors swarm, then fly into "Clients saved $X", which counts up.
      // Fixed-length, so it runs in real time whatever the playback speed.
      const n = railFromEvents(feed.accepted);
      const total = n.money.tax_saved_cents;
      const banner = `${plural(n.firm.clients, "client")} · ${plural(n.firm.agents, "agent")} · ${plural(humanTouches(n), "human touch", "human touches")}`;
      setFinale({ banner, saved: 0 });
      const W = window.innerWidth;
      const H = window.innerHeight;
      // Reduced motion: no swarm, no count-up; the cursors line up and the total shows at once.
      const calm = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
      for (let round = 0; round < (calm ? 0 : SWARM_ROUNDS) && alive; round++) {
        AGENTS.forEach(([id], k) => {
          const p = swarmPos(k, round, W, H);
          place(id, p.x, p.y);
        });
        await realSleep(MOVE_MS);
      }
      if (!alive) return;
      const money = visible(document.querySelector(".stagebox .g")) ?? visible(document.querySelector('[data-target="rail.money"] .big'));
      if (money) {
        // Eight arrows in a row pointing at the number; their name tags hide so they don't pile up
        // over the card (the footer dots carry the same colors).
        const r = money.getBoundingClientRect();
        const label = money.closest(".k") ? money.previousElementSibling?.getBoundingClientRect() : undefined;
        if (label && label.right < r.left) {
          // Rail card: in the gap between "Clients saved" and the figure, on the figure's line.
          const step = clamp((r.left - label.right - 16) / AGENTS.length, 12, 26);
          // Never on top of the "Clients saved" label, even when the gap is narrow.
          const x0 = Math.max(label.right + 6, r.left - 8 - step * AGENTS.length);
          AGENTS.forEach(([id], k) => place(id, x0 + step * k, r.top + r.height / 2 - 6));
        } else {
          // Stage box: a row just under the figure.
          // Kept on screen when the figure sits near the right edge.
          const x0 = Math.max(8, Math.min(r.left, window.innerWidth - 26 * AGENTS.length - 24));
          AGENTS.forEach(([id], k) => place(id, x0 + 26 * k, r.bottom + 8));
        }
        setFinale((f) => (f ? { ...f, lined: true } : f));
      }
      await realSleep(MOVE_MS);
      const ticks = calm ? 1 : 30;
      for (let k = 1; k <= ticks && alive; k++) {
        setFinale((f) => (f ? { ...f, saved: Math.round((total * k) / ticks) } : f));
        await realSleep(40);
      }
      if (!alive) return;
      await realSleep(2400);
      if (alive) setFinale((f) => (f ? { ...f, banner: "", lined: false } : f)); // name tags come back with the banner gone
    };

    (async () => {
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
        if (!live) setStatus("playing");
        const backlog = feed.steps.length - i;
        // One bad step must never freeze the projector: log it and keep playing.
        try {
          if (movesCursor(step)) {
            if (step.action === "drag" && step.from) await moveTo(step.agent!, step.from, step.pane);
            await moveTo(step.agent!, step.target, step.pane);
          }
          if (!alive) return;
          // A new scene: idle cursors go back to their desks instead of floating over moved panes.
          if (step.action === "focus") setMoves({ layout: "", pos: {} });
          applySeed(); // a flag can arrive while the cursor was moving: hide before the mail lands
          state = applyStep(state, step);
          // Public /talk text can't stall the stage: under a live backlog its captions are skipped.
          const skipCaption = live && isCustomerSay(step) && backlog > CUSTOMER_CAPTION_BACKLOG;
          const hold = skipCaption ? 150 : stepHoldMs(step);
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
            const cap = skipCaption ? null : captionFor(step);
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
        if (!next || next.eventId !== step.eventId) {
          setDoneId(step.eventId);
          feed.playedId = Math.max(feed.playedId, step.eventId);
        }
        // Live: drop played steps from the head so a long night doesn't grow memory.
        if (live && i > LIVE_KEEP_PLAYED * 2) {
          feed.steps.splice(0, i - LIVE_KEEP_PLAYED);
          feed.dropped += i - LIVE_KEEP_PLAYED;
          i = LIVE_KEEP_PLAYED;
        }
        setProgress((feed.dropped + i) / Math.max(1, feed.dropped + feed.steps.length));
      }
      if (!alive) return;
      // Events with no steps after the last one (skipped/unknown) still count once the log is done.
      setDoneId((d) => Math.max(d, feed.maxAcceptedId));
      setStatus("ended");
      if (source === "replay") await finaleRun();
    })();

    return () => {
      alive = false;
      cancelAll();
      stopAudio();
    };
  }, [source, run, speak, stopAudio]);

  // ---------------------------------------------------------------- derived view

  const feed = feedRef.current;
  // Rail numbers: the gateway's projections in live mode; otherwise only events the
  // script accepted (deduplicated, valid), counted once their animation has played.
  const logNumbers = useMemo(
    () => (source === "live" ? null : railFromEvents(feed.accepted.filter((e) => e.id <= doneId))),
    // feedVersion: the feed is a mutable ref, so its growth is tracked by version.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [feed, feedVersion, doneId, source],
  );
  const baseNumbers = (source === "live" ? liveNumbers : logNumbers) ?? railFromEvents([]);
  const numbers = finale ? { ...baseNumbers, money: { ...baseNumbers.money, tax_saved_cents: finale.saved } } : baseNumbers;

  const typed: Typed = useCallback(
    (target, full) => (typing && typing.target === target && full !== undefined ? full.slice(0, typing.shown) : full),
    [typing],
  );

  const clock = formatClock(feed.times.get(screen.lastEventId || feed.clockSeed), true) || "--:--:--";
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
      ? { title: loadError === TOKEN_REJECTED ? "Gateway refused the theater" : "Couldn’t load the action log", sub: loadError ?? "" }
      : source === "live" && status === "reconnecting" && !liveNumbers
        ? { title: "Gateway unreachable", sub: loadError ? `Retrying… (${loadError})` : "Retrying…" }
        : !loaded || status === "loading"
          ? { title: "Firm opening…", sub: "Agents at their desks" }
          : empty
            ? { title: "No clients yet", sub: "Marketing is posting" }
            : null;

  // Replay on the fixture replays the fixture (the offline fallback stays offline);
  // from live it plays the whole night from the gateway.
  const onReplay = () => (source === "live" ? setOverride("replay") : setRun((r) => r + 1));

  return (
    <div className={`theater${stage ? " stage" : ""}${finale?.lined ? " lined" : ""}${hasOrders ? " has-orders" : ""}`} style={{ "--t": 1 / Math.max(speed, effSpeed) } as CSSProperties}>
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
          {/* Always mounted: LIVE → RECONNECTING… → NO LOG changes are announced. */}
          <span className="live" role="status">
            <span className={`dot${blinking ? "" : " idle"}`} aria-hidden />
            {statusLabel[status]}
          </span>
          <span className="mono" title="time of the current event">
            <span className="sr-only">Time of the current event: </span>
            {clock}
          </span>
        </div>
      </header>

      {hasOrders && (
        <PaneBoundary resetKey={screen.lastEventId}>
          <OrdersStrip screen={screen} />
        </PaneBoundary>
      )}

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
          <button onClick={onReplay} disabled={status === "loading"} title={source === "live" ? "Replay the whole night from the gateway log" : "Play the log again"}>
            Replay
          </button>
          {HAS_GATEWAY && source !== "live" && (
            <button onClick={() => setOverride("live")} title="Follow the firm live">
              Live
            </button>
          )}
        </div>
      </footer>

      {/* Always mounted, so screen readers announce each change: captions, the overlay, the finale. */}
      <div className="sr-only" role="status" aria-live="polite">
        {caption
          ? `${caption.label}: ${caption.text}`
          : overlay
            ? `${overlay.title}. ${overlay.sub}`
            : finale?.banner
              ? `Night shift replayed. ${finale.banner}`
              : ""}
      </div>
      {caption && (
        <div className="caption" aria-hidden>
          <small>{caption.label}</small>
          {caption.text}
        </div>
      )}

      {finale && (
        <div className={`banner${finale.banner ? " show" : ""}`} aria-hidden>
          Night shift replayed
          <small>{finale.banner}</small>
        </div>
      )}

      {overlay && (
        <div className="overlay" aria-hidden>
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
