// v1.19 §9.1 (D38): the agent market on the theater. Every animation comes from one of its events.

import { describe, expect, it } from "vitest";
import type { UTaxEvent } from "./events";
import { railFromEvents } from "./rail";
import { numbersFromState } from "./live";
import { applySteps, emptyScreen } from "./screen";
import { buildScript, type CursorStep } from "./script";

const quiet = { log: () => {}, onGap: () => {} };
const TX = "0x3b60aa11bb22cc33dd44ee55ff66778899002277";
const ev = (id: number, type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
  id,
  ts: `2026-11-08T05:00:${String(id).padStart(2, "0")}.000Z`,
  type,
  actor: "system",
  case_id: null,
  provider: null,
  status: "ok",
  payload,
  tokens_in: null,
  tokens_out: null,
  cost_usd: null,
  ...extra,
});

/** The contract's happy path, in order (§9.1). */
const happy = (): UTaxEvent[] => [
  ev(1, "job.posted", { job_id: "job_1", template: "share_card", price_usd: "0.50", headline: "Delaware franchise tax: $85,165 → $450" }, { actor: "mkt" }),
  ev(2, "job.claimed", { job_id: "job_1", contractor: "PixelAgent" }),
  ev(3, "job.submitted", { job_id: "job_1", contractor: "PixelAgent", sha256: "ab".repeat(32) }),
  ev(4, "job.verified", { job_id: "job_1", checks: [{ name: "size 1200×630", ok: true }, { name: "headline text", ok: true }] }),
  ev(5, "approval.requested", { approval_id: "ap_1", action_type: "contractor_pay", summary: "Pay PixelAgent 0.50 pathUSD" }),
  ev(6, "touch.training", { approval_id: "ap_1", note: "approve" }, { actor: "ceo" }),
  ev(7, "payment.sent", { job_id: "job_1", contractor: "PixelAgent", amount_usd: "0.50", token: "pathUSD", tx_hash: TX, explorer_url: `https://explore.tempo.xyz/tx/${TX}` }, { provider: "tempo", cost_usd: "0.50" }),
  ev(8, "job.delivered", { job_id: "job_1", image_url: "/assets/ad_3.png" }),
];

const screenOf = (events: UTaxEvent[]) => applySteps(emptyScreen(), buildScript(events, quiet).steps);
const marketSteps = (steps: CursorStep[]) => steps.filter((s) => s.pane === "market" && s.action !== "focus");

describe("D38 market on the theater", () => {
  it("a job moves card → claimed → checked → paid → delivered, each from its own event", () => {
    const events = happy();
    const after = (n: number) => screenOf(events.slice(0, n)).market.jobs[0];
    expect(after(1)).toMatchObject({ id: "job_1", status: "open", price: "0.50", template: "share_card" });
    expect(after(2)).toMatchObject({ status: "claimed", contractor: "PixelAgent" });
    expect(after(3)).toMatchObject({ status: "submitted" });
    expect(after(4)).toMatchObject({ status: "verified", checks: [{ name: "size 1200×630", ok: true }, { name: "headline text", ok: true }] });
    expect(after(7)).toMatchObject({ status: "paid", pay: { amount: "0.50 pathUSD", tx: "0x3b60…2277", ledger: false } });
    expect(after(7).pay?.url).toBe(`https://explore.tempo.xyz/tx/${TX}`);
    expect(after(8)).toMatchObject({ status: "delivered", image: "/assets/ad_3.png" });
  });

  it("the payment animation comes only from payment.sent (never from the approval or the touch)", () => {
    const events = happy();
    const before = buildScript(events.slice(0, 6), quiet).steps;
    expect(before.some((s) => s.target === "market.pay")).toBe(false);
    expect(screenOf(events.slice(0, 6)).market.jobs[0].pay).toBeUndefined();
    const all = buildScript(events, quiet).steps;
    const pay = all.filter((s) => s.target === "market.pay");
    expect(pay).toHaveLength(1);
    expect(pay[0].eventId).toBe(7);
    expect(pay[0]).toMatchObject({ action: "drag", from: "market.wallet.firm" });
  });

  it("a local-wallet payment shows as a ledger token: no tx link", () => {
    const events = happy();
    events[6] = { ...events[6], provider: "local" };
    const job = screenOf(events).market.jobs[0];
    expect(job.pay).toMatchObject({ ledger: true, tx: "", url: null });
  });

  it("failed checks are circled and the job never shows as paid", () => {
    const events = [
      ...happy().slice(0, 3),
      ev(4, "job.failed", { job_id: "job_1", checks: [{ name: "size 1200×630", ok: true }, { name: "headline text", ok: false }], reason: "checks_failed" }),
    ];
    const steps = marketSteps(buildScript(events, quiet).steps);
    expect(steps.filter((s) => s.action === "circle").map((s) => s.text)).toEqual(["headline text"]);
    expect(screenOf(events).market.jobs[0]).toMatchObject({ status: "failed", reason: "checks failed" });
  });

  it("unknown or malformed market events animate nothing; untrusted values never reach the screen", () => {
    const skipped: string[] = [];
    const log = { log: (s: { type: string; reason: string }) => skipped.push(`${s.type}:${s.reason}`), onGap: () => {} };
    const events = [
      ev(1, "job.bribed", { job_id: "job_1" }),
      ev(2, "job.posted", { job_id: "job 1; drop", template: "share_card", price_usd: "0.50", headline: "x" }, { actor: "mkt" }),
      ev(3, "job.posted", { job_id: "job_2", template: "share_card", price_usd: "-1", headline: "x" }, { actor: "mkt" }),
      ev(31, "job.posted", { job_id: "job_3", template: "share_card", price_usd: 0.5, headline: "ok" }, { actor: "mkt" }),
      ev(32, "payment.sent", { job_id: "job_3", contractor: "P", amount_usd: "0.50", token: "pathUSD", tx_hash: TX, explorer_url: "javascript:alert(1)" }, { provider: "tempo" }),
      ev(33, "job.delivered", { job_id: "job_3", image_url: "https://evil.example/x.png" }),
      ev(34, "job.claimed", { job_id: "job_2", contractor: "Ghost" }), // a job never posted: no blank card
    ];
    const script = buildScript(events, log);
    expect(skipped).toEqual(["job.bribed:unknown_type", "job.posted:bad_payload", "job.posted:bad_payload"]);
    const shown = applySteps(emptyScreen(), script.steps).market.jobs;
    expect(shown.map((j) => j.id)).toEqual(["job_3"]); // the never-posted job_2 drew nothing
    const job = shown[0];
    expect(job.pay?.url).toBeNull(); // a non-https explorer link is dropped (the tx still shows)
    expect(job.image).toBeUndefined(); // only the gateway's own /assets/ path renders
  });

  it("the rail counts contractors paid and earned from agents (fixture sums; live from /api/state)", () => {
    const events = [...happy(), ev(9, "agent.sale", { amount_usd: "0.50", token: "pathUSD", tx_hash: TX, explorer_url: null, payer_masked: "0x12…ab" }, { provider: "tempo" })];
    const n = railFromEvents(events);
    expect(n.money.contractor_paid_usd).toBe("0.5");
    expect(n.money.agent_sales_usd).toBe("0.5");
    expect(railFromEvents(happy().slice(0, 4)).money.contractor_paid_usd).toBeUndefined(); // market off: no row
    // The payment's own cost.booked is tokens, never dollars spent (v1.19 §9.1, amended).
    const booked = ev(10, "cost.booked", { provider: "tempo", model: "contractor:share_card", estimated: false }, { cost_usd: "0.50" });
    const dollars = ev(11, "cost.booked", { provider: "openrouter", model: "x", estimated: false }, { cost_usd: "0.02" });
    expect(railFromEvents([...events, booked, dollars]).money.spent_usd).toBe("0.02");
    expect(numbersFromState({ money: { spent_usd: "1.2" }, contractor_paid_usd: "1.5", agent_sales_usd: 0.5 })?.money).toMatchObject({ contractor_paid_usd: "1.5", agent_sales_usd: "0.5" });
    expect(numbersFromState({ money: {} })?.money.contractor_paid_usd).toBeUndefined();
    expect(screenOf(events).rail.log.at(-1)?.text).toBe("An agent bought a franchise-tax check · 0.50 pathUSD (testnet)");
  });

  it("the contractor's approval belongs to Marketing", () => {
    const steps = buildScript(happy().slice(0, 5), quiet).steps;
    expect(steps.find((s) => s.target === "rail.touches")?.text).toBe("contractor pay approval");
  });
});

describe("D38 market in the office and the fixture", () => {
  it("Marketing's desk shows the job step by step (actor system events included)", async () => {
    const { applyStudio, emptyStudio } = await import("@/office/artifact");
    const ctx = { flagged: () => false, seatFlagged: () => false, name: () => "a client", image: () => undefined, asset: (p: string) => `https://gw.example${p}` };
    const st = happy().reduce((s, e) => applyStudio(s, e, ctx), emptyStudio());
    const art = st.desks.mkt.art;
    expect(art?.kind).toBe("job");
    if (art?.kind !== "job") return;
    expect(art).toMatchObject({ jobId: "job_1", price: "0.50", contractor: "PixelAgent", state: "done", image: "https://gw.example/assets/ad_3.png" });
    expect(art.steps.map((s) => s.text)).toContain("Paid PixelAgent 0.50 pathUSD · 0x3b60…2277");
    expect(art.steps.find((s) => s.text.startsWith("Paid"))?.url).toBe(`https://explore.tempo.xyz/tx/${TX}`);
    const bad = applyStudio(emptyStudio(), ev(1, "job.posted", { job_id: "../x", template: "t", price_usd: "0.5", headline: "h" }, { actor: "mkt" }), ctx);
    expect(bad.desks.mkt.art).toBeNull();
  });

  it("the market fixture continues the contract sample and uses only known types", async () => {
    const { readFileSync } = await import("node:fs");
    const { fileURLToPath } = await import("node:url");
    const { parseJsonl } = await import("./events");
    const text = readFileSync(fileURLToPath(new URL("../../public/fixtures/market.sample.jsonl", import.meta.url)), "utf8");
    const { events, bad } = parseJsonl(text);
    expect(bad).toEqual([]);
    expect(events[0].id).toBe(30);
    expect(buildScript(events, quiet).skipped).toEqual([]);
    expect(screenOf(events).market.jobs[0].status).toBe("delivered");
  });
});
