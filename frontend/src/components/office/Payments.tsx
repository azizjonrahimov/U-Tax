"use client";

// D41 F50 (runbook §E.1): the Accounting desk's Payments tab. The ledger from GET /api/payments
// (B52), on open, every 15 s and right after a payment event on the stream. Testnet tokens are
// labelled as such and never shown as dollars; a Stripe test-mode copy is labelled "mirror".

import { useEffect, useRef, useState } from "react";
import { API_URL } from "@/lib/api";
import { formatClock } from "@/theater/format";
import { DeptAvatar } from "@/components/ui/DeptAvatar";
import { EmptyState } from "@/components/ui/EmptyState";
import { money, parseLedger, railName, shortTx, type Ledger } from "@/office/payments";

const TOKEN = process.env.NEXT_PUBLIC_THEATER_TOKEN ?? "";
const POLL_MS = 15_000;

function ledgerUrl(session: string | null) {
  const u = new URL(`${API_URL.replace(/\/+$/, "")}/api/payments`);
  if (!session && TOKEN) u.searchParams.set("token", TOKEN);
  return u.toString();
}

/** `tick` changes whenever a payment.* or agent.sale event arrives: the ledger refetches at once. */
export function Payments({ session, tick }: { session: string | null; tick: number }) {
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [failed, setFailed] = useState(false);
  const seen = useRef<Set<number> | null>(null);
  const [fresh, setFresh] = useState<Set<number>>(new Set());

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(ledgerUrl(session), { cache: "no-store", signal: AbortSignal.timeout(10_000), ...(session ? { headers: { Authorization: `Bearer ${session}` } } : {}) });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const l = parseLedger(await r.json());
        if (!alive || !l) return;
        // A row that wasn't there on the last read flashes lime once (none on the first read).
        const ids = new Set(l.rows.map((x) => x.id));
        setFresh(seen.current ? new Set([...ids].filter((id) => !seen.current!.has(id))) : new Set());
        seen.current = ids;
        setLedger(l);
        setFailed(false);
      } catch {
        if (alive) setFailed(true);
      }
    };
    void load();
    const t = setInterval(() => void load(), POLL_MS);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [session, tick]);

  if (!ledger || ledger.rows.length === 0) {
    return (
      <section className="payapp empty-pay" aria-label="Payments">
        <EmptyState art="sheet" title={failed && !ledger ? "Payments unavailable" : "No payments yet"} line={failed && !ledger ? "The ledger couldn't be read just now; it retries every 15 s." : "Contractor payments and sales show here with their on-chain proof."} />
      </section>
    );
  }
  return (
    <section className="payapp" aria-label="Payments">
      <p className="pay-totals">
        Paid to contractors <b>{ledger.contractorsPaid ? money(ledger.contractorsPaid.amount, ledger.contractorsPaid.token) : "—"}</b>
        {" · "}Received from agents <b>{ledger.agentSales ? money(ledger.agentSales.amount, ledger.agentSales.token) : "—"}</b>
        {" · "}Client payments <b>{ledger.clientsPaidUsd !== null ? money(ledger.clientsPaidUsd, "USD") : "—"}</b>
        <span className="pay-note mono"> testnet tokens have no real value</span>
      </p>
      <div className="pay-scroll">
        <table className="pay-table">
          <thead>
            <tr>
              <th scope="col">time</th>
              <th scope="col">what</th>
              <th scope="col">amount</th>
              <th scope="col">rail</th>
              <th scope="col">proof</th>
              <th scope="col">status</th>
            </tr>
          </thead>
          <tbody>
            {ledger.rows.map((r) => (
              <tr key={r.id} className={fresh.has(r.id) ? "new" : undefined}>
                <td className="mono">{formatClock(r.ts)}</td>
                <td>
                  <span className="pay-what">
                    <DeptAvatar seat={r.kind === "contractor" ? "mkt" : "acct"} size={22} />
                    <span>
                      {r.direction === "out" ? "Paid " : "Received from "}
                      <b>{r.counterparty}</b>
                      {r.jobId ? ` for ${r.jobId}` : ""}
                      {r.kind === "agent_sale" ? " · franchise-tax check" : r.kind === "client" ? " · filing help" : ""}
                    </span>
                  </span>
                </td>
                <td className="mono pay-amt">{money(r.amount, r.token)}</td>
                <td>
                  <span className="pay-chip">{railName(r.rail)}</span>
                </td>
                <td>
                  <span className="pay-proof">
                  {r.tx && (r.explorer ? (
                    <a className="mono" href={r.explorer} target="_blank" rel="noopener noreferrer">
                      tx {shortTx(r.tx)} ↗
                    </a>
                  ) : (
                    <span className="mono">tx {shortTx(r.tx)}</span>
                  ))}
                  {r.stripe &&
                    (r.stripe.url ? (
                      <a className="pay-chip stripe" href={r.stripe.url} target="_blank" rel="noopener noreferrer">
                        Stripe test · {r.stripe.mode} ↗
                      </a>
                    ) : (
                      <span className="pay-chip stripe">Stripe test · {r.stripe.mode}</span>
                    ))}
                  </span>
                </td>
                <td>
                  <span className={r.status === "ok" ? "pay-chip ok" : "pay-chip bad"}>{r.status === "ok" ? (r.direction === "out" ? "paid" : "received") : r.status}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
