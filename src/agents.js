// The coding agents ferris installs into. Paths from each tool's docs (checked November 2026):
//   Claude Code  skills ~/.claude/skills            hook ~/.claude/settings.json  hooks.SessionStart
//   Codex        skills ~/.agents/skills            hook ~/.codex/hooks.json      hooks.SessionStart (trusted once via /hooks)
//   Cursor       skills ~/.cursor/skills, and it also reads ~/.agents/skills and (Third-Party Imports, on by default)
//                ~/.claude/skills                   hook ~/.cursor/hooks.json     hooks.sessionStart (version 1)
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { claudeDir, CliError, expandHome, SKILL_NAME, stateHome } from "./paths.js";
import { runAsync } from "./python.js";

const envHome = (env, rel) => path.resolve(process.env[env] ? expandHome(process.env[env]) : path.join(os.homedir(), rel));
const firstVersion = (s) => (String(s).match(/\d+\.\d+(\.\d+)?/) ?? [null])[0];

export const AGENTS = {
  claude: {
    label: "Claude Code",
    configDir: () => claudeDir(),
    userSkills: () => path.join(claudeDir(), "skills"),
    projectSkills: (p) => path.join(p, ".claude", "skills"),
    hookFile: () => path.join(claudeDir(), "settings.json"),
    // per workspace, personal (not shared); Claude Code reads it only for sessions in that workspace
    workspaceHookFile: (root) => path.join(root, ".claude", "settings.local.json"),
    hookKey: "SessionStart",
    async detect() {
      const r = await runAsync("claude", ["--version"]);
      return r.status === 0 ? { version: firstVersion(r.stdout) } : fs.existsSync(claudeDir()) ? { version: null } : null;
    },
  },
  codex: {
    label: "Codex",
    configDir: () => envHome("CODEX_HOME", ".codex"),
    userSkills: () => path.join(os.homedir(), ".agents", "skills"),
    projectSkills: (p) => path.join(p, ".agents", "skills"),
    hookFile: () => path.join(envHome("CODEX_HOME", ".codex"), "hooks.json"),
    workspaceHookFile: null, // Codex reads hooks from the user file only
    hookKey: "SessionStart",
    async detect() {
      const r = await runAsync("codex", ["--version"]);
      return r.status === 0 ? { version: firstVersion(r.stdout) } : fs.existsSync(envHome("CODEX_HOME", ".codex")) ? { version: null } : null;
    },
  },
  cursor: {
    label: "Cursor",
    configDir: () => envHome("CURSOR_HOME", ".cursor"),
    userSkills: () => path.join(envHome("CURSOR_HOME", ".cursor"), "skills"),
    projectSkills: (p) => path.join(p, ".cursor", "skills"),
    hookFile: () => path.join(envHome("CURSOR_HOME", ".cursor"), "hooks.json"),
    workspaceHookFile: (root) => path.join(root, ".cursor", "hooks.json"),
    hookKey: "sessionStart",
    async detect() {
      if (!fs.existsSync(envHome("CURSOR_HOME", ".cursor"))) return null;
      const r = await runAsync("cursor", ["--version"]);
      return { version: r.status === 0 ? firstVersion(r.stdout) : null };
    },
  },
};

export const AGENT_IDS = Object.keys(AGENTS);

export function parseAgents(list) {
  const ids = (list ?? []).flatMap((s) => String(s).split(",")).map((s) => s.trim().toLowerCase()).filter(Boolean);
  for (const id of ids) if (!AGENTS[id]) throw new CliError(`unknown agent "${id}" (available: ${AGENT_IDS.join(", ")})`);
  return [...new Set(ids)];
}

/** Every agent found on this machine: {id: {version}}. */
export async function detectAgents() {
  const found = {};
  await Promise.all(
    AGENT_IDS.map(async (id) => {
      const d = await AGENTS[id].detect();
      if (d) found[id] = d;
    }),
  );
  return found;
}

/**
 * Skill folders to install for the chosen agents. Cursor reads ~/.claude/skills and ~/.agents/skills too, so it gets
 * its own copy only when neither of those is being installed: one copy per place, as few duplicates in Cursor as possible.
 */
export function skillTargets(agentIds, { dir } = {}) {
  const pick = (id) => (dir ? AGENTS[id].projectSkills(path.resolve(dir)) : AGENTS[id].userSkills());
  const ids = agentIds.length ? agentIds : ["claude"];
  const targets = [];
  for (const id of ids) {
    if (id === "cursor" && (ids.includes("claude") || ids.includes("codex"))) continue;
    targets.push({ agent: id, dir: path.join(pick(id), SKILL_NAME) });
  }
  return targets;
}

export function joinLabels(ids, conj = "and") {
  const l = ids.map((id) => AGENTS[id].label);
  return l.length <= 1 ? l.join("") : `${l.slice(0, -1).join(", ")} ${conj} ${l.at(-1)}`;
}

/** The workspace: the folder that holds .ferris. */
export const workspaceDir = () => path.dirname(stateHome());

/** The hook file install-hook writes for the agent: the workspace's own one, else (Codex) the user's. */
export function hookFileFor(id) {
  const a = AGENTS[id];
  return a.workspaceHookFile ? a.workspaceHookFile(workspaceDir()) : a.hookFile();
}

function hasCheck(file, key) {
  try {
    const s = JSON.parse(fs.readFileSync(file, "utf8"));
    const list = s.hooks?.[key] ?? [];
    const cmds = list.flatMap((e) => (e.hooks ? e.hooks.map((h) => h.command) : [e.command]));
    return cmds.some((c) => /watch\.py'? check( --format \w+)?$/.test(c ?? ""));
  } catch {
    return false;
  }
}

/** Where our session-start hook is: "workspace", "user", both, or neither (empty). */
export function hookWhere(id) {
  const a = AGENTS[id];
  const out = [];
  if (a.workspaceHookFile && hasCheck(a.workspaceHookFile(workspaceDir()), a.hookKey)) out.push("workspace");
  if (hasCheck(a.hookFile(), a.hookKey)) out.push("user");
  return out;
}

export const hookInstalled = (id) => hookWhere(id).length > 0;
