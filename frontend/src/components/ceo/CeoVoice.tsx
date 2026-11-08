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
  CEO_TOOLS,
  clearProposal,
  decideTyped,
  ECHO_TAIL_MS,
  describeRead,
  emptyVoice,
  functionOutputItem,
  isApproveIt,
  isBareCancel,
  localExpiry,
  matchConfirm,
  parseToolResult,
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
import { Speaker } from "@/lib/tts";
import { isBlockedError, isQuietError, MAX_EAR_FAILURES, recognitionCtor, type Recognition } from "@/lib/recognition";
import { API, authJson } from "./gateway";

const MINT_MS = 8000;
const SDP_MS = 10000;
const CHANNEL_MS = 15000;
const DISCONNECT_GRACE_MS = 4000;
/** A press longer than this is push-to-talk: releasing ends it. A shorter one toggles. */
const HOLD_MS = 350;
const MAX_LINES = 6;
/** The browser's ear reopens this long after its own voice stops (the room's echo dies down). */
const EAR_REOPEN_MS = ECHO_TAIL_MS; // the contract's tail after TTS audio (v1.10 §5c)
/** After STOP, function calls from the response it cut off may still arrive: they are fenced this long. */
const STOP_FENCE_MS = 2000;
/** While a confirm lands (and this long after), a model start call is answered locally: no duplicate proposal. */
const CONFIRM_QUIET_MS = 5000;
/** A Confirm click this soon after the card changed is ignored (it was aimed at the card before). */
const CLICK_GUARD_MS = 600;
/** Realtime errors that end the session: fall back to browser speech. */
const FATAL_ERRORS = new Set(["session_expired", "session_closed", "invalid_session"]);
/** A response.create with no response.created after this long is treated as lost. */
const RESPONSE_STALE_MS = 15000;

type Link = "off" | "connecting" | "realtime" | "fallback";
type Flow = "listening" | "thinking" | "speaking" | "idle";
type Line = { who: "you" | "firm" | "alert"; text: string; n: number };
interface Card extends Proposal {
  expired: boolean;
  sending: boolean;
  /** A confirm was sent but its answer never came: it may have run. */
  unknown: boolean;
  /** When this card appeared (ms): a click right after a swap belongs to the card before. */
  shownAt: number;
}

/** Other agent audio on the console (the brief): CeoVoice counts it and mutes the mic meanwhile. */
export const AGENT_AUDIO_EVENT = "utax:agent-audio";
/** Other audio playing now, kept here too: a voice panel that mounts mid-brief still sees it. */
const otherPlaying = new Set<string>();
export const announceAgentAudio = (source: string, on: boolean) => {
  if (on) otherPlaying.add(source);
  else otherPlaying.delete(source);
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(AGENT_AUDIO_EVENT, { detail: { source, on } }));
};



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
  /** A 401: the session expired or was revoked. */
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
    case "console_only":
      return "That one can only be decided in the console. Use the lists below.";
    case "bad_answer":
      return "The gateway answered, but not in a way the console can read, so it may have run. Check the lists.";
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
const IGNORED =
  "That wasn't taken as a confirm: it came while U-Tax was talking, before the proposal was read out, or it wasn't clear. Say “confirm” on its own, or click Confirm.";

/** Refusals after which the action certainly did not run. */
const NOT_RUN = new Set(["expired", "superseded", "flagged", "console_only", "not_a_confirm", "not_found", "bad_args", "unknown_tool", "nothing_pending"]);

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
  /** When STOP last ran (ms): calls from the response it cut off are fenced for STOP_FENCE_MS. */
  const stoppedAt = useRef(0);
  /** When a confirm was last sent (ms), and while one is in flight. */
  const confirmingAt = useRef(0);
  /** Agent audio from elsewhere on the console (the brief) playing now. */
  const otherAudio = useRef(0);
  /** Cards whose confirm got no answer (it may have run): their expiry never says "nothing was done". */
  const unknownIds = useRef(new Set<string>());
  /** Cases seen flagged in any feed batch (until a log reset): never read out. */
  const seenFlagged = useRef(new Set<string>());
  /** Late-bound handlers for closures made before them (a fatal Realtime error, the other-audio listener). */
  const fatalRef = useRef<(why: string) => void>(() => {});
  const applyMicRef = useRef<() => void>(() => {});
  const stopListeningRef = useRef<() => void>(() => {});

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
  const send = useCallback((events: readonly object[]): boolean => {
    const dc = callRef.current?.dc;
    if (dc?.readyState !== "open") return false;
    for (const e of events) dc.send(JSON.stringify(e));
    return true;
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
    if (send([{ type: "response.create" }])) responding.current = Date.now();
  }, [send]);

  /** The mic track is live only while talking (push-to-talk) or hands-free, and never while the browser speaks. */
  const applyMic = useCallback(() => {
    const live = (micRef.current || handsRef.current) && speaking.current === 0 && otherAudio.current === 0;
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
  const speakRef = useRef<(text: string, onDone?: (played: boolean) => void) => void>(() => {});

  /** The firm's voice (v1.10 §5c): POST /api/tts (ElevenLabs), the browser's voice on a 503. */
  const speakerRef = useRef<Speaker | null>(null);
  const tokenRef = useRef(token);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);
  const speaker = useCallback(() => {
    speakerRef.current ??= new Speaker({ purpose: "ceo", api: API, token: () => tokenRef.current });
    return speakerRef.current;
  }, []);

  /**
   * Read a line aloud, queued. While anything plays, the browser's own ear is closed and the
   * Realtime mic is muted (played audio bypasses echo cancellation), and the time counts as agent
   * audio; listening comes back after the echo tail. `onDone(played)` runs when the line was heard
   * to the end (false when it was cut off, stopped or stalled).
   */
  const speak = useCallback(
    (text: string, onDone?: (played: boolean) => void) => {
      if (!text.trim()) {
        onDone?.(true);
        return;
      }
      if (!alive.current) {
        onDone?.(false); // the console is gone: nothing more is said
        return;
      }
      if (speaking.current === 0) vs.current = agentAudio(vs.current, "browser", true, Date.now());
      speaking.current += 1;
      applyMic();
      stopListening();
      if (linkRef.current === "fallback" && micRef.current && !handsRef.current) {
        micRef.current = false; // push-to-talk ended with the answer: the label must not say "Listening"
        setMicOn(false);
      }
      speaker().say(text, (played) => {
        speaking.current = Math.max(0, speaking.current - 1);
        if (speaking.current === 0) vs.current = agentAudio(vs.current, "browser", false, Date.now());
        applyMic();
        onDone?.(played);
        if (speaking.current === 0 && alive.current && linkRef.current === "fallback" && handsRef.current) {
          setTimeout(() => {
            if (alive.current && speaking.current === 0 && otherAudio.current === 0 && handsRef.current) listenRef.current();
          }, EAR_REOPEN_MS);
        }
      });
    },
    [applyMic, speaker, stopListening],
  );
  const hush = useCallback(() => {
    speakerRef.current?.stop(); // every queued line ends as not heard
    // ...and the brief, when the browser reads it (Ceo.tsx): STOP silences every voice on the console.
    if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
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
    if (recRef.current || speaking.current > 0 || otherAudio.current > 0 || linkRef.current !== "fallback" || deaf.current) return;
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
      if (isBlockedError(e.error)) {
        goDeaf("The microphone is blocked for this page. Type commands below; hands-free still reads alerts aloud.");
      } else if (!isQuietError(e.error)) {
        failed = true;
        earErrors.current += 1;
        if (earErrors.current >= MAX_EAR_FAILURES) goDeaf("The browser's speech recognition keeps failing. Type commands below; hands-free still reads alerts aloud.");
      }
    };
    r.onend = () => {
      if (recRef.current !== r) return;
      recRef.current = null;
      if (alive.current && handsRef.current && speaking.current === 0 && otherAudio.current === 0 && linkRef.current === "fallback" && !deaf.current) {
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
    const t = Date.now();
    setNow(t);
    setOutcome(null);
    setCard({ ...p, expired: false, sending: false, unknown: false, shownAt: t });
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
      const voiced = linkRef.current === "realtime" || linkRef.current === "fallback";
      const replaced = vs.current.proposal && vs.current.proposal.proposalId !== p.proposalId;
      vs.current = armProposal(vs.current, p, Date.now(), voiced ? "browser" : "none");
      showCard(p);
      addLine("firm", say);
      // No readback text in a system item: it can carry agent-written words (a post), never system authority.
      if (replaced) tellModel("The CEO typed a new command, so the earlier proposal was replaced. Only the newest card on the console can be confirmed now.", false);
      speak(say, (played) => {
        // Spoken confirms count only once the readback was actually heard (a cut-off reading doesn't arm it).
        if (played) vs.current = readbackHeard(vs.current, p.proposalId, Date.now());
      });
    },
    [addLine, showCard, speak, tellModel],
  );

  /** POST /api/ceo/confirm and report the outcome everywhere. The card was already claimed. */
  const send_confirm = useCallback(
    async (proposalId: string, decision: "confirm" | "cancel", via: "voice" | "click", heard: string) => {
      const epoch = stopEpoch.current;
      if (decision === "confirm") confirmingAt.current = Date.now(); // a cancel leaves nothing to duplicate
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
          let paused = true;
          try {
            await post("/ceo/tool", { name: STOP_EVERYTHING.name, args: STOP_EVERYTHING.args, call_id: `stop-${Date.now()}`, channel: "click" });
          } catch (e) {
            if (unauthorized(e)) return;
            paused = await authJson(token, `${API}/trust`, { method: "POST", body: JSON.stringify({ action_type: "*", state: "PAUSED" }) }).then(
              () => true,
              () => false,
            );
          }
          if (!alive.current) return;
          setStopNote(
            paused
              ? { ok: true, text: `A confirm landed during STOP (${text}), so everything was paused again.` }
              : { ok: false, text: `A confirm landed during STOP (${text}) and pausing again failed. Press STOP EVERYTHING again.` },
          );
          tellModel(`STOP EVERYTHING. A confirm landed at the same moment (${text}); ${paused ? "everything was paused again" : "the second pause failed"}.`, false);
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
          const unknown = code !== "not_a_confirm";
          if (unknown) unknownIds.current.add(proposalId);
          setCard((c) => (c && c.proposalId === proposalId ? { ...c, sending: false, unknown: c.unknown || unknown } : c));
        } else {
          closeCard(proposalId);
          refresh();
        }
        setOutcome({ ok: false, text: msg });
        tellModel(NOT_RUN.has(code) ? `Nothing was done. ${msg}` : `Outcome unknown. ${msg}`, epoch === stopEpoch.current);
        if (linkRef.current !== "realtime") speak(msg);
      }
    },
    [closeCard, post, refresh, speak, tellModel, token, unauthorized],
  );

  /**
   * Confirm or cancel the open card. `via: "voice"` only with the CEO's own words. `claimed`: the
   * Realtime reducer already claimed this card (its id); a click or typed words claim it here.
   */
  const settle = useCallback(
    (decision: "confirm" | "cancel", via: "voice" | "click", heard: string, claimed?: string, onScreen?: string) => {
      const c = claimCard(vs.current, decision, Date.now(), claimed, onScreen);
      if (!c) {
        if (onScreen !== undefined && vs.current.proposal && vs.current.proposal.proposalId !== onScreen)
          setOutcome({ ok: false, text: "That card changed just as you clicked. Check the new one, then click again." });
        return Promise.resolve();
      }
      vs.current = c.state;
      return send_confirm(c.proposal.proposalId, decision, via, heard);
    },
    [send_confirm],
  );

  /** A gateway answer: back to the model (Realtime), or read aloud (fallback and typing). */
  const handle = useCallback(
    (r: ToolResult, callId: string | null, tool: string | null, via: "voice" | "click", said = "") => {
      if (r.kind === "done") {
        setOutcome({ ok: true, text: r.summary });
        refresh();
      }
      if (callId) {
        if (r.kind === "proposal") {
          // The model reads it back; a spoken confirm waits until that audio has played in full.
          const replaced = vs.current.proposal && vs.current.proposal.proposalId !== r.proposalId;
          vs.current = armProposal(vs.current, { ...r, tool }, Date.now(), "model");
          showCard({ ...r, tool });
          send([functionOutputItem(callId, toolOutput(r))]);
          if (replaced) send([actionResultItem("The earlier proposal was replaced. Only the newest one (from the last tool result) can be confirmed now.")]);
          return;
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
        const open = vs.current.proposal;
        if (open && open.proposalId === r.proposalId) {
          // The card on screen already: keep its state (a confirm may be on its way); only learn it is an approve.
          if (isApproveIt(said) && !open.tool) vs.current = { ...vs.current, proposal: { ...open, tool: "approve" } };
          if (!open.settling) setOutcome({ ok: false, text: "Say “confirm”, or click Confirm on the card." });
          return;
        }
        // Never confirm a card the CEO hasn't seen: show it, and ask for a fresh confirm.
        // ("approve it" only ever matches an approve proposal: v1.8.4.) The gateway's own expiry may come sooner; its 409 expired rules.
        const p = { proposalId: r.proposalId, readback: r.readback || "The open proposal.", expiresAt: localExpiry(null, Date.now()), tool: isApproveIt(said) ? "approve" : null };
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
      const stoppedOut = (text: string) => send([functionOutputItem(c.callId, toolOutput({ kind: "error", code: "stopped", message: text }))]);
      if (Date.now() - stoppedAt.current < STOP_FENCE_MS) {
        // A call from the response STOP cut off: never sent on, and the model stays quiet.
        stoppedOut("The CEO pressed STOP EVERYTHING. Nothing new was started.");
        return;
      }
      if (CEO_TOOLS[c.name] === "start" && (vs.current.proposal?.settling || Date.now() - confirmingAt.current < CONFIRM_QUIET_MS)) {
        // The CEO's confirm is landing: a second proposal now would replace or duplicate it.
        send([functionOutputItem(c.callId, toolOutput({ kind: "error", code: "busy", message: "The CEO is confirming the open proposal. Wait for the action result; don't propose again." }))]);
        respond();
        return;
      }
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
        // STOP EVERYTHING came first: no card from it (and it is cancelled at the gateway), and the model stays quiet.
        if (result?.kind === "proposal") void post("/ceo/confirm", { proposal_id: result.proposalId, decision: "cancel", via: "click", heard: "" }).catch(() => {});
        stoppedOut("The CEO pressed STOP EVERYTHING. Nothing new was started.");
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
      const d = decideTyped(vs.current, t, Date.now(), via === "voice");
      if (d?.type === "ignored") {
        setOutcome({ ok: false, text: IGNORED });
        return;
      }
      if (d?.type === "confirm") {
        await settle(d.decision, via, d.heard);
        if (d.decision === "confirm" || isBareCancel(t)) return; // "stop everything" also goes on as a command
      } else if (vs.current.proposal) {
        // The card is settling or expired: a repeated confirm or a bare cancel goes nowhere, but
        // "stop everything" / "stop posting" are commands and must go through.
        const m = matchConfirm(t, vs.current.proposal);
        if (m === "confirm" || (m === "cancel" && isBareCancel(t))) return;
      }
      const epoch = stopEpoch.current;
      try {
        const r = await post("/ceo/command", { text: t.slice(0, 500) });
        if (!alive.current) return;
        if (epoch !== stopEpoch.current) {
          // STOP came while it was on its way: nothing new from it.
          if (r.kind === "proposal") void post("/ceo/confirm", { proposal_id: r.proposalId, decision: "cancel", via: "click", heard: "" }).catch(() => {});
          return;
        }
        handle(r, null, null, via, t);
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
            if (Date.now() - stoppedAt.current >= STOP_FENCE_MS) respond(); // after STOP the model stays quiet
            break;
          case "confirm":
            void settle(ef.decision, "voice", ef.heard, ef.proposalId);
            break;
          case "ignored":
            setOutcome({ ok: false, text: IGNORED });
            break;
          case "unread":
            setOutcome({ ok: false, text: "U-Tax didn't read the whole proposal aloud, so it can't be confirmed by voice yet. Click Confirm or type it, or ask U-Tax to read it again." });
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
            else responding.current = null; // whatever failed, it isn't a response running
            console.warn("[ceo voice] realtime error", ef.code, ef.message);
            if (FATAL_ERRORS.has(ef.code)) fatalRef.current("The voice session ended.");
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
      // A card the model never finished reading: the console reads it now, and only then can speech confirm it.
      const p = vs.current.proposal;
      if (p && p.heardAt === null && !p.settling) {
        vs.current = armProposal(vs.current, p, p.openedAt, "browser");
        speakRef.current(p.readback, (played) => {
          if (played) vs.current = readbackHeard(vs.current, p.proposalId, Date.now());
        });
      }
    },
    [go, hangUp],
  );
  useEffect(() => {
    fatalRef.current = toFallback;
  }, [toFallback]);

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
    dc.onclose = () => {
      if (!stale() && opened) toFallback("The voice line closed.");
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
      const before = vs.current.proposal;
      const r = voiceStep(vs.current, raw, Date.now());
      vs.current = r.state;
      const after = r.state.proposal;
      // The card was just read in full: an earlier "not read yet" notice no longer holds.
      if (before && after && before.proposalId === after.proposalId && before.heardAt === null && after.heardAt !== null) setOutcome(null);
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
    stoppedAt.current = Date.now();
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
      // A note about a confirm that landed during STOP outranks the plain "paused" one.
      setStopNote((n) => (n && n.text.startsWith("A confirm landed") ? n : { ok: true, text: `${summary} Resume each kind under Standing orders when you're ready.` }));
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

  // The console's brief (gateway audio or browser speech) is agent audio too: counted, and the mic muted meanwhile.
  useEffect(() => {
    const onAudio = (e: Event) => {
      const d = (e as CustomEvent<{ source?: unknown; on?: unknown }>).detail ?? {};
      const source = `other:${typeof d.source === "string" ? d.source : "audio"}`;
      const on = d.on === true;
      const was = vs.current.audio.playing.has(source);
      if (on === was) return;
      otherAudio.current = Math.max(0, otherAudio.current + (on ? 1 : -1));
      vs.current = agentAudio(vs.current, source, on, Date.now());
      applyMicRef.current();
      // The browser's own ear (fallback) closes too, and reopens a moment after the room is quiet.
      if (on) stopListeningRef.current();
      else if (otherAudio.current === 0 && linkRef.current === "fallback" && handsRef.current)
        setTimeout(() => {
          if (alive.current && otherAudio.current === 0 && speaking.current === 0 && handsRef.current) listenRef.current();
        }, EAR_REOPEN_MS);
    };
    for (const src of otherPlaying) onAudio(new CustomEvent(AGENT_AUDIO_EVENT, { detail: { source: src, on: true } }));
    window.addEventListener(AGENT_AUDIO_EVENT, onAudio);
    return () => window.removeEventListener(AGENT_AUDIO_EVENT, onAudio);
  }, []);

  // Hands-free: speak each new approval and BLOCKED case from the console's log poll.
  const lineRef = useRef(addLine);
  const closeRef = useRef(closeCard);
  const tellRef = useRef(tellModel);
  const noteRef = useRef((text: string) => {
    setOutcome({ ok: false, text });
    tellRef.current(`The open proposal is gone: ${text}`, false);
  });
  useEffect(() => {
    speakRef.current = speak;
    lineRef.current = addLine;
    closeRef.current = closeCard;
    applyMicRef.current = applyMic;
    stopListeningRef.current = stopListening;
  }, [speak, addLine, closeCard, applyMic, stopListening]);
  useEffect(() => {
    feedRef.current = (events, reset) => {
      const top = events.reduce((m, e) => (typeof e.id === "number" && e.id > m ? e.id : m), 0);
      if (reset) {
        closeRef.current(); // a reloaded log: the card belongs to the old one
        seenFlagged.current = new Set();
      }
      for (const e of events) if (e.type === "case.flagged" && e.case_id) seenFlagged.current.add(e.case_id);
      // The gateway closed the open card itself (a pause from any path, a newer proposal, a restart).
      const open = vs.current.proposal;
      for (const e of events) {
        const pid = typeof e.payload?.proposal_id === "string" ? e.payload.proposal_id : null;
        if (!open || pid !== open.proposalId || open.settling) continue;
        if (e.type === "ceo.cancelled" || e.type === "ceo.confirmed") {
          closeRef.current(open.proposalId);
          const why =
            e.type === "ceo.confirmed"
              ? unknownIds.current.has(open.proposalId)
                ? "went through after all (your earlier confirm landed)"
                : "was confirmed elsewhere"
              : "was cancelled by the gateway (a pause, a newer proposal, or a change)";
          noteRef.current(`That proposal ${why}. Nothing more to do on it here.`);
        }
      }
      if (seenId.current === null || reset) {
        seenId.current = top; // the first load, or a reloaded log: the backlog is never read out
        return;
      }
      if (handsRef.current) {
        const hidden = new Set([...flaggedRef.current, ...seenFlagged.current]);
        for (const line of announcements(events, seenId.current, namesRef.current, hidden)) {
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
        tellRef.current(unknownIds.current.has(cardId) ? "The proposal's time ran out. An earlier confirm may have gone through; the CEO should check the lists." : "The proposal expired. Nothing was done.");
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
      speakerRef.current?.stop();
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
              {card.unknown ? "Time ran out. An earlier confirm may have gone through: check the lists." : "Expired: nothing was done. Ask again if you still want it."}
            </p>
          ) : (
            <p className="how" id="pr-how">
              Say or type “confirm” or “cancel”, or click. <span className="mono" aria-hidden="true">{left}s</span>
              <span className="sr-only">{left > 10 ? "" : `${left} seconds left`}</span>
            </p>
          )}
          <div className="btns">
            {!card.expired && (
              <button
                className="primary"
                onClick={() => void settle("confirm", "click", "", undefined, card.proposalId)}
                disabled={card.sending || left === 0 || now - card.shownAt < CLICK_GUARD_MS}
              >
                {card.sending ? "Working…" : "Confirm"}
              </button>
            )}
            <button
              className="secondary"
              onClick={() => (card.expired ? closeCard(card.proposalId) : void settle("cancel", "click", "", undefined, card.proposalId))}
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
