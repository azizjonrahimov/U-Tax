"use client";

// "Talk to U-Tax" (/talk, contract §8, v1.10 §5c): a customer or the finale judge talks with the
// firm's AI intake agent. Consent first; the microphone is requested only after "I agree, start
// talking". With a Realtime session from the gateway the call runs on it; without one (D30: the
// main path) the browser's speech recognition takes the answers and the firm speaks through
// POST /api/tts (ElevenLabs), or the browser's own voice when that is off. Listening pauses
// while the page speaks, plus an echo tail. After sending, the page follows the case live.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiFetch, API_URL } from "@/lib/api";
import type { UTaxEvent } from "@/theater/events";
import { formatCents } from "@/theater/format";
import { ECHO_TAIL_MS, Speaker, type TalkPhrase } from "@/lib/tts";
import { isBlockedError, isQuietError, MAX_EAR_FAILURES, recognitionCtor, type Recognition, type RecognitionCtor } from "@/lib/recognition";
import { canRecord, serverRecognition, STT_LIMITED, STT_UNAVAILABLE } from "@/lib/stt";

/** D36: after the gateway's STT says it isn't available (503), the browser recognizer listens for this long. */
const STT_RETRY_MS = 10 * 60_000;
/** The public STT (purpose "talk", no sign-in): reliable transcripts, from the visitors' shared budget. */
let talkEar: RecognitionCtor | null = null;
let sttDownAt = 0;
const earCtorFor = (): RecognitionCtor | null => {
  if (canRecord() && Date.now() - sttDownAt > STT_RETRY_MS) return (talkEar ??= serverRecognition("talk", () => null));
  return recognitionCtor();
};
const canHear = () => canRecord() || recognitionCtor() !== null;
import { stateUrl, streamUrl, seedFromState, type Endpoints } from "@/theater/live";
import {
  addTurn,
  awaitingAnswer,
  canSend,
  caseDone,
  customerChars,
  dropPending,
  emptyProgress,
  fillTurn,
  followCase,
  friendlyError,
  isEmail,
  MAX_CUSTOMER_CHARS,
  MAX_TURN_CHARS,
  nextTypedQuestion,
  parseVoiceSession,
  pendingTurns,
  progressLine,
  progressSpeech,
  questionPhrase,
  realtimeSignal,
  reserveTurn,
  STAGES,
  TYPED_SCRIPT,
  voiceBody,
  type CaseProgress,
  type Turn,
} from "@/talk/talk";
import "./talk.css";

const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const THEATER: Endpoints = { base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" };

/** Timeouts: a slow gateway or a blocked network drops to typed chat instead of hanging. */
const MINT_MS = 8000;
const SDP_MS = 10000;
const CHANNEL_MS = 15000;
const STATE_MS = 3000;
const SEND_MS = 20000;
/** A WebRTC "disconnected" often recovers; only one that lasts this long ends the call. */
const DISCONNECT_GRACE_MS = 4000;
/** On Send, wait this long for the last spoken answer's transcript. */
const TRANSCRIPT_WAIT_MS = 2500;
const QUESTION_DELAY_MS = 450;

type Phase = "consent" | "talk" | "sending" | "following";
type Mode = "voice" | "typed";
type VoiceState = "connecting" | "listening" | "thinking" | "speaking" | "idle";

const VOICE_LABEL: Record<VoiceState, string> = {
  connecting: "Connecting…",
  listening: "Listening…",
  thinking: "Thinking…",
  speaking: "U-Tax is speaking",
  idle: "Your turn. Just talk.",
};

/** The firm's voice (v1.10 §5c, purpose "talk"): the gateway's TTS, the browser's voice on a 503. */
let talkSpeaker: Speaker | null = null;
const speakingListeners = new Set<(on: boolean) => void>();
const speaker = () =>
  (talkSpeaker ??= new Speaker({ purpose: "talk", api: `${API_URL}/api`, onAudio: (on) => speakingListeners.forEach((l) => l(on)) }));
/** Say a line now (the newest line wins, as before). With a registry key it is the firm's voice; without, the browser's. */
function say(text: string, phrase?: TalkPhrase) {
  speaker().stop();
  speaker().say(text, undefined, phrase);
}
const hush = () => talkSpeaker?.stop();

/** Listening comes back this long after the page stops speaking (the room's echo dies down; §5c). */
const EAR_TAIL_MS = ECHO_TAIL_MS;
/** Finals heard within this pause are one answer ("We're a Delaware C corp ... with 10 million shares"). */
const ANSWER_GAP_MS = 1200;
/** The last script line asks for the email, which is typed, never spoken: listening stops before it. */
const TYPED_SCRIPT_LAST = TYPED_SCRIPT[TYPED_SCRIPT.length - 1];

const newRequestId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `talk-${Date.now()}-${Math.random().toString(36).slice(2)}`;

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

interface Call {
  pc: RTCPeerConnection;
  mic: MediaStream;
  audio: HTMLAudioElement;
}

export function Talk() {
  const [phase, setPhase] = useState<Phase>("consent");
  const [mode, setMode] = useState<Mode>("typed");
  const [voiceState, setVoiceState] = useState<VoiceState>("connecting");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [full, setFull] = useState(false);
  const [draft, setDraft] = useState("");
  const [email, setEmail] = useState("");
  const [sound, setSound] = useState(true);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [progress, setProgress] = useState<CaseProgress | null>(null);
  const [caseName, setCaseName] = useState<string | null>(null);
  const [streamDown, setStreamDown] = useState(false);
  /** A send that may have reached the gateway (timeout, network, 5xx): a retry returns that case, so edits are locked. */
  const [mayHaveLanded, setMayHaveLanded] = useState(false);
  /** The last spoken answer was still being transcribed when the call ended. */
  const [lostAnswer, setLostAnswer] = useState(false);
  /** The firm is full for today (v1.7.6): the gateway's sentence (it names the inbox); Send stays off. */
  const [atCapacity, setAtCapacity] = useState<string | null>(null);
  /** Speaking answers through the browser's speech recognition (D30's main path): off, on, or not possible here. */
  const [ear, setEar] = useState<"off" | "on" | "paused" | "unsupported" | "blocked" | "failing" | "limited">("off");
  /** Stream start: the last event id before our case opened (so we don't replay the night). */
  const [followFrom, setFollowFrom] = useState(0);
  /** The turns as of the last change: handlers read this, never a state updater with side effects. */
  const turnsRef = useRef<Turn[]>([]);
  const phaseRef = useRef<Phase>("consent");
  const callRef = useRef<Call | null>(null);
  /**
   * Bumped whenever a voice attempt stops being wanted (End voice, Send, Start over, leaving
   * the page). Every await in startVoice checks it, so a late mint, mic prompt or SDP answer
   * never opens a call nobody asked for.
   */
  const attemptRef = useRef(0);
  /** One id per conversation, made at the first send: a retry reuses it, so the gateway returns the same case. */
  const requestId = useRef<string | null>(null);
  /** The stream start measured for that request id (a retry must not move it past the case's events). */
  const requestAfter = useRef<number | null>(null);
  /** Typed script: where it started in the transcript, or null when no script runs (after a voice drop). */
  const scriptFrom = useRef<number | null>(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const soundRef = useRef(sound);
  const usedVoice = useRef(false);
  const spoken = useRef<string>("");
  const listRef = useRef<HTMLOListElement | null>(null);
  const earWanted = useRef(false);
  const recRef = useRef<Recognition | null>(null);
  const pageSpeaking = useRef(false);
  const heardRef = useRef<(text: string) => void>(() => {});
  const earStartRef = useRef<() => void>(() => {});
  const earFailures = useRef(0);
  const heardBuffer = useRef("");
  const heardTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const focusRef = useRef<HTMLElement | null>(null);

  const go = useCallback((p: Phase) => {
    phaseRef.current = p;
    setPhase(p);
  }, []);

  // Keep the newest turn in view.
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  // A new step takes focus, so keyboard and screen-reader users land on it.
  useEffect(() => {
    // Only when focus was lost with the old step: never pull a caller out of the field they're typing in.
    const active = document.activeElement;
    if (!active || active === document.body || !active.isConnected) focusRef.current?.focus();
  }, [phase, mode]);

  useEffect(() => {
    soundRef.current = sound;
    if (callRef.current) callRef.current.audio.muted = !sound;
    if (!sound) hush();
  }, [sound]);

  const setAll = useCallback((next: Turn[], isFull: boolean) => {
    turnsRef.current = next;
    setTurns(next);
    setFull(isFull);
  }, []);

  const push = useCallback(
    (speaker: Turn["speaker"], text: string): Turn[] => {
      const r = addTurn(turnsRef.current, speaker, text);
      setAll(r.turns, r.full);
      return r.turns;
    },
    [setAll],
  );

  /** Stop listening now (the page speaks, the caller turned it off, or the call moved on). */
  const earStop = useCallback(() => {
    const r = recRef.current;
    recRef.current = null;
    if (r) {
      r.onend = r.onresult = r.onerror = null;
      try {
        r.abort();
      } catch {
        /* already stopped */
      }
    }
  }, []);
  /** Listen (continuous) while wanted, never while the page speaks. */
  const earStart = useCallback(() => {
    if (!earWanted.current || recRef.current) return;
    if (pageSpeaking.current) return setEar("paused");
    const Ctor = earCtorFor();
    if (!Ctor) {
      earWanted.current = false;
      return setEar("unsupported");
    }
    const r = new Ctor();
    r.lang = "en-US";
    r.continuous = true;
    r.interimResults = false;
    let failed = false;
    r.onresult = (e) => {
      earFailures.current = 0;
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        const t = res.isFinal ? res[0]?.transcript.trim() : "";
        if (t) heardRef.current(t);
      }
    };
    r.onerror = (e) => {
      if (e.error === STT_LIMITED) {
        // D36: the visitors' shared transcription budget is used up for now: type instead.
        earWanted.current = false;
        setEar("limited");
        return;
      }
      if (e.error === STT_UNAVAILABLE) {
        // No STT on the gateway: the browser's recognizer takes over (onend restarts it), or typing.
        sttDownAt = Date.now();
        if (!recognitionCtor()) {
          earWanted.current = false;
          setEar("unsupported");
        }
        return;
      }
      if (isBlockedError(e.error)) {
        earWanted.current = false;
        setEar("blocked");
      } else if (!isQuietError(e.error)) {
        // Offline, no mic, a browser without the speech service: back off, then give up.
        failed = true;
        earFailures.current += 1;
        if (earFailures.current >= MAX_EAR_FAILURES) {
          earWanted.current = false;
          setEar("failing");
        }
      }
    };
    r.onend = () => {
      if (recRef.current !== r) return;
      recRef.current = null;
      if (earWanted.current && !pageSpeaking.current) setTimeout(() => earWanted.current && earStartRef.current(), failed ? 2000 : 300);
    };
    recRef.current = r;
    try {
      r.start();
      setEar("on");
    } catch {
      recRef.current = null;
    }
  }, []);
  useEffect(() => {
    earStartRef.current = earStart;
  }, [earStart]);
  /** Stop listening for good (until the caller turns it on again). */
  const earOff = useCallback(() => {
    earWanted.current = false;
    clearTimeout(heardTimer.current);
    heardBuffer.current = "";
    earStop();
    setEar("off");
  }, [earStop]);
  const earToggle = useCallback(() => {
    if (earWanted.current) return earOff();
    earWanted.current = true;
    earFailures.current = 0;
    earStart();
  }, [earOff, earStart]);
  // The page's own voice must never be taken as the caller: stop listening while it speaks, plus a tail.
  useEffect(() => {
    let tail: ReturnType<typeof setTimeout> | undefined;
    const onSpeaking = (on: boolean) => {
      pageSpeaking.current = on;
      clearTimeout(tail);
      if (on) {
        if (recRef.current) {
          earStop();
          setEar("paused");
        }
      } else if (earWanted.current) tail = setTimeout(() => !pageSpeaking.current && earStartRef.current(), EAR_TAIL_MS);
    };
    speakingListeners.add(onSpeaking);
    return () => {
      speakingListeners.delete(onSpeaking);
      clearTimeout(tail);
      earWanted.current = false;
      earStop();
    };
  }, [earStop]);

  const later = useCallback((fn: () => void, ms: number) => {
    const t = setTimeout(() => {
      timers.current.delete(t);
      fn();
    }, ms);
    timers.current.add(t);
  }, []);
  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current.clear();
  }, []);

  /** End the current call and any voice attempt still connecting. Returns how many unheard answers were dropped. */
  const hangUp = useCallback((): number => {
    attemptRef.current += 1;
    const c = callRef.current;
    callRef.current = null;
    if (!c) return 0;
    const kept = dropPending(turnsRef.current);
    const dropped = turnsRef.current.length - kept.length;
    if (dropped) {
      turnsRef.current = kept;
      setTurns(kept);
    }
    c.mic.getTracks().forEach((t) => t.stop());
    c.pc.close();
    c.audio.pause();
    c.audio.srcObject = null;
    return dropped;
  }, []);
  useEffect(
    () => () => {
      hangUp();
      clearTimers();
      hush();
    },
    [hangUp, clearTimers],
  );

  /** Typed mode: the agent asks the next scripted question (spoken when sound is on). */
  const askNext = useCallback(() => {
    if (scriptFrom.current === null || phaseRef.current !== "talk") return;
    const q = nextTypedQuestion(turnsRef.current.slice(scriptFrom.current));
    if (!q) return;
    later(() => {
      if (phaseRef.current !== "talk" || scriptFrom.current === null) return;
      if (nextTypedQuestion(turnsRef.current.slice(scriptFrom.current)) !== q) return; // asked already
      push("agent", q);
      if (soundRef.current) say(q, questionPhrase(q));
    }, QUESTION_DELAY_MS);
  }, [later, push]);

  /** Switch to typing. Only from the conversation step: a late voice failure never drags a sent case back. */
  const startTyped = useCallback(
    (why: string | null) => {
      const dropped = hangUp();
      if (phaseRef.current !== "talk" && phaseRef.current !== "consent") return;
      setMode("typed");
      go("talk");
      setNotice(dropped ? `${why ?? ""} Your last answer didn't come through, so please type it.`.trim() : why);
      // A fresh conversation runs the five questions; after a voice drop the caller just finishes typing.
      const hadAnswers = turnsRef.current.some((t) => t.speaker === "customer" && t.text);
      scriptFrom.current = hadAnswers ? null : turnsRef.current.length;
      askNext();
    },
    [askNext, go, hangUp],
  );

  /** "I agree, start talking": mint a session, then ask for the mic (only now), then connect. */
  const startVoice = useCallback(async () => {
    hangUp();
    const attempt = attemptRef.current;
    const stale = () => attempt !== attemptRef.current;
    const fail = (why: string) => {
      if (!stale()) startTyped(why);
    };
    setError(null);
    setNotice(null);
    setMode("voice");
    go("talk");
    setVoiceState("connecting");

    let session;
    try {
      session = parseVoiceSession(await apiFetch<unknown>("/voice/session", { method: "POST", body: "{}", signal: AbortSignal.timeout(MINT_MS) }));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      const code = e instanceof ApiError ? e.code : "network_error";
      if (status === 503 && !stale()) {
        // D30: no Realtime voice. The browser listens, U-Tax speaks; typing works too.
        startTyped(canHear() ? "U-Tax listens through your browser: speak your answers, or type them." : "This browser can't listen, so let's type. U-Tax still reads the questions aloud.");
        earWanted.current = canHear();
        if (earWanted.current) earStart();
        return;
      }
      return fail(friendlyError(status, code));
    }
    if (stale()) return;
    if (!session) return fail("Voice isn't available right now, so let's type.");

    let mic: MediaStream;
    try {
      mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return fail("The microphone is off, so let's type instead.");
    }
    if (stale()) {
      mic.getTracks().forEach((t) => t.stop()); // the caller moved on while the prompt was open
      return;
    }

    let opened = false;
    later(() => {
      if (!stale() && !opened) fail("Voice couldn't connect on this network, so let's type. Same questions, same estimate.");
    }, CHANNEL_MS);
    let pc: RTCPeerConnection;
    let dc: RTCDataChannel;
    const audio = new Audio();
    audio.autoplay = true;
    audio.muted = !soundRef.current;
    try {
      pc = new RTCPeerConnection();
      callRef.current = { pc, mic, audio };
      pc.ontrack = (e) => {
        audio.srcObject = e.streams[0];
      };
      mic.getTracks().forEach((t) => pc.addTrack(t, mic));
      dc = pc.createDataChannel("oai-events");
    } catch (e) {
      // WebRTC off in this browser: release the mic before falling back.
      console.warn("[talk] WebRTC unavailable", e);
      if (!callRef.current) mic.getTracks().forEach((t) => t.stop());
      return fail("Voice isn't supported in this browser, so let's type. Same questions, same estimate.");
    }
    dc.onopen = () => {
      if (stale()) return;
      opened = true;
      usedVoice.current = true;
      // The gateway sets instructions, tools and transcription when minting; the page only starts the opener.
      dc.send(JSON.stringify({ type: "response.create" }));
      setVoiceState("speaking");
    };
    dc.onmessage = (m) => {
      if (stale()) return;
      let sig;
      try {
        sig = realtimeSignal(JSON.parse(m.data));
      } catch {
        console.warn("[talk] unreadable realtime message");
        return;
      }
      if (!sig) return;
      if (sig.kind === "turn") push(sig.speaker, sig.text);
      else if (sig.kind === "reserve") {
        const next = reserveTurn(turnsRef.current, sig.id);
        setAll(next, customerChars(next) >= MAX_CUSTOMER_CHARS);
      }
      else if (sig.kind === "fill") {
        const r = fillTurn(turnsRef.current, sig.id, sig.text);
        setAll(r.turns, r.full);
        if (sig.failed) setNotice("U-Tax didn't catch that last answer. Please say it again, or type it.");
      } else if (sig.kind === "state") setVoiceState(sig.state);
      else console.warn("[talk] realtime error", sig.message);
    };
    let grace: ReturnType<typeof setTimeout> | undefined;
    pc.onconnectionstatechange = () => {
      if (stale()) return;
      clearTimeout(grace);
      if (pc.connectionState === "failed") fail("The voice line dropped. Let's finish by typing; your answers so far are kept.");
      else if (pc.connectionState === "disconnected") {
        grace = setTimeout(() => {
          if (pc.connectionState !== "connected") fail("The voice line dropped. Let's finish by typing; your answers so far are kept.");
        }, DISCONNECT_GRACE_MS);
      }
    };
    try {
      const offer = await pc.createOffer();
      if (stale()) return;
      await pc.setLocalDescription(offer);
      const r = await fetch(session.sdpUrl, {
        method: "POST",
        body: offer.sdp,
        headers: { Authorization: `Bearer ${session.clientSecret}`, "Content-Type": "application/sdp" },
        signal: AbortSignal.timeout(SDP_MS),
      });
      if (stale()) return;
      if (!r.ok) throw new Error(`realtime ${r.status}`);
      const sdp = await r.text();
      if (stale()) return;
      await pc.setRemoteDescription({ type: "answer", sdp });
    } catch (e) {
      if (stale()) return; // closed on purpose (End voice, Send, Start over): not a failure
      console.warn("[talk] voice connect failed", e);
      fail("Voice couldn't connect, so let's type. Same questions, same estimate.");
    }
  }, [earStart, go, hangUp, later, push, setAll, startTyped]);

  const sendLine = () => {
    const text = draft;
    if (!text.trim() || full) return;
    setDraft("");
    push("customer", text);
    if (mode === "typed") askNext();
  };
  // A phrase the browser heard is an answer, exactly like a typed one, but only while a scripted
  // question waits for it: room speech, the projector or a neighbour never becomes a turn.
  useEffect(() => {
    const open = () => phaseRef.current === "talk" && mode === "typed" && !full && !mayHaveLanded && !pageSpeaking.current;
    heardRef.current = (text) => {
      if (!open() || !awaitingAnswer(turnsRef.current)) return;
      heardBuffer.current = `${heardBuffer.current} ${text}`.trim();
      clearTimeout(heardTimer.current);
      heardTimer.current = setTimeout(() => {
        const answer = heardBuffer.current;
        heardBuffer.current = "";
        if (!answer || !open() || !awaitingAnswer(turnsRef.current)) return;
        push("customer", answer);
        // The script is done (only the email is left) or the transcript is full: stop listening.
        const next = scriptFrom.current === null ? null : nextTypedQuestion(turnsRef.current.slice(scriptFrom.current));
        if (next === null || next === TYPED_SCRIPT_LAST) earOff();
        askNext();
      }, ANSWER_GAP_MS);
    };
  }, [askNext, earOff, full, mayHaveLanded, mode, push]);

  /** Send the conversation: the gateway opens a fast-path case and returns its id. */
  const submit = async () => {
    if (!canSend(turnsRef.current, email) || phaseRef.current !== "talk") return;
    setError(null);
    go("sending");
    clearTimers();
    // The last spoken answer is still being transcribed for a moment after it was said.
    for (let waited = 0; callRef.current && pendingTurns(turnsRef.current) > 0 && waited < TRANSCRIPT_WAIT_MS; waited += 100) await sleep(100);
    const wasVoice = mode === "voice";
    const lost = hangUp() > 0;
    hush();
    earOff();
    if (wasVoice) {
      setMode("typed"); // the call is over either way; a failed send continues by typing
      scriptFrom.current = null; // ...without the scripted questions
    }
    setLostAnswer(lost);

    requestId.current ??= newRequestId();
    // Where the stream should start: just before our case opens. Measured once per request id.
    if (requestAfter.current === null) {
      requestAfter.current = 0;
      for (let tries = 0; tries < 2; tries++) {
        try {
          const r = await fetch(stateUrl(THEATER), { cache: "no-store", signal: AbortSignal.timeout(STATE_MS) });
          if (r.ok) {
            requestAfter.current = seedFromState(await r.json()).lastId ?? 0;
            break;
          }
        } catch {
          console.warn("[talk] /api/state unavailable; following from the start of the log");
        }
      }
    }
    try {
      const res = await apiFetch<{ case_id?: unknown; masked_name?: unknown }>("/voice", {
        method: "POST",
        body: JSON.stringify(voiceBody(turnsRef.current, email, usedVoice.current ? "voice" : "typed", requestId.current)),
        signal: AbortSignal.timeout(SEND_MS),
      });
      if (typeof res.case_id !== "string") throw new ApiError(500, "bad_response", "no case id");
      setMayHaveLanded(false);
      setCaseName(typeof res.masked_name === "string" ? res.masked_name : null);
      setFollowFrom(requestAfter.current);
      setProgress({ ...emptyProgress(res.case_id), lastId: requestAfter.current });
      go("following");
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setError(friendlyError(status, e instanceof ApiError ? e.code : "network_error", e instanceof ApiError ? e.message : ""));
      if (e instanceof ApiError && e.code === "at_capacity") setAtCapacity(friendlyError(status, e.code, e.message)); // names where to write instead
      // Only a refusal of the content proves nothing was saved: the next try is a new request.
      // Anything else (429, timeout, network, 5xx) may follow a send that landed, so the same id
      // must come back for the same case, and the conversation is locked so nothing is silently dropped.
      const code = e instanceof ApiError ? e.code : "";
      if (code === "bad_input" || code === "too_long") {
        // The gateway checks the content before anything is saved: nothing landed.
        requestId.current = null;
        requestAfter.current = null;
        setMayHaveLanded(false);
      } else if (status === 0 || status >= 500) {
        setMayHaveLanded(true); // a timeout or server error may follow a send that landed
      }
      setNotice(
        lost
          ? "Your last spoken answer didn't come through, so please type it before sending again."
          : wasVoice
            ? "The voice call ended. You can still type and send."
            : null,
      );
      go("talk");
    }
  };

  // Follow the case on the public theater stream until it reaches an end.
  const caseId = progress?.caseId ?? null;
  const done = progress ? caseDone(progress) : false;
  useEffect(() => {
    if (!caseId || done) return;
    const es = new EventSource(streamUrl(THEATER, followFrom));
    let fails = 0;
    es.onmessage = (m) => {
      fails = 0;
      let e: UTaxEvent;
      try {
        e = JSON.parse(m.data) as UTaxEvent;
      } catch {
        console.warn("[talk] unreadable stream message");
        return;
      }
      if (e?.case_id !== caseId) return;
      setStreamDown(false);
      setProgress((p) => (p ? followCase(p, [e]) : p));
    };
    es.onerror = () => {
      if (++fails >= 3 || es.readyState === EventSource.CLOSED) setStreamDown(true);
    };
    return () => es.close();
  }, [caseId, done, followFrom]);

  // Read the numbers back (and what happens next), once per change.
  const line = progress ? progressLine(progress) : "";
  useEffect(() => {
    if (!progress || !line || line === spoken.current) return;
    if (progress.totalCents === null && !progress.notEligible && !progress.blocked && !progress.askedFor.length) return;
    spoken.current = line;
    if (!soundRef.current) return;
    const parts = progressSpeech(progress);
    speaker().stop();
    for (const part of parts) speaker().say(part.text, undefined, part.phrase);
  }, [progress, line]);

  const startOver = () => {
    hangUp();
    clearTimers();
    earOff(); // back at consent: nothing listens until the caller agrees again
    hush();
    requestId.current = null;
    requestAfter.current = null;
    scriptFrom.current = 0;
    usedVoice.current = false;
    spoken.current = "";
    setAll([], false);
    setFollowFrom(0);
    setDraft("");
    setEmail("");
    setProgress(null);
    setCaseName(null);
    setNotice(null);
    setError(null);
    setStreamDown(false);
    setMayHaveLanded(false);
    setLostAnswer(false);
    setAtCapacity(null);
    setMode("typed");
    go("consent");
  };

  const hasAnswer = turns.some((t) => t.speaker === "customer" && t.text);
  const emailBad = email.trim() !== "" && !isEmail(email);
  const ready = canSend(turns, email);
  const ended = progress !== null && (progress.notEligible !== null || progress.blocked);
  const stageIndex = progress ? STAGES.findIndex((s) => s.id === progress.stage) : -1;
  const focusTarget = (el: HTMLElement | null) => {
    focusRef.current = el;
  };

  return (
    <div className="talk">
      <header>
        <span className="logo">U</span>
        <span className="title">Talk to U-Tax</span>
        <span className="sub">Free Delaware franchise tax estimate · educational, not tax advice</span>
        <button className="ghost" onClick={() => setSound((v) => !v)}>
          {sound ? "Mute U-Tax" : "Unmute U-Tax"}
        </button>
      </header>

      <main>
        {!HAS_GATEWAY && (
          <p className="notice" role="status">
            U-Tax is offline in this build (no gateway configured). The page works, but nothing will be sent.
          </p>
        )}

        {phase === "consent" && (
          <section className="consent" aria-labelledby="consent-h">
            <h1 id="consent-h" tabIndex={-1} ref={focusTarget}>
              Ask U-Tax what your Delaware franchise tax really is.
            </h1>
            <p className="lead">
              You&rsquo;ll talk with U-Tax&rsquo;s <b>AI intake agent</b>. It asks five short questions, reads your numbers back, and the firm
              emails you a memo.
            </p>
            <ul className="terms">
              <li>Your answers are transcribed by a speech service (your browser&rsquo;s, or OpenAI&rsquo;s when a live voice line is available), U-Tax answers in an AI voice (ElevenLabs, or your browser&rsquo;s), and parts may show on the screen here.</li>
              <li>It&rsquo;s an educational estimate, not tax advice.</li>
              <li>Don&rsquo;t say names, emails or phone numbers. The email goes in a box, not out loud.</li>
            </ul>
            <div className="actions">
              <button className="primary" onClick={() => void startVoice()} disabled={!HAS_GATEWAY}>
                I agree, start talking
              </button>
              <button className="secondary" onClick={() => startTyped(null)} disabled={!HAS_GATEWAY}>
                I agree, type instead
              </button>
            </div>
          </section>
        )}

        {(phase === "talk" || phase === "sending") && (
          <section className="convo" aria-label="Conversation">
            {notice && (
              <p className="notice" role="status">
                {notice}
              </p>
            )}
            {mode === "voice" && (
              <div className={`orb ${voiceState}`} role="status" aria-live="polite" tabIndex={-1} ref={focusTarget}>
                <span className="ring" aria-hidden />
                {VOICE_LABEL[voiceState]}
              </div>
            )}
            <ol className="turns" ref={listRef} aria-live="polite" aria-label="Transcript">
              {turns.map((t, i) => (
                <li key={t.id ?? i} className={`${t.speaker}${t.text ? "" : " pending"}`}>
                  <small>{t.speaker === "agent" ? "U-Tax (AI)" : "You"}</small>
                  {t.text || "…"}
                </li>
              ))}
              {turns.length === 0 && mode === "typed" && <li className="agent pending">U-Tax is getting ready…</li>}
            </ol>

            <form
              className="line"
              onSubmit={(e) => {
                e.preventDefault();
                sendLine();
              }}
            >
              <label htmlFor="answer" className="sr-only">
                Your answer
              </label>
              <input
                id="answer"
                ref={mode === "typed" ? focusTarget : undefined}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={MAX_TURN_CHARS}
                placeholder={mode === "voice" ? "Or type an answer…" : "Type your answer…"}
                autoComplete="off"
                disabled={full || phase === "sending" || mayHaveLanded}
              />
              <button className="secondary" type="submit" disabled={!draft.trim() || full || phase === "sending" || mayHaveLanded}>
                Answer
              </button>
              {mode === "typed" && ear !== "unsupported" && (
                <button
                  type="button"
                  className={`secondary ear${ear === "on" || ear === "paused" ? " on" : ""}`}
                  aria-pressed={ear === "on" || ear === "paused"}
                  onClick={earToggle}
                  disabled={full || phase === "sending" || mayHaveLanded || ear === "blocked" || ear === "failing" || ear === "limited"}
                >
                  Speak answers
                </button>
              )}
            </form>
            {mode === "typed" && (ear === "on" || ear === "paused") && (
              <p className="hint" aria-hidden="true">
                {ear === "on" ? "Listening. Answer out loud, or tap Speak answers to stop." : "U-Tax is speaking. Listening resumes after."}
              </p>
            )}
            {mode === "typed" && (ear === "blocked" || ear === "unsupported" || ear === "failing" || ear === "limited") && (
              <p className="hint" role="status">
                {ear === "blocked"
                  ? "The microphone is blocked for this page, so type your answers."
                  : ear === "limited"
                    ? "Lots of people are talking to U-Tax right now, so type your answers for a moment."
                    : ear === "failing"
                    ? "Listening isn't working on this network or device. Type your answers."
                    : "This browser can't listen. Type your answers."}
              </p>
            )}
            <p className="sr-only" role="status" aria-live="polite">
              {ear === "on" ? "Listening" : ear === "paused" ? "Paused while U-Tax speaks" : ""}
            </p>
            {full && <p className="hint">That&rsquo;s all the detail U-Tax needs. Add your email and send.</p>}

            <form
              className="send"
              onSubmit={(e) => {
                e.preventDefault();
                void submit();
              }}
            >
              <label htmlFor="email">Where should the estimate go?</label>
              <div className="row">
                <input
                  id="email"
                  type="email"
                  onFocus={earOff}
                  inputMode="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@startup.com"
                  maxLength={320}
                  aria-invalid={emailBad || undefined}
                  aria-describedby="email-hint"
                  disabled={phase === "sending" || mayHaveLanded}
                />
                <button className="primary" type="submit" disabled={!ready || phase === "sending" || !HAS_GATEWAY || atCapacity !== null}>
                  {phase === "sending" ? "Sending…" : "Send to U-Tax"}
                </button>
              </div>
              <p className="hint" id="email-hint">
                {emailBad ? "Check the email address." : hasAnswer ? "Your email is only used for this memo." : "Answer at least one question first."}
              </p>
              {error && (
                <p className="error" role="alert">
                  {error}
                  {mayHaveLanded ? " It may already have reached U-Tax; sending again won't open a second case." : ""}
                </p>
              )}
              {atCapacity && !error && (
                <p className="error" role="status">
                  {atCapacity}
                </p>
              )}
              {mayHaveLanded && (
                <button
                  type="button"
                  className="ghost"
                  onClick={() => {
                    // The caller chooses: edit and send as a new request (a second case if the first one landed).
                    requestId.current = null;
                    requestAfter.current = null;
                    setMayHaveLanded(false);
                    setError(null);
                  }}
                >
                  Change something and send as a new request
                </button>
              )}
            </form>
            {mode === "voice" && phase === "talk" && (
              <button className="ghost end" onClick={() => startTyped("Call ended. You can still type and send.")}>
                End voice
              </button>
            )}
          </section>
        )}

        {phase === "following" && progress && (
          <section className="follow" aria-labelledby="follow-h">
            <h1 id="follow-h" tabIndex={-1} ref={focusTarget}>
              {caseName ? `${caseName} is with the firm.` : "Your case is with the firm."}
            </h1>
            {!ended && (
              <div className="estimate">
                {progress.totalCents !== null && !progress.askedFor.length ? (
                  <>
                    <small>Estimated Delaware franchise tax</small>
                    <b>{formatCents(progress.totalCents)}</b>
                  </>
                ) : (
                  <small>Working on your numbers…</small>
                )}
              </div>
            )}
            <p className="said" role="status">
              {line}
            </p>
            {!ended && (
              <ol className="steps">
                {STAGES.map((s, i) => (
                  <li key={s.id} className={i < stageIndex ? "done" : i === stageIndex ? "now" : ""} aria-current={i === stageIndex ? "step" : undefined}>
                    <span className="tick" aria-hidden>
                      {i <= stageIndex ? "✓" : ""}
                    </span>
                    {s.label}
                    <span className="sr-only">{i <= stageIndex ? " (done)" : " (not yet)"}</span>
                  </li>
                ))}
              </ol>
            )}
            {lostAnswer && <p className="hint">Your last spoken answer didn&rsquo;t come through. If it mattered, reply to the memo email with it.</p>}
            {streamDown && !done && <p className="hint">Live updates paused. The memo still arrives by email.</p>}
            <div className="actions">
              <Link className="secondary" href="/office">
                Watch the firm work
              </Link>
              <button className="ghost" onClick={startOver}>
                Start over
              </button>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}
