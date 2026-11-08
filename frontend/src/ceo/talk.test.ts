// D34 (contract v1.14 §5a-ter): the CEO conversation's replies.

import { describe, expect, it } from "vitest";
import { parseTalkReply, replyLine, undoLine } from "./talk";

describe("D34 talk replies", () => {
  it("a done turn: what the assistant said and did, with undo ids where they exist", () => {
    const r = parseTalkReply({
      conversation_id: "conv_1",
      say: "On it. Told the Chief of Staff: hire 2 people.",
      actions: [
        { tool: "order", summary: "Told the Chief of Staff: hire 2 people", undo_id: "undo_7" },
        { tool: "approve", summary: "Approved the memo for Team #7" },
      ],
    });
    expect(r).toEqual({
      conversationId: "conv_1",
      say: "On it. Told the Chief of Staff: hire 2 people.",
      actions: [
        { tool: "order", summary: "Told the Chief of Staff: hire 2 people", undoId: "undo_7" },
        { tool: "approve", summary: "Approved the memo for Team #7", undoId: null },
      ],
      pending: false,
    });
  });

  it("a pending turn is polled, and says it is still working", () => {
    expect(parseTalkReply({ conversation_id: "conv_1", say: "Still working on it…", pending: true })).toMatchObject({ pending: true, conversationId: "conv_1" });
    expect(parseTalkReply({ conversation_id: "conv_1", pending: true })?.say).toBe("Still working on it…");
  });

  it("a stop with no conversation yet still reads (conversation_id null)", () => {
    expect(parseTalkReply({ conversation_id: null, say: "Paused everything.", actions: [{ tool: "pause", summary: "Paused everything.", undo_id: "u1" }] })?.conversationId).toBeNull();
  });

  it("wire text is plain, one line and bounded; odd actions and odd undo ids are dropped", () => {
    const r = parseTalkReply({
      say: "line one\n\nline two",
      actions: [null, { summary: "" }, { tool: "x", summary: "ok", undo_id: "bad id with spaces" }, ...Array.from({ length: 20 }, (_, i) => ({ tool: "t", summary: `a${i}` }))],
    });
    expect(r?.say).toBe("line one line two");
    expect(r?.actions[0]).toEqual({ tool: "x", summary: "ok", undoId: null });
    expect(r?.actions.length).toBeLessThanOrEqual(12);
    expect(parseTalkReply({ say: "x".repeat(5000) })?.say.length).toBeLessThanOrEqual(1500);
  });

  it("nothing usable is no reply", () => {
    for (const raw of [null, [], "text", {}, { say: "" }, { actions: [] }]) expect(parseTalkReply(raw)).toBeNull();
  });

  it("what the CEO hears: the words, else the actions", () => {
    expect(replyLine({ conversationId: null, say: "Done that.", actions: [], pending: false })).toBe("Done that.");
    expect(replyLine({ conversationId: null, say: "", actions: [{ tool: "pause", summary: "Paused posting.", undoId: null }], pending: false })).toBe("Paused posting.");
    expect(undoLine({ kind: "done", summary: "Resumed social posts." })).toBe("Resumed social posts.");
    expect(undoLine({})).toBe("Undone.");
  });
});

describe("D34 review: long answers", () => {
  it("reads whole sentences up to the cap; the rest stays on screen", async () => {
    const { spokenPart } = await import("./talk");
    expect(spokenPart("Short one.")).toBe("Short one.");
    const long = `${"First sentence here. ".repeat(10)}${"x".repeat(400)}`;
    const said = spokenPart(long, 120);
    expect(said.length).toBeLessThanOrEqual(120);
    expect(said.endsWith(".")).toBe(true);
    expect(spokenPart("y".repeat(500), 100)).toHaveLength(100);
  });
});
