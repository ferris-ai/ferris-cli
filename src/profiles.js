import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { bundledSkillDir, CliError, ensureStateHome, tilde, userProfilesDir } from "./paths.js";
import { runScript } from "./python.js";
import { ui } from "./ui.js";

const NAME = /^[a-z0-9][a-z0-9_-]*$/;

export const PROFILES_HELP = `ferris profiles [list]                       built-in and custom profiles
ferris profiles show <name>                  the wording a profile gives the write prompt
ferris profiles new <name> [--from general]  create a custom profile in .ferris/profiles that extends another

A profile is the domain wording of the prompt the agent writes the state from: what the project is, what to track, what to add,
and the supersede examples. "delivery" is a vendor delivering to a client; "general" assumes no domain. A custom profile names
the profile it extends and overrides only the slots it changes, usually just "guidance" (extra lines added to the task context).
A workspace picks a profile when it is created: ferris new project-state <name> --profile <profile>. On top of that, a project
can have its own guidance in <workspace>/profile.md (ferris state profile overlay <WS>).`;

function read(file, source) {
  const d = JSON.parse(fs.readFileSync(file, "utf8"));
  return { name: d.name, about: d.about ?? "", source, file, d };
}

/** Built-in profiles first; a custom file with a built-in's name is ignored, as in the engine. */
export function listProfiles() {
  const out = new Map();
  const add = (dir, source) => {
    if (!fs.existsSync(dir)) return;
    for (const f of fs.readdirSync(dir).filter((f) => f.endsWith(".json")).sort()) {
      const name = f.slice(0, -5);
      if (out.has(name) || !NAME.test(name)) continue;
      try {
        out.set(name, read(path.join(dir, f), source));
      } catch (e) {
        out.set(name, { name, about: `unreadable: ${e.message}`, source, file: path.join(dir, f), broken: true });
      }
    }
  };
  add(path.join(bundledSkillDir(), "profiles"), "built-in");
  add(userProfilesDir(), "custom");
  return [...out.values()];
}

export function getProfile(name) {
  const p = listProfiles().find((x) => x.name === name);
  if (!p) throw new CliError(`unknown profile "${name}" (see: ferris profiles)`);
  if (p.broken) throw new CliError(`profile "${name}" is ${p.about}`);
  return p;
}

const SLOTS = ["intro", "role", "task_context", "add_sources", "supersede_section", "guidance"];

export function runProfiles(args, { log = console.log } = {}) {
  const [sub = "list", ...rest] = args;
  if (sub === "--help" || sub === "-h" || sub === "help") {
    log(PROFILES_HELP);
    return 0;
  }
  if (sub === "list") {
    const rows = listProfiles();
    log("");
    for (const p of rows) {
      log(`  ${ui.accent("●")} ${ui.bold(p.name.padEnd(12))} ${ui.dim(p.source.padEnd(9))} ${p.about}`);
    }
    log("");
    log(ui.dim(`  Custom profiles: ${tilde(userProfilesDir())}   ·   ferris profiles new <name> --from general`));
    return 0;
  }
  if (sub === "show") {
    const p = getProfile(rest[0]);
    // The engine resolves `extends`, so what is shown is what the prompt will use.
    const r = runScript("engine.py", ["profile", "show", p.name], { capture: true });
    if (r.status !== 0) throw new CliError((r.stderr || r.stdout || "engine profile show failed").trim());
    const d = JSON.parse(r.stdout.trim().split("\n").pop());
    log(`${ui.bold(p.name)} ${ui.dim(`(${p.source}) ${p.file}`)}`);
    if (d.about) log(d.about);
    log("");
    for (const k of SLOTS) {
      if (!d[k]) continue;
      log(`  ${ui.accent(k)}`);
      for (const l of d[k].split("\n")) log(`    ${l}`);
    }
    return 0;
  }
  if (sub === "new") {
    const { values, positionals } = parseArgs({ args: rest, allowPositionals: true, options: { from: { type: "string" } } });
    const name = positionals[0];
    if (!name || !NAME.test(name)) throw new CliError("give a profile name: lower-case letters, digits, - and _ (ferris profiles new <name>)");
    const base = getProfile(values.from ?? "general");
    if (listProfiles().some((x) => x.name === name)) throw new CliError(`a profile named "${name}" already exists`);
    ensureStateHome();
    const dir = userProfilesDir();
    fs.mkdirSync(dir, { recursive: true });
    const file = path.join(dir, `${name}.json`);
    const d = { name, extends: base.name, about: `Based on ${base.name}: describe the kind of project this is for`, guidance: "" };
    fs.writeFileSync(file, JSON.stringify(d, null, 1) + "\n");
    log(`${ui.ok("✓")} created ${ui.bold(name)} extending ${base.name}`);
    log(`  ${ui.dim("profile")}  ${file}`);
    log("");
    log(`Write the extra lines in "guidance" (terms, what matters, how statuses are worded; guidance only, no facts). To change`);
    log(`more, add any of ${SLOTS.slice(0, -1).join(", ")}; the rest comes from ${base.name}. Then: ${ui.accent(`ferris new project-state <name> --profile ${name}`)}`);
    return 0;
  }
  throw new CliError(`unknown profiles command "${sub}"\n\n${PROFILES_HELP}`);
}
