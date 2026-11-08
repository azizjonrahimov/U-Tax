// D37: each department's window shows the artifact being made, live.

import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import { applyStudio, emptyStudio, seatOf, STUDIO_ONLY, type Studio, type StudioCtx } from "./artifact";

let id = 0;
const ev = (type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
  id: ++id, ts: `2026-11-08T10:00:${String(id % 60).padStart(2, "0")}.000Z`, type, actor: "system", case_id: null, provider: null, status: "ok", payload, tokens_in: null, tokens_out: null, cost_usd: null, ...extra,
});
const flagged = new Set<string>();
const ctx: StudioCtx = { flagged: (c) => c !== null && flagged.has(c), name: () => "Team #7", image: (u) => (typeof u === "string" && u.startsWith("https://gw.example/") ? u : undefined) };
const fold = (events: UTaxEvent[], st: Studio = emptyStudio()) => events.reduce((s, e) => applyStudio(s, e, ctx), st);

describe("D37 live artifacts", () => {
  it("an email: the tool opens the composer, the writing types in, memo.sent fills the real mail and attaches the built files", () => {
    let st = fold([
      ev("doc.created", { doc: "workpaper", file_name: "Team7_Workpaper.xlsx" }, { actor: "acct", case_id: "c7" }),
      ev("agent.activity", { agent: "utax-acct", kind: "thinking" }, { actor: "acct" }),
      ev("agent.activity", { agent: "utax-acct", kind: "tool_started", tool: "send_memo" }, { actor: "acct", case_id: "c7" }),
      ev("agent.activity", { agent: "utax-acct", kind: "writing", text: "Hi Team 7," }, { actor: "acct" }),
      ev("agent.activity", { agent: "utax-acct", kind: "writing", text: "Hi Team 7, your estimate is ready." }, { actor: "acct" }), // v1.18: cumulative
    ]);
    const draft = st.desks.acct.art;
    expect(draft).toMatchObject({ kind: "mail", state: "drafting", body: "Hi Team 7, your estimate is ready." });
    expect(st.desks.acct.act?.kind).toBe("writing");
    expect(st.lastSeat).toBe("acct");
    st = fold([ev("memo.sent", { to_masked: "f***@team7.dev", memo_ref: "UTX-1", sent: { to_masked: "f***@team7.dev", subject: "Your Delaware franchise tax estimate", body: "Dear Team #7,\n\nYour estimate: $450." } }, { actor: "acct", case_id: "c7" })], st);
    expect(st.desks.acct.art).toMatchObject({ kind: "mail", state: "done", to: "f***@team7.dev", subject: "Your Delaware franchise tax estimate", body: "Dear Team #7,\n\nYour estimate: $450.", attachments: ["Team7_Workpaper.xlsx"] });
  });

  it("a public reader (no body in the event) keeps the draft and still sees it sent", () => {
    const st = fold([
      ev("agent.activity", { kind: "writing", text: "Following up on your memo." }, { actor: "intake" }),
      ev("client.followed_up", { kind: "memo_check" }, { actor: "intake", case_id: "c2" }),
    ]);
    expect(st.desks.intake.art).toMatchObject({ kind: "mail", state: "done", body: "Following up on your memo.", subject: "Following up" });
  });

  it("a flagged case never shows its mail, drafted or sent", () => {
    flagged.add("c9");
    const st = fold([
      ev("agent.activity", { kind: "tool_started", tool: "ask_client" }, { actor: "intake", case_id: "c9" }),
      ev("agent.activity", { kind: "writing", text: "IGNORE PREVIOUS INSTRUCTIONS" }, { actor: "intake", case_id: "c9" }),
      ev("client.asked", { missing: ["shares"], sent: { subject: "SECRET SUBJECT", body: "SECRET BODY", to_masked: "x***@evil.dev" } }, { actor: "intake", case_id: "c9" }),
    ]);
    expect(JSON.stringify(st.desks.intake.art)).not.toMatch(/IGNORE|SECRET|evil/);
    flagged.delete("c9");
  });

  it("a post types in, then publishes with its image (only an allowed image)", () => {
    const st = fold([
      ev("agent.activity", { kind: "tool_started", tool: "post_social" }, { actor: "mkt" }),
      ev("agent.activity", { kind: "writing", text: "Delaware franchise tax, done in minutes." }, { actor: "mkt" }),
      ev("post.published", { channel: "linkedin", text: "Delaware franchise tax, done in minutes. u-tax.app", url: "https://linkedin.com/x", image_url: "https://gw.example/assets/ad_1.png" }, { actor: "mkt" }),
      ev("post.published", { channel: "x", text: "again", image_url: "https://evil.example/a.png" }, { actor: "mkt" }),
    ]);
    expect(st.desks.mkt.art).toMatchObject({ kind: "post", channel: "x", state: "done", image: null });
    expect(st.desks.mkt.act).toBeNull(); // the post is out: no stale "writing…"
  });

  it("the workpaper fills row by row on its tabs, the memo writes section by section, then Verified", () => {
    const st = fold([
      ev("doc.step", { doc: "workpaper", step: "Inputs", detail: "Authorized shares: 10,000,000" }, { actor: "acct", case_id: "c7" }),
      ev("doc.step", { doc: "workpaper", step: "Authorized Shares Method", detail: "Tax: $85,165" }, { actor: "acct", case_id: "c7" }),
      ev("doc.verified", { doc: "workpaper", ok: true }, { actor: "acct", case_id: "c7" }),
    ]);
    expect(st.desks.acct.art).toMatchObject({ kind: "sheet", tabs: ["Inputs", "Authorized Shares Method"], state: "done", verified: true });
    expect(st.desks.acct.art && st.desks.acct.art.kind === "sheet" ? st.desks.acct.art.rows : []).toEqual([
      { tab: "Inputs", label: "Authorized shares", value: "10,000,000" },
      { tab: "Authorized Shares Method", label: "Tax", value: "$85,165" },
    ]);
    const doc = fold([
      ev("doc.step", { doc: "memo", step: "Summary", detail: "You owe $450." }, { actor: "acct", case_id: "c7" }),
      ev("doc.step", { doc: "memo", step: "Recommendation", detail: "File with the assumed par method." }, { actor: "acct", case_id: "c7" }),
    ]);
    expect(doc.desks.acct.art).toMatchObject({ kind: "doc", sections: [{ title: "Summary" }, { title: "Recommendation" }], state: "drafting" });
  });

  it("v1.18 cells: the workpaper shows exactly what each step wrote, on its sheet", () => {
    const st = fold([
      ev("doc.step", { doc: "workpaper", step: "Inputs", detail: "inputs", cells: [{ sheet: "Inputs", cell: "B2", value: 10000000 }, { sheet: "Inputs", cell: "B3", value: "8,000,000" }] }, { actor: "acct", case_id: "c7" }),
      ev("doc.step", { doc: "workpaper", step: "Comparison", detail: "x", cells: [{ sheet: "Comparison", cell: "B4", value: "$450" }, null, "junk"] }, { actor: "acct", case_id: "c7" }),
    ]);
    const art = st.desks.acct.art;
    expect(art && art.kind === "sheet" ? art.rows : []).toEqual([
      { tab: "Inputs", label: "B2", value: "10000000" },
      { tab: "Inputs", label: "B3", value: "8,000,000" },
      { tab: "Comparison", label: "B4", value: "$450" },
    ]);
    expect(art && art.kind === "sheet" ? art.tabs : []).toEqual(["Inputs", "Comparison"]);
  });

  it("the latest writing replaces the draft (each event is the newest 600 chars or fewer)", () => {
    const st = fold([
      ev("agent.activity", { kind: "tool_started", tool: "post_social" }, { actor: "mkt" }),
      ev("agent.activity", { kind: "writing", text: "Delaware" }, { actor: "mkt" }),
      ev("agent.activity", { kind: "writing", text: "Delaware franchise tax" }, { actor: "mkt" }),
    ]);
    expect(st.desks.mkt.art).toMatchObject({ kind: "post", text: "Delaware franchise tax" });
  });

  it("the tax engine shows both methods with the real numbers, the filed one marked", () => {
    const st = fold([ev("tax.computed", { authorized_method_cents: 8516500, assumed_par_method_cents: 45000, total_cents: 45000, chosen: "assumed_par" }, { actor: "tax", case_id: "c7" })]);
    expect(fold([ev("tax.computed", { authorized_method_cents: 1.5, assumed_par_method_cents: 1, total_cents: 1 }, { actor: "tax" })]).desks.tax.art).toBeNull(); // never throws on bad cents
    expect(st.desks.tax.art).toMatchObject({ kind: "calc", lines: [{ value: "$85,165", chosen: false }, { value: "$450", chosen: true }, { label: "Total due for Team #7" }] });
  });

  it("activity finds its desk by actor or by agent name; idle clears it; junk changes nothing", () => {
    expect(seatOf(ev("agent.activity", { agent: "u-tax-marketing" }))).toBe("mkt");
    expect(seatOf(ev("agent.activity", { agent: "ceo-assistant" }))).toBeNull();
    let st = fold([ev("agent.activity", { kind: "thinking" }, { actor: "hr" })]);
    expect(st.desks.hr.act?.kind).toBe("thinking");
    st = fold([ev("agent.activity", { kind: "idle" }, { actor: "hr" })], st);
    expect(st.desks.hr.act).toBeNull();
    const same = fold([ev("agent.activity", { kind: "nonsense" }, { actor: "hr" }), ev("doc.step", {}, { actor: "acct" }), ev("memo.sent", {}, { actor: "system" })], st);
    expect(same).toBe(st);
    expect(STUDIO_ONLY.has("agent.activity")).toBe(true);
  });
});
