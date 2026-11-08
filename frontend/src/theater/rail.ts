// Rail numbers. Live mode (slice 3) reads these from GET /api/state (contract
// §4). Fixture mode has no gateway, so it projects the same shape from the
// events played so far, following the contract's definitions:
//   tax_saved_cents = Σ over sent cases of max(0, authorized − (total − annual fee))
//                     (v1.4.1: the fee is paid either way, so it is never a saving)
//   spent_usd       = Σ cost_usd of cost.booked events only (v1.7.4; decimal strings, no floats)

import type { UTaxEvent } from "./events";
import { normalizeDecimal } from "./format";

export interface FirmNumbers {
  firm: { agents: number; clients: number };
  touches: { training: number; operating: number; engineering: number };
  /**
   * v1.19 (D38): `contractor_paid_usd` (Σ payment.sent ok) and `agent_sales_usd` (Σ agent.sale), in
   * testnet tokens; absent when the market is off.
   */
  money: { tax_saved_cents: number; spent_usd: string; contractor_paid_usd?: string; agent_sales_usd?: string };
  /** Counts only: the projector never shows candidate names (contract v1.6). */
  hiring: { applicants: number; screened: number; shortlisted: number; hired: number; declined?: number; awaiting?: number; sourced?: number; contacted?: number };
  queue: { waiting: number; blocked: number };
}

export const AGENT_COUNT = 7;

/** Candidate stages in order; hired and declined are both final. */
const STAGE_RANK: Record<string, number> = { applicant: 0, screened: 1, shortlisted: 2, hired: 3, declined: 3 };
/** Action types with a one-time training touch (contract: trust action_type values). */
export const TRAINABLE_ACTION_TYPES = 5; // v1.19: + contractor_pay

const SCALE = BigInt("10000000000"); // fixed-point scale for USD sums

function toUnits(decimal: string): bigint {
  const n = normalizeDecimal(decimal);
  const neg = n.startsWith("-");
  const [whole, frac = ""] = (neg ? n.slice(1) : n).split(".");
  const units = BigInt(whole) * SCALE + BigInt((frac + "0".repeat(10)).slice(0, 10));
  return neg ? -units : units;
}

function fromUnits(units: bigint): string {
  const neg = units < BigInt(0);
  const abs = neg ? -units : units;
  const whole = abs / SCALE;
  const frac = (abs % SCALE).toString().padStart(10, "0").replace(/0+$/, "");
  return `${neg ? "-" : ""}${whole}${frac ? `.${frac}` : ""}`;
}

/** Sum decimal USD strings exactly. Malformed values are ignored (the script reports them). */
export function sumUsd(values: readonly string[]): string {
  let total = BigInt(0);
  for (const v of values) {
    try {
      total += toUnits(v);
    } catch {
      // skipped: not a decimal string
    }
  }
  return fromUnits(total);
}

const num = (v: unknown) => (typeof v === "number" && Number.isSafeInteger(v) ? v : null);

/**
 * Project the rail numbers from an event list. `skip` holds ids the script
 * rejected, so a malformed event never moves a number.
 */
export function railFromEvents(events: readonly UTaxEvent[], skip: ReadonlySet<number> = new Set()): FirmNumbers {
  const clients = new Set<string>();
  const touches = { training: 0, operating: 0, engineering: 0 };
  const hiring: FirmNumbers["hiring"] & { declined: number; awaiting: number } = { applicants: 0, screened: 0, shortlisted: 0, hired: 0, declined: 0, awaiting: 0 };
  let sourced = 0; // v1.11.1: public leads HR found (hr.sourced counts)
  const contacted = new Set<string>(); // v1.11.1: candidates HR emailed (hr.outreach)
  // Hiring mirrors the gateway projection: each candidate counts once per stage
  // reached, and stages only move forward (a questionnaire reply re-sends
  // hr.applicant for an existing candidate; it must not demote them).
  const reached = new Map<string, Set<string>>(); // candidate → stages reached
  const furthest = new Map<string, number>(); // candidate → furthest stage rank
  const seen = new Set<number>();
  const lastTax = new Map<string, number>(); // case → saving from its latest valid tax.computed
  const sentSaving = new Map<string, number>(); // case → saving as of its latest memo.sent
  const status = new Map<string, string>();
  const costs: string[] = [];
  const paid: string[] = []; // v1.19: tokens paid to contractors
  const sales: string[] = []; // v1.19: tokens agents paid us

  for (const e of [...events].sort((a, b) => a.id - b.id)) {
    if (!Number.isSafeInteger(e.id) || seen.has(e.id) || skip.has(e.id)) continue;
    seen.add(e.id);
    // Contract v1.7.4: spent = Σ cost_usd over cost.booked only. Other events (tool.bought,
    // law.searched, post.published, screen.call) may show a cost but are booked separately, so
    // summing them would double-count. A failed provider call still books its cost.
    // v1.19 §9.1: a contractor payment's cost.booked is testnet tokens, not dollars: never "spent".
    const model = (e.payload as Record<string, unknown> | null)?.model;
    const tokensOnly = typeof model === "string" && model.startsWith("contractor:");
    if (e.type === "cost.booked" && typeof e.cost_usd === "string" && !tokensOnly) costs.push(e.cost_usd);
    const amount = (e.payload as Record<string, unknown> | null)?.amount_usd;
    const amountStr = typeof amount === "number" && Number.isFinite(amount) ? String(amount) : typeof amount === "string" ? amount : null;
    if (e.status === "ok" && amountStr !== null && e.type === "payment.sent") paid.push(amountStr);
    if (e.status === "ok" && amountStr !== null && e.type === "agent.sale") sales.push(amountStr);
    if (e.status !== "ok") continue;
    const p = (e.payload ?? {}) as Record<string, unknown>;
    const c = e.case_id;
    const candidate = typeof p.candidate_id === "string" ? p.candidate_id : null;
    const stage = e.type.startsWith("hr.") ? e.type.slice(3) : "";
    if (candidate && Object.hasOwn(STAGE_RANK, stage)) {
      const set = reached.get(candidate) ?? new Set<string>();
      set.add(stage);
      reached.set(candidate, set);
      furthest.set(candidate, Math.max(furthest.get(candidate) ?? -1, STAGE_RANK[stage]));
    }
    switch (e.type) {
      case "email.received":
        if (c) clients.add(c);
        break;
      case "voice.command":
        // A /talk customer opens a fast-path case without an email (contract §5).
        if (c && p.speaker === "customer") clients.add(c);
        break;
      case "touch.training":
      case "touch.operating":
      case "touch.engineering":
        touches[e.type.slice(e.type.indexOf(".") + 1) as keyof typeof touches] += 1;
        break;
      case "tax.computed": {
        const a = num(p.authorized_method_cents);
        const t = num(p.total_cents);
        const fee = num(p.annual_report_fee_cents);
        if (!c) break;
        // Gateway parity (contract v1.7.1): a malformed later computation keeps the last valid value.
        if (a !== null && t !== null && fee !== null && a >= 0 && t >= fee && fee >= 0) lastTax.set(c, Math.max(0, a - (t - fee)));
        break;
      }
      case "memo.sent":
        // A case's saving is the tax.computed preceding its latest memo.sent.
        if (c) sentSaving.set(c, lastTax.get(c) ?? 0);
        break;
      case "case.status":
        if (c && typeof p.to === "string") status.set(c, p.to);
        break;
      case "case.blocked":
        if (c) status.set(c, "BLOCKED");
        break;
      case "hr.sourced": {
        const n = num(p.count);
        if (n !== null && n >= 0) sourced += n;
        break;
      }
      case "hr.outreach":
        if (candidate) contacted.add(candidate);
        break;
    }
  }

  for (const stages of reached.values()) {
    if (stages.has("applicant")) hiring.applicants += 1;
    if (stages.has("screened")) hiring.screened += 1;
    if (stages.has("shortlisted")) hiring.shortlisted += 1;
    if (stages.has("hired")) hiring.hired += 1;
    if (stages.has("declined")) hiring.declined += 1;
  }
  // Only once D31 hiring happened, so the pre-D31 rail shape is unchanged.
  if (sourced > 0) hiring.sourced = sourced;
  if (contacted.size > 0) hiring.contacted = contacted.size;
  hiring.awaiting = [...furthest.values()].filter((r) => r === STAGE_RANK.shortlisted).length;

  let saved = 0;
  for (const v of sentSaving.values()) {
    const next = saved + v;
    if (Number.isSafeInteger(next)) saved = next; // an absurd value can never break the money panel
  }
  const blocked = [...status.values()].filter((s) => s === "BLOCKED").length;

  return {
    firm: { agents: AGENT_COUNT, clients: clients.size },
    touches,
    money: {
      tax_saved_cents: saved,
      spent_usd: sumUsd(costs),
      ...(paid.length ? { contractor_paid_usd: sumUsd(paid) } : {}),
      ...(sales.length ? { agent_sales_usd: sumUsd(sales) } : {}),
    },
    hiring,
    // Waiting chips need the gateway's queue; a log alone can't know it.
    queue: { waiting: 0, blocked },
  };
}

/** The scoreboard number: training + operating. Engineering fixes are a separate column (premise 1). */
export const humanTouches = (n: FirmNumbers) => n.touches.training + n.touches.operating;
