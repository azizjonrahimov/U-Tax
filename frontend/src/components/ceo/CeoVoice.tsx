"use client";

// /ceo voice mode (contract v1.8 §5b and §8, v1.8.1 and v1.8.4; design D28). The CEO runs the firm by talking:
// - a mic (tap to talk, or hold) and a hands-free switch, on an OpenAI Realtime session minted
//   for the CEO; its function calls go to POST /api/ceo/tool and the answers go back to it;
// - a start action comes back as a proposal: a confirm card with the readback and a countdown,
//   confirmed only by the CEO's own words (spoken after the readback, outside the agent's audio,
//   or typed) or a click, never by the model;
// - STOP EVERYTHING (also Esc) pauses every action type at once, with no confirm, and fences
//   anything still in flight;
// - without a session (no key, or no WebRTC): browser speech recognition or typing, through
//   POST /api/ceo/command, answers read aloud by the browser.

import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { ApiError } from "@/lib/api";
import type { UTaxEvent } from "@/theater/events";
import { parseVoiceSession } from "@/talk/talk";
import { ceoError } from "@/ceo/ceo";
import {
  actionResultItem,
  agentAudio,
  announcements,
  armProposal,
  claimCard,
  clearProposal,
  decideTyped,
  describeRead,
  emptyVoice,
  functionOutputItem,
  isBareCancel,
  matchConfirm,
  parseToolResult,
  PROPOSAL_TTL_MS,
  readbackHeard,
  secondsLeft,
  STOP_EVERYTHING,
  toolOutput,
  voiceStep,
  type FunctionCall,
  type Proposal,
  type ToolResult,
  type VoiceEffect,
  type VoiceState,
} from "@/ceo/voice";
import { MicIcon } from "@/components/MicIcon";
import { API, authJson } from "./gateway";

const MINT_MS = 8000;
const SDP_MS = 10000;
const CHANNEL_MS = 15000;
const DISCONNECT_GRACE_MS = 4000;
/** A press longer than this is push-to-talk: releasing ends it. A shorter one toggles. */
const HOLD_MS = 350;
const MAX_LINES = 6;
/** The browser's ear reopens this long after its own voice stops (the room's echo dies down). */
const EAR_REOPEN_MS = 600;
/** A spoken line that hasn't ended after ~this per word (plus 2 s, at most 30 s) is treated as stalled. */
const SPEECH_MS_PER_WORD = 450;
const SPEECH_MAX_MS = 30000;
/** A response.create with no response.created after this long is treated as lost. */
const RESPONSE_STALE_MS = 15000;

type Link = "off" | "connecting" | "realtime" | "fallback";
type Flow = "listening" | "thinking" | "speaking" | "idle";
type Line = { who: "you" | "firm" | "alert"; text: string; n: number };
interface Card extends Proposal {
  expired: boolean;
  sending: boolean;
}

// Browser speech recognition (Chrome ships it prefixed); feature-detected, typed minimally.
interface Recognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { resultIndex: number; results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal: boolean }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionCtor = new () => Recognition;
const recognitionCtor = (): RecognitionCtor | null => {
  if (typeof window === "undefined") return null;
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

const hasSpeech = () => typeof window !== "undefined" && "speechSynthesis" in window;

interface Call {
  pc: RTCPeerConnection;
  dc: RTCDataChannel;
  mic: MediaStream;
  audio: HTMLAudioElement;
}

export interface VoiceProps {
  token: string;
  /** Refresh the console's lists (after anything the voice changed). */
  refresh: () => void;
  /** A 401: the token is wrong or rotated. */
  signOut: (why: string) => void;
  /** The console's poll hands each batch of new admin events here; `reset` after a log restart. */
  feedRef: MutableRefObject<((events: readonly UTaxEvent[], reset: boolean) => void) | null>;
  names: ReadonlyMap<string, string>;
  flagged: ReadonlySet<string>;
}

/** Gateway refusals for voice actions, as sentences. */
function voiceError(e: unknown): string {
  if (!(e instanceof ApiError)) return "The gateway sent something unexpected.";
  switch (e.code) {
    case "used":
      return "That proposal was already decided. Check the lists for what happened.";
    case "expired":
      return "That proposal expired before it was confirmed. Ask again.";
    case "superseded":
      return "That proposal was replaced or cancelled (a newer one, a change, or a pause). Ask again to hear the current one.";
    case "flagged":
      return "QA flagged that case, so it can't be decided by voice. Decide it in the lists below.";
    case "not_a_confirm":
      return "That didn't sound like a confirm. Say “confirm”, or click Confirm.";
    case "nothing_pending":
      return "Nothing is waiting on you.";
    case "timeout":
    case "network_error":
      return "No answer from the gateway, so it may or may not have run. Check the lists before trying again.";
    case "unknown_tool":
    case "bad_args":
    case "not_found":
      return e.message || ceoError(e.status, e.code, e.message);
    default:
      return ceoError(e.status, e.code, e.message);
  }
}
/** Refusals after which the action certainly did not run. */
const NOT_RUN = new Set(["expired", "superseded", "flagged", "not_a_confirm", "not_found", "bad_args", "unknown_tool", "nothing_pending"]);

export function CeoVoice({ token, refresh, signOut, feedRef, names, flagged }: VoiceProps) {
  const [link, setLink] = useState<Link>("off");
  const [flow, setFlow] = useState<Flow>("idle");
  const [micOn, setMicOn] = useState(false);
  const [handsFree, setHandsFree] = useState(false);
  const [card, setCard] = useState<Card | null>(null);
  const [now, setNow] = useState(0);
  const [lines, setLines] = useState<Line[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<{ ok: boolean; text: string } | null>(null);
  const [stopNote, setStopNote] = useState<{ ok: boolean | null; text: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [canListen, setCanListen] = useState(true);

  const vs = useRef<VoiceState>(emptyVoice());
  const callRef = useRef<Call | null>(null);
  const attempt = useRef(0);
  const linkRef = useRef<Link>("off");
  const micRef = useRef(false);
  const handsRef = useRef(false);
  const recRef = useRef<Recognition | null>(null);
  const speaking = useRef(0);
  const seenId = useRef<number | null>(null);
  const namesRef = useRef(names);
  const flaggedRef = useRef(flagged);
  const lineNo = useRef(0);
  const press = useRef<{ at: number; wasOn: boolean } | null>(null);
  const pointerDone = useRef(false);
  const stopping = useRef(false);
  const alive = useRef(true);
  /** Bumped by STOP EVERYTHING: a tool call or confirm that started before it never acts after it. */
  const stopEpoch = useRef(0);
  /** Function calls still at the gateway: the model is asked to speak once, after the last. */
  const inflight = useRef(0);
  /** A model response is running (or was just asked for): a second response.create would be refused. */
  const responding = useRef<number | null>(null);
  const wantResponse = useRef(false);

  useEffect(() => {
    namesRef.current = names;
    flaggedRef.current = flagged;
  }, [names, flagged]);

  const go = useCallback((l: Link) => {
    linkRef.current = l;
    setLink(l);
  }, []);

  const addLine = useCallback((who: Line["who"], text: string) => {
    lineNo.current += 1;
    const n = lineNo.current;
    setLines((ls) => [...ls, { who, text, n }].slice(-MAX_LINES));
  }, []);

  /** Realtime client events, only while the channel is open. */
  const send = useCallback((events: readonly object[]) => {
    const dc = callRef.current?.dc;
    if (dc?.readyState !== "open") return;
    for (const e of events) dc.send(JSON.stringify(e));
  }, []);

  /** Ask the model to speak: once, after the last tool result, and never over a running response. */
  const respond = useCallback(() => {
    if (inflight.current > 0) return;
    const since = responding.current;
    if (since !== null && Date.now() - since < RESPONSE_STALE_MS) {
      wantResponse.current = true;
      return;
    }
    wantResponse.current = false;
    responding.current = Date.now();
    send([{ type: "response.create" }]);
  }, [send]);

  /** The mic track is live only while talking (push-to-talk) or hands-free, and never while the browser speaks. */
  const applyMic = useCallback(() => {
    const live = (micRef.current || handsRef.current) && speaking.current === 0;
    callRef.current?.mic.getAudioTracks().forEach((t) => (t.enabled = live));
  }, []);

  // ------------------------------------------------------------ browser speech

  const stopListening = useCallback(() => {
    const r = recRef.current;
    recRef.current = null;
    if (r) {
      r.onend = null;
      r.onresult = null;
      r.onerror = null;
      try {
        r.abort();
      } catch {
        /* already stopped */
      }
    }
  }, []);

  // listen() and onSpoken() call each other through these refs (speech ends → listen again).
  const listenRef = useRef<() => void>(() => {});
  const spokenRef = useRef<(text: string) => void>(() => {});

  /**
   * Read a line aloud, queued. While the browser speaks, its own ear is closed and the Realtime
   * mic is muted (the OS voice bypasses echo cancellation), and the time counts as agent audio.
   * `onDone` runs when this line has been spoken (or at once when nothing can speak).
   */
  const speak = useCallback(
    (text: string, onDone?: () => void) => {
      if (!hasSpeech() || !text) {
        onDone?.();
        return;
      }
      const u = new SpeechSynthesisUtterance(text);
      if (speaking.current === 0) vs.current = agentAudio(vs.current, "browser", true, Date.now());
      speaking.current += 1;
      applyMic();
      stopListening();
      if (linkRef.current === "fallback" && micRef.current && !handsRef.current) {
        micRef.current = false; // push-to-talk ended with the answer: the label must not say "Listening"
        setMicOn(false);
      }
      let ended = false;
      const done = () => {
        if (ended) return;
        ended = true;
        speaking.current = Math.max(0, speaking.current - 1);
        if (speaking.current === 0) vs.current = agentAudio(vs.current, "browser", false, Date.now());
        applyMic();
        onDone?.();
        if (speaking.current === 0 && alive.current && linkRef.current === "fallback" && handsRef.current) {
          setTimeout(() => {
            if (alive.current && speaking.current === 0 && handsRef.current) listenRef.current();
          }, EAR_REOPEN_MS);
        }
      };
      u.onend = done;
      u.onerror = done;
      // Browser speech can stall without ever ending (seen headless; Chrome does it too). Past a
      // generous length-based limit, silence it (so no echo follows) and carry on as if it ended.
      const limit = Math.min(SPEECH_MAX_MS, 2000 + text.split(/\s+/).length * SPEECH_MS_PER_WORD);
      setTimeout(() => {
        if (ended) return;
        if (window.speechSynthesis.speaking) window.speechSynthesis.cancel();
        done();
      }, limit);
      window.speechSynthesis.speak(u);
    },
    [applyMic, stopListening],
  );
  const hush = useCallback(() => {
    if (hasSpeech()) window.speechSynthesis.cancel(); // fires each utterance's onerror → done
  }, []);

  /** The browser can't listen (no recognition, mic blocked, or it keeps failing): hands-free still reads alerts. */
  const deaf = useRef(false);
  const earErrors = useRef(0);
  const goDeaf = useCallback((why: string | null) => {
    deaf.current = true;
    setCanListen(false);
    micRef.current = false;
    setMicOn(false);
    if (why) setNotice(why);
  }, []);

  const listen = useCallback(() => {
    if (recRef.current || speaking.current > 0 || linkRef.current !== "fallback" || deaf.current) return;
    const Ctor = recognitionCtor();
    if (!Ctor) return goDeaf(null);
    const r = new Ctor();
    r.lang = "en-US";
    r.continuous = handsRef.current;
    r.interimResults = false;
    r.onresult = (e) => {
      earErrors.current = 0;
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const res = e.results[i];
        if (res.isFinal && res[0]?.transcript.trim()) spokenRef.current(res[0].transcript);
      }
    };
    let failed = false;
    r.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        goDeaf("The microphone is blocked for this page. Type commands below; hands-free still reads alerts aloud.");
      } else if (e.error !== "no-speech" && e.error !== "aborted") {
        failed = true;
        earErrors.current += 1;
        if (earErrors.current >= 3) goDeaf("The browser's speech recognition keeps failing. Type commands below; hands-free still reads alerts aloud.");
      }
    };
    r.onend = () => {
      if (recRef.current !== r) return;
      recRef.current = null;
      if (alive.current && handsRef.current && speaking.current === 0 && linkRef.current === "fallback" && !deaf.current) {
        // A failing recognizer (offline, no device) restarts after a pause, never in a tight loop.
        if (failed) setTimeout(() => alive.current && handsRef.current && listenRef.current(), 2000);
        else listenRef.current();
      } else {
        micRef.current = false;
        setMicOn(false);
      }
    };
    recRef.current = r;
    try {
      r.start();
    } catch {
      recRef.current = null; // already running elsewhere, or refused: the next tap retries
    }
  }, [goDeaf]);
  useEffect(() => {
    listenRef.current = listen;
  }, [listen]);

  // ------------------------------------------------------------ the gateway

  const post = useCallback(
    async (path: string, body: unknown): Promise<ToolResult> => {
      const raw = await authJson<unknown>(token, `${API}${path}`, { method: "POST", body: JSON.stringify(body) });
      const r = parseToolResult(raw, Date.now());
      if (!r) throw new ApiError(0, "bad_answer", "The gateway's answer wasn't readable.");
      return r;
    },
    [token],
  );

  /** A 401 ends the session; anything else is the caller's to show. */
  const unauthorized = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        signOut(ceoError(401, "unauthorized", ""));
        return true;
      }
      return false;
    },
    [signOut],
  );

  /** Tell the model what actually happened (v1.8.1), and let it say so unless STOP silenced it. */
  const tellModel = useCallback(
    (summary: string, speakUp = true) => {
      if (linkRef.current !== "realtime") return;
      send([actionResultItem(summary)]);
      if (speakUp) respond();
    },
    [respond, send],
  );

  const showCard = useCallback((p: Proposal) => {
    setNow(Date.now());
    setOutcome(null);
    setCard({ ...p, expired: false, sending: false });
  }, []);
  const closeCard = useCallback((proposalId?: string) => {
    vs.current = clearProposal(vs.current, proposalId);
    setCard((c) => (c && (proposalId === undefined || c.proposalId === proposalId) ? null : c));
  }, []);

  /**
   * A card from a typed command (no model to read it): the console reads it aloud. On a live
   * Realtime line, spoken confirms wait until that reading has ended; otherwise words decide it at once.
   */
  const openLocalCard = useCallback(
    (p: Proposal, say: string) => {
      const realtime = linkRef.current === "realtime";
      vs.current = armProposal(vs.current, p, Date.now(), realtime ? "browser" : "none");
      showCard(p);
      addLine("firm", say);
      speak(say, () => {
        vs.current = readbackHeard(vs.current, p.proposalId, Date.now());
      });
    },
    [addLine, showCard, speak],
  );

  /** POST /api/ceo/confirm and report the outcome everywhere. The card was already claimed. */
  const send_confirm = useCallback(
    async (proposalId: string, decision: "confirm" | "cancel", via: "voice" | "click", heard: string) => {
      const epoch = stopEpoch.current;
      setCard((c) => (c && c.proposalId === proposalId ? { ...c, sending: true } : c));
      try {
        const r = await post("/ceo/confirm", { proposal_id: proposalId, decision, via, heard });
        if (!alive.current) return;
        closeCard(proposalId);
        const text = decision === "cancel" ? "Cancelled. Nothing was done." : r.kind === "done" ? r.summary : "Done.";
        setOutcome({ ok: true, text: decision === "cancel" ? text : `Done: ${text}` });
        refresh();
        if (decision === "confirm" && epoch !== stopEpoch.current) {
          // It landed while STOP EVERYTHING ran: pause again, so nothing it re-enabled keeps going.
          await post("/ceo/tool", { name: STOP_EVERYTHING.name, args: STOP_EVERYTHING.args, call_id: `stop-${Date.now()}`, channel: "click" }).catch(() => {});
          if (!alive.current) return;
          setStopNote({ ok: true, text: `A confirm landed during STOP (${text}), so everything was paused again.` });
          tellModel(`STOP EVERYTHING. A confirm landed at the same moment (${text}); everything was paused again.`, false);
          return;
        }
        tellModel(text, epoch === stopEpoch.current);
        if (linkRef.current !== "realtime") speak(text);
      } catch (e) {
        if (!alive.current || unauthorized(e)) return;
        const code = e instanceof ApiError ? e.code : "";
        const msg = voiceError(e);
        if (code === "not_a_confirm" || code === "timeout" || code === "network_error") {
          // Still open: say it again, or click (a retry of one that did land answers "used").
          const cur = vs.current.proposal;
          if (cur && cur.proposalId === proposalId) vs.current = { ...vs.current, proposal: { ...cur, settling: false } };
          setCard((c) => (c && c.proposalId === proposalId ? { ...c, sending: false } : c));
        } else {
          closeCard(proposalId);
          refresh();
        }
        setOutcome({ ok: false, text: msg });
        tellModel(NOT_RUN.has(code) ? `Nothing was done. ${msg}` : `Outcome unknown. ${msg}`, epoch === stopEpoch.current);
        if (linkRef.current !== "realtime") speak(msg);
      }
    },
    [closeCard, post, refresh, speak, tellModel, unauthorized],
  );

  /**
   * Confirm or cancel the open card. `via: "voice"` only with the CEO's own words. `claimed`: the
   * Realtime reducer already claimed this card (its id); a click or typed words claim it here.
   */
  const settle = useCallback(
    (decision: "confirm" | "cancel", via: "voice" | "click", heard: string, claimed?: string) => {
      const c = claimCard(vs.current, decision, Date.now(), claimed);
      if (!c) return Promise.resolve();
      vs.current = c.state;
      return send_confirm(c.proposal.proposalId, decision, via, heard);
    },
    [send_confirm],
  );

  /** A gateway answer: back to the model (Realtime), or read aloud (fallback and typing). */
  const handle = useCallback(
    (r: ToolResult, callId: string | null, tool: string | null, via: "voice" | "click") => {
      if (r.kind === "done") {
        setOutcome({ ok: true, text: r.summary });
        refresh();
      }
      if (callId) {
        if (r.kind === "proposal") {
          // The model reads it back; a spoken confirm waits until that audio has played.
          vs.current = armProposal(vs.current, { ...r, tool }, Date.now(), "model");
          showCard(r);
        }
        send([functionOutputItem(callId, toolOutput(r))]);
        return;
      }
      if (r.kind === "proposal") return openLocalCard(r, r.readback);
      if (r.kind === "pending") {
        // The gateway matched a confirm or cancel word to its newest open proposal (none open here).
        if (r.decision === "cancel") {
          void post("/ceo/confirm", { proposal_id: r.proposalId, decision: "cancel", via, heard: "" })
            .then(() => {
              setOutcome({ ok: true, text: "Cancelled. Nothing was done." });
              refresh();
            })
            .catch((e: unknown) => !unauthorized(e) && setOutcome({ ok: false, text: voiceError(e) }));
          return;
        }
        // Never confirm a card the CEO hasn't seen: show it, and ask for a fresh confirm.
        const p = { proposalId: r.proposalId, readback: r.readback || "The open proposal.", expiresAt: Date.now() + PROPOSAL_TTL_MS };
        return openLocalCard(p, `${p.readback} Confirm again to go ahead.`);
      }
      const text = r.kind === "read" ? describeRead(r.data) : r.kind === "done" ? r.summary : r.kind === "unknown" ? r.say : "";
      addLine("firm", text);
      speak(text);
    },
    [addLine, openLocalCard, post, refresh, send, showCard, speak, unauthorized],
  );

  const runTool = useCallback(
    async (c: FunctionCall) => {
      const mine = attempt.current;
      const epoch = stopEpoch.current;
      inflight.current += 1;
      let output: string;
      let result: ToolResult | null = null;
      try {
        result = await post("/ceo/tool", { name: c.name, args: c.args, call_id: c.callId });
        output = toolOutput(result);
      } catch (e) {
        if (unauthorized(e)) return;
        output = toolOutput({ kind: "error", code: e instanceof ApiError ? e.code : "error", message: voiceError(e) });
      } finally {
        inflight.current = Math.max(0, inflight.current - 1);
      }
      if (mine !== attempt.current || !alive.current) return;
      if (epoch !== stopEpoch.current) {
        // STOP EVERYTHING came first: no card from it, and the model stays quiet.
        send([functionOutputItem(c.callId, toolOutput({ kind: "error", code: "stopped", message: "The CEO pressed STOP EVERYTHING. Nothing new was started." }))]);
        return;
      }
      if (result) handle(result, c.callId, c.name, "voice");
      else send([functionOutputItem(c.callId, output)]);
      respond();
    },
    [handle, post, respond, send, unauthorized],
  );

  /** Words from the CEO outside Realtime (browser speech, or typed): a confirm word decides the card, else a command. */
  const command = useCallback(
    async (text: string, via: "voice" | "click") => {
      const t = text.trim();
      if (!t) return;
      addLine("you", t);
      const d = decideTyped(vs.current, t, Date.now());
      if (d?.type === "confirm") {
        await settle(d.decision, via, d.heard);
        if (d.decision === "confirm" || isBareCancel(t)) return; // "stop everything" also goes on as a command
      } else if (vs.current.proposal && matchConfirm(t, vs.current.proposal)) return; // the card is settling or expired
      try {
        handle(await post("/ceo/command", { text: t.slice(0, 500) }), null, null, via);
      } catch (e) {
        if (!alive.current || unauthorized(e)) return;
        const msg = voiceError(e);
        addLine("firm", msg);
        speak(msg);
      }
    },
    [addLine, handle, post, settle, speak, unauthorized],
  );
  useEffect(() => {
    spokenRef.current = (text) => void command(text, "voice");
  }, [command]);

  // ------------------------------------------------------------ the Realtime session

  const run = useCallback(
    (effects: readonly VoiceEffect[]) => {
      for (const ef of effects) {
        switch (ef.type) {
          case "tool":
            void runTool(ef.call);
            break;
          case "reply":
            send([functionOutputItem(ef.callId, ef.output)]);
            respond();
            break;
          case "confirm":
            void settle(ef.decision, "voice", ef.heard, ef.proposalId);
            break;
          case "ignored":
            setOutcome({ ok: false, text: "Heard “confirm” while U-Tax was still talking. Say it again, or click Confirm." });
            break;
          case "heard":
            addLine("you", ef.text);
            break;
          case "agent":
            addLine("firm", ef.text);
            break;
          case "state":
            setFlow(ef.state);
            break;
          case "response":
            if (ef.active) responding.current = Date.now();
            else {
              responding.current = null;
              if (wantResponse.current) respond();
            }
            break;
          case "error":
            if (ef.code === "conversation_already_has_active_response") wantResponse.current = true;
            console.warn("[ceo voice] realtime error", ef.code, ef.message);
            break;
        }
      }
    },
    [addLine, respond, runTool, send, settle],
  );

  const hangUp = useCallback(() => {
    attempt.current += 1;
    responding.current = null;
    wantResponse.current = false;
    const c = callRef.current;
    callRef.current = null;
    if (!c) return;
    c.mic.getTracks().forEach((t) => t.stop());
    c.pc.close();
    c.audio.pause();
    c.audio.srcObject = null;
  }, []);

  /** Without a session: browser speech (when there is one) or typing. */
  const toFallback = useCallback(
    (why: string) => {
      hangUp();
      go("fallback");
      setFlow("idle");
      vs.current = agentAudio(vs.current, "model", false, Date.now());
      const ear = recognitionCtor() !== null;
      if (!ear) deaf.current = true;
      setCanListen(ear && !deaf.current);
      setNotice(`${why} ${ear ? "The browser listens instead; answers are read aloud." : "Type commands below; answers are read aloud."}`);
      if (ear && (micRef.current || handsRef.current)) listenRef.current();
      else if (!ear) {
        micRef.current = false;
        setMicOn(false);
      }
    },
    [go, hangUp],
  );

  /** First mic press or hands-free: mint a CEO session, then ask for the mic (only now), then connect. */
  const connect = useCallback(async () => {
    hangUp();
    const mine = attempt.current;
    const stale = () => mine !== attempt.current || !alive.current;
    go("connecting");
    setNotice(null);
    let session;
    try {
      session = parseVoiceSession(
        await authJson<unknown>(token, `${API}/voice/session`, { method: "POST", body: JSON.stringify({ mode: "ceo" }), signal: AbortSignal.timeout(MINT_MS) }),
      );
    } catch (e) {
      if (stale() || unauthorized(e)) return;
      const status = e instanceof ApiError ? e.status : 0;
      return toFallback(status === 503 ? "The voice model isn't set up on the gateway." : status === 429 ? "Too many voice sessions this hour." : "The voice session couldn't start.");
    }
    if (stale()) return;
    if (!session) return toFallback("The voice session couldn't start.");
    if (typeof RTCPeerConnection === "undefined" || !navigator.mediaDevices?.getUserMedia) return toFallback("This browser can't run the voice line.");

    let mic: MediaStream;
    try {
      mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    } catch {
      if (stale()) return;
      go("off");
      micRef.current = false;
      handsRef.current = false;
      setMicOn(false);
      setHandsFree(false);
      setNotice("The microphone is blocked for this page. Allow it, or type a command below.");
      return;
    }
    if (stale()) {
      mic.getTracks().forEach((t) => t.stop());
      return;
    }

    let opened = false;
    const timer = setTimeout(() => {
      if (!stale() && !opened) toFallback("The voice line couldn't connect on this network.");
    }, CHANNEL_MS);
    let pc: RTCPeerConnection;
    let dc: RTCDataChannel;
    const audio = new Audio();
    audio.autoplay = true;
    try {
      pc = new RTCPeerConnection();
      dc = pc.createDataChannel("oai-events");
      callRef.current = { pc, dc, mic, audio };
      pc.ontrack = (e) => {
        audio.srcObject = e.streams[0];
      };
      mic.getTracks().forEach((t) => pc.addTrack(t, mic));
      applyMic();
    } catch (e) {
      console.warn("[ceo voice] WebRTC unavailable", e);
      clearTimeout(timer);
      if (!callRef.current) mic.getTracks().forEach((t) => t.stop());
      return toFallback("This browser can't run the voice line.");
    }
    dc.onopen = () => {
      if (stale()) return;
      opened = true;
      clearTimeout(timer);
      go("realtime");
      setFlow("idle");
      // The gateway set instructions, tools and transcription when minting. No opener: the CEO speaks first.
    };
    dc.onmessage = (m) => {
      if (stale()) return;
      let raw: unknown;
      try {
        raw = JSON.parse(m.data);
      } catch {
        console.warn("[ceo voice] unreadable realtime message");
        return;
      }
      const r = voiceStep(vs.current, raw, Date.now());
      vs.current = r.state;
      run(r.effects);
    };
    let grace: ReturnType<typeof setTimeout> | undefined;
    pc.onconnectionstatechange = () => {
      if (stale()) return;
      clearTimeout(grace);
      if (pc.connectionState === "failed") toFallback("The voice line dropped.");
      else if (pc.connectionState === "disconnected")
        grace = setTimeout(() => {
          if (!stale() && pc.connectionState !== "connected") toFallback("The voice line dropped.");
        }, DISCONNECT_GRACE_MS);
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
      if (stale()) return;
      clearTimeout(timer);
      console.warn("[ceo voice] connect failed", e);
      toFallback("The voice line couldn't connect.");
    }
  }, [applyMic, go, hangUp, run, toFallback, token, unauthorized]);

  // ------------------------------------------------------------ the controls

  const micStart = useCallback(() => {
    micRef.current = true;
    setMicOn(true);
    setOutcome(null);
    if (linkRef.current === "off") void connect();
    else if (linkRef.current === "realtime") applyMic();
    else if (linkRef.current === "fallback") listen();
  }, [applyMic, connect, listen]);
  const micStop = useCallback(() => {
    micRef.current = false;
    setMicOn(false);
    if (linkRef.current === "realtime") applyMic();
    else if (linkRef.current === "fallback" && !handsRef.current) recRef.current?.stop(); // finish the phrase, then stop
  }, [applyMic]);

  const toggleHandsFree = useCallback(() => {
    const on = !handsRef.current;
    handsRef.current = on;
    setHandsFree(on);
    if (on) {
      if (linkRef.current === "off") void connect();
      else if (linkRef.current === "realtime") applyMic();
      else if (linkRef.current === "fallback") {
        stopListening();
        listen(); // again, continuous
      }
    } else {
      if (linkRef.current === "realtime") applyMic();
      else if (linkRef.current === "fallback" && !micRef.current) stopListening();
    }
  }, [applyMic, connect, listen, stopListening]);

  const endVoice = useCallback(() => {
    hangUp();
    stopListening();
    hush();
    micRef.current = false;
    handsRef.current = false;
    setMicOn(false);
    setHandsFree(false);
    setFlow("idle");
    vs.current = agentAudio(vs.current, "model", false, Date.now());
    go("off");
  }, [go, hangUp, hush, stopListening]);

  /** STOP EVERYTHING: pause every action type now. No confirm, never waits on the model, fences what's in flight. */
  const stopEverything = useCallback(async () => {
    if (stopping.current) return;
    stopping.current = true;
    stopEpoch.current += 1;
    wantResponse.current = false;
    hush();
    send([{ type: "response.cancel" }, { type: "output_audio_buffer.clear" }]);
    const p = vs.current.proposal;
    closeCard();
    // Best effort: the pause cancels open proposals at the gateway anyway (v1.8.4), and STOP must not wait.
    if (p && !p.settling) void post("/ceo/confirm", { proposal_id: p.proposalId, decision: "cancel", via: "click", heard: "" }).catch(() => {});
    setStopNote({ ok: null, text: "Stopping everything…" });
    let signedOut = false;
    try {
      let summary: string;
      try {
        const r = await post("/ceo/tool", { name: STOP_EVERYTHING.name, args: STOP_EVERYTHING.args, call_id: `stop-${Date.now()}`, channel: "click" });
        summary = r.kind === "done" ? r.summary : "Everything is paused.";
      } catch (e) {
        if (unauthorized(e)) {
          signedOut = true;
          return;
        }
        // The voice routes refused or aren't there: the same pause through the standing-order route.
        await authJson(token, `${API}/trust`, { method: "POST", body: JSON.stringify({ action_type: "*", state: "PAUSED" }) });
        summary = "Everything is paused.";
      }
      if (!alive.current) return;
      setStopNote({ ok: true, text: `${summary} Resume each kind under Standing orders when you're ready.` });
      // The model was just cut off: tell it what happened, but don't start it talking again.
      tellModel(`The CEO pressed STOP EVERYTHING. ${summary}`, false);
      if (linkRef.current !== "realtime") speak("Everything is paused.");
    } catch (e) {
      if (unauthorized(e)) {
        signedOut = true;
        return;
      }
      if (alive.current) setStopNote({ ok: false, text: `STOP didn't reach the gateway: ${voiceError(e)} Press it again.` });
    } finally {
      stopping.current = false;
      if (!signedOut && alive.current) refresh();
    }
  }, [closeCard, hush, post, refresh, send, speak, tellModel, token, unauthorized]);

  // Esc is STOP EVERYTHING, anywhere on the console (not while an input method is composing).
  const stopRef = useRef(stopEverything);
  useEffect(() => {
    stopRef.current = stopEverything;
  }, [stopEverything]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.repeat || e.isComposing) return;
      e.preventDefault();
      void stopRef.current();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Hands-free: speak each new approval and BLOCKED case from the console's log poll.
  const speakRef = useRef(speak);
  const lineRef = useRef(addLine);
  const closeRef = useRef(closeCard);
  useEffect(() => {
    speakRef.current = speak;
    lineRef.current = addLine;
    closeRef.current = closeCard;
  }, [speak, addLine, closeCard]);
  useEffect(() => {
    feedRef.current = (events, reset) => {
      const top = events.reduce((m, e) => (typeof e.id === "number" && e.id > m ? e.id : m), 0);
      if (reset) closeRef.current(); // a reloaded log: the card belongs to the old one
      if (seenId.current === null || reset) {
        seenId.current = top; // the first load, or a reloaded log: the backlog is never read out
        return;
      }
      if (handsRef.current) {
        for (const line of announcements(events, seenId.current, namesRef.current, flaggedRef.current)) {
          lineRef.current("alert", line);
          speakRef.current(line);
        }
      }
      seenId.current = Math.max(seenId.current, top);
    };
    return () => {
      feedRef.current = null;
    };
  }, [feedRef]);

  // The card's countdown; at zero it can't be confirmed (by voice or click) any more.
  const cardId = card?.proposalId ?? null;
  const tellRef = useRef(tellModel);
  useEffect(() => {
    tellRef.current = tellModel;
  }, [tellModel]);
  useEffect(() => {
    if (!cardId) return;
    const t = setInterval(() => {
      const n = Date.now();
      setNow(n);
      const p = vs.current.proposal;
      if (p && p.proposalId === cardId && n >= p.expiresAt && !p.settling) {
        vs.current = clearProposal(vs.current, cardId);
        setCard((c) => (c && c.proposalId === cardId ? { ...c, expired: true } : c));
        tellRef.current("The proposal expired. Nothing was done.");
      }
    }, 250);
    return () => clearInterval(t);
  }, [cardId]);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      hangUp();
      stopListening();
      if (hasSpeech()) window.speechSynthesis.cancel();
    };
  }, [hangUp, stopListening]);

  // ------------------------------------------------------------ view

  const listening = link === "realtime" ? (micOn || handsFree) && flow !== "speaking" : link === "fallback" && (micOn || handsFree) && canListen;
  const micLabel =
    link === "connecting"
      ? "Connecting the voice line…"
      : link === "realtime"
        ? flow === "speaking"
          ? "U-Tax is speaking"
          : flow === "thinking"
            ? "Thinking…"
            : handsFree
              ? "Hands-free: listening"
              : micOn
                ? "Listening… tap again when done"
                : "Microphone off"
        : link === "fallback"
          ? listening
            ? handsFree
              ? "Hands-free: the browser is listening"
              : "Listening…"
            : canListen
              ? "Microphone off (browser speech)"
              : handsFree
                ? "Hands-free: alerts read aloud · type to answer"
                : "Typing only"
          : "Voice off";
  const left = card ? secondsLeft(card.expiresAt, now) : 0;

  return (
    <>
      {/* Docked at the top while the console scrolls: the controls, STOP's result and the open card only. */}
      <div className="voice-dock" role="region" aria-label="Voice control">
      <div className="vbar">
        <button className="stop" onClick={() => void stopEverything()} aria-keyshortcuts="Escape">
          STOP EVERYTHING <kbd>Esc</kbd>
        </button>
        <button
          className={`mic${micOn ? " on" : ""}`}
          aria-pressed={micOn}
          disabled={link === "connecting" || (link === "fallback" && !canListen) || handsFree}
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            // Keep the release even if the pointer slides off the button (else the mic stays live).
            try {
              e.currentTarget.setPointerCapture(e.pointerId);
            } catch {
              /* not capturable: the release still arrives when it stays on the button */
            }
            press.current = { at: Date.now(), wasOn: micRef.current };
            if (!micRef.current) micStart();
          }}
          onPointerUp={() => {
            const p = press.current;
            press.current = null;
            if (!p) return;
            pointerDone.current = true;
            if (p.wasOn || Date.now() - p.at > HOLD_MS) micStop(); // a second tap, or the end of a hold
          }}
          onPointerCancel={() => {
            press.current = null;
            micStop();
          }}
          onClick={() => {
            // Keyboard and assistive tech click without pointer events: a plain toggle.
            if (pointerDone.current) {
              pointerDone.current = false;
              return;
            }
            if (micRef.current) micStop();
            else micStart();
          }}
        >
          <MicIcon size={22} />
          {micOn ? "Talking… tap to finish" : "Talk"}
        </button>
        <label className="hf">
          <input type="checkbox" role="switch" checked={handsFree} onChange={toggleHandsFree} disabled={link === "connecting"} />
          Hands-free
        </label>
        <span className={`vstate${listening ? " live" : ""}`} role="status" aria-live="polite">
          {micLabel}
        </span>
        {link !== "off" && (
          <button className="ghost" onClick={endVoice}>
            End voice
          </button>
        )}
      </div>

      {stopNote && (
        <p className={stopNote.ok === false ? "error banner" : "stopped"} role={stopNote.ok === false ? "alert" : "status"}>
          {stopNote.text}{" "}
          {stopNote.ok !== null && (
            <button className="ghost inline" onClick={() => setStopNote(null)}>
              Dismiss
            </button>
          )}
        </p>
      )}

      {card && (
        <div className={`proposal${card.expired ? " expired" : ""}`} role="group" aria-labelledby="pr-read" aria-describedby="pr-how">
          <p className="readback" id="pr-read">
            {card.readback}
          </p>
          {card.expired ? (
            <p className="how" id="pr-how">
              Expired: nothing was done. Ask again if you still want it.
            </p>
          ) : (
            <p className="how" id="pr-how">
              Say or type “confirm” or “cancel”, or click. <span className="mono" aria-hidden="true">{left}s</span>
              <span className="sr-only">{left > 10 ? "" : `${left} seconds left`}</span>
            </p>
          )}
          <div className="btns">
            {!card.expired && (
              <button className="primary" onClick={() => void settle("confirm", "click", "")} disabled={card.sending || left === 0}>
                {card.sending ? "Working…" : "Confirm"}
              </button>
            )}
            <button
              className="secondary"
              onClick={() => (card.expired ? closeCard(card.proposalId) : void settle("cancel", "click", ""))}
              disabled={card.sending}
            >
              {card.expired ? "Dismiss" : "Cancel"}
            </button>
          </div>
        </div>
      )}
      {outcome && (
        <p className={outcome.ok ? "ok" : "error"} role="status">
          {outcome.text}
        </p>
      )}
      </div>

      <section className="voice" aria-label="Voice transcript and typed commands">
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}

      {lines.length > 0 && (
        <ol className="vlog" aria-label="Voice transcript">
          {lines.map((l) => (
            <li key={l.n} className={l.who}>
              <b>{l.who === "you" ? "You" : l.who === "alert" ? "Alert" : "U-Tax"}</b> {l.text}
            </li>
          ))}
        </ol>
      )}

      <form
        className="cmd"
        onSubmit={(e) => {
          e.preventDefault();
          const t = draft;
          setDraft("");
          void command(t, "click");
        }}
      >
        <label htmlFor="cmd" className="sr-only">
          Type a command
        </label>
        <input
          id="cmd"
          value={draft}
          maxLength={500}
          autoComplete="off"
          placeholder="Or type: what happened overnight? · approve the memo for Team 7 · stop posting"
          onChange={(e) => setDraft(e.target.value)}
        />
        <button className="secondary" type="submit" disabled={!draft.trim()}>
          Send
        </button>
      </form>
    </section>
    </>
  );
}
