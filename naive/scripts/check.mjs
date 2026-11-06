// Offline check, no Naïve key needed. Fails (exit 1) on any of:
//  1. naive.config.ts refused by the blueprint engine's own loader (the validation `naive up` runs),
//  2. an agent whose allowed tools are not EXACTLY its gateway tools + board (+ delegation for the
//     Chief of Staff), or a deny-listed tool that is not denied,
//  3. src/crew.ts GATEWAY_TOOLS out of step with contracts/api.md §6,
//  4. a prompt naming a gateway tool its agent may not call (or missing one it may),
//  5. a system prompt without the firm rules or the disclaimer,
//  6. templates/memo.md missing, using placeholders outside contract §7, or without the disclaimer line,
//  7. a `tasks[]` declaration (the engine would create an extra org "CEO" agent outside the policy),
//  8. an agent without its own identity + vault + token env (contract v1.3 §1),
//  9. the config ACCEPTING a bad env (http / localhost / non-/mcp URL, short, padded or shared tokens).
// Run: npm run check   (sets dummy UTAX_MCP_URL / UTAX_TOOL_TOKEN_* when none are set)

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createJiti } from "jiti";
import { loadConfig, whatItCanDo } from "@usenaive-sdk/blueprints";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const DUMMY = {
  UTAX_MCP_URL: "https://utax-check.example.com/mcp",
  ...Object.fromEntries(["COS", "INTAKE", "TAX", "QA", "ACCT", "MKT", "HR"].map((a) => [`UTAX_TOOL_TOKEN_${a}`, `offline-check-dummy-token-${a.toLowerCase()}-0123456789abcdef`])),
};
for (const [k, v] of Object.entries(DUMMY)) process.env[k] ??= v;

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
  const rows = [...s6.matchAll(/^\| `(\w+)` \| (\w+) \|/gm)].map((m) => ({ tool: m[1], agent: m[2] }));
  const contractTools = rows.map((r) => r.tool).sort();
  if (contractTools.length === 0) fail("contracts/api.md: could not read the §6 tool table");
  else if (contractTools.join() !== [...GATEWAY_TOOLS].sort().join())
    fail(`src/crew.ts GATEWAY_TOOLS (${[...GATEWAY_TOOLS].sort()}) != contract §6 (${contractTools})`);
  // The Agent column: a seat may hold a tool only if the contract gives it to that actor or "any",
  // and every actor-specific tool is held by its actor.
  for (const { tool, agent } of rows) {
    for (const seat of Object.values(CREW)) {
      const holds = seat.tools.includes(tool);
      if (holds && agent !== "any" && agent !== seat.actor) fail(`${seat.name}: holds ${tool}, which contract §6 gives to ${agent}`);
      if (!holds && agent === seat.actor) fail(`${seat.name}: missing ${tool}, which contract §6 gives to ${agent}`);
    }
  }
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
  // 8. own identity, own vault, own token
  const identity = config.identities.find((i) => i.name === agent.identity);
  if (!identity) fail(`${seat.name}: holds no declared identity, so no vault bearer would reach it`);
  const holders = config.agents.filter((a) => a.identity === agent.identity);
  if (holders.length !== 1) fail(`${seat.name}: identity ${agent.identity} is shared by ${holders.map((a) => a.name)}`);
  const vaults = config.vaults.filter((v) => v.identity === agent.identity);
  const creds = vaults.flatMap((v) => v.credentials);
  if (creds.length !== 1 || creds[0].kind !== "static_bearer" || creds[0].value.from_env !== seat.tokenEnv)
    fail(`${seat.name}: its identity's vault must hold exactly one static_bearer from ${seat.tokenEnv}`);
  else if (creds[0].mcp_server_url !== agent.mcp_servers?.[0]?.url) fail(`${seat.name}: vault credential URL != its MCP server URL`);
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
  const absent = allowed.filter((p) => !used.includes(p));
  if (extra.length) fail(`templates/memo.md: placeholders outside contract §7: ${extra}`);
  if (absent.length) fail(`templates/memo.md: missing contract §7 placeholders: ${absent}`);
  if (/^\s*subject:/i.test(memo)) fail("templates/memo.md: starts with a Subject: line; the gateway sets the subject and sends the whole file as the body");
  if (!memo.includes(`${DISCLAIMER}.`)) fail(`templates/memo.md: missing the line "${DISCLAIMER}."`);
  if (/\$\s?\d/.test(memo.replace(/\{\{[^}]*\}\}/g, ""))) fail("templates/memo.md: contains a literal dollar amount; amounts come only from placeholders");
}

// 9. the config must REFUSE a bad env. Each case loads the config in a fresh process.
const LOADER = `import("@usenaive-sdk/blueprints").then(async (b) => { const r = await b.loadConfig(process.argv[1]); process.exit(r.ok ? 0 : 3); })`;
const good = { ...process.env, ...DUMMY };
const badEnvs = {
  "http URL": { UTAX_MCP_URL: "http://utax.example.com/mcp" },
  "localhost URL": { UTAX_MCP_URL: "https://localhost:8000/mcp" },
  "private IP URL": { UTAX_MCP_URL: "https://10.0.0.5/mcp" },
  "IPv6 loopback URL": { UTAX_MCP_URL: "https://[::1]/mcp" },
  "URL not ending in /mcp": { UTAX_MCP_URL: "https://utax.example.com/" },
  "empty token": { UTAX_TOOL_TOKEN_QA: "" },
  "short token": { UTAX_TOOL_TOKEN_TAX: "short" },
  "token with CR": { UTAX_TOOL_TOKEN_HR: DUMMY.UTAX_TOOL_TOKEN_HR + String.fromCharCode(13) },
  "token with spaces": { UTAX_TOOL_TOKEN_INTAKE: ` ${DUMMY.UTAX_TOOL_TOKEN_INTAKE} ` },
  "URL with query": { UTAX_MCP_URL: "https://utax.example.com/mcp?token=abc" },
  "single-label host": { UTAX_MCP_URL: "https://gateway/mcp" },
  "shared token": { UTAX_TOOL_TOKEN_MKT: DUMMY.UTAX_TOOL_TOKEN_ACCT },
};
const control = spawnSync(process.execPath, ["-e", LOADER, ROOT], { cwd: ROOT, env: good, encoding: "utf8" });
if (control.status !== 0) fail(`guard tests: the good env did not load (exit ${control.status}): ${control.stderr.slice(0, 200)}`);
for (const [name, patch] of Object.entries(badEnvs)) {
  const r = spawnSync(process.execPath, ["-e", LOADER, ROOT], { cwd: ROOT, env: { ...good, ...patch }, encoding: "utf8" });
  if (r.status !== 3) fail(`guard tests: config accepted a bad env (${name}), exit ${r.status}`);
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
