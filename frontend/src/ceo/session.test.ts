// D33: the CEO's email+password sign-in (contract v1.13 §5a-bis).

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { login, loginMessage, logout, nextPath, parseSession, readSession, SESSION_KEY, writeSession } from "./session";

class MemStorage {
  m = new Map<string, string>();
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, String(v));
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
}

const g = globalThis as unknown as { sessionStorage?: MemStorage };
let store: MemStorage;
beforeEach(() => {
  store = new MemStorage();
  g.sessionStorage = store;
});
afterEach(() => {
  writeSession(null);
  delete g.sessionStorage;
});

const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("D33 sign-in", () => {
  it("a good login stores the session for this tab, and nothing else", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const f = (async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      return json(200, { session: "s.abc", expires_at: "2099-01-01T00:00:00Z" });
    }) as unknown as typeof fetch;
    expect(await login(" CEO@u-tax.test ", "pw", f)).toEqual({ ok: true });
    expect(calls[0].url).toMatch(/\/api\/auth\/login$/);
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ email: "CEO@u-tax.test", password: "pw" });
    expect(readSession()?.session).toBe("s.abc");
    expect(JSON.parse(store.getItem(SESSION_KEY)!)).toEqual({ session: "s.abc", expiresAt: Date.parse("2099-01-01T00:00:00Z") });
  });

  it("401 bad_login says 'Wrong email or password', and stores nothing", async () => {
    const f = (async () => json(401, { error: { code: "bad_login", message: "wrong email or password" } })) as unknown as typeof fetch;
    expect(await login("ceo@u-tax.test", "nope", f)).toEqual({ ok: false, message: "Wrong email or password." });
    expect(readSession()).toBeNull();
  });

  it("429 and outages have their own words", async () => {
    expect(loginMessage(429, "rate_limited")).toMatch(/Too many tries/);
    const down = (async () => {
      throw new TypeError("network");
    }) as unknown as typeof fetch;
    expect(await login("a@b.c", "x", down)).toEqual({ ok: false, message: expect.stringMatching(/Can't reach/) });
    const garbled = (async () => json(200, { session: 5 })) as unknown as typeof fetch;
    expect((await login("a@b.c", "x", garbled)).ok).toBe(false);
  });

  it("an expired or malformed stored session is no session", () => {
    expect(parseSession(JSON.stringify({ session: "s", expiresAt: 1000 }), 2000)).toBeNull();
    expect(parseSession("not json", 0)).toBeNull();
    expect(parseSession(JSON.stringify({ session: "", expiresAt: 9e15 }), 0)).toBeNull();
    expect(parseSession(JSON.stringify({ session: "s", expiresAt: 9e15 }), 0)).toEqual({ session: "s", expiresAt: 9e15 });
  });

  it("an old admin token left in storage is removed, never used", () => {
    store.setItem("utax.ceo.token", "admin-test");
    expect(readSession()).toBeNull();
    expect(store.getItem("utax.ceo.token")).toBeNull();
  });

  it("logout forgets the session at once and revokes it with the Bearer", async () => {
    writeSession({ session: "s.live", expiresAt: Date.now() + 60_000 });
    const calls: RequestInit[] = [];
    const f = (async (_u: string, init?: RequestInit) => {
      calls.push(init!);
      expect(readSession()).toBeNull(); // gone before the network answers
      return json(200, { ok: true });
    }) as unknown as typeof fetch;
    await logout(f);
    expect((calls[0].headers as Record<string, string>).Authorization).toBe("Bearer s.live");
    expect(store.getItem(SESSION_KEY)).toBeNull();
  });

  it("after sign-in, only a local path is followed", () => {
    expect(nextPath("/ceo")).toBe("/ceo");
    expect(nextPath(null)).toBe("/office");
    for (const bad of ["https://evil.example", "//evil.example", "/\\evil.example", "ceo"]) expect(nextPath(bad)).toBe("/office");
  });
});

describe("D33 review: sign-in edges", () => {
  it("never follows a path the URL parser turns into another site", () => {
    for (const bad of ["/\t/evil.example", "/\n/evil.example", "/\r/evil.example", "/\t\evil.example", " //evil.example", "/\u0000/evil.example"]) {
      expect(nextPath(bad)).toBe("/office");
    }
    expect(nextPath(new URLSearchParams("next=/%09/evil.example").get("next"))).toBe("/office");
    expect(nextPath("/ceo?x=1#a")).toBe("/ceo?x=1#a");
    expect(nextPath("/office?d=tax")).toBe("/office?d=tax");
  });

  it("a cached session expires on the clock, without a storage change", () => {
    const t = Date.now();
    writeSession({ session: "s.t", expiresAt: t + 10_000 });
    const a = readSession(t + 9_999);
    expect(a?.session).toBe("s.t");
    expect(readSession(t + 9_999)).toBe(a); // a stable snapshot for React
    expect(readSession(t + 10_000)).toBeNull();
    store.setItem(SESSION_KEY, JSON.stringify({ session: "s.u", expiresAt: t + 20_000 }));
    expect(readSession(t + 10_001)?.session).toBe("s.u"); // a storage change is re-read
  });

  it("with storage off, the session lives in memory until sign-out", () => {
    delete g.sessionStorage;
    writeSession({ session: "s.mem", expiresAt: 9e15 });
    expect(readSession()?.session).toBe("s.mem");
    writeSession(null);
    expect(readSession()).toBeNull();
  });

  it("a session that is already over, or has no usable expiry, is not a sign-in, and nothing is stored", async () => {
    for (const body of [{ session: "s", expires_at: "2000-01-01T00:00:00Z" }, { session: "s" }, { session: "s", expires_at: "soon" }, { session: 5, expires_at: "2099-01-01T00:00:00Z" }]) {
      const f = (async () => json(200, body)) as unknown as typeof fetch;
      expect((await login("a@b.c", "x", f)).ok).toBe(false);
      expect(readSession()).toBeNull();
    }
    expect(loginMessage(400, "bad_input")).toMatch(/Enter your email/);
  });

  it("a good login stores only the session: no email or password anywhere", async () => {
    const f = (async () => json(200, { session: "s.k", expires_at: "2099-01-01T00:00:00Z" })) as unknown as typeof fetch;
    await login("ceo@u-tax.test", "hunter2-secret", f);
    expect([...store.m.keys()]).toEqual([SESSION_KEY]);
    expect(JSON.stringify([...store.m.values()])).not.toMatch(/hunter2|ceo@u-tax/);
  });

  it("logout without a session calls nothing; a failed revoke still signs out quietly, with keepalive", async () => {
    let n = 0;
    await logout((async () => {
      n++;
      return json(200, {});
    }) as unknown as typeof fetch);
    expect(n).toBe(0);
    writeSession({ session: "s.x", expiresAt: Date.now() + 60_000 });
    let init: RequestInit | undefined;
    await expect(
      logout((async (_u: string, i?: RequestInit) => {
        init = i;
        throw new TypeError("down");
      }) as unknown as typeof fetch),
    ).resolves.toBeUndefined();
    expect(init?.keepalive).toBe(true);
    expect(readSession()).toBeNull();
  });
});
