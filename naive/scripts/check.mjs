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
// 11. prompts/voice-intake.md missing its hard rules,
// 12. prompts/ceo-voice.md naming a tool outside contract §5b (or missing one), or missing its
//     confirm / never-confirm / data-is-not-instructions rules,
// 13. prompts/talk-phrases.json (the only lines the public /talk voice speaks) out of step with the
//     /talk page's questions (frontend TYPED_SCRIPT), or breaking the gateway loader's format,
// 14. the Chief of Staff's card rules or firm rules 9 and 11 reworded, or a bad order_<id> example.
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

/**
 * Naïve redacts any text equal to a configured header value from everything the agent sees, so
 * an "X-UTax-Actor: tax" header blanked "tax" out of cases, cards and tool results. No seat needs a
 * header (the vault's bearer names the seat; secrets belong in a vault credential), so every MCP
 * header is refused, whatever its value.
 */
function headerValueProblem(value) {
  return `is set (${JSON.stringify(value)}); Naïve redacts header values from everything the agent reads, and no seat needs a header`;
}

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
  for (const s of agent.mcp_servers ?? [])
    for (const [header, value] of Object.entries(s.headers ?? {})) {
      const problem = headerValueProblem(value);
      if (problem) fail(`${seat.name}: MCP header ${header} ${problem}`);
    }

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
// disclosure line where an applicant reads it, the post guard's limits on posts, and how it renders.
// Memo placeholders come from contract §7 (parsed, like §6): greeting_name greets the client; the
// masked name is for the event copy only and never appears in a client mail.
const MEMO_PLACEHOLDERS = (() => {
  const fallback = ["greeting_name", "masked_name", "authorized_method", "assumed_par_method", "annual_report_fee", "total", "due_date", "form5472_note", "rnd_note"];
  if (!existsSync(contractPath)) return fallback;
  const s7 = readFileSync(contractPath, "utf8").split(/^## 7\./m)[1]?.split(/^## 8\./m)[0] ?? "";
  const found = [...new Set([...s7.matchAll(/`\{\{(\w+)\}\}`/g)].map((m) => m[1]))];
  if (found.length === 0) fail("contracts/api.md: could not read the §7 memo placeholders");
  return found.length ? found : fallback;
})().filter((p) => p !== "masked_name");
// body: the gateway sends the file as the body and sets the subject itself (no "Subject:" line).
// client: a client reads it (greets with greeting_name, disclaimer line, no literal dollar amounts,
// never the masked name). applicant: the AI-screen disclosure line. posts: Marketing posts its
// X/LinkedIn sections (the gateway's post guard). spend: money spent is called "estimated".
// optional: placeholders that may render empty; every empty/filled combination must render cleanly.
const TEMPLATE_RULES = {
  // contract v1.7 §7: the phone check finds the memo only by its last line
  "memo.md": { body: true, client: true, placeholders: MEMO_PLACEHOLDERS, optional: ["form5472_note", "rnd_note"],
    lastLine: "Reference: {{memo_ref}}" },
  "ask-client.md": { body: true, client: true, placeholders: ["greeting_name", "missing_list", "problems_line"], optional: ["problems_line"] },
  "not-eligible-not_incorporated.md": { body: true, client: true, placeholders: ["greeting_name"] },
  "not-eligible-outside_delaware.md": { body: true, client: true, placeholders: ["greeting_name"] },
  "not-eligible-not_a_corporation.md": { body: true, client: true, placeholders: ["greeting_name"] },
  "hr-questionnaire.md": { body: true, applicant: true, noAmounts: true, placeholders: [] },
  "hr-hired.md": { body: true, noAmounts: true, placeholders: [] },
  "hr-declined.md": { body: true, noAmounts: true, placeholders: [] },
  // v1.11 (design D31): the gateway's Spec in backend/app/gateway/templates.py names these placeholders
  "hr-outreach.md": { body: true, applicant: true, noAmounts: true, placeholders: ["slot_time", "minutes", "booking_url"], optional: ["booking_url"] },
  "follow-up-memo_check.md": { body: true, client: true, placeholders: ["greeting_name"] },
  "follow-up-missing_facts.md": { body: true, client: true, placeholders: ["greeting_name", "missing_list"] },
  "follow-up-call_reminder.md": { body: true, client: true, placeholders: ["greeting_name", "call_time"] },
  "ceo-update-email.md": { body: true, placeholders: ["headline", "lead", "summary", "action_line", "console_url"] },
  "ceo-digest-email.md": {
    body: true, spend: true,
    placeholders: ["period", "clients_new", "memos_sent", "calls_booked", "tax_saved", "agent_spend", "touches_training",
      "touches_operating", "touches_engineering", "pending_approvals", "blocked_lines", "hiring_line", "flags_lines", "console_url"],
  },
  "login-brief.md": {
    body: true, spend: true,
    placeholders: ["greeting", "clients_new", "memos_sent", "tax_saved", "agent_spend", "approvals_line", "blocked_line", "hiring_line"],
    optional: ["approvals_line", "blocked_line", "hiring_line"],
  },
  "sourcing-posts.md": { posts: true, placeholders: [] },
  "job-post.md": { posts: true, placeholders: [] },
  "demo-script.md": {}, // presenter notes
  "demo-video.md": {}, // the submission video's shot list
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
const GUARD_SELF_TEST = {
  "only $400k": "amount", "about 50 dollars": "in words", "USD 400": "in words", "$1,200 saved": "amount",
  "see utax.ai": "link", "Team #7 saved": "Team", "case_7 done": "case", "(@utax)": "@handle",
  "mail a@b.ly": "email", "call 415 555 0123": "phone", "call 4155550123": "phone",
};
for (const [bad, why] of Object.entries(GUARD_SELF_TEST)) {
  const problem = postProblem(bad);
  if (!problem?.includes(why)) fail(`post guard self-test: "${bad}" should be refused for ${why}, got ${problem}`);
}
if (postProblem("$85,165, or $400 plus the $50 fee, $450 in all."))
  fail("post guard self-test: the standard example was refused");

/** Marketing's sections of a posts template: "## X…" and "## LinkedIn post…", up to the next heading. */
const postSections = (text) =>
  [...text.matchAll(/^## (X[^\n]*|LinkedIn post[^\n]*)\n([\s\S]*?)(?=^## |(?![\s\S]))/gm)].map((m) => ({ title: m[1], body: m[2].trim() }));

/**
 * The gateway's renderer (lane/backend templates._fill), ported: a line that is only a placeholder
 * whose value is empty is dropped; every other placeholder is substituted. Stricter on purpose: the
 * gateway also collapses 3+ newlines, this port doesn't, so the template itself leaves no gaps.
 */
function renderTemplate(text, values) {
  return text
    .split("\n")
    .filter((line) => {
      const only = line.trim().match(/^\{\{\s*(\w+)\s*\}\}$/);
      return !(only && !values[only[1]]);
    })
    .join("\n")
    .replace(/\{\{\s*(\w+)\s*\}\}/g, (_, key) => values[key] ?? "")
    .trim();
}

const DISCLOSURE = "This screen is run by an AI agent. A human makes the final hiring decision.";
const GREETING = /^(hi|hello|hey|dear)\b[^\n]*\{\{\s*greeting_name\s*\}\}/i;

/** Every problem with one template file under its rule (pure, so it can be self-tested). */
function templateProblems(file, text, rule) {
  const out = [];
  const bad = (msg) => out.push(`templates/${file}: ${msg}`);
  if (text.includes("\r")) bad("has CR characters; keep LF");
  const used = [...new Set([...text.matchAll(/\{\{\s*(\w+)\s*\}\}/g)].map((m) => m[1]))];
  if (rule.placeholders) {
    const extra = used.filter((p) => !rule.placeholders.includes(p));
    const absent = rule.placeholders.filter((p) => !used.includes(p));
    if (extra.length) bad(`unknown placeholders ${extra}`);
    if (absent.length) bad(`missing placeholders ${absent}`);
  }
  // render every combination of empty / filled optional values: no gaps, no stray spaces
  const optional = (rule.optional ?? []).filter((p) => used.includes(p));
  for (let mask = 0; rule.body && mask < 1 << optional.length; mask++) {
    const values = Object.fromEntries(used.map((p) => [p, "Value."]));
    optional.forEach((p, i) => { if (mask & (1 << i)) values[p] = ""; });
    const empty = optional.filter((_, i) => mask & (1 << i));
    const rendered = renderTemplate(text, values);
    const where = empty.length ? `with ${empty} empty` : "with every value set";
    if (/\n[ \t]*\n[ \t]*\n/.test(rendered)) bad(`renders a double blank line ${where}`);
    if (/[^\s] {2,}\S/.test(rendered) || /[ \t]+$/m.test(rendered) || /^[ \t]+\S/m.test(rendered.replace(/^ {2,}- .*/gm, "")))
      bad(`renders stray spaces ${where}`);
  }
  if (rule.client) {
    const first = text.split("\n").find((l) => l.trim()) ?? "";
    if (!GREETING.test(first)) bad("the first line must greet the client with {{greeting_name}} (contract v1.6 §7)");
    if (used.includes("masked_name")) bad("uses {{masked_name}}; a client mail never shows the masked name");
    if (!text.includes(`${DISCLAIMER}.`)) bad(`missing the line "${DISCLAIMER}."`);
    if (/\$\s?\d/.test(text.replace(/\{\{[^}]*\}\}/g, ""))) bad("literal dollar amount in a client template");
  }
  if (rule.lastLine) {
    const last = text.split("\n").filter((l) => l.trim()).pop()?.trim();
    if (last !== rule.lastLine) bad(`the last line must be exactly "${rule.lastLine}" (got "${last}")`);
  }
  if (rule.applicant && !text.includes(DISCLOSURE)) bad("missing the AI-screen disclosure line");
  if (rule.body && /^\s*subject:/i.test(text)) bad("starts with a Subject: line; the gateway sets the subject");
  if (rule.noAmounts && /\$\s?\d/.test(text)) bad("literal dollar amount");
  if (rule.spend && /(?<!estimated )\{\{\s*agent_spend\s*\}\}/.test(text.replace(/\s+/g, " ")))
    bad('{{agent_spend}} must read "an estimated {{agent_spend}}" (spend is an estimate)');
  if (rule.posts) {
    const sections = postSections(text);
    if (sections.length === 0) bad('no "## X" or "## LinkedIn post" sections found');
    for (const { title, body } of sections) {
      if (!body) bad(`section "${title}" is empty`);
      const problem = postProblem(body);
      if (problem) bad(`"${title}" ${problem}; the post guard would refuse it`);
      if (title.startsWith("X") && [...body].length > 255) bad(`"${title}" is ${[...body].length} characters (max 255 + appended link)`);
    }
  }
  return out;
}

// The template rules must be able to fail: each bad template below has to produce its problem.
const CLIENT_RULE = { body: true, client: true, placeholders: ["greeting_name", "x"], optional: ["x"] };
const TEMPLATE_SELF_TEST = [
  ["Hi {{masked_name}},\n{{greeting_name}} {{x}}\nEducational estimate — not tax advice.", "first line must greet"],
  ["Hello {{greeting_name}},\n\n{{x}}\n\nEnd.\nEducational estimate — not tax advice.", "double blank line"],
  ["Hi {{greeting_name}},\nTotal is due. {{x}} Thanks.\nEducational estimate — not tax advice.", "stray spaces"],
  ["Hi {{greeting_name}},\n{{x}} {{nope}}\nEducational estimate — not tax advice.", "unknown placeholders"],
  ["Hi {{greeting_name}}, {{masked_name}}\n{{x}}\nEducational estimate — not tax advice.", "never shows the masked name"],
  ["Subject: hi\nHi {{greeting_name}},\n{{x}}\nEducational estimate — not tax advice.", "Subject: line"],
];
for (const [text, why] of TEMPLATE_SELF_TEST)
  if (!templateProblems("self-test.md", text, CLIENT_RULE).some((p) => p.includes(why))) fail(`template rule self-test: expected "${why}" for ${JSON.stringify(text)}`);
if (!templateProblems("self-test.md", "Hi {{greeting_name}},\nEducational estimate — not tax advice.\nReference: {{x}}\nP.S.",
  { ...CLIENT_RULE, lastLine: "Reference: {{x}}" }).some((p) => p.includes("last line")))
  fail('template rule self-test: expected "last line" when the reference is not last');
if (templateProblems("self-test.md", "Hi {{greeting_name}},\nTotal is due.\n{{x}}\n\nEnd.\nEducational estimate — not tax advice.", CLIENT_RULE).length)
  fail("template rule self-test: a well-formed template was refused");

const templatesDir = join(ROOT, "templates");
for (const file of existsSync(templatesDir) ? readdirSync(templatesDir).filter((f) => f.endsWith(".md")) : []) {
  const rule = TEMPLATE_RULES[file];
  if (!rule) {
    fail(`templates/${file}: no rule in check.mjs TEMPLATE_RULES (add one)`);
    continue;
  }
  for (const problem of templateProblems(file, readFileSync(join(templatesDir, file), "utf8"), rule)) fail(problem);
}
for (const required of Object.keys(TEMPLATE_RULES)) if (!existsSync(join(templatesDir, required))) fail(`templates/${required} is missing`);

// 14. hard rules from the first live case and contract v1.11.1, kept word for word: the Chief of
// Staff's card rules (it once created cards with no title or body and a made-up blocked_by, then
// repeated the call) and firm rule 11 (order ids). Every order_<n> example is the contract's shape.
const MUST_KEEP = {
  "chief-of-staff.md": ["carries all three: `title`", "`body`", "`assignee`", "names only a card id you read with `board_read` in this session",
    "Never send the same call again unchanged", "The body starts with `order_<id>`", "Don't also `send_to_agent` for the same work"],
  "_firm-rules.md": ["pass `order_id: \"order_42\"` on every U-Tax tool you call for that card", "Never make one up",
    "Everyone else never creates or assigns a card"],
};
for (const [file, rules] of Object.entries(MUST_KEEP)) {
  const path = join(ROOT, "prompts", file);
  const text = existsSync(path) ? readFileSync(path, "utf8").replace(/\s+/g, " ") : "";
  if (!text) fail(`prompts/${file} is missing`);
  else for (const must of rules) if (!text.includes(must)) fail(`prompts/${file}: must keep the rule "${must}"`);
}
for (const file of readdirSync(join(ROOT, "prompts")).filter((f) => f.endsWith(".md")))
  for (const m of readFileSync(join(ROOT, "prompts", file), "utf8").matchAll(/\border_[A-Za-z0-9<>_-]*/g))
    if (!/^order_(?:[0-9]+|<id>)$/.test(m[0]) && m[0] !== "order_id")
      fail(`prompts/${file}: "${m[0]}" isn't an order id the contract accepts (order_<digits>, or order_<id> as the placeholder)`);

// 11. the /talk voice agent's prompt (backend uses it for the OpenAI Realtime session)
const voicePath = join(ROOT, "prompts", "voice-intake.md");
if (!existsSync(voicePath)) fail("prompts/voice-intake.md is missing");
else {
  const voice = readFileSync(voicePath, "utf8").replace(/\s+/g, " ");
  for (const must of ["not tax advice", "Never claim to be a person", "transcribed", "OK to continue?", "Read the facts back",
    "Never compute", "Never ask for, repeat or accept an SSN, EIN", "Never say an email address out loud"])
    if (!voice.includes(must)) fail(`prompts/voice-intake.md: must keep the rule "${must}"`);
  if (/utax\.\w+/.test(voice)) fail("prompts/voice-intake.md: the voice agent has no gateway tools; it must not name any");
}

// 13. the /talk phrase registry (contract v1.10.2): POST /api/tts purpose "talk" speaks only these
// lines; the gateway fills each {placeholder} with a whole number (an arg named *_cents is spoken as
// dollars, "$85,165"). Mirrors the backend's strict loader (backend/app/gateway/talk_phrases.py),
// which refuses the whole file on any bad entry: a flat key → line object, keys of a-z 0-9 _ . -
// (1–64), lines of 1–600 characters that aren't blank, plain {name} placeholders of at most 32
// characters ({{ }} for a literal brace). Plus the U-Tax rules: question.N is the page's own
// question N word for word (frontend/src/talk/talk.ts TYPED_SCRIPT, which the page shows and maps to
// these keys), as many questions as voice-intake.md has, and every other key with exactly its
// expected placeholders.
const CLIENTS_INBOX = "utax-clients@agentmail.to";
const TALK_FIXED = {
  "estimate.authorized": ["total_cents"],
  "estimate.assumed_par": ["total_cents"],
  "memo.on_its_way": [],
  at_capacity: [],
};

/** voice-intake.md's numbered questions, each its first "…" quote (whitespace collapsed). */
function talkQuestions(md) {
  const section = md.split(/^## /m).find((s) => s.startsWith("What you collect")) ?? "";
  return section.split(/\n(?=\d+\. )/).slice(1)
    .map((item) => item.replace(/\s+/g, " ").match(/^\d+\. \*\*.+?\*\*.*?"([^"]+)"/)?.[1] ?? null);
}

/** The /talk page's own questions (TYPED_SCRIPT in frontend/src/talk/talk.ts), or null. */
function pageQuestions(ts) {
  const block = ts.match(/export const TYPED_SCRIPT[^=]*=\s*\[([\s\S]*?)\];/)?.[1];
  if (!block) return null;
  try {
    return [...block.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => JSON.parse(`"${m[1]}"`));
  } catch {
    return null; // a JS-only escape (\', \x41, \u{…}): reported as "could not read" below
  }
}

/**
 * A line's placeholder names, Python str.format style ({{ and }} are literal braces), or an error
 * for anything the loader refuses: an unmatched brace, a format spec, a conversion, an attribute
 * or an index, or a name that isn't a plain identifier of at most 32 characters.
 */
function talkPlaceholders(line) {
  const names = [];
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (c === "}") {
      if (line[i + 1] === "}") { i++; continue; }
      return { error: "an unmatched }" };
    }
    if (c !== "{") continue;
    if (line[i + 1] === "{") { i++; continue; }
    const end = line.indexOf("}", i);
    if (end < 0) return { error: "an unmatched {" };
    const name = line.slice(i + 1, end);
    if (!/^[a-z_][a-z0-9_]{0,31}$/.test(name))
      return { error: `the placeholder {${name}} (plain {name} of at most 32 characters only: no format spec, !conversion, .attr or [index])` };
    names.push(name);
    i = end;
  }
  return { names };
}

/** Every problem with a talk-phrases registry (pure, so it can be self-tested). */
function talkPhraseProblems(phrases, { questions, scriptCount }) {
  const out = [];
  const bad = (msg) => out.push(`prompts/talk-phrases.json: ${msg}`);
  if (!phrases || typeof phrases !== "object" || Array.isArray(phrases)) return ["prompts/talk-phrases.json: not a flat JSON object of key → line"];
  if (!questions || questions.length === 0) bad("could not read the /talk page's questions (TYPED_SCRIPT in frontend/src/talk/talk.ts)");
  else if (questions.length !== scriptCount)
    bad(`the /talk page has ${questions.length} questions but voice-intake.md has ${scriptCount}; they are one script`);
  const expected = { ...Object.fromEntries((questions ?? []).map((q, i) => [`question.${i + 1}`, []])), ...TALK_FIXED };
  const have = Object.keys(phrases);
  const missing = Object.keys(expected).filter((k) => !have.includes(k));
  const extra = have.filter((k) => !(k in expected));
  if (missing.length) bad(`missing keys ${missing}`);
  if (extra.length) bad(`unknown keys ${extra}`);
  for (const [key, line] of Object.entries(phrases)) {
    if (!/^[a-z0-9_.-]{1,64}$/.test(key)) bad(`key "${key}" must be 1–64 characters of a-z 0-9 _ . -`);
    if (typeof line !== "string") { bad(`${key} must be a string line (the file is flat: key → line)`); continue; }
    if (!line.trim() || line.length > 600) bad(`${key} is ${line.trim() ? line.length : "a blank line of " + line.length} characters (1–600, not blank)`);
    const { names, error } = talkPlaceholders(line);
    if (error) { bad(`${key} has ${error}`); continue; }
    const want = expected[key];
    if (want && JSON.stringify([...new Set(names)].sort()) !== JSON.stringify([...want].sort()))
      bad(`${key} must have exactly the placeholders ${want.length ? want.map((n) => `{${n}}`).join(", ") : "none"} (has ${names.length ? names.map((n) => `{${n}}`).join(", ") : "none"})`);
    if (/\$\s*\{\w+_cents\}|\{\w+_cents\}\s*(?:cents|dollars?)\b/i.test(line))
      bad(`${key} wraps a {…_cents} arg in "$" or a unit; a *_cents arg is already spoken as dollars ("$85,165")`);
    const m = key.match(/^question\.(\d+)$/);
    const q = m ? questions?.[Number(m[1]) - 1] : null;
    if (q && line !== q) bad(`${key} must be the /talk page's question ${m[1]} word for word: "${q}"`);
    if (key === "question.1" && !/\bAI\b/.test(line)) bad("question.1 must say the caller is talking to an AI");
    if (key.startsWith("estimate.") && !line.includes("not tax advice")) bad(`${key} speaks a figure, so it must say "not tax advice"`);
    const emails = line.match(/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g) ?? [];
    if (emails.some((e) => key !== "at_capacity" || e !== CLIENTS_INBOX)) bad(`${key} names an email address (only at_capacity names ${CLIENTS_INBOX})`);
  }
  if (typeof phrases.at_capacity === "string" && !phrases.at_capacity.includes(CLIENTS_INBOX))
    bad(`at_capacity must name ${CLIENTS_INBOX} as plain text`);
  return out;
}

const talkPath = join(ROOT, "prompts", "talk-phrases.json");
const talkPagePath = join(ROOT, "..", "frontend", "src", "talk", "talk.ts");
if (!existsSync(talkPath)) fail("prompts/talk-phrases.json is missing");
else if (existsSync(voicePath)) {
  const script = {
    questions: existsSync(talkPagePath) ? pageQuestions(readFileSync(talkPagePath, "utf8")) : null,
    scriptCount: talkQuestions(readFileSync(voicePath, "utf8")).filter(Boolean).length,
  };
  // Strict like the loader (Path.read_text(encoding="utf-8") raises on bad bytes): no U+FFFD repair.
  const strictUtf8 = (bytes) => new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  let phrases = null;
  let parsed = false;
  try {
    phrases = JSON.parse(strictUtf8(readFileSync(talkPath)));
    parsed = true;
  } catch (e) {
    fail(`prompts/talk-phrases.json: not valid UTF-8 JSON (${e.message})`);
  }
  try {
    strictUtf8(Buffer.from([0x7b, 0x22, 0xe9, 0x22, 0x7d])); // {"é"} saved as cp1252
    fail("talk-phrases rule self-test: invalid UTF-8 must be refused");
  } catch {
    /* refused, as it should be */
  }
  for (const v of [null, [], false, "x"])
    if (!talkPhraseProblems(v, script).some((p) => p.includes("not a flat JSON object")))
      fail(`talk-phrases rule self-test: expected "not a flat JSON object" for ${JSON.stringify(v)}`);
  if (parsed && (!phrases || typeof phrases !== "object" || Array.isArray(phrases)))
    for (const problem of talkPhraseProblems(phrases, script)) fail(problem);
  else if (parsed) {
    for (const problem of talkPhraseProblems(phrases, script)) fail(problem);
    // The rule must be able to fail: each broken copy has to produce its problem.
    const TALK_SELF_TEST = [
      [(p) => { p["memo.on_its_way"] = "On its way {n!r}."; }, "plain {name}"],
      [(p) => { p["memo.on_its_way"] = "On its way {total_cents:,}."; }, "plain {name}"],
      [(p) => { p["memo.on_its_way"] = "On its way {who.name}."; }, "plain {name}"],
      [(p) => { p["memo.on_its_way"] = `On its way {${"a".repeat(33)}}.`; }, "at most 32 characters"],
      [(p) => { p["memo.on_its_way"] = "On its way {"; }, "unmatched {"],
      [(p) => { p["memo.on_its_way"] = "On its way }"; }, "unmatched }"],
      [(p) => { p["memo.on_its_way"] = "On its way {n}."; }, "exactly the placeholders none"],
      [(p) => { p["estimate.authorized"] = "It's {total_cents} cents. Not tax advice."; }, "already spoken as dollars"],
      [(p) => { p["estimate.authorized"] = "It's ${total_cents}. Not tax advice."; }, "already spoken as dollars"],
      [(p) => { p["estimate.authorized"] = "It's {total_cents} dollars. Not tax advice."; }, "already spoken as dollars"],
      [(p) => { p["estimate.assumed_par"] = "It's about {total_cents}."; }, "not tax advice"],
      [(p) => { delete p["question.2"]; }, "missing keys question.2"],
      [(p) => { p["question.1"] = "Are you a Delaware company?"; }, "word for word"],
      [(p) => { p["memo.on_its_way"] = "Write to me@example.com"; }, "names an email address"],
      [(p) => { p["Voice Note"] = "hi"; }, "a-z 0-9"],
      [(p) => { p.extra = "hi"; }, "unknown keys extra"],
      [(p) => { p["memo.on_its_way"] = { text: "Bye" }; }, "must be a string line"],
      [(p) => { p["memo.on_its_way"] = ""; }, "not blank"],
      [(p) => { p["memo.on_its_way"] = "   "; }, "not blank"],
      [(p) => { p["memo.on_its_way"] = "a".repeat(601); }, "601 characters"],
      [(p) => { p.at_capacity = "We're full."; }, "plain text"],
    ];
    // The self-tests run on a synthetic registry and script, never the real files, so they test the
    // rule alone: a real page or prompt that drifts fails only with its own message above.
    const SELF_SCRIPT = { questions: ["Hi, I'm U-Tax's AI. First?", "Second?"], scriptCount: 2 };
    const SELF_PHRASES = {
      "question.1": "Hi, I'm U-Tax's AI. First?",
      "question.2": "Second?",
      "estimate.authorized": "It's {total_cents}. An educational estimate, not tax advice.",
      "estimate.assumed_par": "It's {total_cents}. An educational estimate, not tax advice.",
      "memo.on_its_way": "On its way.",
      at_capacity: `Please email ${CLIENTS_INBOX}.`,
    };
    const baseline = talkPhraseProblems(SELF_PHRASES, SELF_SCRIPT);
    if (baseline.length) fail(`talk-phrases rule self-test: the synthetic registry must pass (${baseline.join("; ")})`);
    for (const [mutate, why] of TALK_SELF_TEST) {
      const copy = structuredClone(SELF_PHRASES);
      mutate(copy);
      if (!talkPhraseProblems(copy, SELF_SCRIPT).some((p) => p.includes(why))) fail(`talk-phrases rule self-test: expected "${why}"`);
    }
    const noAi = "Is your company a Delaware corporation?";
    if (!talkPhraseProblems({ ...SELF_PHRASES, "question.1": noAi }, { ...SELF_SCRIPT, questions: [noAi, "Second?"] })
      .some((p) => p.includes("talking to an AI")))
      fail('talk-phrases rule self-test: expected "talking to an AI"');
    for (const scriptCount of [1, 3]) // the page has fewer, or more, questions than voice-intake.md
      if (!talkPhraseProblems(SELF_PHRASES, { ...SELF_SCRIPT, scriptCount }).some((p) => p.includes("they are one script")))
        fail(`talk-phrases rule self-test: expected "they are one script" (voice-intake count ${scriptCount})`);
    if (talkPlaceholders("A literal {{brace}} and {total_cents}.").names?.join() !== "total_cents")
      fail("talk-phrases rule self-test: {{ }} must read as a literal brace");
  }
}

// 12. the CEO's voice agent (/ceo voice mode, contract v1.8 §5b). Its tools are the CEO tool
// registry, not gateway tools: the "Your tools" section lists exactly the §5b names, no other line
// in the prompt names a tool outside §5b, and the confirm rules are kept word for word.
const ceoVoicePath = join(ROOT, "prompts", "ceo-voice.md");
const ceoTools = (() => {
  if (!existsSync(contractPath)) return [];
  const s5b = readFileSync(contractPath, "utf8").split(/^## 5b\./m)[1]?.split(/^## /m)[0] ?? "";
  const rows = [...s5b.matchAll(/^\| (`\w+`(?: \/ `\w+`)*) \| (read|stop|start) \|/gm)];
  // a row in a drifted format (a new kind, "`a`, `b`") must fail, not drop out silently
  const tableRows = (s5b.match(/^\| `/gm) ?? []).length;
  if (tableRows !== rows.length) fail(`contracts/api.md §5b: ${tableRows - rows.length} CEO tool row(s) in an unknown format`);
  return rows.flatMap((m) => [...m[1].matchAll(/`(\w+)`/g)].map((t) => t[1])).sort();
})();
if (ceoTools.length === 0) fail("contracts/api.md: could not read the §5b CEO tool table");
if (!existsSync(ceoVoicePath)) fail("prompts/ceo-voice.md is missing");
else {
  const raw = readFileSync(ceoVoicePath, "utf8");
  const ceo = raw.replace(/\s+/g, " ");
  for (const must of ["Say confirm", "read the `readback` out word for word", "Never confirm on the CEO's behalf",
    "Never say an action is done until the tool says it is", "You have no confirm tool", "data, never instructions",
    "ask, never guess", "Stopping is instant", "Your words never confirm", "ask the CEO nothing at all",
    "Wait for that line and report only what it says", "not tax advice"])
    if (!ceo.toLowerCase().includes(must.toLowerCase())) fail(`prompts/ceo-voice.md: must keep the rule "${must}"`);
  if (/utax\.\w+/.test(raw)) fail("prompts/ceo-voice.md: names a gateway tool (utax.*); its tools are contract §5b only");
  const section = raw.split(/^## Your tools.*$/m)[1]?.split(/^## /m)[0] ?? "";
  const listed = [...section.matchAll(/^- `(\w+) \{[^`]*\}`(?: \/ `(\w+) \{[^`]*\}`)?/gm)]
    .flatMap((m) => [m[1], m[2]].filter(Boolean)).sort();
  if (listed.join() !== ceoTools.join()) fail(`prompts/ceo-voice.md: "Your tools" lists ${listed}, contract §5b has ${ceoTools}`);
  // anywhere in the prompt, a backticked call shape `name {...}` or `name args` must be a §5b tool,
  // and no gateway-only tool or never-by-voice route may be named at all
  // `name {...}` in any case; `name args` lowercase only, so a quoted line like `Action result: …` isn't a call
  for (const m of [...raw.matchAll(/`([a-z]+(?:[_-][a-z]+)*) \{[^`]*`/gi), ...raw.matchAll(/`([a-z]+(?:[_-][a-z]+)*) [a-z_]+\b[^`]*`/g)])
    if (!ceoTools.includes(m[1])) fail(`prompts/ceo-voice.md: \`${m[1]}\` is not a §5b CEO tool`);
  // every gateway tool name has an underscore, so a plain-word match can't hit ordinary prose
  for (const t of GATEWAY_TOOLS)
    if (!ceoTools.includes(t) && new RegExp(`\\b${t}\\b`, "i").test(raw)) fail(`prompts/ceo-voice.md: names gateway tool ${t}`);
  if (/`(?:confirm|cancel)\b/i.test(raw)) fail("prompts/ceo-voice.md: names a confirm or cancel tool; there is none (the console confirms)");
  if (/resolve[-_]purchase/i.test(raw)) fail("prompts/ceo-voice.md: resolve-purchase is never by voice (contract §5b)");
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
  "URL with credentials": { UTAX_MCP_URL: "https://user:pass@utax.example.com/mcp" },
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
