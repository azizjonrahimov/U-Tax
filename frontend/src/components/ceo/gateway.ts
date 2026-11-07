// Admin requests for the CEO console: the token rides only the Bearer header, never a URL.

import { ApiError, API_URL } from "@/lib/api";
import { supersededBy } from "@/ceo/ceo";

export const API = `${API_URL}/api`;
export const REQUEST_MS = 15000;

/** A gateway refusal with its extra fields. */
export class GatewayError extends ApiError {
  constructor(status: number, code: string, message: string, readonly details: Record<string, unknown>) {
    super(status, code, message);
  }
}

/** An admin request: Bearer header, a timeout, and contract errors as GatewayError. */
export async function authJson<T>(token: string, url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (init.body != null) headers.set("Content-Type", "application/json");
  let r: Response;
  try {
    r = await fetch(url, { cache: "no-store", ...init, headers, signal: init.signal ?? AbortSignal.timeout(REQUEST_MS) });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") throw new ApiError(0, "timeout", "timed out");
    throw new ApiError(0, "network_error", e instanceof Error ? e.message : "network error");
  }
  const body: unknown = await r.json().catch(() => null);
  if (!r.ok) {
    const top = typeof body === "object" && body !== null ? (body as Record<string, unknown>) : {};
    const err = typeof top.error === "object" && top.error !== null ? (top.error as Record<string, unknown>) : {};
    throw new GatewayError(
      r.status,
      typeof err.code === "string" ? err.code : "http_error",
      typeof err.message === "string" ? err.message : `HTTP ${r.status}`,
      { ...top, ...err }, // extra fields (v1.7.8 superseded: approval_id, superseded_by), wherever the gateway puts them
    );
  }
  return body as T;
}

/** The newer approval after a 409 superseded: from the body when the gateway sends it, else from the message. */
export function newerApproval(e: ApiError): string | null {
  if (e instanceof GatewayError && "superseded_by" in e.details) {
    const by = e.details.superseded_by;
    return typeof by === "string" && /^apr_[A-Za-z0-9_-]{1,40}$/.test(by) ? by : null;
  }
  return supersededBy(e.message);
}
