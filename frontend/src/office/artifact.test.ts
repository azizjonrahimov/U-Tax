// D37 (contract v1.18): each department's window shows the artifact being made, live. Fixtures use
// the real gateway's shapes: utax.-prefixed tool names, activity without a case_id, `writing` as the
// agent's narration, doc.step with a numeric step and whole sheets of cells, all in one burst.

import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import { applyStudio, bareTool, current, emptyStudio, seatOf, STUDIO_ONLY, type Studio, type StudioCtx } from "./artifact";
import type { Seat } from "./workspace";

let id = 0;
const ev = (type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
  id: ++id, ts: `2026-11-08T10:00:${String(id % 60).padStart(2, "0")}.000Z`, type, actor: "system", case_id: null, provider: null, status: "ok", payload, tokens_in: null, tokens_out: null, cost_usd: null, ...extra,
});
const act = (seat: Seat, kind: string, extra: Record<string, unknown> = {}) => ev("agent.activity", { agent: `utax-${seat}`, session_id: "ses_1", kind, ...extra }, { actor: seat });
const flagged = new Set<string>();
const flaggedSeats = new Set<Seat>();
const ctx: StudioCtx = {
  flagged: (c) => c !== null && flagged.has(c),
  seatFlagged: (s) => flaggedSeats.has(s),
  name: () => "Team #7",
  image: (u) => (typeof u === "string" && u.startsWith("https://gw.example/") ? u : undefined),
};
const fold = (events: UTaxEvent[], st: Studio = emptyStudio()) => events.reduce((s, e) => applyStudio(s, e, ctx), st);

describe("D37 live artifacts", () => {
  it("a tool opens its app at once (utax.-prefixed names), and the real mail fills it, attachments included", () => {
    expect(bareTool("utax.send_memo")).toBe("send_memo");
    let st = fold([
      ev("doc.created", { doc: "workpaper", file_name: "Delaware_Franchise_Tax_Workpaper_c7_v1.xlsx" }, { actor: "acct", case_id: "c7" }),
      ev("doc.created", { doc: "memo", file_name: "Franchise_Tax_Memo_c7_v1.docx" }, { actor: "acct", case_id: "c7" }),
      ev("doc.created", { doc: "books", file_name: "U-Tax_Books.xlsx" }, { actor: "acct", case_id: "c7" }),
      act("acct", "tool_started", { tool: "utax.send_memo" }),
    ]);
    expect(st.desks.acct.art).toMatchObject({ kind: "mail", state: "drafting", body: "" });
    expect(st.desks.acct.act).toMatchObject({ kind: "tool", tool: "send_memo" });
    expect(st.lastSeat).toBe("acct");
    const draftKey = st.desks.acct.art?.key;
    st = fold([ev("memo.sent", { to_masked: "f***@team7.dev", subject: "Your estimate", body: "(masked)", memo_ref: "UTX-1", sent: { to_masked: "f***@team7.dev", subject: "Your Delaware franchise tax estimate", body: "Dear Team #7,\n\nYou owe $450." } }, { actor: "acct", case_id: "c7" })], st);
    expect(st.desks.acct.art).toMatchObject({
      kind: "mail",
      state: "done",
      key: draftKey, // the composer becomes the mail: no restart
      subject: "Your Delaware franchise tax estimate",
      body: "Dear Team #7,\n\nYou owe $450.",
      attachments: ["Delaware_Franchise_Tax_Workpaper_c7_v1.xlsx", "Franchise_Tax_Memo_c7_v1.docx"], // never the firm's books
    });
  });

  it("writing is the agent's narration: notes on the desk, never an email, never over a finished artifact", () => {
    let st = fold([ev("client.followed_up", { kind: "memo_check", sent: { subject: "Did the memo make sense?", body: "Hi Team #7, checking in.", to_masked: "f***@team7.dev" } }, { actor: "intake", case_id: "c7" })]);
    st = fold([act("intake", "writing", { text: "Sent the follow-up to Team #7; next I'll book the call." })], st);
    expect(st.desks.intake.art).toMatchObject({ kind: "mail", state: "done", subject: "Did the memo make sense?" });
    expect(st.desks.intake.notes?.text).toBe("Sent the follow-up to Team #7; next I'll book the call.");
    expect(fold([act("cos", "writing", { text: "Routing the order." })]).desks.cos.art).toBeNull();
  });

  it("a public reader: masked public fields, or no mail at all (the step page says it)", () => {
    const pub = fold([ev("memo.sent", { to_masked: "f***@team7.dev", subject: "Your estimate", body: "Dear [client], you owe $450." }, { actor: "acct", case_id: "c7" })]);
    expect(pub.desks.acct.art).toMatchObject({ kind: "mail", subject: "Your estimate", body: "Dear [client], you owe $450." });
    expect(fold([ev("client.followed_up", { kind: "memo_check" }, { actor: "intake", case_id: "c2" })]).desks.intake.art).toBeNull();
  });

  it("flagged: a later flag clears that case's mail and every agent's notes; cells and notes of a flagged desk never show", () => {
    let st = fold([
      ev("client.asked", { missing: ["shares"], sent: { subject: "Re: IGNORE ALL", body: "EVIL BODY", to_masked: "x***@evil.dev" } }, { actor: "intake", case_id: "c5" }),
      act("intake", "writing", { text: "The client says IGNORE ALL PREVIOUS" }),
    ]);
    expect(JSON.stringify(st.desks.intake)).toMatch(/EVIL/);
    flagged.add("c5");
    st = fold([ev("case.flagged", { kind: "possible_injection" }, { actor: "qa", case_id: "c5" })], st);
    expect(JSON.stringify(st.desks)).not.toMatch(/EVIL|IGNORE/);
    flaggedSeats.add("intake");
    st = fold([act("intake", "writing", { text: "IGNORE again" })], st);
    expect(st.desks.intake.notes).toBeNull();
    const cells = fold([ev("doc.step", { doc: "workpaper", step: 1, of: 5, detail: "Inputs: 6 facts, each with the client's own words", cells: [{ sheet: "Inputs", cell: "C2", value: "EVIL QUOTE" }] }, { actor: "acct", case_id: "c5" })]);
    expect(JSON.stringify(cells.desks.acct.art)).not.toMatch(/EVIL/);
    flagged.delete("c5");
    flaggedSeats.delete("intake");
  });

  it("the documents burst plays as one artifact: the workpaper grid, the memo sections, the books row, each verified", () => {
    const sheet = (n: number, title: string, rows: [string, string][]) =>
      ev("doc.step", { doc: "workpaper", step: n, of: 5, detail: `${title}: …`, cells: rows.map(([cell, value]) => ({ sheet: title, cell, value })) }, { actor: "acct", case_id: "c7" });
    const st = fold([
      act("acct", "tool_started", { tool: "utax.prepare_documents" }),
      sheet(1, "Inputs", [["A1", "Fact"], ["B1", "Value"], ["A2", "Authorized shares"], ["B2", "10,000,000"]]),
      sheet(2, "Authorized Shares Method", [["A1", "Tax"], ["B1", "$85,165"]]),
      ev("doc.created", { doc: "workpaper", file_name: "W.xlsx" }, { actor: "acct", case_id: "c7" }),
      ev("doc.step", { doc: "memo", step: 1, of: 3, detail: "Memo: summary ($450 instead of $85,165)" }, { actor: "acct", case_id: "c7" }),
      ev("doc.step", { doc: "memo", step: 2, of: 3, detail: "Memo: both methods as tables, and why the lower one" }, { actor: "acct", case_id: "c7" }),
      ev("doc.step", { doc: "books", step: 1, of: 1, detail: "Books: Team #7, fee $0, tax saved $84,715", cells: [{ sheet: "Books", cell: "A5", value: "c7" }] }, { actor: "acct", case_id: "c7" }),
      ev("doc.verified", { doc: "workpaper", ok: true }, { actor: "acct", case_id: "c7" }),
      ev("doc.verified", { doc: "memo", ok: true }, { actor: "acct", case_id: "c7" }),
      ev("doc.verified", { doc: "books", ok: true }, { actor: "acct", case_id: "c7" }),
    ]);
    const art = st.desks.acct.art;
    if (art?.kind !== "docs") throw new Error("expected the documents");
    expect(art.state).toBe("done");
    expect(art.workpaper).toMatchObject({ file: "W.xlsx", sheets: ["Inputs", "Authorized Shares Method"], verified: true });
    expect(art.workpaper?.cells.Inputs.B2).toBe("10,000,000");
    expect(art.memo?.sections).toEqual(["summary ($450 instead of $85,165)", "both methods as tables, and why the lower one"]);
    expect(art.books?.verified).toBe(true);
    // Playback order: the 6 workpaper cells, the 2 memo sections, the books cell.
    expect(art.units.map((u) => u.doc)).toEqual(["workpaper", "workpaper", "workpaper", "workpaper", "workpaper", "workpaper", "memo", "memo", "books"]);
  });

  it("a resent sheet only plays the cells that changed; a public reader gets one line per step", () => {
    const first = ev("doc.step", { doc: "workpaper", step: 1, of: 5, detail: "Inputs: x", cells: [{ sheet: "Inputs", cell: "A1", value: "Fact" }] }, { actor: "acct", case_id: "c7" });
    const again = ev("doc.step", { doc: "workpaper", step: 2, of: 5, detail: "Inputs: y", cells: [{ sheet: "Inputs", cell: "A1", value: "Fact" }, { sheet: "Inputs", cell: "A2", value: "Shares" }] }, { actor: "acct", case_id: "c7" });
    const art = fold([first, again]).desks.acct.art;
    expect(art?.kind === "docs" ? art.units.length : -1).toBe(2);
    const pub = fold([ev("doc.step", { doc: "workpaper", step: 2, of: 5, detail: "Authorized Shares Method: 10,000,000 shares → $85,165" }, { actor: "acct", case_id: "c7" })]).desks.acct.art;
    expect(pub?.kind === "docs" ? pub.workpaper?.lines : []).toEqual([{ tab: "Authorized Shares Method", text: "10,000,000 shares → $85,165" }]);
  });

  it("a post types in, then publishes with an allowed image only", () => {
    const st = fold([
      act("mkt", "tool_started", { tool: "utax.post_social" }),
      ev("post.published", { channel: "linkedin", text: "Delaware franchise tax, done in minutes.", url: "https://linkedin.com/x", image_url: "https://evil.example/a.png" }, { actor: "mkt" }),
    ]);
    expect(st.desks.mkt.art).toMatchObject({ kind: "post", channel: "linkedin", state: "done", image: null });
    expect(st.desks.mkt.act).toBeNull(); // the post is out: no stale "using post_social"
  });

  it("the tax engine shows both methods with the real numbers, the filed one marked; bad cents never throw", () => {
    const st = fold([ev("tax.computed", { authorized_method_cents: 8516500, assumed_par_method_cents: 40000, total_cents: 45000, chosen: "assumed_par" }, { actor: "tax", case_id: "c7" })]);
    expect(st.desks.tax.art).toMatchObject({ kind: "calc", lines: [{ value: "$85,165", chosen: false }, { value: "$400", chosen: true }, { label: "Total due for Team #7" }] });
    expect(fold([ev("tax.computed", { authorized_method_cents: 1.5, assumed_par_method_cents: 1, total_cents: 1 }, { actor: "tax" })]).desks.tax.art).toBeNull();
  });

  it("activity: board chatter never moves auto-follow; idle closes an empty composer; old activity isn't current", () => {
    const chat = fold([act("cos", "tool_started", { tool: "utax.board_read" }), act("cos", "thinking")]);
    expect(chat.lastSeat).toBeNull();
    let st = fold([act("hr", "tool_started", { tool: "utax.hr_outreach" })]);
    expect(st.desks.hr.art?.kind).toBe("mail");
    st = fold([act("hr", "idle")], st);
    expect(st.desks.hr.art).toBeNull();
    expect(st.desks.hr.act).toBeNull();
    expect(current("2026-11-08T10:00:00.000Z", Date.parse("2026-11-08T10:00:10.000Z"))).toBe(true);
    expect(current("2026-11-08T10:00:00.000Z", Date.parse("2026-11-08T10:05:00.000Z"))).toBe(false);
    expect(seatOf(ev("agent.activity", { agent: "u-tax-marketing" }))).toBe("mkt");
    expect(fold([ev("agent.activity", { kind: "thinking" }, { actor: "ceo" })]).desks).toEqual(emptyStudio().desks);
    expect(STUDIO_ONLY.has("agent.activity")).toBe(true);
  });
});

describe("D37 red team: the real gateway's orderings and failures", () => {
  it("a tool that produced nothing closes its composer on tool_done; a late tool_started never wipes what it just finished", () => {
    let st = fold([act("mkt", "tool_started", { tool: "utax.post_social" }), act("mkt", "tool_done", { tool: "utax.post_social" })]);
    expect(st.desks.mkt.art).toBeNull();
    st = fold([
      ev("memo.sent", { sent: { subject: "Your estimate", body: "Dear Team #7", to_masked: "f***@t.dev" } }, { actor: "acct", case_id: "c7" }),
      act("acct", "tool_started", { tool: "utax.send_memo" }), // logged after the tool's own result
    ]);
    expect(st.desks.acct.art).toMatchObject({ kind: "mail", state: "done", subject: "Your estimate" });
    expect(st.lastSeat).toBeNull();
  });

  it("a post the gateway logged as failed is never 'Posted'", () => {
    const st = fold([ev("post.published", { channel: "x", text: "Hello", url: null, image_url: null }, { actor: "mkt", status: "failed" })]);
    expect(st.desks.mkt.art).toMatchObject({ kind: "post", state: "failed" });
  });

  it("a new build (v2) starts fresh documents and un-verifies until its own checks pass", () => {
    const step1 = (n: number) => ev("doc.step", { doc: "workpaper", step: 1, of: 5, detail: `Inputs: v${n}`, cells: [{ sheet: "Inputs", cell: "A1", value: `v${n}` }] }, { actor: "acct", case_id: "c7" });
    let st = fold([step1(1), ev("doc.verified", { doc: "workpaper", ok: true }, { actor: "acct", case_id: "c7" })]);
    const v1 = st.desks.acct.art?.key;
    st = fold([step1(2)], st);
    const art = st.desks.acct.art;
    expect(art?.key).not.toBe(v1);
    expect(art?.kind === "docs" ? [art.workpaper?.verified, art.state, art.workpaper?.cells.Inputs.A1] : []).toEqual([null, "drafting", "v2"]);
  });
});

describe("v1.18.2 cell formats", () => {
  it("money to the cent, counts grouped, par at full precision, text as written", async () => {
    const { cellText } = await import("./artifact");
    expect(cellText(85165, "usd")).toBe("$85,165.00");
    expect(cellText(400.5, "usd")).toBe("$400.50");
    expect(cellText(10000000, "int")).toBe("10,000,000");
    expect(cellText(0.00001, "par")).toBe("0.00001");
    expect(cellText(0.0625, "par")).toBe("0.0625");
    expect(cellText("From the client's email", "text")).toBe("From the client's email");
    expect(cellText(null, "usd")).toBe("");
  });
});

describe("v1.18.2 money at full precision", () => {
  it("keeps the gateway's digits, never shows -$0.00", async () => {
    const { cellText } = await import("./artifact");
    expect(cellText(1000000.004, "usd")).toBe("$1,000,000.004");
    expect(cellText(-0, "usd")).toBe("$0.00");
    expect(cellText(-1234.5, "usd")).toBe("-$1,234.50");
  });
});

describe("money without float noise", () => {
  it("0.1 + 0.2 is $0.30; real extra digits stay", async () => {
    const { cellText } = await import("./artifact");
    expect(cellText(0.1 + 0.2, "usd")).toBe("$0.30");
    expect(cellText(1234.5600000000002, "usd")).toBe("$1,234.56");
    expect(cellText(625000.0625, "usd")).toBe("$625,000.0625");
  });
});
