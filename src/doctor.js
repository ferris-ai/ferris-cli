import fs from "node:fs";
import path from "node:path";
import { AGENT_IDS, AGENTS, detectAgents, hookWhere } from "./agents.js";
import { bundledSkillDir, findStateHome, pkg, projectsDir, readMarker, scriptsSkillDir, SKILL_NAME, tilde } from "./paths.js";
import { listProfiles } from "./profiles.js";
import { findPython } from "./python.js";
import { fail, info, line, ok, spin, ui, warn } from "./ui.js";

export async function runDoctor() {
  let good = true;
  const bad = (s) => {
    good = false;
    fail(s);
  };
  const { name, version } = pkg();
  line("");
  line(`  ${ui.accent("●")} ${ui.bold("ferris doctor")}  ${ui.dim(`${name} v${version}`)}`);
  line("");

  const [py, agents] = await spin("Checking", async () => [findPython({ required: false }), await detectAgents()], { done: null });
  ok(`Node ${process.versions.node}`);
  if (py) ok(`Python ${py.version} ${ui.dim(`(${py.cmd})`)}`);
  else bad(`Python 3.9+ not found as python3 or python ${ui.dim("(set FERRIS_PYTHON)")}`);
  try {
    bundledSkillDir();
  } catch (e) {
    bad(e.message);
  }

  line("");
  let anyInstalled = false;
  for (const id of AGENT_IDS) {
    const a = AGENTS[id];
    const found = agents[id];
    // Cursor also reads the Claude Code and shared ~/.agents folders
    const dirs = id === "cursor"
      ? [AGENTS.cursor.userSkills(), AGENTS.claude.userSkills(), AGENTS.codex.userSkills()]
      : [a.userSkills()];
    const where = dirs.map((d) => path.join(d, SKILL_NAME)).find((d) => readMarker(d));
    const m = where ? readMarker(where) : null;
    if (m) anyInstalled = true;
    const hooks = hookWhere(id);
    const hook = hooks.length ? ui.ok(`reminders on ${ui.dim(`(${hooks.includes("workspace") ? "this workspace" : "all workspaces"})`)}`) : ui.dim("reminders off here");
    const name = `${a.label}${found?.version ? ` ${found.version}` : ""}`.padEnd(20);
    if (!found && !m) info(`${name} ${ui.dim("not found")}`);
    else if (!m) warn(`${name} skill not installed ${ui.dim(`· ferris install skill --agent ${id}`)}`);
    else if (m.version !== version) warn(`${name} skill v${m.version}, CLI v${version} ${ui.dim(`· ferris install skill --agent ${id}`)}  ${hook}`);
    else ok(`${name} skill v${m.version} ${ui.dim(tilde(where))}  ${hook}`);
  }
  if (!anyInstalled) bad(`The skill is not installed for any agent ${ui.dim("· ferris install skill")}`);
  try {
    info(`Scripts run from ${ui.dim(tilde(path.join(scriptsSkillDir(), "scripts")))}`);
  } catch (e) {
    bad(e.message);
  }
  line("");

  const profiles = listProfiles();
  info(`Profiles: ${profiles.map((p) => (p.broken ? ui.err(p.name) : p.name)).join(", ")}`);
  const found = findStateHome();
  const dir = projectsDir();
  const n = fs.existsSync(dir)
    ? fs.readdirSync(dir, { withFileTypes: true }).filter((e) => e.isDirectory() && fs.existsSync(path.join(dir, e.name, "config.json"))).length
    : 0;
  if (found) info(`${n} project${n === 1 ? "" : "s"} in ${ui.dim(tilde(found))}`);
  else info(`No .ferris in this workspace yet ${ui.dim(`· ferris new project-state creates ${tilde(dir.replace(/\/projects$/, ""))}`)}`);
  // Workspaces from the earlier event-log version of the skill cannot be continued.
  const legacy = fs.existsSync(dir)
    ? fs.readdirSync(dir, { withFileTypes: true }).filter((e) => e.isDirectory() && fs.existsSync(path.join(dir, e.name, "events.jsonl")) && !fs.existsSync(path.join(dir, e.name, "history.jsonl"))).map((e) => e.name)
    : [];
  for (const name of legacy) warn(`${name}: made by the earlier event-log version of the skill and cannot be continued ${ui.dim("· start a new workspace: ferris new project-state")}`);
  line("");
  return good ? 0 : 1;
}
