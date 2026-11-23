import { parseArgs } from "node:util";
import { runDoctor } from "./doctor.js";
import { NEW_HELP, runNew } from "./new.js";
import { CliError, pkg, readMarker, SKILL_NAME, tilde } from "./paths.js";
import { PROFILES_HELP, runProfiles } from "./profiles.js";
import path from "node:path";
import { AGENT_IDS, AGENTS, detectAgents, parseAgents } from "./agents.js";
import { installForAgents, runSetup } from "./setup.js";
import { uninstallSkill } from "./skill.js";
import { runState, runWatch, STATE_HELP } from "./state.js";
import { banner, line, ok, ui } from "./ui.js";

export const HELP = `Get started
  ferris setup                                     guided first run: check, pick agents, install, start a project

Setup
  ferris install skill [--agent A]... [--dir P]    install or update the skill for Claude Code, Codex, Cursor
  ferris uninstall skill [--agent A]... [--dir P]  remove it
  ferris new project-state [name] [options]        create a workspace for a project (ferris new project-state --help)
  ferris profiles [list|show|new]                  the prompt wording a project's state uses (delivery, general, yours)
  ferris doctor                                    check Python, Claude Code, the installed skill and reminders

For the agent
  ferris state <command> ...                       run a step of the state engine (ferris state --help)
  ferris watch <subcommand> ...                    reminder hook, registry, notes, project context skill

  ferris --version | --help

--agent claude|codex|cursor (repeatable; default: the agents found on this machine). --dir P installs into the
project folder P (.claude/skills, .agents/skills, .cursor/skills) instead of your user folders.
State lives in the nearest .ferris folder (git-ignored), created where you run ferris new.
Environment: PROJECT_STATE_HOME (overrides .ferris), CLAUDE_CONFIG_DIR (~/.claude), FERRIS_PYTHON, FERRIS_SKILL_DIR.`;

function colorHelp(text) {
  if (!ui.color) return text;
  return text
    .split("\n")
    .map((l) => {
      if (/^[A-Z][a-z ]+$/.test(l)) return ui.accent(l);
      const m = l.match(/^(  ferris [^ ]+(?: [^ ]+)*?)(\s{2,})(.*)$/);
      return m ? ui.bold(m[1]) + m[2] + ui.dim(m[3]) : l;
    })
    .join("\n");
}

function printHelp({ brand = true } = {}) {
  if (brand && ui.color) line(banner({ version: pkg().version }));
  line(colorHelp(HELP));
  if (brand && ui.color) line(`\n  ${ui.dim("New here?")} ${ui.accent("ferris setup")}\n`);
}

function skillArgs(rest) {
  const { values, positionals } = parseArgs({
    args: rest,
    allowPositionals: true,
    options: { dir: { type: "string" }, force: { type: "boolean" }, agent: { type: "string", multiple: true } },
  });
  const [what = "skill", which = "project-state"] = positionals;
  if (what !== "skill") throw new CliError(`unknown target "${what}" (available: skill)`);
  if (which !== "project-state") throw new CliError(`unknown skill "${which}" (available: project-state)`);
  return values;
}

export async function main(argv) {
  const [cmd, ...rest] = argv;
  switch (cmd) {
    case undefined:
      printHelp();
      return 0;
    case "help":
    case "--help":
    case "-h":
      printHelp();
      return 0;
    case "--version":
    case "-v":
      line(pkg().version);
      return 0;
    case "setup":
      return runSetup(rest);
    case "install": {
      const o = skillArgs(rest);
      line(banner({ version: pkg().version, compact: true }));
      let agents = parseAgents(o.agent);
      if (!agents.length) agents = Object.keys(await detectAgents());
      if (!agents.length) agents = ["claude"];
      await installForAgents(agents, { dir: o.dir, force: o.force });
      line(`\n  ${ui.dim("Next:")} start a new agent session so it loads the skill, then ${ui.accent("ferris new project-state")} in your workspace\n`);
      return 0;
    }
    case "uninstall": {
      const o = skillArgs(rest);
      const ids = parseAgents(o.agent);
      const targets = (ids.length ? ids : AGENT_IDS).map((id) =>
        path.join(o.dir ? AGENTS[id].projectSkills(path.resolve(o.dir)) : AGENTS[id].userSkills(), SKILL_NAME));
      let n = 0;
      for (const target of targets) {
        if (!readMarker(target) && !o.force) continue;
        if (uninstallSkill({ target, force: o.force, log: () => {} })) {
          ok(`Removed ${tilde(target)}`);
          n++;
        }
      }
      if (!n) line("  No project-state skill installed by ferris was found there.");
      return 0;
    }
    case "new":
      return runNew(rest);
    case "profiles":
      return runProfiles(rest);
    case "state":
      return runState(rest);
    case "watch":
      return runWatch(rest);
    case "doctor":
      return runDoctor();
    default:
      throw new CliError(`unknown command "${cmd}" (ferris --help)`);
  }
}

export { NEW_HELP, PROFILES_HELP, STATE_HELP };
