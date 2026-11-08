"use client";

// D41 F50.3 (runbook §E): the CEO console's Payments card, under the money card in aside.side.
// The last 5 rows of GET /api/payments (B52), read with the console's admin session (Bearer
// header, never a URL), on open, every 15 s and when `tick` changes (a new activity line).
// Testnet tokens keep their token name (never "$"); a Stripe test-mode copy says "mirror".
// Any error hides the card: the ledger in the office is the full view.

import { useEffect, useState } from "react";
import { formatClock } from "@/theater/format";
import { DeptAvatar } from "@/components/ui/DeptAvatar";
import { money, parseLedger, railName, shortTx, type Ledger, type PayRow } from "@/office/payments";
import { API, authJson } from "./gateway";

const POLL_MS = 15_000;
const SHOWN = 5;
const LEDGER_HREF = "/office?d=acct&tab=payments";

/** What a row is, in words ("Paid PixelAgent", "Team #1 paid"). */
function what(r: PayRow): string {
  if (r.direction === "out") return `Paid ${r.counterparty}${r.jobId ? ` · ${r.jobId}` : ""}`;
  if (r.kind === "client") return `${r.counterparty} paid`;
  return `Sale to ${r.counterparty}`;
}

export function Payments({ token, tick }: { token: string; tick: number }) {
  const [ledger, setLedger] = useState<Ledger | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const l = parseLedger(await authJson<unknown>(token, `${API}/payments`));
        if (!alive) return;
        if (!l) throw new Error("malformed ledger");
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
  }, [token, tick]);

  if (failed || !ledger) return null;
  const rows = ledger.rows.slice(0, SHOWN);
  return (
    <section aria-labelledby="pay-h">
      <h5 id="pay-h">
        payments <span>{ledger.rows.length ? `latest ${rows.length}` : ""}</span>
      </h5>
      <div className="card pays">
        {rows.length === 0 ? (
          <p className="hint">No payments yet. Contractor payments and sales show here with their proof.</p>
        ) : (
          <ul className="pay-rows">
            {rows.map((r) => (
              <li key={r.id} className={r.status === "ok" ? "pay-row" : "pay-row bad"}>
                <DeptAvatar seat="acct" size={22} />
                <div className="pay-what">
                  <p>
                    {what(r)} <span className="amt">{money(r.amount, r.token)}</span>
                  </p>
                  <small>
                    {formatClock(r.ts)} · {railName(r.rail)}
                    {r.status !== "ok" ? ` · ${r.status}` : ""}
                    {r.tx &&
                      (r.explorer ? (
                        <>
                          {" · "}
                          <a href={r.explorer} target="_blank" rel="noreferrer">
                            tx {shortTx(r.tx)} ↗
                          </a>
                        </>
                      ) : (
                        ` · tx ${shortTx(r.tx)}`
                      ))}
                  </small>
                  {r.stripe?.mode === "mirror" &&
                    (r.stripe.url ? (
                      <a className="pay-chip" href={r.stripe.url} target="_blank" rel="noreferrer">
                        Stripe test · mirror ↗
                      </a>
                    ) : (
                      <span className="pay-chip">Stripe test · mirror</span>
                    ))}
                </div>
              </li>
            ))}
          </ul>
        )}
        <a className="pay-open" href={LEDGER_HREF}>
          Open the ledger ↗
        </a>
      </div>
    </section>
  );
}
