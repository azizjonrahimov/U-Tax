// Theater panes. Pure rendering of ScreenState; every element a cursor can
// visit carries data-target matching the script's target keys.

import { memo, type CSSProperties, type ReactNode } from "react";
import type { FirmNumbers } from "@/theater/rail";
import { humanTouches, TRAINABLE_ACTION_TYPES } from "@/theater/rail";
import { visibleOrders, type Cell, type MarketJob, type ScreenState } from "@/theater/screen";
import type { Pane as PaneKey } from "@/theater/script";
import { formatCents, formatClock, formatUsd } from "@/theater/format";
import { assetAllowed, originOf, parseAssetHosts } from "@/theater/live";
import { MicIcon } from "@/components/MicIcon";

/** Text as it should show right now (a step being typed reveals progressively). */
export type Typed = (target: string, full: string | undefined) => string | undefined;

export const SHOW_BLOCKED = process.env.NEXT_PUBLIC_SHOW_BLOCKED !== "0";
export const SHOW_QUEUE = process.env.NEXT_PUBLIC_SHOW_QUEUE !== "0";
/** v1.6.4: images render only from these hosts (NEXT_PUBLIC_ASSET_HOSTS, comma-separated). */
const ASSET_HOSTS = parseAssetHosts(process.env.NEXT_PUBLIC_ASSET_HOSTS);
/** D31: the gateway's own origin serves the ad images it renders (no NEXT_PUBLIC_ASSET_HOSTS entry needed). */
const GATEWAY_ORIGIN = originOf(process.env.NEXT_PUBLIC_API_URL);
const imageOk = (url: string | undefined) => assetAllowed(url, ASSET_HOSTS, GATEWAY_ORIGIN);
/** An image that fails to load (a tunnel host gone after a restart) disappears instead of showing a broken icon. */
const hideBroken = (e: { currentTarget: HTMLImageElement }) => {
  e.currentTarget.hidden = true;
};

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Rail case rows shown (blocked cases always show as cards above them). */
const MAX_CASE_ROWS = 8;

function Pane(props: {
  k: PaneKey;
  screen: ScreenState;
  label: string;
  name: ReactNode;
  tag: string;
  children: ReactNode;
}) {
  const { k, screen } = props;
  const failure = screen.failure?.pane === k ? screen.failure : undefined;
  return (
    <section className={`pane${screen.camera === k ? " focus" : ""}`} data-pane={k} data-target={k} aria-label={props.label}>
      <div className="ph">
        {props.label} <b>{props.name}</b>
        <span className="tag">{props.tag}</span>
      </div>
      <div className="pb">{props.children}</div>
      {failure && <div className="failnote">✗ {failure.text}</div>}
    </section>
  );
}

/** Wrap each quoted span (first occurrence, non-overlapping) in a highlight. */
function highlight(body: string, quotes: Record<string, string>): ReactNode[] {
  const ranges = Object.entries(quotes)
    .map(([fact, q]) => ({ fact, i: q ? body.indexOf(q) : -1, len: q.length }))
    .filter((r) => r.i >= 0)
    .sort((a, b) => a.i - b.i);
  const out: ReactNode[] = [];
  let at = 0;
  for (const r of ranges) {
    if (r.i < at) continue;
    out.push(body.slice(at, r.i));
    out.push(
      <span key={r.fact} className="hl" data-target={`mail.quote.${r.fact}`}>
        {body.slice(r.i, r.i + r.len)}
      </span>,
    );
    at = r.i + r.len;
  }
  out.push(body.slice(at));
  return out;
}

export function MailPane({ screen, typed }: { screen: ScreenState; typed: Typed }) {
  const view = screen.mailCase ? screen.cases[screen.mailCase] : undefined;
  const m = view?.mail;
  return (
    <Pane k="mail" screen={screen} label="Mail" name="firm inbox" tag="via Gmail · rendered from log">
      {!m?.body ? (
        <div className="empty">No mail yet.</div>
      ) : (
        <>
          <div className="from" data-target="mail.message">
            From: {m.from} · “{m.hidden ? "hidden: flagged by QA" : m.subject}”{view?.name ? ` · ${view.name}` : ""}
          </div>
          {m.hidden ? (
            <div className="body hidden-body">hidden: flagged by QA</div>
          ) : (
            <div className="body">{highlight(m.body, m.quotes)}</div>
          )}
          {m.compose && (
            <div className="compose" data-target="mail.compose">
              <div className="to">{m.compose.header}</div>
              <div className="txt mono" data-target="mail.memo">
                {typed("mail.memo", m.compose.text)}
              </div>
              <span className={`btn${m.compose.pressed ? " pressed" : ""}`} data-target="mail.send">
                Send
              </span>
              {m.compose.sent && (
                <span className="sent" data-target="mail.sent">
                  {m.compose.sent}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </Pane>
  );
}

const FACT_ROWS: [string, string][] = [
  ["incorporated_delaware", "Delaware corporation"],
  ["authorized_shares", "Authorized shares"],
  ["issued_shares", "Issued shares"],
  ["gross_assets", "Gross assets (proj. Dec 31)"],
  ["stated_par", "Stated par"],
  ["foreign_owner_25", "Foreign owner 25%+"],
  ["gross_receipts", "Gross receipts"],
  ["years_with_receipts", "Years with receipts"],
];
const RESULT_ROWS: [string, string][] = [
  ["authorized_method", "Authorized-shares method"],
  ["assumed_par_method", "Assumed-par method"],
  ["annual_report_fee", "+ annual report fee"],
];
/** Case chip tone by contract status (not display wording). */
const CASE_TONE: Record<string, "ok" | "bad" | "run"> = {
  QA_PASSED: "ok",
  SENT: "ok",
  CALL_BOOKED: "ok",
  BLOCKED: "bad",
  QA_FAILED: "bad",
  NOT_ELIGIBLE: "bad",
};
const OPTIONAL = new Set(["gross_receipts", "years_with_receipts"]);
const KNOWN = new Set([...FACT_ROWS, ...RESULT_ROWS].map(([k]) => k).concat("total"));

function Value({ k, cell, extra }: { k: string; cell?: Cell; extra?: ReactNode }) {
  const cls = ["v", cell?.mark ?? "", cell?.missing ? "missing" : "", cell?.circle ? "circled" : ""].filter(Boolean).join(" ");
  return (
    <td className={cls} data-target={`sheet.cell.${k}`} title={cell?.circle ?? cell?.tick}>
      {cell?.text || "—"}
      {cell?.assumed && <span className="assumed">assumed</span>}
      {cell?.tick && !extra && <span className="tick">✓</span>}
      {extra}
    </td>
  );
}

// Memoized: these only re-render when the screen or numbers change, not on every typing tick.
export const SheetPane = memo(function SheetPane({ screen }: { screen: ScreenState }) {
  const view = screen.sheetCase ? screen.cases[screen.sheetCase] : undefined;
  const sh = view?.sheet;
  const cells = sh?.cells ?? {};
  const facts = FACT_ROWS.filter(([k]) => !OPTIONAL.has(k) || cells[k]);
  const other = Object.keys(cells).filter((k) => !KNOWN.has(k));
  const qa = sh?.qa ? (
    <span className={`qa${sh.qa.startsWith("✓") ? "" : " bad"}`} data-target="sheet.qa">
      {sh.qa}
    </span>
  ) : null;
  return (
    <Pane k="sheet" screen={screen} label="Sheet" name={view ? `${view.name} · franchise tax` : "franchise tax"} tag="engine v1 · rendered from log">
      <table>
        <tbody>
          {facts.map(([k, label]) => (
            <tr key={k}>
              <td>{label}</td>
              <Value k={k} cell={cells[k]} />
            </tr>
          ))}
          {RESULT_ROWS.map(([k, label]) => (
            <tr key={k}>
              <td>{label}</td>
              <Value k={k} cell={cells[k]} />
            </tr>
          ))}
          <tr>
            <td>
              <b>Estimated total</b>
            </td>
            <Value k="total" cell={cells.total} extra={qa} />
          </tr>
          {other.map((k) => (
            <tr key={k}>
              <td>{k.replace(/_/g, " ")}</td>
              <Value k={k} cell={cells[k]} />
            </tr>
          ))}
        </tbody>
      </table>
      {sh && sh.flags.length > 0 && (
        <div className="flags" data-target="sheet.flag">
          {sh.flags.map((f) => (
            <span key={f} className="flag">
              {f}
            </span>
          ))}
        </div>
      )}
    </Pane>
  );
});

export function SearchPane({ screen, typed }: { screen: ScreenState; typed: Typed }) {
  const s = screen.search;
  return (
    <Pane k="search" screen={screen} label="Search" name={s.source ? "people" : "law"} tag={s.source ?? "Naïve Data catalogue"}>
      <div className="q" data-target="search.query">
        {typed("search.query", s.query) || <span className="empty">No searches yet.</span>}
      </div>
      {s.results.map((r, i) => (
        <div key={i} className="res" data-target={`search.result.${i}`}>
          {r}
        </div>
      ))}
      {s.more && (
        <div className="res" data-target="search.more">
          {s.more}
        </div>
      )}
    </Pane>
  );
}

export const CalendarPane = memo(function CalendarPane({ screen }: { screen: ScreenState }) {
  const last = screen.calendar.blocks.at(-1);
  const start = last?.start ? new Date(last.start) : null;
  // Guard the date math: a time at the edge of the Date range must not break the render.
  const base = start && Number.isFinite(start.getTime()) && Math.abs(start.getTime()) < 8.6e15 - 3_600_000 ? new Date(start) : null;
  if (base) base.setMinutes(base.getMinutes() < 30 ? 0 : 30, 0, 0);
  const slots = Array.from({ length: 4 }, (_, i) => (base ? new Date(base.getTime() + (i - 1) * 30 * 60_000) : null));
  const day = base ? base.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }) : "today";
  return (
    <Pane k="calendar" screen={screen} label="Calendar" name={day} tag="via calendar invite">
      <div className="cal" data-target="calendar.block">
        {slots.map((t, i) => {
          const label = t ? formatClock(t.toISOString()) : ["9:00", "9:30", "10:00", "10:30"][i];
          const here = t && base && i === 1 ? last : undefined;
          return (
            <div key={i} style={{ display: "contents" }}>
              <div className="h">{label}</div>
              <div className="slot">{here && <div className="block">{here.text}</div>}</div>
            </div>
          );
        })}
      </div>
      {!last && <div className="empty">No calls booked yet.</div>}
    </Pane>
  );
});

export function SocialPane({ screen, typed }: { screen: ScreenState; typed: Typed }) {
  const s = screen.social;
  const img = imageOk(s.image) ? s.image : undefined;
  return (
    <Pane k="social" screen={screen} label="Social" name="posts & DMs" tag={`via ${s.channel ?? "social"} · rendered from log`}>
      {!s.caption && !s.dm ? (
        <div className="empty">No posts yet.</div>
      ) : (
        <>
          {s.caption && (
            <div className="post" data-target="social.caption">
              <div className="chan">{s.channel}</div>
              {img && (
                // eslint-disable-next-line @next/next/no-img-element -- agent-supplied https image, rendered as given
                <img key={img} src={img} alt="" data-target="social.image" referrerPolicy="no-referrer" onError={hideBroken} />
              )}
              <div>{typed("social.caption", s.caption)}</div>
              <span className="btn" data-target="social.post">
                Post
              </span>
              {s.posted && (
                <span className="sent" data-target="social.posted">
                  ✓ {s.posted}
                </span>
              )}
            </div>
          )}
          {s.dm && (
            <div className="post" data-target="social.dm" style={{ marginTop: 8 }}>
              <div className="chan">DM draft · {s.dm.recipient}</div>
              <div>{typed("social.dm", s.dm.text)}</div>
              <span className="btn" data-target="social.dm_send">
                Send (CEO)
              </span>
              {s.dm.sent && <span className="sent">✓ sent by the CEO</span>}
            </div>
          )}
        </>
      )}
    </Pane>
  );
}

/** A market total shows once it's above zero (the market off, or nothing paid yet: no row). */
const tokensShown = (v: string | undefined) => v !== undefined && Number(v) > 0 && Number.isFinite(Number(v));

const JOB_STATUS: Record<MarketJob["status"], string> = {
  open: "posted · waiting for a contractor",
  claimed: "claimed",
  submitted: "work submitted · checking",
  verified: "all checks passed",
  failed: "failed",
  paid: "paid",
  delivered: "delivered",
};

/**
 * v1.19 (D38): Marketing hires an outside agent. Every state below is one event: the job card
 * (job.posted), the grey contractor cursor (job.claimed), the blurred card (job.submitted), the
 * checks (job.verified / job.failed), the coin (payment.sent), the unblurred card (job.delivered).
 */
export function MarketPane({ screen }: { screen: ScreenState }) {
  const jobs = screen.market.jobs;
  const job = jobs.at(-1);
  if (!job) return null;
  const src = job.image && GATEWAY_ORIGIN ? `${GATEWAY_ORIGIN}${job.image}` : undefined;
  const img = imageOk(src) ? src : undefined;
  const claimed = job.status !== "open";
  const submitted = job.card === true;
  return (
    <Pane k="market" screen={screen} label="Market" name="outside agents" tag="testnet tokens · no real value">
      <div className="wallets">
        <span className="chip wallet" data-target="market.wallet.firm">
          U-Tax wallet
        </span>
        <span className={`coin${job.pay ? " flying" : ""}`} key={job.pay ? `${job.id}:paid` : job.id} aria-hidden="true" />
        <span className="chip wallet" data-target="market.wallet.contractor">
          {job.contractor ?? "contractor"} wallet
        </span>
      </div>
      <div className={`job ${job.status}`} data-target="market.job">
        <div className="chan">
          {job.template.replace(/_/g, " ")} · {job.price} pathUSD
        </div>
        <div className="headline">{job.headline}</div>
        {claimed && job.contractor && (
          <span className="contractor" data-target="market.claim">
            <span className="arrow" aria-hidden="true" />
            {job.contractor} · contractor
          </span>
        )}
        {submitted && (
          <div className={`thumb${job.status === "delivered" ? " clear" : ""}`} data-target="market.thumb">
            {job.status === "delivered" && img ? (
              // eslint-disable-next-line @next/next/no-img-element -- the gateway's own asset, rendered as given
              <img src={img} alt="Share card made by the contractor" referrerPolicy="no-referrer" onError={hideBroken} data-target="market.delivered" />
            ) : (
              <span>{job.status === "delivered" ? "card delivered" : "card (hidden until paid)"}</span>
            )}
          </div>
        )}
        {job.checks.length > 0 && (
          <ul className="checks">
            {job.checks.map((c, i) => (
              <li key={i} className={c.ok ? "ok" : "bad"} data-target={`market.check.${i}`}>
                {c.ok ? "✓" : "✕"} {c.name}
              </li>
            ))}
          </ul>
        )}
        <div className="state" data-target="market.status">
          {JOB_STATUS[job.status]}
          {job.status === "failed" && job.reason ? ` · ${job.reason}` : ""}
        </div>
        {job.pay && (
          <div className="paid" data-target="market.pay">
            ✓ Paid {job.pay.amount}
            {job.pay.ledger ? (
              " · ledger token"
            ) : job.pay.tx ? (
              <>
                {" · "}
                {job.pay.url ? (
                  <a className="mono" href={job.pay.url} target="_blank" rel="noopener noreferrer">
                    {job.pay.tx}
                  </a>
                ) : (
                  <span className="mono">{job.pay.tx}</span>
                )}
              </>
            ) : null}
          </div>
        )}
      </div>
      {jobs.length > 1 && (
        <div className="earlier">
          {jobs.slice(0, -1).map((j) => (
            <div key={j.id}>
              {j.headline} · {JOB_STATUS[j.status]}
            </div>
          ))}
        </div>
      )}
    </Pane>
  );
}

export function PhonePane({ screen, typed }: { screen: ScreenState; typed: Typed }) {
  const p = screen.phone;
  return (
    <Pane k="phone" screen={screen} label="Phone" name="device & calls" tag="rendered from log">
      {!p.dial && !p.check ? (
        <div className="empty">No calls or checks yet.</div>
      ) : (
        <>
          {p.check && (
            <span className={`chip ${p.check.ok ? "ok" : "bad"}`} data-target="phone.check">
              {p.check.ok ? "✓" : "✗"} {p.check.text}
            </span>
          )}
          {p.dial && (
            <div data-target="phone.dial">
              <div className="from">Screen call · {p.dial}</div>
              <div className="wave" aria-hidden>
                {Array.from({ length: 16 }, (_, i) => (
                  <i key={i} style={{ animationDelay: `${(i % 5) * 0.12}s` }} />
                ))}
              </div>
              <div className="transcript" data-target="phone.transcript">
                {typed("phone.transcript", p.transcript)}
              </div>
            </div>
          )}
        </>
      )}
    </Pane>
  );
}

export const Rail = memo(function Rail({ screen, numbers }: { screen: ScreenState; numbers: FirmNumbers }) {
  const trust = Object.values(screen.rail.trust);
  const auto = trust.filter((t) => t === "AUTO").length;
  const saved = formatCents(numbers.money.tax_saved_cents);
  const spent = formatUsd(numbers.money.spent_usd);
  const blocked = screen.caseOrder.map((c) => screen.caseRows[c]).filter((r) => r?.blocked);
  return (
    <aside aria-label="Firm numbers">
      <div className="stagebox">
        <div>
          Clients served<b>{numbers.firm.clients}</b>
        </div>
        <div>
          Human touches<b>{humanTouches(numbers)}</b>
        </div>
        <div>
          Saved vs spent<b className="g">{saved} / {spent}</b>
        </div>
        {tokensShown(numbers.money.contractor_paid_usd) && (
          <div>
            Contractors paid (testnet)<b>{Number(numbers.money.contractor_paid_usd).toFixed(2)} pathUSD</b>
          </div>
        )}
        {tokensShown(numbers.money.agent_sales_usd) && (
          <div>
            Earned from agents (testnet)<b>{Number(numbers.money.agent_sales_usd).toFixed(2)} pathUSD</b>
          </div>
        )}
      </div>
      <section className="pane" data-target="rail.touches" aria-label="Human touches">
        <div className="ph">Human touches</div>
        <div className="pb">
          <div className="k">
            <span>Operating</span>
            <span className="big">{numbers.touches.operating}</span>
          </div>
          <div className="k" data-target="rail.trust">
            <span>Training (1 per action type)</span>
            <span className="mono">
              {numbers.touches.training} / {TRAINABLE_ACTION_TYPES}
              {trust.length ? ` · ${auto} AUTO` : ""}
            </span>
          </div>
          {numbers.touches.engineering > 0 && (
            <div className="k">
              <span>Engineering fixes</span>
              <span className="mono">{numbers.touches.engineering}</span>
            </div>
          )}
          <div className="log">
            {screen.rail.log.slice(-4).map((l) => (
              <div key={`${l.eventId}:${l.kind}`}>
                <span className="h">{formatClock(l.ts)}</span>
                {l.voice && (
                  <span className="by-voice">
                    <MicIcon label="by voice" />{" "}
                  </span>
                )}
                {l.text}
              </div>
            ))}
          </div>
          {screen.rail.naive && (
            // No click behind these: the gateway answered Naïve's tool confirm itself (v1.9 §6b-bis).
            <div className={`naive${screen.rail.naive.allowed ? "" : " denied"}`} data-target="rail.naive">
              <span aria-hidden="true">{screen.rail.naive.allowed ? "✓ " : "✕ "}</span>
              {screen.rail.naive.text}
              <span className="n">
                {" "}
                · {screen.rail.naive.allowedCount + screen.rail.naive.deniedCount} auto, 0 clicks
              </span>
            </div>
          )}
          {screen.rail.pending.map((p) => (
            <div key={p.approvalId} className="pending">
              ⏳ {p.text} · waiting on CEO
            </div>
          ))}
        </div>
      </section>
      <section className="pane" data-target="rail.money" aria-label="Money">
        <div className="ph">Money</div>
        <div className="pb">
          <div className="k">
            <span>Clients saved</span>
            <span className="big">{saved}</span>
          </div>
          <div className="k">
            <span>Firm spent (est.)</span>
            <span className="mono">{spent}</span>
          </div>
          {tokensShown(numbers.money.contractor_paid_usd) && (
            <div className="k">
              <span>Contractors paid (testnet)</span>
              <span className="mono">{Number(numbers.money.contractor_paid_usd).toFixed(2)} pathUSD</span>
            </div>
          )}
          {tokensShown(numbers.money.agent_sales_usd) && (
            <div className="k">
              <span>Earned from agents (testnet)</span>
              <span className="mono">{Number(numbers.money.agent_sales_usd).toFixed(2)} pathUSD</span>
            </div>
          )}
        </div>
      </section>
      <section className="pane" data-target="rail.hiring" aria-label="Hiring">
        <div className="ph">Hiring</div>
        <div className="pb hiring">
          {/* Counts only: candidate names never reach the projector. */}
          {plural(numbers.hiring.applicants, "applicant")} → {numbers.hiring.screened} screened · {numbers.hiring.shortlisted} shortlisted
          {numbers.hiring.hired > 0 && ` · ${numbers.hiring.hired} hired`}
          {(numbers.hiring.sourced ?? 0) + (numbers.hiring.contacted ?? 0) > 0 && (
            <>
              <br />
              {[
                numbers.hiring.sourced ? `${plural(numbers.hiring.sourced, "lead")} found` : "",
                numbers.hiring.contacted ? `${numbers.hiring.contacted} emailed with a call invite` : "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </>
          )}
          {(numbers.hiring.awaiting ?? 0) > 0 && (
            <>
              <br />
              <b>Shortlist ready · waiting on CEO</b>
            </>
          )}
        </div>
      </section>
      <section className="pane" data-target="rail.cases" aria-label="Cases">
        <div className="ph">
          Cases{screen.rail.crm && <span className="tag">{screen.rail.crm}</span>}
        </div>
        <div className="pb cases">
          {SHOW_BLOCKED &&
            blocked.map((r) => (
              <div key={`b-${r.caseId}`} className="blocked">
                <b>BLOCKED · {r.name}</b>
                {r.blocked}
              </div>
            ))}
          {screen.caseOrder.length === 0 && <div className="empty">No clients yet. Marketing is posting.</div>}
          {screen.caseOrder.slice(-MAX_CASE_ROWS).map((c) => {
            const r = screen.caseRows[c];
            const tone = r.raw && Object.hasOwn(CASE_TONE, r.raw) ? CASE_TONE[r.raw] : "run";
            return (
              <div key={c} className="case">
                <span>
                  {r.name}
                  {r.flags?.map((f) => (
                    <span key={f} className="chip flagged" title={f}>
                      flagged · {f}
                    </span>
                  ))}
                </span>
                <span className={`st ${tone}`}>
                  {r.status}
                </span>
              </div>
            );
          })}
          {SHOW_QUEUE && numbers.queue.waiting > 0 && (
            <div className="waiting" aria-label={`${numbers.queue.waiting} waiting`}>
              {Array.from({ length: Math.min(numbers.queue.waiting, 8) }, (_, i) => (
                <span key={i} className="chip">
                  waiting
                </span>
              ))}
              {numbers.queue.waiting > 8 && <span className="chip">+{numbers.queue.waiting - 8}</span>}
            </div>
          )}
        </div>
      </section>
    </aside>
  );
});

// ---------------------------------------------------------------- D31: CEO orders

/** Desks in the firm's order, with the short badge each order line carries. */
const DESKS: readonly [seat: string, name: string, badge: string][] = [
  ["cos", "Chief of Staff", "CoS"],
  ["intake", "Intake", "IN"],
  ["tax", "Tax", "TX"],
  ["qa", "QA", "QA"],
  ["acct", "Accounting", "AC"],
  ["mkt", "Marketing", "MK"],
  ["hr", "HR", "HR"],
];
const BADGE: Readonly<Record<string, string>> = Object.fromEntries(DESKS.map(([seat, , badge]) => [seat, badge]));
/** Order cards side by side (the newest ones). */
const ORDERS_SHOWN = 3;

/**
 * The live CEO orders (D31): each order the CEO gave, and every step the firm took for it, in
 * time order. The desk row lights a department while its agent works (any step of theirs).
 */
export const OrdersStrip = memo(function OrdersStrip({ screen }: { screen: ScreenState }) {
  const shown = visibleOrders(screen).slice(-ORDERS_SHOWN);
  return (
    <section className="orders" data-target="orders" aria-label="CEO orders">
      <div className="oh">
        {/* The CEO's cursor lands here when an order is spoken (rail.voice). */}
        <span className="ot" data-target="rail.voice">
          CEO orders
        </span>
        <div className="desks" aria-hidden>
          {DESKS.map(([seat, name]) => {
            const lit = Object.hasOwn(screen.desks, seat) ? screen.desks[seat] : "";
            // A new key restarts the glow: the desk fades back to idle unless its agent acts again.
            return (
              <span key={`${seat}:${lit}`} className={`desk${lit ? " lit" : ""}`} style={{ "--c": `var(--a-${seat})` } as CSSProperties}>
                <i />
                {name}
              </span>
            );
          })}
        </div>
      </div>
      <div className="cards">
        {shown.map((o, i) => (
          <article key={o.id} className={`order${i === shown.length - 1 ? " newest" : ""}`} data-target={`orders.${o.id}`}>
            <header>
              <span className="num">Order {o.n}</span>
              <time className="mono">{formatClock(o.ts)}</time>
            </header>
            <p className="said">&ldquo;{o.text}&rdquo;</p>
            {o.lines.length === 0 ? (
              <p className="wait">Waiting for the Chief of Staff…</p>
            ) : (
              // column-reverse: the newest line sits at the bottom and the oldest scroll off the top.
              <ol>
                {[...o.lines].reverse().map((l) => (
                  <li key={l.key}>
                    <span className="dept" style={{ background: `var(--a-${Object.hasOwn(BADGE, l.seat) ? l.seat : "ceo"})` }} aria-hidden>
                      {Object.hasOwn(BADGE, l.seat) ? BADGE[l.seat] : "•"}
                    </span>
                    <span className="lt">
                      {l.text}
                      {(l.times ?? 1) > 1 && <span className="times"> ×{l.times}</span>}
                    </span>
                    {l.image && imageOk(l.image) && (
                      // eslint-disable-next-line @next/next/no-img-element -- the gateway's own ad PNG, host-gated
                      <img src={l.image} alt="The ad image" referrerPolicy="no-referrer" onError={hideBroken} />
                    )}
                  </li>
                ))}
              </ol>
            )}
          </article>
        ))}
      </div>
    </section>
  );
});
