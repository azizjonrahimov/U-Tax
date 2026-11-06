// Theater panes. Pure rendering of ScreenState; every element a cursor can
// visit carries data-target matching the script's target keys.

import type { ReactNode } from "react";
import type { FirmNumbers } from "@/theater/rail";
import { humanTouches } from "@/theater/rail";
import type { Cell, ScreenState } from "@/theater/screen";
import type { Pane as PaneKey } from "@/theater/script";
import { formatCents, formatUsd } from "@/theater/format";

/** Text as it should show right now (a step being typed reveals progressively). */
export type Typed = (target: string, full: string | undefined) => string | undefined;

export const SHOW_BLOCKED = process.env.NEXT_PUBLIC_SHOW_BLOCKED !== "0";
export const SHOW_QUEUE = process.env.NEXT_PUBLIC_SHOW_QUEUE !== "0";

export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const timeOf = (iso: string | undefined, seconds = false) => {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", ...(seconds ? { second: "2-digit" } : {}), hour12: false });
};

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
            From: {m.from} · “{m.subject}”{view?.name ? ` · ${view.name}` : ""}
          </div>
          <div className="body">{highlight(m.body, m.quotes)}</div>
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

export function SheetPane({ screen }: { screen: ScreenState }) {
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
}

export function SearchPane({ screen, typed }: { screen: ScreenState; typed: Typed }) {
  const s = screen.search;
  return (
    <Pane k="search" screen={screen} label="Search" name="law" tag="Naïve Data catalogue">
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

export function CalendarPane({ screen }: { screen: ScreenState }) {
  const last = screen.calendar.blocks.at(-1);
  const start = last?.start ? new Date(last.start) : null;
  const base = start && !Number.isNaN(start.getTime()) ? new Date(start) : null;
  if (base) base.setMinutes(base.getMinutes() < 30 ? 0 : 30, 0, 0);
  const slots = Array.from({ length: 4 }, (_, i) => (base ? new Date(base.getTime() + (i - 1) * 30 * 60_000) : null));
  const day = base ? base.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }) : "today";
  return (
    <Pane k="calendar" screen={screen} label="Calendar" name={day} tag="via Google Calendar">
      <div className="cal" data-target="calendar.block">
        {slots.map((t, i) => {
          const label = t ? timeOf(t.toISOString()) : ["9:00", "9:30", "10:00", "10:30"][i];
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
}

export function SocialPane({ screen, typed }: { screen: ScreenState; typed: Typed }) {
  const s = screen.social;
  const img = s.image?.startsWith("https://") ? s.image : undefined;
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
                <img src={img} alt="" data-target="social.image" />
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

export function Rail({ screen, numbers }: { screen: ScreenState; numbers: FirmNumbers }) {
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
              {numbers.touches.training} / 4{trust.length ? ` · ${auto} AUTO` : ""}
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
                <span className="h">{timeOf(l.ts)}</span>
                {l.text}
              </div>
            ))}
          </div>
          {screen.rail.pending.map((p) => (
            <div key={p.approvalId} className="pending">
              ⏳ waiting on CEO · {p.text}
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
        </div>
      </section>
      <section className="pane" data-target="rail.hiring" aria-label="Hiring">
        <div className="ph">Hiring</div>
        <div className="pb hiring">
          {plural(numbers.hiring.applicants, "applicant")} → {numbers.hiring.screened} screened · {numbers.hiring.shortlisted} shortlisted
          {screen.rail.hiring.at(-1) && (
            <>
              <br />
              <b>{screen.rail.hiring.at(-1)}</b>
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
          {screen.caseOrder.map((c) => {
            const r = screen.caseRows[c];
            const ok = r.status === "sent" || r.status === "call booked" || r.status === "qa passed";
            const bad = r.status === "blocked" || r.status === "not eligible";
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
                <span className={`st${ok ? " ok" : bad ? "" : " run"}`} style={bad ? { color: "var(--red)", borderColor: "var(--red)" } : undefined}>
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
}
