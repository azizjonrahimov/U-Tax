// D35: the CEO downloads a case's real files with the session as a Bearer, never a URL token.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { docName, downloadDoc } from "./docs";
import { readSession, writeSession } from "@/ceo/session";

const g = globalThis as unknown as { document?: unknown; sessionStorage?: unknown };
let clicked: string[] = [];
beforeEach(() => {
  clicked = [];
  const store = new Map<string, string>();
  g.sessionStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => store.set(k, v), removeItem: (k: string) => store.delete(k) };
  g.document = {
    createElement: () => {
      const a = { href: "", download: "", rel: "", click: () => clicked.push(a.download), remove: () => {} };
      return a;
    },
    body: { appendChild: () => {} },
  };
});
afterEach(() => {
  delete g.document;
  delete g.sessionStorage;
});

const reply = (status: number, body: BodyInit | null = null) => new Response(body, { status });

describe("D35 document downloads", () => {
  it("only names the gateway serves: the case's workpaper and memo, never the books", () => {
    expect(docName("case_41", "Delaware_Franchise_Tax_Workpaper_case_41_v1.xlsx")).toBe(true);
    expect(docName("case_41", "Franchise_Tax_Memo_case_41_v1.docx")).toBe(true);
    expect(docName("case_41", "U-Tax_Books.xlsx")).toBe(false);
    expect(docName("case_41", "../secrets.xlsx")).toBe(false);
    expect(docName(null, "Memo.docx")).toBe(false);
    expect(docName("c7", "Memo.docx")).toBe(false);
  });

  it("fetches with the Bearer (no token in the URL) and saves the file under its own name", async () => {
    let seen: { url: string; auth: string | null } | null = null;
    const f = (async (url: string, init?: RequestInit) => {
      seen = { url, auth: new Headers(init?.headers).get("Authorization") };
      return reply(200, "xlsx bytes");
    }) as unknown as typeof fetch;
    const r = await downloadDoc("s.live", "case_41", "Franchise_Tax_Memo_case_41_v1.docx", f);
    expect(r).toEqual({ ok: true });
    expect(seen).toEqual({ url: expect.stringMatching(/\/api\/docs\/case_41\/Franchise_Tax_Memo_case_41_v1\.docx$/), auth: "Bearer s.live" });
    expect(clicked).toEqual(["Franchise_Tax_Memo_case_41_v1.docx"]);
  });

  it("a dead session signs the tab out; a missing file and an outage say so", async () => {
    writeSession({ session: "s.dead", expiresAt: Date.now() + 60_000 });
    // `expired`: the console signs out with the reason (F1), not a silent bounce to the sign-in page.
    expect(await downloadDoc("s.dead", "case_41", "Franchise_Tax_Memo_case_41_v1.docx", (async () => reply(401)) as unknown as typeof fetch)).toMatchObject({ ok: false, expired: true });
    expect(readSession()).toBeNull();
    expect(await downloadDoc("s", "case_41", "Franchise_Tax_Memo_case_41_v1.docx", (async () => reply(404)) as unknown as typeof fetch)).toEqual({ ok: false, message: expect.stringMatching(/isn't there/) });
    const down = (async () => {
      throw new TypeError("network");
    }) as unknown as typeof fetch;
    expect(await downloadDoc("s", "case_41", "Franchise_Tax_Memo_case_41_v1.docx", down)).toEqual({ ok: false, message: expect.stringMatching(/Can't reach/) });
    expect(clicked).toEqual([]);
  });
});
