// Rail numbers. Live mode (slice 3) reads these from GET /api/state (contract
// §4). Fixture mode has no gateway, so it projects the same shape from the
// events played so far, following the contract's definitions:
//   tax_saved_cents = Σ over sent cases of max(0, authorized − (total − annual fee))
//                     (v1.4.1: the fee is paid either way, so it is never a saving)
//   spent_usd       = Σ envelope cost_usd (decimal strings, no floats)

import type { UTaxEvent } from "./events";
import { normalizeDecimal } from "./format";

export interface FirmNumbers {
  firm: { agents: number; clients: number };
  touches: { training: number; operating: number; engineering: number };
  money: { tax_saved_cents: number; spent_usd: string };
  hiring: { applicants: number; screened: number; shortlisted: number; hired: number };
  queue: { waiting: number; blocked: number };
}

export const AGENTS = 7;

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
  const hiring = { applicants: 0, screened: 0, shortlisted: 0, hired: 0 };
  const lastTax = new Map<string, number>(); // case → franchise tax saved
  const sent = new Set<string>();
  const status = new Map<string, string>();
  const costs: string[] = [];

  for (const e of [...events].sort((a, b) => a.id - b.id)) {
    if (skip.has(e.id) || e.status !== "ok") continue;
    const p = (e.payload ?? {}) as Record<string, unknown>;
    if (typeof e.cost_usd === "string") costs.push(e.cost_usd);
    const c = e.case_id;
    switch (e.type) {
      case "email.received":
        if (c) clients.add(c);
        break;
      case "touch.training":
      case "touch.operating":
      case "touch.engineering":
        touches[e.type.slice(e.type.indexOf(".") + 1) as keyof typeof touches] += 1;
        break;
      case "hr.applicant":
        hiring.applicants += 1;
        break;
      case "hr.screened":
        hiring.screened += 1;
        break;
      case "hr.shortlisted":
        hiring.shortlisted += 1;
        break;
      case "hr.hired":
        hiring.hired += 1;
        break;
      case "tax.computed": {
        const a = num(p.authorized_method_cents);
        const t = num(p.total_cents);
        const fee = num(p.annual_report_fee_cents);
        if (c && a !== null && t !== null && fee !== null) lastTax.set(c, Math.max(0, a - (t - fee)));
        break;
      }
      case "memo.sent":
        if (c) sent.add(c);
        break;
      case "case.status":
        if (c && typeof p.to === "string") status.set(c, p.to);
        break;
      case "case.blocked":
        if (c) status.set(c, "BLOCKED");
        break;
    }
  }

  let saved = 0;
  for (const c of sent) saved += lastTax.get(c) ?? 0;
  const blocked = [...status.values()].filter((s) => s === "BLOCKED").length;

  return {
    firm: { agents: AGENTS, clients: clients.size },
    touches,
    money: { tax_saved_cents: saved, spent_usd: sumUsd(costs) },
    hiring,
    // Waiting chips need the gateway's queue; a log alone can't know it.
    queue: { waiting: 0, blocked },
  };
}

/** The scoreboard number: training + operating. Engineering fixes are a separate column (premise 1). */
export const humanTouches = (n: FirmNumbers) => n.touches.training + n.touches.operating;
