// Offline check, no Naïve key needed. Fails (exit 1) on any of:
//  1. naive.config.ts refused by the blueprint engine's own loader (the validation `naive up` runs),
//  2. an agent whose allowed tools are not EXACTLY its gateway tools + board (+ delegation for the
//     Chief of Staff), or a deny-listed tool that is not denied,
//  3. src/crew.ts GATEWAY_TOOLS out of step with contracts/api.md §6,
//  4. a prompt naming a gateway tool its agent may not call (or missing one it may),
//  5. a system prompt without the firm rules or the disclaimer,
//  6. templates/memo.md missing, using placeholders outside contract §7, or without the disclaimer line,
//  7. a `tasks[]` declaration (the engine would create an extra org "CEO" agent outside the policy),
//  8. an agent without its own identity + vault + token env + seat URL /mcp/<actor> (contract v1.4 §1, §6),
//  9. the config ACCEPTING a bad env (http / localhost / non-/mcp URL, short, padded or shared tokens),
// 10. a template breaking its rule (placeholders, disclaimer, AI-screen disclosure, post guard),
// 11. prompts/voice-intake.md missing its hard rules.
// Run: npm run check   (sets dummy UTAX_MCP_URL / UTAX_TOOL_TOKEN_* when none are set)

import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync } from "node:fs";
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
  if (!agent.mcp_servers?.[0]?.url?.endsWith(`/mcp/${seat.actor}`)) fail(`${seat.name}: MCP URL must be its own seat, <base>/mcp/${seat.actor} (contract v1.4 §6)`);
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

// 6. memo template (contract §7): placeholders, disclaimer and amounts are checked in 10; the
// memo-only rule is that the gateway sets the subject and sends the whole file as the body.
const memoPath = join(ROOT, "templates", "memo.md");
if (existsSync(memoPath) && /^\s*subject:/i.test(readFileSync(memoPath, "utf8")))
  fail("templates/memo.md: starts with a Subject: line; the gateway sets the subject and sends the whole file as the body");

// 10. every template has a rule: its placeholders, the disclaimer where a client reads it, the
// disclosure line where an applicant reads it, and the post guard's limits on posts.
const MEMO_PLACEHOLDERS = ["masked_name", "authorized_method", "assumed_par_method", "annual_report_fee", "total", "due_date", "form5472_note", "rnd_note"];
// body: the gateway sends the file as the body and sets the subject itself (no "Subject:" line).
// client: a client reads it (disclaimer line, no literal dollar amounts). applicant: the AI-screen
// disclosure line. posts: Marketing posts its X/LinkedIn sections (the gateway's post guard).
// spend: every money-spent figure is called "estimated".
const TEMPLATE_RULES = {
  "memo.md": { body: true, client: true, placeholders: MEMO_PLACEHOLDERS },
  "ask-client.md": { body: true, client: true, placeholders: ["masked_name", "missing_list", "problems_line"] },
  "not-eligible-not_incorporated.md": { body: true, client: true, placeholders: ["masked_name"] },
  "not-eligible-outside_delaware.md": { body: true, client: true, placeholders: ["masked_name"] },
  "not-eligible-not_a_corporation.md": { body: true, client: true, placeholders: ["masked_name"] },
  "hr-questionnaire.md": { body: true, applicant: true, noAmounts: true, placeholders: [] },
  "hr-hired.md": { body: true, noAmounts: true, placeholders: [] },
  "hr-declined.md": { body: true, noAmounts: true, placeholders: [] },
  "ceo-update-email.md": { body: true, placeholders: ["headline", "lead", "summary", "action_line", "console_url"] },
  "ceo-digest-email.md": {
    body: true, spend: true,
    placeholders: ["period", "clients_new", "memos_sent", "calls_booked", "tax_saved", "agent_spend", "touches_training",
      "touches_operating", "touches_engineering", "pending_approvals", "blocked_lines", "hiring_line", "flags_lines", "console_url"],
  },
  "login-brief.md": {
    body: true, spend: true,
    placeholders: ["greeting", "clients_new", "memos_sent", "tax_saved", "agent_spend", "approvals_line", "blocked_line", "hiring_line"],
  },
  "sourcing-posts.md": { posts: true, placeholders: [] },
  "job-post.md": { posts: true, placeholders: [] },
  "demo-script.md": {}, // presenter notes
  "backup-slide.md": {}, // presenter notes
};

// The gateway's post_social guard (lane/backend perform.post_problem + masking), ported so a
// template that passes here is not refused live. No allowed URL: the gateway appends the only one.
const POST_GUARD = [
  [/(?<![\w@.])@[A-Za-z0-9_]{2,}/, "an @handle"],
  [/\b(?:https?:\/\/|www\.)\S+|\b[a-z0-9-]+\.(?:com|io|ai|dev|co|net|org|app|xyz)\b\S*/i, "a link"],
  [/\bcase[_ -]?\d+\b|\bteam\s*#?\s*\d+\b/i, "a case or Team #N"],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/, "an email address"],
  [/(?<![\w$])(?:\+?\d{1,2}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]\d{4}(?!\d)|(?<!\d)\d{10,11}(?!\d)/, "a phone number"],
  [/(?:\busd\s*\d[\d, ]*|\d[\d,]*(?:\.\d+)?\s*(?:k|m|mm|bn)?\s*(?:dollars|bucks|usd)\b)/i, "an amount in words"],
];
const POST_AMOUNTS = new Set(["$85,165", "$400", "$450", "$50"]);
function postProblem(text) {
  for (const [re, what] of POST_GUARD) if (re.test(text)) return `contains ${what}`;
  for (const m of text.matchAll(/\$\s?\d[\d, ]*(?:\.\d+)?\s?(?:[kKmMbB]\b|mm\b|bn\b)?/g)) {
    const amount = m[0].replace(/ /g, "").replace(/[,.]+$/, "");
    if (!POST_AMOUNTS.has(amount)) return `names the amount ${amount}`;
  }
  return null;
}
// The ported guard must refuse what the live one refuses, and pass the firm's standard example.
for (const bad of ["only $400k", "about 50 dollars", "see utax.ai", "Team #7 saved", "case_7 done", "(@utax)", "call 415 555 0123", "mail a@b.co", "$1,200 saved", "USD 400"])
  if (!postProblem(bad)) fail(`post guard self-test: "${bad}" was not refused`);
if (postProblem("$85,165, or $400 plus the $50 fee, $450 in all."))
  fail("post guard self-test: the standard example was refused");

/** Marketing's sections of a posts template: "## X…" and "## LinkedIn post…", up to the next heading. */
const postSections = (text) =>
  [...text.matchAll(/^## (X[^\n]*|LinkedIn post[^\n]*)\n([\s\S]*?)(?=^## |(?![\s\S]))/gm)].map((m) => ({ title: m[1], body: m[2].trim() }));

const DISCLOSURE = "This screen is run by an AI agent. A human makes the final hiring decision.";
const templatesDir = join(ROOT, "templates");
for (const file of existsSync(templatesDir) ? readdirSync(templatesDir).filter((f) => f.endsWith(".md")) : []) {
  const rule = TEMPLATE_RULES[file];
  if (!rule) {
    fail(`templates/${file}: no rule in check.mjs TEMPLATE_RULES (add one)`);
    continue;
  }
  const text = readFileSync(join(templatesDir, file), "utf8");
  if (text.includes("\r")) fail(`templates/${file}: has CR characters; keep LF`);
  if (rule.placeholders) {
    const used = [...new Set([...text.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]))];
    const extra = used.filter((p) => !rule.placeholders.includes(p));
    const absent = rule.placeholders.filter((p) => !used.includes(p));
    if (extra.length) fail(`templates/${file}: unknown placeholders ${extra}`);
    if (absent.length) fail(`templates/${file}: missing placeholders ${absent}`);
  }
  if (rule.client) {
    if (!text.includes(`${DISCLAIMER}.`)) fail(`templates/${file}: missing the line "${DISCLAIMER}."`);
    if (/\$\s?\d/.test(text.replace(/\{\{[^}]*\}\}/g, ""))) fail(`templates/${file}: literal dollar amount in a client template`);
  }
  if (rule.applicant && !text.includes(DISCLOSURE)) fail(`templates/${file}: missing the AI-screen disclosure line`);
  if (rule.body && /^\s*subject:/i.test(text)) fail(`templates/${file}: starts with a Subject: line; the gateway sets the subject`);
  if (rule.noAmounts && /\$\s?\d/.test(text)) fail(`templates/${file}: literal dollar amount`);
  if (rule.spend && /(?<!estimated )\{\{\s*agent_spend\s*\}\}/.test(text.replace(/\s+/g, " ")))
    fail(`templates/${file}: {{agent_spend}} must read "an estimated {{agent_spend}}" (spend is an estimate)`);
  if (rule.posts) {
    const sections = postSections(text);
    if (sections.length === 0) fail(`templates/${file}: no "## X" or "## LinkedIn post" sections found`);
    for (const { title, body } of sections) {
      if (!body) fail(`templates/${file}: section "${title}" is empty`);
      const problem = postProblem(body);
      if (problem) fail(`templates/${file}: "${title}" ${problem}; the post guard would refuse it`);
      if (title.startsWith("X") && [...body].length > 255) fail(`templates/${file}: "${title}" is ${[...body].length} characters (max 255 + appended link)`);
    }
  }
}
for (const required of Object.keys(TEMPLATE_RULES)) if (!existsSync(join(templatesDir, required))) fail(`templates/${required} is missing`);

// 11. the /talk voice agent's prompt (backend uses it for the OpenAI Realtime session)
const voicePath = join(ROOT, "prompts", "voice-intake.md");
if (!existsSync(voicePath)) fail("prompts/voice-intake.md is missing");
else {
  const voice = readFileSync(voicePath, "utf8").replace(/\s+/g, " ");
  for (const must of ["not tax advice", "Never claim to be a person", "transcribed", "OK to continue?", "Read the numbers back",
    "Never compute", "Never ask for, repeat or accept an SSN, EIN", "Never say an email address out loud"])
    if (!voice.includes(must)) fail(`prompts/voice-intake.md: must keep the rule "${must}"`);
  if (/utax\.\w+/.test(voice)) fail("prompts/voice-intake.md: the voice agent has no gateway tools; it must not name any");
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
