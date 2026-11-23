import fs from "node:fs";
import path from "node:path";
import { CliError, projectsDir, resolveWs, stateHome } from "./paths.js";
import { runScript } from "./python.js";

// script, and the position of the workspace among the arguments (positionals come first in every script).
const STATE = {
  setup: { script: "engine.py", sub: "setup", ws: 1, create: true },
  status: { script: "engine.py", sub: "status", ws: 0 },
  stage: { script: "engine.py", sub: "stage", ws: 0 },
  check: { script: "engine.py", sub: "check", ws: 0 },
  apply: { script: "engine.py", sub: "apply", ws: 0 },
  recompute: { script: "engine.py", sub: "recompute", ws: 0 },
  fold: { script: "engine.py", sub: "fold", ws: 0 },
  report: { script: "render.py", ws: 0 },
  ingest: { script: "ingest_folder.py", ws: 0 },
};

// watch.py subcommands whose first argument is a workspace.
const WATCH_WS = new Set(["register", "info", "tip", "scan", "mark", "snooze", "notes", "context-skill"]);

export const STATE_HELP = `ferris state <command> [args]   run a step of the state engine (the agent's interface)

  list                                  tracked workspaces under the state home, with pending batches
  setup <units.jsonl> <WS> [batches.json] [--today D] [--profile NAME]
                                        create a workspace or add units; cuts new batches
  status <WS>                           pending batches and the next step for each
  stage <WS> <B>                        write WS/workdir/write_prompt.md for batch B
  check <WS> <B>                        dry-run validation of the agent's reply for the current step
  apply <WS> <B>                        apply WS/reply/<B>.json; flags dependents for recompute
  recompute <WS> <B>                    apply WS/reply/<B>.recompute.<n>.json (at most 3 rounds)
  fold <WS> <B>                         save WS/store/<B>.json; the batch is done
  report <WS> [--batch B]               HTML report and brief (WS/current/)
  profile list|show|set|overlay|status  the prompt wording of a workspace and its project guidance
  ingest <WS> --out FILE [--source DIR] [--all]
                                        read the source folder into candidate units

<WS> is a workspace path or a project slug (.ferris/projects/<slug> in the nearest .ferris).
setup, status, stage, check, apply, recompute, fold, profile and list print one JSON object.`;

function withWs(args, index, opts) {
  if (args.length <= index) return args;
  const out = [...args];
  out[index] = resolveWs(args[index], opts);
  return out;
}

export function runState(args) {
  const [cmd, ...rest] = args;
  if (!cmd || cmd === "help" || cmd === "--help" || cmd === "-h") {
    console.log(STATE_HELP);
    return cmd ? 0 : 1;
  }
  if (cmd === "list") return listWorkspaces();
  if (cmd === "profile") {
    // profile list|show take no workspace; set|overlay|status take one right after the subcommand
    const [sub, ...r] = rest;
    const takesWs = ["set", "overlay", "status"].includes(sub) && r.length > 0;
    return runScript("engine.py", ["profile", ...(sub ? [sub] : []), ...(takesWs ? withWs(r, 0, { mustExist: true }) : r)]);
  }
  const spec = STATE[cmd];
  if (!spec) throw new CliError(`unknown state command "${cmd}"\n\n${STATE_HELP}`);
  if (rest.length <= spec.ws) throw new CliError(`ferris state ${cmd}: missing arguments\n\n${STATE_HELP}`);
  const argv = withWs(rest, spec.ws, { mustExist: !spec.create });
  return runScript(spec.script, spec.sub ? [spec.sub, ...argv] : argv);
}

export function runWatch(args) {
  const [sub, ...rest] = args;
  if (sub && WATCH_WS.has(sub) && rest.length && !(sub === "context-skill" && rest.includes("--remove"))) {
    return runScript("watch.py", [sub, ...withWs(rest, 0, {})]);
  }
  return runScript("watch.py", args);
}

export function listWorkspaces() {
  const home = stateHome();
  const dir = projectsDir();
  const rows = [];
  const entries = fs.existsSync(dir) ? fs.readdirSync(dir, { withFileTypes: true }) : [];
  for (const e of entries) {
    if (!e.isDirectory()) continue;
    const ws = path.join(dir, e.name);
    const cfgPath = path.join(ws, "config.json");
    if (!fs.existsSync(cfgPath)) continue;
    let cfg = {};
    try {
      cfg = JSON.parse(fs.readFileSync(cfgPath, "utf8"));
    } catch {
      rows.push({ slug: e.name, workspace: ws, error: "config.json is not valid JSON" });
      continue;
    }
    const row = { slug: cfg.project?.slug ?? e.name, name: cfg.project?.name ?? null, workspace: ws };
    if (fs.existsSync(path.join(ws, "input", "batches.json"))) {
      const r = runScript("engine.py", ["status", ws], { capture: true });
      if (r.status === 0) {
        const s = JSON.parse(r.stdout);
        Object.assign(row, { batches: s.batches, done: s.done.at(-1) ?? null, pending: s.pending.map((p) => p.batch) });
      } else {
        row.error = (r.stderr || r.stdout).trim();
      }
    } else {
      Object.assign(row, { batches: 0, done: null, pending: [] });
    }
    row.people = Array.isArray(cfg.people) ? cfg.people.length : 0;
    row.last_update = cfg.last_update ?? null;
    rows.push(row);
  }
  console.log(JSON.stringify({ state_home: home, workspaces: rows }));
  return 0;
}
