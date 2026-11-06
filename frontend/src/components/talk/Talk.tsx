"use client";

// "Talk to U-Tax" (/talk, contract §8): a customer or the finale judge talks with the firm's
// AI intake agent. Consent first; the microphone is requested only after "I agree, start
// talking". Voice runs on an OpenAI Realtime session the gateway mints (the key never
// reaches the browser); without one the page types, and speaks with browser speech.
// After sending, the page follows the case live on the public theater stream.

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiFetch, API_URL } from "@/lib/api";
import type { UTaxEvent } from "@/theater/events";
import { formatCents } from "@/theater/format";
import { stateUrl, streamUrl, seedFromState, type Endpoints } from "@/theater/live";
import {
  addTurn,
  canSend,
  emptyProgress,
  followCase,
  friendlyError,
  MAX_TURN_CHARS,
  nextTypedQuestion,
  parseVoiceSession,
  progressLine,
  realtimeSignal,
  STAGES,
  TRANSCRIPTION_UPDATE,
  voiceBody,
  type CaseProgress,
  type Turn,
} from "@/talk/talk";
import "./talk.css";

const HAS_GATEWAY = Boolean(process.env.NEXT_PUBLIC_API_URL);
const THEATER: Endpoints = { base: API_URL, token: process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "" };

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

const hasSpeech = () => typeof window !== "undefined" && "speechSynthesis" in window;
function say(text: string) {
  if (!hasSpeech()) return;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
}
const hush = () => {
  if (hasSpeech()) window.speechSynthesis.cancel();
};

const newRequestId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `talk-${Date.now()}-${Math.random().toString(36).slice(2)}`;

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
  /** Stream start: the last event id before our case opened (so we don't replay the night). */
  const [followFrom, setFollowFrom] = useState(0);
  /** The turns as of the last change: handlers read this, never a state updater with side effects. */
  const turnsRef = useRef<Turn[]>([]);
  const callRef = useRef<Call | null>(null);
  const requestId = useRef(newRequestId());
  const soundRef = useRef(sound);
  const usedVoice = useRef(false);
  const spoken = useRef<string>("");

  useEffect(() => {
    soundRef.current = sound;
    if (!sound) hush();
  }, [sound]);

  const push = useCallback((speaker: Turn["speaker"], text: string): Turn[] => {
    const r = addTurn(turnsRef.current, speaker, text);
    turnsRef.current = r.turns;
    setTurns(r.turns);
    setFull(r.full);
    return r.turns;
  }, []);

  const hangUp = useCallback(() => {
    const c = callRef.current;
    callRef.current = null;
    if (!c) return;
    c.mic.getTracks().forEach((t) => t.stop());
    c.pc.close();
    c.audio.pause();
    c.audio.srcObject = null;
  }, []);
  useEffect(() => () => {
    hangUp();
    hush();
  }, [hangUp]);

  /** Typed mode: the agent asks the next scripted question (spoken when sound is on). */
  const askNext = useCallback(
    (current: Turn[]) => {
      const q = nextTypedQuestion(current);
      if (!q) return;
      window.setTimeout(() => {
        if (nextTypedQuestion(turnsRef.current) !== q) return; // asked already (or the page moved on)
        push("agent", q);
        if (soundRef.current) say(q);
      }, 450);
    },
    [push],
  );

  const startTyped = useCallback(
    (why: string | null) => {
      hangUp();
      setMode("typed");
      setPhase("talk");
      setNotice(why);
      askNext(turnsRef.current);
    },
    [askNext, hangUp],
  );

  /** "I agree, start talking": mint a session, then ask for the mic (only now), then connect. */
  const startVoice = useCallback(async () => {
    setError(null);
    setMode("voice");
    setPhase("talk");
    setVoiceState("connecting");
    let session;
    try {
      session = parseVoiceSession(await apiFetch<unknown>("/voice/session", { method: "POST", body: "{}" }));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      const code = e instanceof ApiError ? e.code : "network_error";
      return startTyped(status === 503 ? "Voice isn't available right now, so let's type. Same questions, same estimate." : friendlyError(status, code));
    }
    if (!session) return startTyped("Voice isn't available right now, so let's type.");
    let mic: MediaStream;
    try {
      mic = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return startTyped("The microphone is off, so let's type instead.");
    }
    const pc = new RTCPeerConnection();
    const audio = new Audio();
    audio.autoplay = true;
    callRef.current = { pc, mic, audio };
    pc.ontrack = (e) => {
      audio.srcObject = e.streams[0];
    };
    mic.getTracks().forEach((t) => pc.addTrack(t, mic));
    const dc = pc.createDataChannel("oai-events");
    dc.onopen = () => {
      usedVoice.current = true;
      // Transcription only (the gateway sets instructions and tools when minting), then the opener.
      dc.send(JSON.stringify(TRANSCRIPTION_UPDATE));
      dc.send(JSON.stringify({ type: "response.create" }));
      setVoiceState("speaking");
    };
    dc.onmessage = (m) => {
      let sig;
      try {
        sig = realtimeSignal(JSON.parse(m.data));
      } catch {
        return;
      }
      if (!sig) return;
      if (sig.kind === "turn") push(sig.speaker, sig.text);
      else if (sig.kind === "state") setVoiceState(sig.state);
      else console.warn("[talk] realtime error", sig.message);
    };
    pc.onconnectionstatechange = () => {
      if (callRef.current?.pc !== pc) return;
      if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
        startTyped("The voice line dropped. Let's finish by typing; your answers so far are kept.");
      }
    };
    try {
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const r = await fetch(session.sdpUrl, {
        method: "POST",
        body: offer.sdp,
        headers: { Authorization: `Bearer ${session.clientSecret}`, "Content-Type": "application/sdp" },
      });
      if (!r.ok) throw new Error(`realtime ${r.status}`);
      await pc.setRemoteDescription({ type: "answer", sdp: await r.text() });
    } catch (e) {
      console.warn("[talk] voice connect failed", e);
      startTyped("Voice couldn't connect, so let's type. Same questions, same estimate.");
    }
  }, [push, startTyped]);

  const sendLine = () => {
    const text = draft;
    if (!text.trim() || full) return;
    setDraft("");
    const next = push("customer", text);
    if (mode === "typed") askNext(next);
  };

  /** Send the conversation: the gateway opens a fast-path case and returns its id. */
  const submit = async () => {
    if (!canSend(turns, email) || phase === "sending") return;
    setError(null);
    setPhase("sending");
    hangUp();
    hush();
    // Where the stream should start: just before our case opens (so we don't replay the night).
    let after = 0;
    try {
      const r = await fetch(stateUrl(THEATER), { cache: "no-store" });
      if (r.ok) after = seedFromState(await r.json()).lastId ?? 0;
    } catch {
      /* the case still opens; following starts from the beginning */
    }
    try {
      const res = await apiFetch<{ case_id?: unknown; masked_name?: unknown }>("/voice", {
        method: "POST",
        body: JSON.stringify(voiceBody(turns, email, usedVoice.current ? "voice" : "typed", requestId.current)),
      });
      if (typeof res.case_id !== "string") throw new ApiError(500, "bad_response", "no case id");
      setCaseName(typeof res.masked_name === "string" ? res.masked_name : null);
      setFollowFrom(after);
      setProgress({ ...emptyProgress(res.case_id), lastId: after });
      setPhase("following");
    } catch (e) {
      setError(e instanceof ApiError ? friendlyError(e.status, e.code) : friendlyError(0, "network_error"));
      setPhase("talk"); // the same request_id on retry: the gateway returns the same case
    }
  };

  // Follow the case on the public theater stream.
  const caseId = progress?.caseId ?? null;
  useEffect(() => {
    if (!caseId) return;
    const es = new EventSource(streamUrl(THEATER, followFrom));
    let fails = 0;
    es.onmessage = (m) => {
      fails = 0;
      let e: UTaxEvent;
      try {
        e = JSON.parse(m.data) as UTaxEvent;
      } catch {
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
  }, [caseId, followFrom]);

  // Read the numbers back (and the memo), once each.
  const line = progress ? progressLine(progress) : "";
  useEffect(() => {
    if (!progress || !line || line === spoken.current) return;
    if (progress.totalCents === null && !progress.notEligible && !progress.blocked && !progress.askedFor.length) return;
    spoken.current = line;
    if (soundRef.current) say(line);
  }, [progress, line]);

  const startOver = () => {
    hangUp();
    hush();
    requestId.current = newRequestId();
    usedVoice.current = false;
    spoken.current = "";
    turnsRef.current = [];
    setFollowFrom(0);
    setTurns([]);
    setFull(false);
    setDraft("");
    setEmail("");
    setProgress(null);
    setCaseName(null);
    setNotice(null);
    setError(null);
    setStreamDown(false);
    setPhase("consent");
  };

  const ready = canSend(turns, email);
  const stageIndex = progress ? STAGES.findIndex((s) => s.id === progress.stage) : -1;

  return (
    <div className="talk">
      <header>
        <span className="logo">U</span>
        <span className="title">Talk to U-Tax</span>
        <span className="sub">Free Delaware franchise tax estimate · educational, not tax advice</span>
        <button className="ghost" aria-pressed={sound} onClick={() => setSound((v) => !v)}>
          {sound ? "Sound on" : "Sound off"}
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
            <h1 id="consent-h">Ask U-Tax what your Delaware franchise tax really is.</h1>
            <p className="lead">
              You&rsquo;ll talk with U-Tax&rsquo;s <b>AI intake agent</b>. It asks five short questions, reads your numbers back, and the firm
              emails you a memo.
            </p>
            <ul className="terms">
              <li>The chat is transcribed by U-Tax, with OpenAI&rsquo;s help, and parts may show on the screen here.</li>
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
              <div className={`orb ${voiceState}`} role="status" aria-live="polite">
                <span className="ring" aria-hidden />
                {VOICE_LABEL[voiceState]}
              </div>
            )}
            <ol className="turns" aria-live="polite" aria-label="Transcript">
              {turns.map((t, i) => (
                <li key={i} className={t.speaker}>
                  <small>{t.speaker === "agent" ? "U-Tax (AI)" : "You"}</small>
                  {t.text}
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
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                maxLength={MAX_TURN_CHARS}
                placeholder={mode === "voice" ? "Or type an answer…" : "Type your answer…"}
                autoComplete="off"
                disabled={full || phase === "sending"}
              />
              <button className="secondary" type="submit" disabled={!draft.trim() || full || phase === "sending"}>
                Answer
              </button>
            </form>
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
                  inputMode="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@startup.com"
                  maxLength={320}
                  disabled={phase === "sending"}
                />
                <button className="primary" type="submit" disabled={!ready || phase === "sending" || !HAS_GATEWAY}>
                  {phase === "sending" ? "Sending…" : "Send to U-Tax"}
                </button>
              </div>
              <p className="hint">
                {turns.some((t) => t.speaker === "customer") ? "Your email is only used for this memo." : "Answer at least one question first."}
              </p>
              {error && (
                <p className="error" role="alert">
                  {error}
                </p>
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
            <h1 id="follow-h">{caseName ? `${caseName} is with the firm.` : "Your case is with the firm."}</h1>
            <div className="estimate" aria-live="polite">
              {progress.totalCents !== null ? (
                <>
                  <small>Estimated Delaware franchise tax</small>
                  <b>{formatCents(progress.totalCents)}</b>
                </>
              ) : (
                <small>Working on your numbers…</small>
              )}
            </div>
            <p className="said" role="status">
              {line}
            </p>
            <ol className="steps">
              {STAGES.map((s, i) => (
                <li key={s.id} className={i < stageIndex ? "done" : i === stageIndex ? "now" : ""} aria-current={i === stageIndex ? "step" : undefined}>
                  <span className="tick" aria-hidden>
                    {i <= stageIndex ? "✓" : ""}
                  </span>
                  {s.label}
                </li>
              ))}
            </ol>
            {streamDown && <p className="hint">Live updates paused. The memo still arrives by email.</p>}
            <div className="actions">
              <Link className="secondary" href="/">
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
