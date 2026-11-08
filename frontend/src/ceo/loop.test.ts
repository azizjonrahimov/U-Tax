// D33: the CEO voice loop guards (the owner heard orders "repeating the same thing").

import { describe, expect, it } from "vitest";
import { Speaker, type SpeakerDeps } from "@/lib/tts";
import {
  earFinals,
  ECHO_WINDOW_MS,
  isStopCommand,
  isStopEverything,
  newEarMemory,
  newEarSession,
  parseToolResult,
  rememberSaid,
  REPEAT_HEARD_MS,
  REPEAT_SAID_MS,
  sameWords,
  shouldSay,
  toolOutput,
} from "./voice";

const fin = (index: number, text: string) => ({ index, text, isFinal: true });

describe("D33 ear: one utterance is one order", () => {
  it("the same final re-delivered at the same index in one session passes once", () => {
    const mem = newEarMemory();
    const s = newEarSession(0);
    expect(earFinals([fin(0, "hire 2 people")], s, mem, 100).take).toEqual(["hire 2 people"]);
    expect(earFinals([fin(0, "hire 2 people")], s, mem, 150)).toEqual({ take: [], dropped: [] });
  });

  it("the same words again at a new index right away are a repeat, dropped and reported", () => {
    const mem = newEarMemory();
    const s = newEarSession(0);
    earFinals([fin(0, "hire 2 people")], s, mem, 100);
    expect(earFinals([fin(0, "hire 2 people"), fin(1, "Hire 2 people.")], s, mem, 250)).toEqual({
      take: [],
      dropped: [{ text: "Hire 2 people.", why: "repeat" }],
    });
  });

  it("a re-delivery across a restart counts from the new session's start, not the first delivery", () => {
    const mem = newEarMemory();
    earFinals([fin(0, "advertise our firm on x")], newEarSession(0), mem, 100);
    // The console spoke for 5 s, then the ear reopened at 6000 and got the old final again.
    const later = newEarSession(6000);
    expect(earFinals([fin(0, "Advertise our firm on X.")], later, mem, 6300).take).toEqual([]);
  });

  it("the CEO saying it again later is a new order", () => {
    const mem = newEarMemory();
    earFinals([fin(0, "advertise our firm on x")], newEarSession(0), mem, 100);
    const s = newEarSession(1000);
    expect(earFinals([fin(0, "advertise our firm on x")], s, mem, 1000 + REPEAT_HEARD_MS + 500).take).toEqual(["advertise our firm on x"]);
  });

  it("a browser that reuses index 0 for each new final never loses the new utterance", () => {
    const mem = newEarMemory();
    const s = newEarSession(0);
    earFinals([fin(0, "hire 2 people")], s, mem, 100);
    expect(earFinals([fin(0, "confirm")], s, mem, 3000).take).toEqual(["confirm"]);
  });

  it("a different utterance right after always passes (confirm right after an order)", () => {
    const mem = newEarMemory();
    const s = newEarSession(0);
    earFinals([fin(0, "hire 2 people")], s, mem, 100);
    expect(earFinals([fin(1, "confirm")], s, mem, 200).take).toEqual(["confirm"]);
  });

  it("the console's own recent line coming back through the mic is dropped as an echo", () => {
    const mem = newEarMemory();
    rememberSaid(mem, "Say confirm or cancel.", 1000);
    rememberSaid(mem, "I'm listening. Tell me an order.", 1100);
    const s = newEarSession(2000);
    expect(earFinals([fin(0, "say confirm or cancel")], s, mem, 2500).dropped).toEqual([{ text: "say confirm or cancel", why: "echo" }]);
    expect(earFinals([fin(1, "tell me an order")], s, mem, 2600).dropped[0]?.why).toBe("echo");
    // A short real word is not an echo just because a line contained it.
    expect(earFinals([fin(2, "cancel")], s, mem, 2700).take).toEqual(["cancel"]);
    // Past the window it is the CEO talking.
    expect(earFinals([fin(3, "say confirm or cancel")], s, mem, 1000 + ECHO_WINDOW_MS + 1).take).toEqual(["say confirm or cancel"]);
  });

  it("words compare without case, punctuation or spacing", () => {
    expect(sameWords("Say confirm, or cancel.", "say  confirm or cancel")).toBe(true);
    expect(sameWords("confirm", "cancel")).toBe(false);
  });
});

describe("D33 speaker: a reply is said once, and nothing that arms a confirm is skipped", () => {
  it("the same reply to the same command, just said, is not said again", () => {
    const last = { key: "reply:hello", text: "I'm listening.", at: 1000 };
    expect(shouldSay(last, "reply:hello", "I'm listening.", 1000 + REPEAT_SAID_MS - 1)).toBe(false);
    expect(shouldSay(last, "reply:hello", "I'm listening.", 1000 + REPEAT_SAID_MS + 1)).toBe(true);
  });

  it("lines without a key (alerts, readbacks) are always said, even with the same words", () => {
    const last = { key: "reply:x", text: "Approval needed: social post for a case.", at: 1000 };
    expect(shouldSay(last, undefined, "Approval needed: social post for a case.", 1001)).toBe(true);
  });

  it("another command's reply with the same words is said", () => {
    expect(shouldSay({ key: "reply:a", text: "Done.", at: 1000 }, "reply:b", "Done.", 1001)).toBe(true);
    expect(shouldSay(null, "reply:a", "Done.", 0)).toBe(true);
  });

  it("a TTS end that fires twice finishes the line once and starts the next line once", async () => {
    const ends: ((ok: boolean) => void)[] = [];
    const spoken: string[] = [];
    const deps: SpeakerDeps = {
      fetch: (async () => new Response(null, { status: 503 })) as unknown as typeof fetch,
      playBlob: async () => true,
      synth: (text) =>
        new Promise((resolve) => {
          spoken.push(text);
          ends.push((ok) => resolve(ok));
        }),
      now: () => 0,
      setTimeout: () => 0,
      clearTimeout: () => {},
    };
    const s = new Speaker({ purpose: "ceo", api: "/api", deps });
    const done: string[] = [];
    s.say("One.", () => done.push("one"));
    s.say("Two.", () => done.push("two"));
    const settle = async () => {
      for (let i = 0; i < 6; i++) await Promise.resolve();
    };
    await settle();
    ends[0](true);
    ends[0](true); // the second end of the same line
    await settle();
    expect(done).toEqual(["one"]);
    expect(spoken).toEqual(["One.", "Two."]);
    ends[1](true);
    await settle();
    expect(done).toEqual(["one", "two"]);
  });
});

describe("D33: v1.13 say, and a spoken stop", () => {
  it("{kind: say} parses, falls back to a neutral line, and tells the model only what to say", () => {
    expect(parseToolResult({ kind: "say", say: "Say confirm or cancel." }, 0)).toEqual({ kind: "say", say: "Say confirm or cancel." });
    expect(parseToolResult({ kind: "say" }, 0)).toEqual({ kind: "say", say: "I'm listening." });
    expect(toolOutput({ kind: "say", say: "I'm listening. Tell me an order." })).toBe(JSON.stringify({ ok: false, say: "I'm listening. Tell me an order." }));
  });

  it("the whole utterance 'stop everything', fillers allowed, takes the STOP path", () => {
    for (const t of ["Stop everything", "stop everything!", "please pause everything", "halt all of it", "Stop the firm.", "okay, stop everything", "hey stop everything", "U-Tax, pause everything", "stop everything now"])
      expect(isStopEverything(t)).toBe(true);
  });

  it("scoped pauses and other words never pause the whole firm", () => {
    for (const t of ["stop posting", "stop all posts", "pause all social posts", "stop all hiring", "halt the firm's hiring", "stop the firm from posting", "stop everything except payroll", "don't stop everything is fine", "confirm", "everything stop"])
      expect(isStopEverything(t)).toBe(false);
  });

  it("stop-kind commands are recognized (they cancel the open card under v1.13.1)", () => {
    for (const t of ["stop posting", "okay, pause emails", "halt hiring"]) expect(isStopCommand(t)).toBe(true);
    for (const t of ["don't stop", "hire 2 people", "unpause posting"]) expect(isStopCommand(t)).toBe(false);
  });
});
