// D33: the CEO voice loop guards (the owner heard orders "repeating the same thing").

import { describe, expect, it } from "vitest";
import { Speaker, type SpeakerDeps } from "@/lib/tts";
import { isFresh, parseToolResult, REPEAT_HEARD_MS, REPEAT_SAID_MS, sameWords, toolOutput } from "./voice";

describe("D33 voice loop: one utterance is one order, one line is said once", () => {
  it("the same final delivered twice (a re-delivery across a restart) passes once", () => {
    let last: { text: string; at: number } | null = null;
    const passed: string[] = [];
    // A scripted recognizer: the same final, twice, 200 ms apart; then the CEO says it again later.
    for (const [text, at] of [["Advertise our firm on X.", 1000], ["advertise our firm on x", 1200], ["Advertise our firm on X", 1000 + REPEAT_HEARD_MS + 500]] as const) {
      if (!isFresh(last, text, at, REPEAT_HEARD_MS)) continue;
      last = { text, at };
      passed.push(text);
    }
    expect(passed).toEqual(["Advertise our firm on X.", "Advertise our firm on X"]);
  });

  it("a different utterance right after always passes (confirm right after an order)", () => {
    expect(isFresh({ text: "hire 2 people", at: 1000 }, "confirm", 1100, REPEAT_HEARD_MS)).toBe(true);
    expect(isFresh(null, "confirm", 0, REPEAT_SAID_MS)).toBe(true);
  });

  it("words compare without case, punctuation or spacing", () => {
    expect(sameWords("Say confirm, or cancel.", "say  confirm or cancel")).toBe(true);
    expect(sameWords("confirm", "cancel")).toBe(false);
  });

  it("v1.13 {kind: say} parses, and tells the model only what to say", () => {
    expect(parseToolResult({ kind: "say", say: "Say confirm or cancel." }, 0)).toEqual({ kind: "say", say: "Say confirm or cancel." });
    expect(parseToolResult({ kind: "say" }, 0)).toEqual({ kind: "say", say: "Say confirm or cancel." });
    expect(toolOutput({ kind: "say", say: "I'm listening. Tell me an order." })).toBe(JSON.stringify({ ok: false, say: "I'm listening. Tell me an order." }));
  });

  it("a TTS end that fires twice finishes the line once and starts the next line once", async () => {
    const ends: ((ok: boolean) => void)[] = [];
    const spoken: string[] = [];
    const deps: SpeakerDeps = {
      fetch: (async () => new Response(null, { status: 503 })) as unknown as typeof fetch,
      playBlob: async () => true,
      // The browser voice: its end handler is called twice (a double onend).
      synth: (text) =>
        new Promise((resolve) => {
          spoken.push(text);
          ends.push((ok) => {
            resolve(ok);
            resolve(ok);
          });
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
    expect(spoken).toEqual(["One.", "Two."]);
  });
});

describe("D33: a spoken stop always takes the STOP path", () => {
  it("recognizes stop/pause everything, and nothing else", async () => {
    const { isStopEverything } = await import("./voice");
    for (const t of ["Stop everything", "stop everything!", "please pause everything", "halt all of it", "Stop the firm."]) expect(isStopEverything(t)).toBe(true);
    for (const t of ["stop posting", "don't stop everything is fine", "confirm", "everything stop"]) expect(isStopEverything(t)).toBe(false);
  });
});
