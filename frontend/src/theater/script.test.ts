import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it, vi } from "vitest";
import { parseJsonl, type UTaxEvent } from "./events";
import {
  appendEvents,
  buildScript,
  KNOWN_TYPES,
  startScript,
  stepsForEvent,
  type CursorStep,
  type SkippedEvent,
} from "./script";

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), "utf8");
const FIXTURE = read("../../public/fixtures/events.sample.jsonl");
const parsed = parseJsonl(FIXTURE);
const events = parsed.events;
const quiet = { log: () => {} };

const ev = (id: number, type: string, payload: Record<string, unknown>, extra: Partial<UTaxEvent> = {}): UTaxEvent => ({
  id,
  ts: `2026-11-07T21:00:${String(id % 60).padStart(2, "0")}.000Z`,
  type,
  actor: "intake",
  case_id: "case_1",
  provider: "local",
  status: "ok",
  payload,
  tokens_in: null,
  tokens_out: null,
  cost_usd: null,
  ...extra,
});

const email = (id: number, caseId: string, name: string) =>
  ev(id, "email.received", { masked_name: name, from_masked: "a@b.dev", subject: "s", body: "We have 10,000,000 shares" }, { case_id: caseId });

const stepsOf = (steps: CursorStep[], type: string) => {
  const ids = new Set(events.filter((e) => e.type === type).map((e) => e.id));
  return steps.filter((s) => ids.has(s.eventId));
};
const content = (steps: CursorStep[]) => steps.filter((s) => s.action !== "focus" && s.action !== "switch");

describe("fixture", () => {
  it("matches contracts/events.sample.jsonl (re-copy it when the contract changes)", () => {
    const norm = (s: string) => s.replace(/\r\n/g, "\n").trim();
    expect(norm(FIXTURE)).toBe(norm(read("../../../contracts/events.sample.jsonl")));
  });

  it("parses cleanly and uses only event types the theater knows", () => {
    expect(parsed.bad).toEqual([]);
    expect(events).toHaveLength(29);
    for (const e of events) expect(KNOWN_TYPES).toContain(e.type);
  });
});

describe("buildScript on the happy-path fixture", () => {
  const { steps, skipped } = buildScript(events, quiet);

  it("skips nothing", () => {
    expect(skipped).toEqual([]);
  });

  it("is deterministic: same events, same script, whatever the input order", () => {
    expect(buildScript(events, quiet)).toEqual({ steps, skipped });
    expect(buildScript([...events].reverse(), quiet)).toEqual({ steps, skipped });
  });

  it("every step comes from a real event (premise 7)", () => {
    const byId = new Map(events.map((e) => [e.id, e]));
    for (const s of steps) {
      expect(byId.has(s.eventId)).toBe(true);
      expect(s.ts).toBe(byId.get(s.eventId)!.ts);
      if (s.text !== undefined) expect(s.text).not.toMatch(/undefined|NaN|\[object/);
    }
  });

  it("steps stay in event order and keys are unique", () => {
    const ids = steps.map((s) => s.eventId);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    expect(new Set(steps.map((s) => s.key)).size).toBe(steps.length);
  });

  it("has the prefix property, so live mode can append", () => {
    for (let n = 0; n <= events.length; n++) {
      const partial = buildScript(events.slice(0, n), quiet).steps;
      expect(partial).toEqual(steps.slice(0, partial.length));
    }
  });

  it("moves the camera only when the pane changes, never to the rail", () => {
    let camera: string | null = null;
    for (const s of steps) {
      if (s.action === "focus") {
        expect(s.pane).not.toBe("rail");
        expect(s.agent).toBeNull();
        expect(s.pane).not.toBe(camera);
        camera = s.pane;
      } else if (s.pane !== "rail") {
        expect(s.pane).toBe(camera);
      }
    }
  });

  it("email.received: Intake opens the message; the case appears on the rail", () => {
    const s = stepsOf(steps, "email.received").filter((x) => x.caseId === "case_7");
    expect(s.find((x) => x.action === "open")).toMatchObject({
      pane: "mail",
      target: "mail.message",
      agent: "intake",
      meta: { from: "founder@team7.dev", masked_name: "Team #7" },
    });
    expect(s.find((x) => x.pane === "rail")).toMatchObject({ target: "rail.cases", action: "appear", text: "Team #7", agent: null });
  });

  it("facts.extracted: highlights each quote, then drags it into its cell; assumed par is filled", () => {
    const s = stepsOf(steps, "facts.extracted");
    expect(s.filter((x) => x.action === "highlight").map((x) => [x.target, x.text])).toEqual([
      ["mail.quote.incorporated_delaware", "We're a Delaware C-corp"],
      ["mail.quote.authorized_shares", "10,000,000"],
      ["mail.quote.issued_shares", "8,000,000"],
      ["mail.quote.gross_assets", "$500k"],
      ["mail.quote.foreign_owner_25", "No foreign owners"],
    ]);
    expect(s.filter((x) => x.action === "drag").map((x) => [x.from, x.target, x.text])).toEqual([
      ["mail.quote.incorporated_delaware", "sheet.cell.incorporated_delaware", "Yes"],
      ["mail.quote.authorized_shares", "sheet.cell.authorized_shares", "10,000,000"],
      ["mail.quote.issued_shares", "sheet.cell.issued_shares", "8,000,000"],
      ["mail.quote.gross_assets", "sheet.cell.gross_assets", "$500,000"],
      ["mail.quote.foreign_owner_25", "sheet.cell.foreign_owner_25", "No"],
    ]);
    expect(s.find((x) => x.target === "sheet.cell.stated_par")).toMatchObject({ action: "fill", text: "$0.00001", meta: { assumed: true } });
    expect(s.find((x) => x.action === "switch")).toMatchObject({ pane: "sheet", text: "Team #7" });
  });

  it("law.searched: Tax types the query and the result card appears", () => {
    const s = stepsOf(steps, "law.searched");
    expect(s.find((x) => x.action === "type")).toMatchObject({
      pane: "search",
      agent: "tax",
      target: "search.query",
      text: "delaware franchise tax assumed par value method",
    });
    expect(s.find((x) => x.target === "search.result.0")?.text).toBe(
      "8 Del. C. § 503 · assumed par value capital method · min $400, max $200,000",
    );
  });

  it("tax.computed: Tax fills both methods, the lower one wins", () => {
    const s = stepsOf(steps, "tax.computed");
    expect(Object.fromEntries(s.filter((x) => x.action === "fill").map((x) => [x.target, x.text]))).toEqual({
      "sheet.cell.authorized_method": "$85,165",
      "sheet.cell.assumed_par_method": "$400",
      "sheet.cell.annual_report_fee": "$50",
      "sheet.cell.total": "$450",
    });
    const marks = s.filter((x) => x.action === "mark").map((x) => [x.target, x.text]);
    expect(marks).toContainEqual(["sheet.cell.assumed_par_method", "win"]);
    expect(marks).toContainEqual(["sheet.cell.authorized_method", "lose"]);
    expect(s.some((x) => x.target.startsWith("sheet.flag."))).toBe(false);
  });

  it("qa.passed: QA ticks each checked cell and stamps the sheet", () => {
    const s = stepsOf(steps, "qa.passed");
    expect(s.filter((x) => x.action === "tick").map((x) => x.target)).toEqual([
      "sheet.cell.authorized_shares",
      "sheet.cell.issued_shares",
      "sheet.cell.gross_assets",
      "sheet.cell.total",
    ]);
    expect(s.every((x) => x.agent === "qa" || x.agent === null)).toBe(true);
    expect(s.at(-1)).toMatchObject({ target: "sheet.qa", text: "✓ QA" });
    expect(s.some((x) => x.action === "block")).toBe(false);
  });

  it("memo.sent: Accounting types the real memo text and clicks Send", () => {
    const memo = events.find((e) => e.type === "memo.sent")!;
    const s = content(stepsOf(steps, "memo.sent"));
    expect(s.map((x) => [x.target, x.action])).toEqual([
      ["mail.compose", "appear"],
      ["mail.memo", "type"],
      ["mail.send", "click"],
      ["mail.sent", "appear"],
    ]);
    expect(s[1].text).toBe(memo.payload.body);
    expect(s[1].agent).toBe("acct");
    expect(s[3].text).toBe("✓ Sent · 41 s after last fact");
  });

  it("call.booked: Intake drags a 15-min block named after the masked case", () => {
    expect(stepsOf(steps, "call.booked").find((x) => x.action === "drag")).toMatchObject({
      pane: "calendar",
      target: "calendar.block",
      agent: "intake",
      text: "Team #7 · 15-min review",
      meta: { start: "2026-11-08T17:30:00.000Z", minutes: 15 },
    });
  });

  it("post.published, screen.call and phone.checked map to their panes", () => {
    expect(content(stepsOf(steps, "post.published")).map((x) => [x.pane, x.target, x.action, x.agent])).toEqual([
      ["social", "social.caption", "type", "mkt"],
      ["social", "social.post", "click", "mkt"],
      ["social", "social.posted", "appear", null],
    ]);
    expect(content(stepsOf(steps, "screen.call")).map((x) => [x.target, x.action, x.agent])).toEqual([
      ["phone.dial", "dial", "hr"],
      ["phone.transcript", "type", "hr"],
    ]);
    expect(stepsOf(steps, "phone.checked").at(-1)).toMatchObject({ target: "phone.check", action: "tick", agent: "qa" });
  });

  it("touch.* puts the white CEO cursor on the rail; system updates have no cursor", () => {
    for (const s of stepsOf(steps, "touch.training").concat(stepsOf(steps, "touch.operating"))) {
      expect(s).toMatchObject({ pane: "rail", target: "rail.touches", agent: "ceo", action: "count" });
    }
    expect(stepsOf(steps, "touch.training")[0].meta).toMatchObject({ kind: "training", approval_id: "apr_1" });
    for (const type of ["trust.changed", "case.status", "approval.requested", "cost.booked", "crm.synced"]) {
      for (const s of stepsOf(steps, type)) expect(s.agent).toBeNull();
    }
    expect(stepsOf(steps, "trust.changed")[0].text).toBe("email → AUTO");
    expect(stepsOf(steps, "cost.booked")[0].text).toBe("$0.0034");
    expect(stepsOf(steps, "crm.synced")[0].text).toBe("CRM deal · call booked");
  });

  it("hr.* counts on the rail with the stage", () => {
    expect(stepsOf(steps, "hr.shortlisted")[0]).toMatchObject({ target: "rail.hiring", agent: "hr", text: "Candidate A · shortlisted", meta: { stage: "shortlisted" } });
  });

  it("facts.missing marks the missing cells; client.asked types the re-ask", () => {
    const missing = stepsOf(steps, "facts.missing").filter((x) => x.action === "fill");
    expect(missing.map((x) => x.target)).toEqual([
      "sheet.cell.authorized_shares",
      "sheet.cell.issued_shares",
      "sheet.cell.gross_assets",
      "sheet.cell.foreign_owner_25",
    ]);
    expect(missing.every((x) => x.text === "?" && x.caseId === "case_12")).toBe(true);
    expect(stepsOf(steps, "client.asked").at(-1)?.text).toBe("✓ Sent · asked for 4 facts");
  });

  it("brief.generated: the Chief of Staff says the brief (audio is fetched by event id)", () => {
    expect(stepsOf(steps, "brief.generated")[0]).toMatchObject({ target: "rail.brief", agent: "cos", action: "say", meta: { brief_event_id: 29 } });
  });
});

describe("validation: never animate what was not logged", () => {
  it("zero events → empty script", () => {
    expect(buildScript([], quiet)).toEqual({ steps: [], skipped: [] });
  });

  it("unknown types are skipped and logged, never animated", () => {
    const log = vi.fn<(s: SkippedEvent) => void>();
    const { steps, skipped } = buildScript([email(1, "case_1", "Team #1"), ev(2, "ufo.landed", {})], { log });
    expect(skipped).toEqual([{ eventId: 2, type: "ufo.landed", reason: "unknown_type" }]);
    expect(log).toHaveBeenCalledExactlyOnceWith({ eventId: 2, type: "ufo.landed", reason: "unknown_type" });
    expect(steps.some((s) => s.eventId === 2)).toBe(false);
  });

  it("logs to console.warn by default", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    buildScript([ev(1, "ufo.landed", {})]);
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });

  it.each(["constructor", "toString", "__proto__", "hasOwnProperty"])("prototype key %s as a type is unknown, not a pane", (type) => {
    for (const status of ["ok", "failed"] as const) {
      const { steps, skipped } = buildScript([ev(1, type, {}, { status })], quiet);
      expect(steps).toEqual([]);
      expect(skipped).toEqual([{ eventId: 1, type, reason: "unknown_type" }]);
    }
  });

  it.each(KNOWN_TYPES)("%s with an empty payload is skipped as bad_payload", (type) => {
    const { steps, skipped } = buildScript([ev(1, type, {})], quiet);
    expect(steps).toEqual([]);
    expect(skipped).toMatchObject([{ eventId: 1, type, reason: "bad_payload" }]);
  });

  it("a string \"false\" is never truthy: QA and phone checks need real booleans", () => {
    const { steps, skipped } = buildScript(
      [
        ev(1, "qa.passed", { checks: [{ field: "total", ok: "false", note: "x" }] }, { actor: "qa" }),
        ev(2, "phone.checked", { screenshot_url: null, rendered_ok: "true" }, { actor: "qa" }),
      ],
      quiet,
    );
    expect(steps).toEqual([]);
    expect(skipped.map((s) => s.reason)).toEqual(["bad_payload", "bad_payload"]);
  });

  it.each([
    ["id NaN", { id: Number.NaN }],
    ["id as string", { id: "5" }],
    ["id zero", { id: 0 }],
    ["unknown status", { status: "weird" }],
    ["unknown actor", { actor: "alien" }],
    ["numeric cost", { cost_usd: 0.5 }],
  ])("a malformed envelope (%s) is skipped as bad_event", (_name, patch) => {
    const bad = { ...ev(1, "case.status", { from: "NEW", to: "READY" }, { actor: "system" }), ...patch } as unknown as UTaxEvent;
    const { steps, skipped } = buildScript([bad], quiet);
    expect(steps).toEqual([]);
    expect(skipped[0].reason).toBe("bad_event");
  });

  it("a malformed event never stops later events", () => {
    const { steps, skipped } = buildScript([ev(1, "facts.extracted", {}), ev(2, "case.status", { from: "NEW", to: "INTAKE_PENDING" }, { actor: "system" })], quiet);
    expect(skipped).toMatchObject([{ eventId: 1, type: "facts.extracted", reason: "bad_payload" }]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ eventId: 2, action: "status", text: "intake pending" });
  });

  it("a non-integer fact drops the event rather than showing a wrong number", () => {
    const { skipped } = buildScript([ev(1, "facts.extracted", { facts: { authorized_shares: 1.5 }, quotes: {}, assumed: [] })], quiet);
    expect(skipped).toMatchObject([{ reason: "bad_payload", detail: "authorized_shares: expected integer" }]);
  });

  it("case_id __proto__ is just a string", () => {
    const { steps } = buildScript([ev(1, "call.booked", { start: "x", minutes: 15, invitee_masked: "m" }, { case_id: "__proto__" })], quiet);
    expect(steps.find((s) => s.action === "drag")?.text).toBe("__proto__ · 15-min review");
  });

  it("untrusted URLs: only https: and local: reach the renderer", () => {
    const { steps } = buildScript(
      [
        ev(1, "post.published", { channel: "x", text: "hi", url: "javascript:alert(1)", image_url: "https://cdn.example/a.png" }, { actor: "mkt", case_id: null }),
        ev(2, "post.published", { channel: "x", text: "hi", url: "https://x.com/p/1", image_url: "data:image/png;base64,AAAA" }, { actor: "mkt", case_id: null }),
      ],
      quiet,
    );
    const posted = steps.filter((s) => s.target === "social.posted").map((s) => s.meta?.url);
    expect(posted).toEqual([null, "https://x.com/p/1"]);
    const images = steps.filter((s) => s.target === "social.image").map((s) => s.meta?.image_url);
    expect(images).toEqual(["https://cdn.example/a.png"]);
  });

  it("status skipped → no steps; status failed → one fail step on the event's pane", () => {
    const { steps, skipped } = buildScript(
      [ev(1, "law.searched", { query: "q", results: [] }, { actor: "tax", status: "skipped" }), ev(2, "memo.sent", {}, { actor: "acct", status: "failed" })],
      quiet,
    );
    expect(skipped).toEqual([{ eventId: 1, type: "law.searched", reason: "status_skipped" }]);
    expect(content(steps).map((s) => [s.pane, s.action, s.agent])).toEqual([["mail", "fail", "acct"]]);
  });
});

describe("mapping details", () => {
  it("qa.failed: QA circles the mismatch; only case.blocked raises the BLOCKED card", () => {
    const { steps } = buildScript(
      [
        ev(1, "qa.failed", { checks: [{ field: "issued_shares", ok: true, note: "ok" }, { field: "total", ok: false, note: "engine re-run differs" }] }, { actor: "qa" }),
        ev(2, "case.blocked", { reason: "QA failed: total mismatch", needs: "human" }, { actor: "system" }),
      ],
      quiet,
    );
    expect(content(steps).map((s) => [s.target, s.action, s.text])).toEqual([
      ["sheet.cell.issued_shares", "tick", "ok"],
      ["sheet.cell.total", "circle", "engine re-run differs"],
      ["sheet.qa", "appear", "✗ QA"],
      ["rail.cases", "block", "QA failed: total mismatch"],
    ]);
  });

  it("tax.computed with flags raises the 5472 and R&D notes", () => {
    const { steps } = buildScript(
      [
        ev(1, "tax.computed", {
          authorized_method_cents: 100,
          assumed_par_method_cents: 200,
          chosen: "authorized",
          annual_report_fee_cents: 5000,
          fee_source: "snapshot",
          total_cents: 5100,
          due_date: "2027-03-01",
          form5472_flag: true,
          rnd_flag: true,
          engine_version: "1",
        }, { actor: "tax" }),
      ],
      quiet,
    );
    expect(steps.filter((s) => s.action === "mark").map((s) => [s.target, s.text])).toContainEqual(["sheet.cell.authorized_method", "win"]);
    expect(steps.map((s) => s.target)).toEqual(expect.arrayContaining(["sheet.flag.form5472", "sheet.flag.rnd"]));
  });

  it("facts: unquoted values are filled (not dropped); quoted+assumed values are dragged once", () => {
    const { steps } = buildScript(
      [ev(1, "facts.extracted", { facts: { authorized_shares: 5000, issued_shares: 100 }, quotes: { issued_shares: "100" }, assumed: ["issued_shares"] })],
      quiet,
    );
    expect(content(steps).map((s) => [s.target, s.action, s.meta])).toEqual([
      ["mail.quote.issued_shares", "highlight", undefined],
      ["sheet.cell.authorized_shares", "fill", { assumed: false }],
      ["sheet.cell.issued_shares", "drag", { assumed: true }],
    ]);
  });

  it("v1.2 voice.command: a customer's words have no agent cursor; a CEO order has the CEO's", () => {
    const { steps } = buildScript(
      [
        ev(1, "voice.command", { text: "we have 10M shares", channel: "voice", speaker: "customer", case_id: "case_9" }, { case_id: "case_9" }),
        ev(2, "voice.command", { text: "pause posting", channel: "typed", speaker: "ceo", case_id: null }, { actor: "ceo", case_id: null }),
        ev(3, "ceo.notified", { channel: "email", subject: "Approval needed", summary: "First memo", reason: "approval" }, { actor: "cos", case_id: null }),
      ],
      quiet,
    );
    expect(steps[0]).toMatchObject({ target: "rail.voice", agent: null, action: "say", meta: { speaker: "customer", case_id: "case_9" } });
    expect(steps[1]).toMatchObject({ agent: "ceo", meta: { speaker: "ceo" } });
    expect(steps[2]).toMatchObject({ target: "rail.notify", agent: "cos", text: "Approval needed", meta: { reason: "approval" } });
  });

  it("dm.drafted is typed by Marketing; the white CEO cursor clicks Send", () => {
    const { steps } = buildScript(
      [
        ev(1, "dm.drafted", { recipient_label: "EA candidate", text: "Hi!" }, { actor: "mkt", case_id: null }),
        ev(2, "dm.sent_by_human", { recipient_label: "EA candidate", text: "Hi!" }, { actor: "ceo", case_id: null }),
      ],
      quiet,
    );
    expect(content(steps).map((s) => [s.pane, s.target, s.action, s.agent])).toEqual([
      ["social", "social.dm", "type", "mkt"],
      ["social", "social.dm_send", "click", "ceo"],
    ]);
  });

  it("tool.bought, cost.booked in exponent form, a cost with no amount, case.not_eligible, failed phone render", () => {
    const { steps, skipped } = buildScript(
      [
        ev(1, "tool.bought", { tool: "Apify actor", provider: "apify" }, { actor: "mkt", case_id: null, cost_usd: "1.5" }),
        ev(2, "tool.bought", { tool: "free tool", provider: "local" }, { actor: "mkt", case_id: null }),
        ev(3, "cost.booked", { provider: "openrouter", model: "m", estimated: true }, { case_id: null, cost_usd: "1E-7" }),
        ev(4, "case.not_eligible", { reason: "not Delaware" }),
        ev(5, "phone.checked", { screenshot_url: "local://s.png", rendered_ok: false }, { actor: "qa" }),
      ],
      quiet,
    );
    expect(skipped).toMatchObject([{ eventId: 2, reason: "bad_payload", detail: "cost_usd: required" }]);
    const texts = content(steps).map((s) => s.text);
    expect(texts).toEqual(["Apify actor · $1.50", "$0.0000001", "not eligible", "Memo failed to render on phone"]);
    expect(steps.find((s) => s.eventId === 3)?.meta?.cost_usd).toBe("0.0000001");
    expect(steps.at(-1)).toMatchObject({ action: "circle", meta: { screenshot_url: "local://s.png" } });
  });

  it("call.booked before any email falls back to the case id", () => {
    const { steps } = buildScript([ev(1, "call.booked", { start: "x", minutes: 15, invitee_masked: "m" })], quiet);
    expect(steps.find((s) => s.action === "drag")?.text).toBe("case_1 · 15-min review");
  });

  it("concurrent cases: the mail pane switches back before highlighting the right email", () => {
    const facts = ev(3, "facts.extracted", { facts: { authorized_shares: 10000000 }, quotes: { authorized_shares: "10,000,000" }, assumed: [] }, { case_id: "case_a" });
    const { steps } = buildScript([email(1, "case_a", "Team A"), email(2, "case_b", "Team B"), facts], quiet);
    const ev3 = steps.filter((s) => s.eventId === 3);
    expect(ev3.slice(0, 2)).toMatchObject([
      { action: "switch", pane: "mail", text: "Team A", meta: { case_id: "case_a" } },
      { action: "highlight", target: "mail.quote.authorized_shares" },
    ]);
    expect(ev3.find((s) => s.action === "switch" && s.pane === "sheet")).toMatchObject({ text: "Team A" });
  });

  it("stepsForEvent never mutates the state it is given", () => {
    const state = startScript().state;
    const frozen = JSON.stringify(state);
    stepsForEvent(state, email(1, "case_1", "Team #1"));
    expect(JSON.stringify(state)).toBe(frozen);
    expect(Object.keys(state.caseNames)).toEqual([]);
  });
});

describe("appendEvents (live mode)", () => {
  const whole = buildScript(events, quiet).steps;

  it("appending in chunks gives exactly the whole-log script", () => {
    for (const size of [1, 3, 7, 29]) {
      let cursor = startScript();
      const steps: CursorStep[] = [];
      for (let i = 0; i < events.length; i += size) {
        const res = appendEvents(cursor, events.slice(i, i + size), quiet);
        cursor = res.cursor;
        steps.push(...res.steps);
      }
      expect(steps).toEqual(whole);
      expect(cursor.lastId).toBe(29);
    }
  });

  it("an SSE replay after reconnect (ids already applied) adds nothing", () => {
    const first = appendEvents(startScript(), events.slice(0, 20), quiet);
    const replay = appendEvents(first.cursor, events.slice(10, 25), quiet);
    expect(replay.steps).toEqual(whole.filter((s) => s.eventId > 20 && s.eventId <= 25));
    const again = appendEvents(replay.cursor, [events[24], events[24]], quiet);
    expect(again.steps).toEqual([]);
  });

  it("a late lower id is dropped, never inserted, so emitted keys never change meaning", () => {
    const a = appendEvents(startScript(), [events[9]], quiet);
    const late = appendEvents(a.cursor, [events[8]], quiet);
    expect(late.steps).toEqual([]);
    expect(late.cursor.lastId).toBe(10);
  });

  it("content keys do not depend on earlier camera moves", () => {
    const e3 = events.find((e) => e.type === "law.searched")!;
    const alone = content(buildScript([e3], quiet).steps);
    const after = content(buildScript(events.filter((e) => e.id <= e3.id), quiet).steps).filter((s) => s.eventId === e3.id);
    expect(after).toEqual(alone);
  });

  it("a seeded cursor shows masked names and skips history it was told about", () => {
    const callBooked = events.find((e) => e.type === "call.booked")!;
    const cursor = startScript({ caseNames: { case_7: "Team #7" }, lastId: callBooked.id - 1 });
    const res = appendEvents(cursor, events, quiet);
    expect(res.steps[0].eventId).toBe(callBooked.id);
    expect(res.steps.find((s) => s.action === "drag")?.text).toBe("Team #7 · 15-min review");
  });
});

describe("review pass 2", () => {
  it("mail and sheet events need a case_id, so one client's numbers never land on another's sheet", () => {
    const { steps, skipped } = buildScript([ev(1, "case.status", { from: "NEW", to: "READY" }, { actor: "system" }), ev(2, "tax.computed", {}, { actor: "tax", case_id: null })], quiet);
    expect(skipped).toMatchObject([{ eventId: 2, reason: "bad_event", detail: "case_id required" }]);
    expect(steps.every((s) => s.eventId === 1)).toBe(true);
  });

  it("a QA verdict that contradicts its checks is skipped", () => {
    const check = (ok: boolean) => ({ field: "total", ok, note: "n" });
    const { steps, skipped } = buildScript(
      [
        ev(1, "qa.passed", { checks: [check(true), check(false)] }, { actor: "qa" }),
        ev(2, "qa.failed", { checks: [] }, { actor: "qa" }),
        ev(3, "qa.failed", { checks: [check(true)] }, { actor: "qa" }),
      ],
      quiet,
    );
    expect(steps).toEqual([]);
    expect(skipped.map((s) => s.detail)).toEqual(Array(3).fill("checks contradict the verdict"));
  });

  it("missing ids are reported as a gap; a late arrival is then dropped", () => {
    const log = vi.fn<(s: SkippedEvent) => void>();
    const a = appendEvents(startScript(), [events[0], events[3]], { log });
    expect(a.skipped).toEqual([{ eventId: 2, type: "?", reason: "gap", detail: "ids 2..3 never arrived" }]);
    expect(log).toHaveBeenCalledOnce();
    expect(appendEvents(a.cursor, [events[1]], quiet).steps).toEqual([]);
  });

  it("two different events with the same id are reported, the first one wins", () => {
    const x = ev(1, "case.status", { from: "NEW", to: "READY" }, { actor: "system" });
    const y = ev(1, "case.status", { from: "NEW", to: "BLOCKED" }, { actor: "system" });
    const { steps, skipped } = buildScript([x, y, x], quiet);
    expect(steps.map((s) => s.text)).toEqual(["ready"]);
    expect(skipped).toMatchObject([{ eventId: 1, reason: "bad_event", detail: "duplicate id with different content" }]);
  });

  it("an invalid lastId seed falls back to 0 instead of disabling dedup", () => {
    for (const lastId of [Number.NaN, -3, 1.5]) {
      const c = startScript({ lastId });
      expect(c.lastId).toBe(0);
      const once = appendEvents(c, events.slice(0, 3), quiet);
      expect(appendEvents(once.cursor, events.slice(0, 3), quiet).steps).toEqual([]);
    }
  });

  it("URLs: local:// assets only, https without credentials, rejections are flagged", () => {
    const post = (id: number, url: string) => ev(id, "post.published", { channel: "x", text: "t", url, image_url: null }, { actor: "mkt", case_id: null });
    const { steps } = buildScript(
      [post(1, "local://post/1"), post(2, "local:../../etc/passwd"), post(3, "local://a/../b"), post(4, "https://u:p@h.com/"), post(5, "http://h.com/"), post(6, "https://h.com/a b")],
      quiet,
    );
    expect(steps.filter((s) => s.target === "social.posted").map((s) => [s.meta?.url, s.meta?.url_rejected])).toEqual([
      ["local://post/1", false],
      [null, true],
      [null, true],
      [null, true],
      [null, true],
      ["https://h.com/a%20b", false],
    ]);
  });

  it("facts: negative par is malformed; null optional R&D facts count as absent", () => {
    const facts = (extra: Record<string, unknown>) =>
      ev(1, "facts.extracted", { facts: { authorized_shares: 100, ...extra }, quotes: {}, assumed: [] });
    expect(buildScript([facts({ stated_par: "-0.01" })], quiet).skipped).toMatchObject([{ detail: "stated_par: negative" }]);
    const ok = buildScript([facts({ gross_receipts_cents: null, years_with_receipts: null })], quiet);
    expect(ok.skipped).toEqual([]);
    expect(content(ok.steps).map((s) => s.target)).toEqual(["sheet.cell.authorized_shares"]);
  });

  it("a search with many results shows 5 cards and a +N chip", () => {
    const results = Array.from({ length: 12 }, (_, i) => ({ cite: `c${i}`, snippet: "s" }));
    const { steps } = buildScript([ev(1, "law.searched", { query: "q", results }, { actor: "tax" })], quiet);
    expect(steps.filter((s) => s.target.startsWith("search.result.")).length).toBe(5);
    expect(steps.at(-1)).toMatchObject({ target: "search.more", text: "+7 more results" });
  });
});

describe("parseJsonl", () => {
  it("strips a UTF-8 BOM", () => {
    const line = JSON.stringify(ev(1, "case.status", { from: "NEW", to: "READY" }, { actor: "system" }));
    expect(parseJsonl(`﻿${line}
`)).toEqual({ events: [JSON.parse(line)], bad: [] });
  });

  it("skips bad lines instead of failing the whole replay", () => {
    const text = `${JSON.stringify(ev(1, "case.status", { from: "NEW", to: "READY" }, { actor: "system" }))}\n{"id": 2, "trunc\n42\nnull\n\n`;
    const { events: out, bad } = parseJsonl(text);
    expect(out.map((e) => e.id)).toEqual([1]);
    expect(bad.map((b) => b.line)).toEqual([2, 3, 4]);
  });
});
