"use client";

// D35 (contract v1.15 §6, task 39): the CEO downloads a case's real files (the verified
// workpaper .xlsx and memo .docx) from GET /api/docs/<case_id>/<file_name>. The session rides
// the Authorization header and the file is saved from a blob: never a ?token= link that lands
// in a log. The firm's books stay in the firm (no route serves them).

import { API_URL } from "@/lib/api";
import { writeSession } from "@/ceo/session";

/** Only names the gateway can serve (its own check, mirrored so no request is wasted). */
export const docName = (caseId: string | null, file: string | null | undefined): boolean =>
  !!caseId && /^case_[0-9]{1,6}$/.test(caseId) && !!file && /^[A-Za-z0-9_]{1,80}\.(xlsx|docx)$/.test(file);

export type DownloadResult = { ok: true } | { ok: false; message: string };

/** Fetch the file with the session and save it under its own name. */
export async function downloadDoc(session: string, caseId: string, file: string, fetchImpl: typeof fetch = fetch): Promise<DownloadResult> {
  if (!docName(caseId, file)) return { ok: false, message: "That file can't be downloaded." };
  let r: Response;
  try {
    r = await fetchImpl(`${API_URL}/api/docs/${encodeURIComponent(caseId)}/${encodeURIComponent(file)}`, {
      headers: { Authorization: `Bearer ${session}` },
      cache: "no-store",
      signal: AbortSignal.timeout(30_000),
    });
  } catch {
    return { ok: false, message: "Can't reach the firm right now. Try again in a moment." };
  }
  if (r.status === 401 || r.status === 403) {
    writeSession(null); // the session is dead at the gateway: sign this tab out
    return { ok: false, message: "Your sign-in expired. Sign in again to download." };
  }
  if (r.status === 404) return { ok: false, message: "That file isn't there (a newer version may have replaced it)." };
  if (!r.ok) return { ok: false, message: `The firm couldn't send the file (HTTP ${r.status}).` };
  const blob = await r.blob();
  const url = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = url;
    a.download = file;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
  } finally {
    // Long enough for the browser to start the save.
    setTimeout(() => URL.revokeObjectURL(url), 30_000);
  }
  return { ok: true };
}
