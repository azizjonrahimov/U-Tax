// D41 design-fidelity gate: every class the prototype uses must exist in the live components.
// Run from the repo root:  node crew/scripts/proto-classes.mjs
// Exit 0 = every prototype class is present; exit 1 = prints what is missing, per page.
import { readFileSync, existsSync } from "node:fs";

const PAGES = [
  {
    name: "/ceo",
    proto: "docs/designs/inside/ceo-console-prototype.html",
    live: ["frontend/src/components/ceo/Ceo.tsx", "frontend/src/components/ceo/CeoVoice.tsx", "frontend/src/components/ceo/CaseList.tsx", "frontend/src/components/ceo/Payments.tsx"],
  },
  {
    name: "/office",
    proto: "docs/designs/inside/office-prototype.html",
    live: ["frontend/src/components/office/Office.tsx", "frontend/src/components/theater/Theater.tsx", "frontend/src/components/theater/Panes.tsx", "frontend/src/components/office/TaskBanner.tsx", "frontend/src/components/office/Payments.tsx", "frontend/src/components/office/Mail.tsx"],
  },
];
// Sample-only or state-only classes in the prototypes (no live element needs to carry them).
const IGNORE = new Set(["r1", "r2", "r3", "r4", "reveal", "b", "v", "n"]);

function protoClasses(html) {
  const body = html.replace(/<style[\s\S]*?<\/style>/g, "").replace(/<script[\s\S]*?<\/script>/g, "");
  const out = new Set();
  for (const m of body.matchAll(/class="([^"]+)"/g)) for (const c of m[1].split(/\s+/)) if (c) out.add(c);
  return out;
}
function liveClasses(src) {
  const out = new Set();
  // className="a b", className={`a ${x ? "b" : ""}`}, cx("a", ...), and string literals inside className expressions
  for (const m of src.matchAll(/className=(?:"([^"]*)"|\{([\s\S]*?)\}(?=[\s/>]))/g)) {
    const text = m[1] ?? m[2] ?? "";
    for (const s of text.matchAll(/["'`]([^"'`]*)["'`]/g)) for (const c of s[1].replace(/\$\{[^}]*\}/g, " ").split(/\s+/)) if (/^[a-z][\w-]*$/i.test(c)) out.add(c);
    if (m[1]) for (const c of m[1].split(/\s+/)) if (c) out.add(c);
  }
  return out;
}

let missingAny = false;
for (const p of PAGES) {
  if (!existsSync(p.proto)) { console.error(`missing prototype ${p.proto}`); process.exit(2); }
  const want = protoClasses(readFileSync(p.proto, "utf8"));
  const have = new Set();
  for (const f of p.live) if (existsSync(f)) for (const c of liveClasses(readFileSync(f, "utf8"))) have.add(c);
  const missing = [...want].filter((c) => !have.has(c) && !IGNORE.has(c)).sort();
  console.log(`${p.name}: ${want.size} prototype classes, ${missing.length} missing${missing.length ? ": " + missing.join(" ") : ""}`);
  if (missing.length) missingAny = true;
}
process.exit(missingAny ? 1 : 0);
