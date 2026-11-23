import fs from "node:fs";
import path from "node:path";
import { bundledSkillDir, CliError, MARKER, pkg, readMarker, SKILL_NAME, userSkillDir } from "./paths.js";

/**
 * Text rewrites applied to the installed copy so the agent drives the engine through the `ferris` command.
 * Order matters: the specific sentences go before the generic prefixes. The scripts themselves are copied
 * unchanged; `ferris state` / `ferris watch` call them.
 */
export const REWRITES = [
  ["Run scripts as `python3 SKILL_DIR/scripts/<name>.py ...` with absolute paths.", "Run the scripts through the `ferris` command (section \"Installed with the ferris CLI\")."],
  ["`W` below is `python3 SKILL_DIR/scripts/watch.py` (absolute path to the script).", "`W` below is `ferris watch`."],
  ["python3 SKILL_DIR/scripts/engine.py ", "ferris state "],
  ["python3 SKILL_DIR/scripts/render.py ", "ferris state report "],
  ["python3 SKILL_DIR/scripts/watch.py", "ferris watch"],
  ["python3 scripts/ingest_folder.py ", "ferris state ingest "],
];

export const CLI_SECTION = `## Installed with the ferris CLI
This copy was installed by \`ferris install skill\` (npm package \`@ferris-ai/cli\`) for Claude Code, Codex or Cursor; where this skill says Claude Code, read it as whichever of them you are. Run every script step through the \`ferris\` command; it calls the scripts in \`SKILL_DIR/scripts/\` with the same arguments:

| Step | Command |
|---|---|
| setup, status, stage, check, apply, recompute, fold | \`ferris state <step> ...\` (arguments as in \`scripts/engine.py\`) |
| profile and project guidance of a workspace | \`ferris state profile list\|show\|set\|overlay\|status ...\` (as \`engine.py profile\`) |
| HTML report and brief | \`ferris state report WS [--batch B]\` |
| read a source folder into candidate units | \`ferris state ingest WS --out ... [--source DIR] [--all]\` |
| reminder hook, registry, notes, project context | \`ferris watch <subcommand> ...\`; the hook goes to the agent the user works in: \`ferris watch install-hook --agent claude\|codex\|cursor\` (Codex asks the user to trust a new hook once with \`/hooks\`) |
| tracked workspaces and their pending batches | \`ferris state list\` |
| profiles (prompt wording) a new workspace can use | \`ferris profiles\`, \`ferris profiles show <name>\` |

- Project state lives in a \`.ferris\` folder, found like git finds \`.git\`: the nearest \`.ferris\` walking up from the current folder. Workspaces are \`.ferris/projects/<slug>\`; \`.ferris\` ignores itself in git. Create new workspaces there (\`ferris new project-state\`, or \`.ferris/projects/<slug>\` of the folder the user works in), not in \`~/.project-state\`.
- Wherever a command takes \`WS\`, the project's slug also works when you run it from the folder that holds \`.ferris\` or any folder below it: \`ferris state status northwind-dispatch\` uses \`.ferris/projects/northwind-dispatch\`. From anywhere else pass the full workspace path.
- The project context skill (\`ferris watch context-skill WS\`) is written to the user-level skills folder (\`~/.claude/skills/<slug>-state\`) and linked for Cursor, not into the workspace. Say so when you offer it.
- A workspace made by the earlier event-log version of the skill cannot be continued. To start again, run \`ferris new project-state "<name>" --folder <source folder>\` (or \`--units <file>\`) with the same project name and sources; the old workspace stays as it is and the user may delete it.
- \`ferris watch --help\` lists the subcommands. A subcommand takes no flags such as \`--help\`: its first argument is read as a workspace name.
- A workspace made by \`ferris new project-state\` has \`"created_by": "ferris new project-state"\` in \`config.json\`, with \`project\`, \`parties\`, \`sources\` and \`start\` filled and no \`people\` yet. It is not an older workspace: continue \`references/onboarding.md\` at step 2 (with a source folder) or, when \`input/batches.json\` already exists (made from a units file), build and confirm the people table of step 2 from the authors in \`WS/input/units.jsonl\`, then go to step 3 item 2.
- If \`ferris\` is not found, run the same step as \`python3 SKILL_DIR/scripts/<engine|render|watch|ingest_folder>.py\` with a workspace path, and tell the user to reinstall the CLI (\`npm i -g @ferris-ai/cli\`).

`;

const SKIP = new Set(["__pycache__", ".DS_Store", MARKER]);

export function transformText(text, file) {
  let out = text;
  for (const [from, to] of REWRITES) out = out.split(from).join(to);
  if (path.basename(file) === "SKILL.md" && !out.includes("## Installed with the ferris CLI")) {
    const at = out.indexOf("## Which flow");
    out = at >= 0 ? out.slice(0, at) + CLI_SECTION + out.slice(at) : out + "\n" + CLI_SECTION;
  }
  return out;
}

/** Rewrite every markdown file under dir in place. */
export function transformSkillDir(dir) {
  const walk = (d) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.name.endsWith(".md")) {
        const before = fs.readFileSync(p, "utf8");
        const after = transformText(before, p);
        if (after !== before) fs.writeFileSync(p, after);
      }
    }
  };
  walk(dir);
}

export function skillTarget({ dir, target } = {}) {
  if (target) return target;
  return dir ? path.join(path.resolve(dir), ".claude", "skills", SKILL_NAME) : userSkillDir();
}

function stamp() {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\..*/, "");
}

export function installSkill({ dir, target: given, force = false, log = console.log } = {}) {
  const src = bundledSkillDir();
  const target = skillTarget({ dir, target: given });
  const prev = fs.existsSync(target) ? readMarker(target) : null;
  if (fs.existsSync(target)) {
    if (prev) {
      fs.rmSync(target, { recursive: true, force: true });
    } else if (force) {
      const backup = `${target}.bak-${stamp()}`;
      fs.renameSync(target, backup);
      log(`moved the existing ${target} to ${backup}`);
    } else {
      throw new CliError(`${target} exists and was not installed by ferris. Re-run with --force to move it aside (it is kept as a .bak folder).`);
    }
  }
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.cpSync(src, target, { recursive: true, filter: (p) => !SKIP.has(path.basename(p)) });
  transformSkillDir(target);
  const { name, version } = pkg();
  fs.writeFileSync(
    path.join(target, MARKER),
    JSON.stringify({ package: name, version, installed_at: new Date().toISOString() }, null, 1) + "\n",
  );
  log(`${prev ? `updated ${prev.version} -> ${version}` : `installed ${version}`}: ${target}`);
  return { target, version, previous: prev?.version ?? null };
}

export function uninstallSkill({ dir, target: given, force = false, log = console.log } = {}) {
  const target = skillTarget({ dir, target: given });
  if (!fs.existsSync(target)) {
    log(`not installed: ${target}`);
    return false;
  }
  if (!readMarker(target) && !force) {
    throw new CliError(`${target} was not installed by ferris; remove it yourself or re-run with --force.`);
  }
  fs.rmSync(target, { recursive: true, force: true });
  log(`removed ${target}`);
  return true;
}
