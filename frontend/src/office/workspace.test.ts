import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "@/theater/events";
import { applySteps, emptyScreen } from "@/theater/screen";
import { buildScript } from "@/theater/script";
import { applyEvent, emptyOffice, stepOf, type StepCtx } from "./workspace";

const ev = (id: number, type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
  id, ts: `2026-11-07T21:00:${String(id % 60).padStart(2, "0")}.000Z`, type, actor: "system", case_id: null, provider: null, status: "ok", payload, tokens_in: null, tokens_out: null, cost_usd: null, ...extra,
});
const flaggedSet = new Set<string>();
const ctx: StepCtx = {
  name: (id) => (id === "case_7" ? "Team #7" : "a new client"),
  flagged: (id) => id !== null && flaggedSet.has(id),
  image: (u) => (typeof u === "string" && u.startsWith("https://gw.example/") ? u : undefined),
};
const fold = (evs: UTaxEvent[]) => evs.reduce((o, e) => applyEvent(o, e, ctx), emptyOffice());
const quiet = { log: () => {}, onGap: () => {} };

describe("D32 workspaces: tool.called drives each department's browser", () => {
  it("a tool call opens its site in the department's window, with the status the gateway gave", () => {
    const o = fold([
      ev(1, "tool.called", { tool: "compute_tax", status: "done" }, { actor: "tax" }),
      ev(2, "tool.called", { tool: "send_memo", status: "needs_approval" }, { actor: "acct" }),
      ev(3, "tool.called", { tool: "post_social", status: "refused" }, { actor: "mkt" }),
      ev(4, "tool.called", { tool: "law_search", status: "error" }, { actor: "tax" }),
    ]);
    expect(o.desks.tax.steps.map((s) => [s.site, s.text, s.status])).toEqual([
      ["Naïve Data · law search", "Searched the law: failed", "error"],
      ["utax · compute_tax", "Ran the tax engine", "done"],
    ]);
    expect(o.desks.acct.steps[0]).toMatchObject({ text: "Asked the CEO to approve: sent the memo", status: "needs_approval", site: "AgentMail · compose" });
    expect(o.desks.mkt.steps[0].status).toBe("refused");
    expect(o.desks.tax.tabs).toEqual(["utax · compute_tax", "Naïve Data · law search"]);
    expect(o.desks.tax.counts.tools).toBe(2);
    expect(o.lastSeat).toBe("tax");
  });

  it("an unknown tool or actor never breaks the desk, and an agent's odd tool name is cleaned", () => {
    const o = fold([ev(1, "tool.called", { tool: "x<script>y", status: "weird" }, { actor: "intake" }), ev(2, "tool.called", { tool: "get_case", status: "done" }, { actor: "system" })]);
    expect(o.desks.intake.steps[0]).toMatchObject({ site: "utax · xscripty", status: "other" });
    expect(Object.values(o.desks).flatMap((d) => d.steps)).toHaveLength(1);
  });

  it("the window shows masked and numeric fields only: never a subject, body or candidate name", () => {
    const o = fold([
      ev(1, "email.received", { masked_name: "Team #7", from_masked: "f***@team7.dev", subject: "SECRET-SUBJECT", body: "SECRET-BODY" }, { actor: "intake", case_id: "case_7" }),
      ev(2, "hr.outreach", { candidate_id: "c1", label: "Jane Doe <jane@x.com>", slot: "s" }, { actor: "hr" }),
      ev(3, "tax.computed", { authorized_method_cents: 8516500, assumed_par_method_cents: 40000, chosen: "assumed_par", annual_report_fee_cents: 5000, fee_source: "s", total_cents: 45000, due_date: "d", form5472_flag: false, rnd_flag: false, engine_version: "1" }, { actor: "tax", case_id: "case_7" }),
    ]);
    const all = JSON.stringify(o);
    expect(all).not.toMatch(/SECRET|Jane|jane@/);
    expect(o.desks.intake.steps[0]).toMatchObject({ text: "Opened a new email from Team #7", site: "AgentMail · inbox" });
    expect(o.desks.hr.steps[0].text).toBe("Emailed a candidate with a call invite");
    expect(o.desks.tax.steps[0].fields.map((f) => f.value)).toEqual(["$85,165", "$400", "$450"]);
    expect(o.desks.tax.steps[0].focus).toBe(2);
  });

  it("a flagged case's email shows no sender", () => {
    flaggedSet.add("case_9");
    const d = stepOf(ev(1, "email.received", { masked_name: "Team #9", from_masked: "x***@y.dev", subject: "s", body: "b" }, { actor: "intake", case_id: "case_9" }), ctx.name, ctx.flagged, ctx.image);
    expect(d?.fields).toEqual([{ label: "From", value: "Hidden: flagged by QA" }]);
    flaggedSet.delete("case_9");
  });

  it("images pass only the caller's check; replays file once; a desk keeps its newest 40 steps and 4 tabs", () => {
    const o = fold([
      ev(1, "ad.created", { kind: "client", headline: "h", image_url: "https://gw.example/assets/ad_1.png" }, { actor: "mkt" }),
      ev(2, "post.published", { channel: "x", text: "t", url: null, image_url: "https://evil.example/a.png" }, { actor: "mkt" }),
      ev(2, "post.published", { channel: "x", text: "t", url: null, image_url: "https://evil.example/a.png" }, { actor: "mkt" }),
    ]);
    expect(o.desks.mkt.steps.map((s) => s.image)).toEqual([undefined, "https://gw.example/assets/ad_1.png"]);
    expect(o.desks.mkt.steps).toHaveLength(2);
    const many = fold(Array.from({ length: 50 }, (_, k) => ev(k + 1, "tool.called", { tool: ["get_case", "extract_facts", "ask_client", "book_call", "follow_up"][k % 5], status: "done" }, { actor: "intake" })));
    expect(many.desks.intake.steps).toHaveLength(40);
    expect(many.desks.intake.tabs).toHaveLength(4);
  });
});

describe("D32: tool.called on the theater", () => {
  it("lights the seat's desk without a cursor, and a malformed one is skipped", () => {
    const skipped: string[] = [];
    const { steps } = buildScript(
      [ev(1, "tool.called", { tool: "compute_tax", status: "done" }, { actor: "tax" }), ev(2, "tool.called", { status: "done" }, { actor: "tax" })],
      { ...quiet, log: (k) => skipped.push(`${k.eventId}:${k.reason}`) },
    );
    expect(steps.every((s) => s.agent === null)).toBe(true);
    expect(skipped).toEqual(["2:bad_payload"]);
    const s = applySteps(emptyScreen(), steps);
    expect(Object.keys(s.desks)).toEqual(["tax"]);
    expect(s.camera).toBeNull(); // a rail step never moves the camera
  });
});
