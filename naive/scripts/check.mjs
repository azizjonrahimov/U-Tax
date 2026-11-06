// Offline check, no Naïve key needed:
//  1. loads naive.config.ts through the blueprint engine's own loader (the same validation `naive up` runs),
//  2. every agent is deny-by-default and offered exactly its gateway tools from src/crew.ts,
//  3. every prompt names exactly its agent's gateway tools (no more, no fewer),
//  4. every prompt carries the firm rules and the disclaimer,
//  5. the memo template uses only the contract §7 placeholders and carries the disclaimer line.
// Run: npm run check   (sets a dummy UTAX_MCP_URL when none is set)

import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfig, whatItCanDo } from "@usenaive-sdk/blueprints";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
process.env.UTAX_MCP_URL ??= "https://utax-check.example.com/mcp";

const failures = [];
const fail = (msg) => failures.push(msg);

const loaded = await loadConfig(ROOT);
if (!loaded.ok) {
  console.error(`config ${loaded.reason}: ${loaded.message}`);
  process.exit(1);
}
const config = loaded.config;

// The crew map, read from source so this script needs no TS toolchain.
const crewSrc = readFileSync(join(ROOT, "src", "crew.ts"), "utf8");
const allTools = [...crewSrc.match(/GATEWAY_TOOLS = \[([\s\S]*?)\]/)[1].matchAll(/"(\w+)"/g)].map((m) => m[1]);
const seats = [...crewSrc.matchAll(/name: "([\w-]+)",\s*actor: "\w+",\s*prompt: "([\w.-]+)",[\s\S]*?tools: \[([^\]]*)\]/g)].map(
  (m) => ({ name: m[1], prompt: m[2], tools: [...m[3].matchAll(/"(\w+)"/g)].map((t) => t[1]) }),
);
if (seats.length !== 7) fail(`expected 7 seats in src/crew.ts, parsed ${seats.length}`);

const DISCLAIMER = "Educational estimate — not tax advice";

for (const seat of seats) {
  const agent = config.agents.find((a) => a.name === seat.name);
  if (!agent) {
    fail(`${seat.name}: not declared in naive.config.ts`);
    continue;
  }
  // 2. policy
  if (agent.tools?.default_config.permission !== "deny") fail(`${seat.name}: default permission is not deny`);
  const offered = Object.entries(agent.tools?.configs ?? {})
    .filter(([k, v]) => k.startsWith("utax.") && v.enabled && v.permission !== "deny")
    .map(([k]) => k.slice(5))
    .sort();
  if (offered.join() !== [...seat.tools].sort().join()) fail(`${seat.name}: offered ${offered} but crew says ${seat.tools}`);
  for (const t of ["email.send", "email.read", "social.post", "phone.send", "mobile", "data_run"]) {
    if (agent.tools?.configs[t]?.permission !== "deny") fail(`${seat.name}: native tool ${t} is not denied`);
  }
  if (!agent.identity) fail(`${seat.name}: holds no identity, so the vault bearer would not reach it`);

  // 3. prompt names exactly its tools
  const prompt = readFileSync(join(ROOT, "prompts", seat.prompt), "utf8");
  const named = [...new Set([...prompt.matchAll(/utax\.(\w+)/g)].map((m) => m[1]))].sort();
  const unknown = named.filter((t) => !allTools.includes(t));
  if (unknown.length) fail(`${seat.prompt}: names unknown gateway tools ${unknown}`);
  if (named.join() !== [...seat.tools].sort().join()) fail(`${seat.prompt}: names utax.${named} but may call ${seat.tools}`);

  // 4. system prompt = own file + firm rules
  if (!agent.system?.includes("## Firm rules")) fail(`${seat.name}: system prompt is missing the firm rules`);
  if (!agent.system?.includes("SSN")) fail(`${seat.name}: system prompt never mentions SSNs`);
  if (!/educational estimate,? (—|-)? ?not tax advice/i.test(agent.system ?? "")) fail(`${seat.name}: no disclaimer`);
}

const cos = config.agents.find((a) => a.name === "chief-of-staff");
if (cos?.multiagent?.type !== "coordinator" || !cos.multiagent.board) fail("chief-of-staff: not a coordinator with a board");
if ((cos?.multiagent?.agents ?? []).length !== 6) fail("chief-of-staff: roster is not the 6 members");
if (!cos?.schedules?.some((s) => s.cron === "0 8 * * 0")) fail("chief-of-staff: no Sunday 8 AM brief schedule");

// 5. templates
const templatesDir = join(ROOT, "templates");
let templateFiles = [];
try {
  templateFiles = readdirSync(templatesDir).filter((f) => f.endsWith(".md"));
} catch {}
if (templateFiles.includes("memo.md")) {
  const memo = readFileSync(join(templatesDir, "memo.md"), "utf8");
  const allowed = ["masked_name", "authorized_method", "assumed_par_method", "annual_report_fee", "total", "due_date", "form5472_note", "rnd_note"];
  const used = [...memo.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]);
  const extra = used.filter((p) => !allowed.includes(p));
  if (extra.length) fail(`templates/memo.md: placeholders outside contract §7: ${extra}`);
  if (!memo.includes(`${DISCLAIMER}.`)) fail(`templates/memo.md: missing the line "${DISCLAIMER}."`);
}

console.log(`config: ${loaded.path}`);
console.log(`agents: ${config.agents.map((a) => a.name).join(", ")}`);
console.log("what it can do:");
for (const line of whatItCanDo(config)) console.log(`  - ${line}`);
if (failures.length) {
  console.error(`\n${failures.length} check(s) failed:`);
  for (const f of failures) console.error(`  ✗ ${f}`);
  process.exit(1);
}
console.log("\nall checks passed");
