"use client";

// D41 F49 (runbook §D.2): the Intake desk's Inbox. Threads per case (masked names), the thread as
// bubbles: inbound on the left, what the firm sent on the right ("sent via AgentMail"). Text only;
// a flagged case's bodies are never shown.

import { useState } from "react";
import { formatClock } from "@/theater/format";
import { EmptyState } from "@/components/ui/EmptyState";
import { unanswered, type Thread } from "@/office/mail";

const initials = (name: string) => {
  const m = /^Team #(\d+)$/.exec(name);
  if (m) return `T${m[1]}`.slice(0, 3);
  return name.replace(/[^A-Za-z0-9 ]/g, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]?.toUpperCase()).join("") || "?";
};
const via = (provider: string | null) => (provider === "stripe" ? "Stripe" : "AgentMail");

export function Mail({ threads }: { threads: Thread[] }) {
  const [pick, setPick] = useState<string | null>(null);
  const open = threads.find((t) => t.caseId === pick) ?? threads[0];
  if (!open) {
    return (
      <section className="mailapp empty-mail" aria-label="Inbox">
        <EmptyState art="inbox" title="No email yet" line="Intake checks the firm inbox every 30 s and answers each client." />
      </section>
    );
  }
  return (
    <section className="mailapp" aria-label="Inbox">
      <ul className="ml-list" aria-label="Threads">
        {threads.map((t) => {
          const last = t.messages.at(-1);
          return (
            <li key={t.caseId}>
              <button className={t.caseId === open.caseId ? "ml-row on" : "ml-row"} aria-current={t.caseId === open.caseId ? "true" : undefined} onClick={() => setPick(t.caseId)}>
                <span className="ml-ini" aria-hidden>
                  {initials(t.name)}
                </span>
                <span className="ml-mid">
                  <b>{t.name}</b>
                  <span>{t.flagged ? "hidden: flagged by QA" : last?.subject || last?.label || ""}</span>
                </span>
                <span className="ml-time">
                  {last ? formatClock(last.ts) : ""}
                  {unanswered(t) && <i className="ml-new" aria-label="waiting on the firm" />}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <div className="ml-thread">
        <header className="ml-head">
          <b>{open.name}</b> · <span className="mono">{open.caseId}</span> · {open.messages.length} {open.messages.length === 1 ? "message" : "messages"}
        </header>
        <ol className="ml-msgs">
          {open.messages.map((m) => (
            <li key={m.id} className={m.dir === "in" ? "ml-msg in" : "ml-msg out"}>
              {m.label && <p className="ml-label mono">{m.label}</p>}
              {!open.flagged && m.subject && <p className="ml-subj">{m.subject}</p>}
              {open.flagged ? <p className="ml-body hidden-body">hidden: flagged by QA</p> : m.body && <p className="ml-body">{m.body}</p>}
              <p className="ml-meta mono">
                {m.dir === "out" ? `sent via ${via(m.provider)} · ` : m.provider === "stripe" ? "via Stripe · " : "received · "}
                {formatClock(m.ts)}
                {m.status !== "ok" ? ` · ${m.status}` : ""}
              </p>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
