import fs from "node:fs";
import path from "node:path";
import { parseArgs } from "node:util";
import { CliError, ensureStateHome, expandHome, gitRootOf, insideIgnoredStateHome, installedSkillDirs, projectsDir, readMarker, slugify, syncedFolderHint, tilde } from "./paths.js";
import { getProfile, listProfiles } from "./profiles.js";
import { runScriptAsync } from "./python.js";
import { ask, bar, box, choose, closePrompts, info, line, ok, spin, ui, warn } from "./ui.js";

export const NEW_HELP = `ferris new project-state [name] [options]   create a project-state workspace

  --profile NAME      wording of the state prompt: delivery (default), general, or a custom one (ferris profiles)
  --alias NAME        another name people use for the project (repeatable)
  --vendor NAME       the vendor's name (the side delivering the work)
  --client NAME       the client's name
  --folder DIR        the folder that holds the project's documents
  --units FILE        a ready units.jsonl instead of a folder (runs setup)
  --batches FILE      with --units: your own batch cut
  --start YYYY-MM-DD  track from this date (default: the oldest document)
  --ws DIR            workspace location (default: ./.ferris/projects/<slug>, in the nearest .ferris)
  -y, --yes           do not prompt; use only what was given

Prompts for whatever is missing when run in a terminal. Then, in Claude Code, Codex or Cursor, say
"start tracking <name> with the project-state skill" to read the documents and confirm the people.`;

const DATE = /^\d{4}-\d{2}-\d{2}$/;

export function parseNewArgs(rest) {
  const { values: v, positionals } = parseArgs({
    args: rest,
    allowPositionals: true,
    options: {
      profile: { type: "string" },
      alias: { type: "string", multiple: true },
      vendor: { type: "string" },
      client: { type: "string" },
      folder: { type: "string" },
      units: { type: "string" },
      batches: { type: "string" },
      start: { type: "string" },
      ws: { type: "string" },
      yes: { type: "boolean", short: "y" },
      help: { type: "boolean", short: "h" },
    },
  });
  return { ...v, name: positionals.join(" ").trim(), aliases: v.alias ?? null };
}

/** Fill in what is missing by asking (only on a terminal, unless --yes). */
export async function promptNew(o) {
  const out = { ...o };
  if (!out.name) out.name = await ask("Project name");
  if (!out.profile) {
    const profiles = listProfiles().filter((p) => !p.broken);
    out.profile =
      profiles.length > 1
        ? await choose(
            "What kind of work is it?",
            profiles.map((p) => ({
              label: p.name,
              value: p.name,
              hint: p.name === "delivery" ? "a vendor delivering to a client: deliverables, acceptance, changes"
                : p.name === "general" ? "anything else: rules, decisions, commitments, dates, statuses"
                : p.about.slice(0, 60),
            })),
          )
        : "delivery";
  }
  if (out.aliases === null) {
    const a = await ask("Other names people use for it", { hint: "comma-separated, optional" });
    out.aliases = a ? a.split(",").map((s) => s.trim()).filter(Boolean) : [];
  }
  if (out.profile === "delivery" || out.vendor || out.client) {
    if (!out.vendor) out.vendor = await ask("Vendor", { hint: "the side delivering the work" });
    if (!out.client) out.client = await ask("Client");
  }
  if (!out.folder && !out.units) out.folder = await ask("Folder with the project's documents", { hint: "optional, drag it in" });
  if (out.folder) out.folder = out.folder.replace(/^['"]|['"]$/g, "").replace(/\\ /g, " ");
  if (!out.start) out.start = await ask("Track from", { hint: "YYYY-MM-DD, optional; default the oldest document" });
  return out;
}

const KINDS = [
  ["Email", /\.(eml|mbox|msg)$/i],
  ["Chat", /\.json$/i],
  ["Notes", /\.(md|txt|vtt|srt)$/i],
  ["Files", /\.(pdf|docx?|xlsx?|pptx?|csv|rtf|odt|html?)$/i],
];

/** Count the folder's files by kind: names and sizes only, no content is read. */
export function scanFolder(dir, limit = 50000) {
  const counts = Object.fromEntries(KINDS.map(([k]) => [k, 0]));
  counts.Other = 0;
  let n = 0;
  let bytes = 0;
  const walk = (d) => {
    let entries;
    try {
      entries = fs.readdirSync(d, { withFileTypes: true });
    } catch {
      return;
    }
    for (const e of entries) {
      if (n >= limit) return;
      if (e.name.startsWith(".") || e.name === "node_modules") continue;
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.isFile()) {
        n++;
        try {
          bytes += fs.statSync(p).size;
        } catch {
          // unreadable file: counted, size unknown
        }
        const kind = KINDS.find(([, re]) => re.test(e.name));
        counts[kind ? kind[0] : "Other"]++;
      }
    }
  };
  walk(dir);
  return { counts, files: n, bytes, truncated: n >= limit };
}

function human(bytes) {
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let b = bytes;
  while (b >= 1024 && i < u.length - 1) {
    b /= 1024;
    i++;
  }
  return `${b < 10 && i ? b.toFixed(1) : Math.round(b)} ${u[i]}`;
}

export function printScan(scan) {
  const max = Math.max(1, ...Object.values(scan.counts));
  for (const [k, c] of Object.entries(scan.counts)) {
    if (!c) continue;
    line(`    ${k.padEnd(6)} ${String(c).padStart(6)}  ${bar(c / max, 22)}`);
  }
  info(`${human(scan.bytes)} in total${scan.truncated ? ` · stopped counting at ${scan.files} files` : ""}`);
}

/** Validate options and write the workspace. Returns {ws, cfg, scan, setup}. */
export async function createWorkspace(o) {
  const name = (o.name ?? "").trim();
  if (!name) throw new CliError('a project name is required: ferris new project-state "<name>"');
  if (o.folder && o.units) throw new CliError("give either --folder or --units, not both");
  if (o.batches && !o.units) throw new CliError("--batches needs --units");
  if (o.start && !DATE.test(o.start)) throw new CliError(`--start must be YYYY-MM-DD, not "${o.start}"`);
  const profile = getProfile(o.profile || "delivery");

  const slug = slugify(name);
  if (!slug) throw new CliError(`cannot make a folder name from "${name}"`);
  const ws = o.ws ? path.resolve(expandHome(o.ws)) : path.join(projectsDir(), slug);
  if (fs.existsSync(path.join(ws, "config.json"))) {
    throw new CliError(`a workspace already exists at ${ws}; update it from Claude Code ("update ${name}")`);
  }
  // In .ferris the folder ignores itself, so a git repository is fine; anywhere else in a repository is refused.
  const managed = !o.ws || insideIgnoredStateHome(ws);
  const repo = gitRootOf(ws);
  if (repo && !managed) throw new CliError(`${ws} is inside a git repository (${repo}); the workspace holds client data: leave out --ws to use .ferris (git-ignored), or pick a folder outside the repository`);

  let sourceDir = null;
  if (o.folder) {
    sourceDir = path.resolve(expandHome(o.folder));
    if (!fs.existsSync(sourceDir) || !fs.statSync(sourceDir).isDirectory()) throw new CliError(`not a folder: ${sourceDir}`);
  }
  let unitsFile = null;
  if (o.units) {
    unitsFile = path.resolve(expandHome(o.units));
    if (!fs.existsSync(unitsFile)) throw new CliError(`not found: ${unitsFile}`);
  }

  const parties = {};
  if (o.vendor) parties.vendor = o.vendor;
  if (o.client) parties.client = o.client;
  const aliases = [...new Set((o.aliases ?? []).filter((a) => a && a !== name))];
  const cfg = {
    created: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    created_by: "ferris new project-state",
    profile: profile.name,
    excluded_authors: ["ferris", "ferris ai", "ferris agent"],
    excluded_provider_prefixes: ["ferris"],
    cadence: "weekly",
    mode: "ask",
    project: { name, slug, aliases },
    ...(Object.keys(parties).length ? { parties } : {}),
    ...(sourceDir ? { sources: [{ type: "folder", path: sourceDir }] } : {}),
    ...(o.start ? { start: o.start } : {}),
    last_update: null,
  };

  const scan = sourceDir
    ? await spin("Looking through the folder", async () => scanFolder(sourceDir), {
        done: (s) => `Found ${ui.bold(s.files)} file${s.files === 1 ? "" : "s"} ${ui.dim("· names and sizes only, nothing opened")}`,
      })
    : null;
  if (scan) printScan(scan);

  if (!o.ws) ensureStateHome();
  fs.mkdirSync(ws, { recursive: true });
  fs.writeFileSync(path.join(ws, "config.json"), JSON.stringify(cfg, null, 1) + "\n");
  ok(`Workspace ${ui.bold(slug)} ${ui.dim(tilde(ws))}${repo ? ui.dim(" · git-ignored") : ""}`);
  const synced = syncedFolderHint(ws);
  if (synced) warn(`${ws} looks like a synced folder (${synced}); the workspace holds client data`);

  let setup = null;
  if (unitsFile) {
    const args = ["setup", unitsFile, ws, ...(o.batches ? [path.resolve(expandHome(o.batches))] : [])];
    args.push("--profile", profile.name);
    setup = await spin("Cutting the units into batches", async () => {
      const r = await runScriptAsync("engine.py", args);
      if (r.status !== 0) throw new Error((r.stderr || r.stdout).trim() || "engine setup failed");
      return JSON.parse(r.stdout.trim().split("\n").pop());
    }, { done: (s) => `${ui.bold(s.new_units)} units in ${ui.bold(s.batches.length)} weekly batch${s.batches.length === 1 ? "" : "es"}` });
  }
  return { ws, cfg, scan, setup, profile: profile.name };
}

const LABELS = { claude: "Claude Code", codex: "Codex", cursor: "Cursor" };

export function nextCard({ cfg, scan, setup }, agents = []) {
  const name = cfg.project.name;
  const l = agents.map((a) => LABELS[a]);
  const who = !l.length ? "your coding agent" : l.length === 1 ? l[0] : `${l.slice(0, -1).join(", ")} or ${l.at(-1)}`;
  const lines = [
    `${ui.dim(`In ${who}, in this workspace, say:`)}`,
    `${ui.accent("›")} ${ui.bold(`start tracking ${name} with the project-state skill`)}`,
    "",
    scan
      ? ui.dim("The agent reads the folder, shows the people it found for")
      : setup
        ? ui.dim("The agent confirms the people from the units, then processes")
        : ui.dim("No documents yet: tell Claude where they are, or re-run"),
    scan
      ? ui.dim("you to confirm, then works through the history week by week.")
      : setup
        ? ui.dim("the batches and opens the report.")
        : ui.dim("with --folder or --units."),
  ];
  return box(lines, { title: "Next" });
}

export async function runNew(args) {
  const [template, ...rest] = args;
  if (!template || template === "--help" || template === "-h") {
    line(NEW_HELP);
    return template ? 0 : 1;
  }
  if (template !== "project-state") throw new CliError(`unknown template "${template}" (available: project-state)`);
  let o = parseNewArgs(rest);
  if (o.help) {
    line(NEW_HELP);
    return 0;
  }
  if (!o.yes && ui.interactive) {
    line("");
    try {
      o = await promptNew(o);
    } finally {
      closePrompts();
    }
    line("");
  }
  const result = await createWorkspace(o);
  line("");
  if (!installedSkillDirs().some((d) => readMarker(d))) warn(`The project-state skill is not installed for any agent yet: run ${ui.accent("ferris install skill")}`);
  line(nextCard(result));
  line("");
  return 0;
}
