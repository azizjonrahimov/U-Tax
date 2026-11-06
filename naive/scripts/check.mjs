// Offline check, no Naïve key needed. Fails (exit 1) on any of:
//  1. naive.config.ts refused by the blueprint engine's own loader (the validation `naive up` runs),
//  2. an agent whose allowed tools are not EXACTLY its gateway tools + board (+ delegation for the
//     Chief of Staff), or a deny-listed tool that is not denied,
//  3. src/crew.ts GATEWAY_TOOLS out of step with contracts/api.md §6,
//  4. a prompt naming a gateway tool its agent may not call (or missing one it may),
//  5. a system prompt without the firm rules or the disclaimer,
//  6. templates/memo.md missing, using placeholders outside contract §7, or without the disclaimer line,
//  7. a `tasks[]` declaration (the engine would create an extra org "CEO" agent outside the policy).
// Run: npm run check   (sets dummy UTAX_MCP_URL / UTAX_TOOL_SECRET when none are set)

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createJiti } from "jiti";
import { loadConfig, whatItCanDo } from "@usenaive-sdk/blueprints";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
process.env.UTAX_MCP_URL ??= "https://utax-check.example.com/mcp";
process.env.UTAX_TOOL_SECRET ??= "offline-check-dummy-secret-0123456789abcdef";

const failures = [];
const fail = (msg) => failures.push(msg);

const loaded = await loadConfig(ROOT);
if (!loaded.ok) {
  console.error(`config ${loaded.reason}: ${loaded.message}`);
  process.exit(1);
}
const config = loaded.config;

const jiti = createJiti(import.meta.url);
const { CREW, GATEWAY_TOOLS, MCP_SERVER } = await jiti.import(join(ROOT, "src", "crew.ts"));
const policy = await jiti.import(join(ROOT, "src", "policy.ts"));
const DENY_ALWAYS = [...policy.ASK_BY_DEFAULT, ...policy.NATIVE_BYPASS, ...policy.ESCALATION];
const DISCLAIMER = "Educational estimate — not tax advice";

// 3. contract §6 is the source of truth for tool names
const contractPath = join(ROOT, "..", "contracts", "api.md");
if (existsSync(contractPath)) {
  const api = readFileSync(contractPath, "utf8");
  const s6 = api.split(/^## 6\./m)[1]?.split(/^## 7\./m)[0] ?? "";
  const contractTools = [...s6.matchAll(/^\| `(\w+)` \|/gm)].map((m) => m[1]).sort();
  if (contractTools.length === 0) fail("contracts/api.md: could not read the §6 tool table");
  else if (contractTools.join() !== [...GATEWAY_TOOLS].sort().join())
    fail(`src/crew.ts GATEWAY_TOOLS (${[...GATEWAY_TOOLS].sort()}) != contract §6 (${contractTools})`);
} else {
  fail("contracts/api.md not found next to naive/");
}

for (const [key, seat] of Object.entries(CREW)) {
  const agent = config.agents.find((a) => a.name === seat.name);
  if (!agent) {
    fail(`${seat.name}: not declared in naive.config.ts`);
    continue;
  }
  for (const t of seat.tools) if (!GATEWAY_TOOLS.includes(t)) fail(`${seat.name}: tool ${t} is not a gateway tool`);

  // 2. exact policy
  const configs = agent.tools?.configs ?? {};
  if (agent.tools?.default_config.permission !== "deny") fail(`${seat.name}: default permission is not deny`);
  const expected = new Set([
    ...seat.tools.map((t) => `${MCP_SERVER}.${t}`),
    ...policy.BOARD,
    ...(key === "cos" ? policy.COORDINATOR : []),
  ]);
  const allowed = Object.entries(configs)
    .filter(([, v]) => v.enabled && v.permission !== "deny")
    .map(([k]) => k);
  const extra = allowed.filter((k) => !expected.has(k));
  const missing = [...expected].filter((k) => !allowed.includes(k));
  if (extra.length) fail(`${seat.name}: allows tools outside its seat: ${extra}`);
  if (missing.length) fail(`${seat.name}: missing tools: ${missing}`);
  for (const t of DENY_ALWAYS) if (configs[t]?.permission !== "deny") fail(`${seat.name}: ${t} is not denied by name`);
  if (key !== "cos") for (const t of policy.COORDINATOR) if (configs[t]?.permission !== "deny") fail(`${seat.name}: ${t} not denied`);
  if (!agent.identity) fail(`${seat.name}: holds no identity, so the vault bearer would not reach it`);
  const server = agent.mcp_servers?.find((s) => s.name === MCP_SERVER);
  if (server?.headers?.["X-UTax-Actor"] !== seat.actor) fail(`${seat.name}: X-UTax-Actor header is not ${seat.actor}`);

  // 4. prompt names exactly its tools
  const prompt = readFileSync(join(ROOT, "prompts", seat.prompt), "utf8");
  const named = [...new Set([...prompt.matchAll(/utax\.(\w+)/g)].map((m) => m[1]))].sort();
  if (named.join() !== [...seat.tools].sort().join()) fail(`${seat.prompt}: names utax.${named} but may call ${seat.tools}`);

  // 5. system prompt = own file + firm rules
  if (!agent.system?.includes("## Firm rules")) fail(`${seat.name}: system prompt is missing the firm rules`);
  if (!agent.system?.includes("SSN")) fail(`${seat.name}: system prompt never mentions SSNs`);
  if (!/educational estimate,? (—|-)? ?not tax advice/i.test(agent.system ?? "")) fail(`${seat.name}: no disclaimer`);
}

const cos = config.agents.find((a) => a.name === CREW.cos.name);
const members = Object.entries(CREW).filter(([k]) => k !== "cos").map(([, s]) => s.name).sort();
if (cos?.multiagent?.type !== "coordinator" || !cos.multiagent.board) fail("chief-of-staff: not a coordinator with a board");
if ((cos?.multiagent?.agents ?? []).map((a) => a.name).sort().join() !== members.join()) fail("chief-of-staff: roster is not the 6 members");
if (!cos?.schedules?.some((s) => s.cron === "0 8 * * 0")) fail("chief-of-staff: no Sunday 8 AM brief schedule");
if (config.agents.length !== Object.keys(CREW).length) fail(`config declares ${config.agents.length} agents, crew has ${Object.keys(CREW).length}`);

// 7. no hidden orchestrator
if ((config.tasks ?? []).length > 0) fail("tasks[] is declared: the engine would create an org CEO agent outside the policy");

// 6. memo template (contract §7)
const memoPath = join(ROOT, "templates", "memo.md");
if (!existsSync(memoPath)) {
  fail("templates/memo.md is missing (contract §7)");
} else {
  const memo = readFileSync(memoPath, "utf8");
  const allowed = ["masked_name", "authorized_method", "assumed_par_method", "annual_report_fee", "total", "due_date", "form5472_note", "rnd_note"];
  const used = [...memo.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]);
  const extra = used.filter((p) => !allowed.includes(p));
  if (extra.length) fail(`templates/memo.md: placeholders outside contract §7: ${extra}`);
  if (!memo.includes(`${DISCLAIMER}.`)) fail(`templates/memo.md: missing the line "${DISCLAIMER}."`);
  if (/\$\s?\d/.test(memo.replace(/\{\{[^}]*\}\}/g, ""))) fail("templates/memo.md: contains a literal dollar amount; amounts come only from placeholders");
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
