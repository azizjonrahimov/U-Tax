"use client";

// D33 (contract v1.13 §5a-bis): the CEO's sign-in. Email and password go to POST /api/auth/login
// once; the gateway answers a signed session that lasts 12 h. It lives in this tab's
// sessionStorage only (a window the console opens with window.open gets a copy), and travels as a
// Bearer header, or as ?token= on the read-only state/events URLs an EventSource can't add a
// header to. The admin token is for scripts only: no screen asks for it any more.

import { useSyncExternalStore } from "react";
import { API_URL } from "@/lib/api";

export const SESSION_KEY = "utax.ceo.session";
/** The old admin-token key: anything left there is removed, never used. */
const LEGACY_TOKEN_KEY = "utax.ceo.token";

export interface CeoSession {
  session: string;
  /** Epoch ms. */
  expiresAt: number;
}

const listeners = new Set<() => void>();
let memory: CeoSession | null = null;
let cachedRaw: string | null | undefined;
let cached: CeoSession | null = null;

/** A stored session, if it is well formed and not expired at `now`. */
export function parseSession(raw: string | null, now: number): CeoSession | null {
  if (!raw) return null;
  try {
    const v: unknown = JSON.parse(raw);
    if (typeof v !== "object" || v === null) return null;
    const { session, expiresAt } = v as Record<string, unknown>;
    if (typeof session !== "string" || !session || session.length > 512) return null;
    if (typeof expiresAt !== "number" || !Number.isFinite(expiresAt) || expiresAt <= now) return null;
    return { session, expiresAt };
  } catch {
    return null;
  }
}

function readRaw(): string | null {
  try {
    sessionStorage.removeItem(LEGACY_TOKEN_KEY);
    return sessionStorage.getItem(SESSION_KEY);
  } catch {
    return memory ? JSON.stringify(memory) : null; // storage off: it lives until the page closes
  }
}

/** The current session, or null (signed out or expired). Stable between changes, for React. */
export function readSession(now = Date.now()): CeoSession | null {
  const raw = readRaw();
  if (raw !== cachedRaw || (cached && cached.expiresAt <= now)) {
    cachedRaw = raw;
    cached = parseSession(raw, now);
  }
  return cached;
}

export function writeSession(s: CeoSession | null) {
  memory = s;
  try {
    if (s) sessionStorage.setItem(SESSION_KEY, JSON.stringify(s));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    /* memory holds it */
  }
  listeners.forEach((l) => l());
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const token = () => readSession()?.session ?? null;
const none = () => null;

/** The session token for this tab (null on the server, signed out, or expired). */
export function useSessionToken(): string | null {
  return useSyncExternalStore(subscribe, token, none);
}

export type LoginResult = { ok: true } | { ok: false; message: string };

/** Contract errors, in the CEO's words. */
export function loginMessage(status: number, code: string | null): string {
  if (status === 401) return "Wrong email or password.";
  if (status === 429) return "Too many tries. Wait a few minutes, then try again.";
  if (status === 400 || code === "bad_input") return "Enter your email and password.";
  return "Can't reach the firm right now. Try again in a moment.";
}

/** Sign in: on success the session is stored for this tab. */
export async function login(email: string, password: string, fetchImpl: typeof fetch = fetch): Promise<LoginResult> {
  try {
    const r = await fetchImpl(`${API_URL}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim(), password }),
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    const body: unknown = await r.json().catch(() => null);
    if (!r.ok) {
      const code = typeof body === "object" && body !== null ? (body as { error?: { code?: unknown } }).error?.code : null;
      return { ok: false, message: loginMessage(r.status, typeof code === "string" ? code : null) };
    }
    const { session, expires_at } = (body ?? {}) as { session?: unknown; expires_at?: unknown };
    const expiresAt = typeof expires_at === "string" ? Date.parse(expires_at) : NaN;
    if (typeof session !== "string" || !session || !Number.isFinite(expiresAt)) return { ok: false, message: loginMessage(500, null) };
    writeSession({ session, expiresAt });
    return { ok: true };
  } catch {
    return { ok: false, message: loginMessage(0, null) };
  }
}

/** Sign out: the gateway revokes the session (best effort), and this tab forgets it at once. */
export async function logout(fetchImpl: typeof fetch = fetch): Promise<void> {
  const s = readSession();
  writeSession(null);
  if (!s) return;
  try {
    await fetchImpl(`${API_URL}/api/auth/logout`, {
      method: "POST",
      headers: { Authorization: `Bearer ${s.session}` },
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
  } catch {
    /* it expires on its own */
  }
}

/** Where to go after signing in: a local path only (never another site), else the office. */
export function nextPath(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.includes("\\")) return "/office";
  return raw;
}
