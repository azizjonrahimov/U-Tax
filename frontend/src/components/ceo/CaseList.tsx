"use client";

// F1: the firm's cases in the CEO console, each with its status and its verified documents to
// download, so a case's files stay reachable after the Accounting desk has moved on. Only files
// the gateway serves get a button (src/office/artifact.ts stepCaseDocs); the books stay in the firm.

import { useState } from "react";
import type { CaseRow } from "@/ceo/ceo";
import { docName, downloadDoc } from "@/office/docs";

const MAX_CASES = 12;

/** A status the way the CEO says it ("QA_PASSED" → "QA passed"). */
export function statusWord(status: string): string {
  const s = status.replace(/_/g, " ").toLowerCase();
  return s.replace(/\bqa\b/g, "QA").replace(/^./, (c) => c.toUpperCase());
}

/** What kind of file it is, from its name (the gateway's own names). */
const kind = (file: string) => (/Workpaper/i.test(file) ? "Workpaper (Excel)" : /Memo/i.test(file) ? "Memo (Word)" : file.endsWith(".xlsx") ? "Excel" : "Word");

function Download({ token, caseId, file }: { token: string; caseId: string; file: string }) {
  const [state, setState] = useState<{ busy: boolean; error: string | null }>({ busy: false, error: null });
  if (!docName(caseId, file)) return null;
  return (
    <span className="dl">
      <button
        className="ghost"
        disabled={state.busy}
        title={`Download ${file}`}
        onClick={async () => {
          setState({ busy: true, error: null });
          let error: string | null = "The download didn't finish. Try again.";
          try {
            const r = await downloadDoc(token, caseId, file);
            error = r.ok ? null : r.message;
          } finally {
            setState({ busy: false, error });
          }
        }}
      >
        {state.busy ? "Downloading…" : `⬇ ${kind(file)}`}
      </button>
      {state.error && (
        <span className="error" role="alert">
          {state.error}
        </span>
      )}
    </span>
  );
}

export function CaseList({ cases, served, token }: { cases: readonly CaseRow[]; served: Record<string, string[]>; token: string }) {
  if (!cases.length) return null;
  // Newest first (case ids grow), at most a dozen.
  const shown = [...cases].sort((a, b) => Number(b.caseId.replace(/\D/g, "")) - Number(a.caseId.replace(/\D/g, ""))).slice(0, MAX_CASES);
  return (
    <section aria-labelledby="cases-h" className="cases">
      <h2 id="cases-h">Cases</h2>
      <ul>
        {shown.map((c) => {
          const files = c.flagged ? [] : (served[c.caseId] ?? []);
          return (
            <li key={c.caseId} className="case-row">
              <div className="case-head">
                <strong>{c.name}</strong>
                <span className={`case-status${c.flagged ? " flagged" : ""}`}>{c.flagged ? "Flagged" : statusWord(c.status)}</span>
              </div>
              {files.length > 0 ? (
                <div className="case-docs">
                  <span className="dim">Verified ✓</span>
                  {files.map((f) => (
                    <Download key={f} token={token} caseId={c.caseId} file={f} />
                  ))}
                </div>
              ) : (
                <p className="dim case-none">{c.flagged ? "Documents hidden: flagged by QA." : "No verified documents yet."}</p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
