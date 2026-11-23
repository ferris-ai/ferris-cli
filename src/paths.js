import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const SKILL_NAME = "project-state";
export const MARKER = ".ferris.json";
export const PKG_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export class CliError extends Error {
  constructor(message, code = 1) {
    super(message);
    this.name = "CliError";
    this.code = code;
  }
}

export function pkg() {
  return JSON.parse(fs.readFileSync(path.join(PKG_ROOT, "package.json"), "utf8"));
}

export function expandHome(p) {
  if (p === "~") return os.homedir();
  if (p.startsWith("~/")) return path.join(os.homedir(), p.slice(2));
  return p;
}

// Same environment variables and defaults as scripts/watch.py, so both sides agree on locations.
function home(env, rel) {
  const v = process.env[env];
  return path.resolve(v ? expandHome(v) : path.join(os.homedir(), rel));
}

export const FERRIS_DIR = ".ferris";

/**
 * The nearest .ferris folder walking up from `start` (like git and .git), or null. PROJECT_STATE_HOME overrides it.
 * Mirrors scripts/ferris_home.py; the CLI also passes its answer to the scripts it runs, so both always agree.
 */
export function findStateHome(start = process.cwd()) {
  if (process.env.PROJECT_STATE_HOME) return path.resolve(expandHome(process.env.PROJECT_STATE_HOME));
  let d = path.resolve(expandHome(start));
  for (;;) {
    if (path.basename(d) === FERRIS_DIR) return d;
    const c = path.join(d, FERRIS_DIR);
    if (fs.existsSync(c) && fs.statSync(c).isDirectory()) return c;
    const up = path.dirname(d);
    if (up === d) return null;
    d = up;
  }
}

/**
 * The workspace root a new .ferris goes in, the way coding agents scope a workspace: the git repository's root
 * when inside one, else the current folder.
 */
export function workspaceRoot(start = process.cwd()) {
  return gitRootOf(start) ?? path.resolve(start);
}

/** The state home: the nearest .ferris, else <workspace root>/.ferris (created on first write by ensureStateHome). */
export const stateHome = () => findStateHome() ?? path.join(workspaceRoot(), FERRIS_DIR);
export const projectsDir = () => path.join(stateHome(), "projects");

const IGNORE_ALL = "# Written by ferris: everything in .ferris is local project state (client data). Never commit it.\n*\n";

/** Create the state home with a .gitignore that ignores the whole folder, itself included. Returns its path. */
export function ensureStateHome() {
  const h = stateHome();
  fs.mkdirSync(path.join(h, "projects"), { recursive: true });
  const gi = path.join(h, ".gitignore");
  if (!fs.existsSync(gi)) fs.writeFileSync(gi, IGNORE_ALL);
  return h;
}

/** True when p is inside a .ferris folder whose .gitignore ignores everything. */
export function insideIgnoredStateHome(p) {
  let d = path.resolve(p);
  for (;;) {
    if (path.basename(d) === FERRIS_DIR) {
      try {
        return fs.readFileSync(path.join(d, ".gitignore"), "utf8").split("\n").some((l) => l.trim() === "*");
      } catch {
        return false;
      }
    }
    const up = path.dirname(d);
    if (up === d) return false;
    d = up;
  }
}

export const claudeDir = () => home("CLAUDE_CONFIG_DIR", ".claude");
export const userSkillDir = () => path.join(claudeDir(), "skills", SKILL_NAME);

export function bundledSkillDir() {
  const d = path.join(PKG_ROOT, "skill", SKILL_NAME);
  if (fs.existsSync(path.join(d, "SKILL.md")) && fs.existsSync(path.join(d, "scripts", "engine.py"))) return d;
  throw new CliError("the project-state skill is missing from this package; reinstall @ferris-ai/cli");
}

export const userProfilesDir = () => path.join(stateHome(), "profiles");

export function readMarker(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, MARKER), "utf8"));
  } catch {
    return null;
  }
}

/**
 * Where the Python scripts run from. The installed skill comes first so the commands match the docs the
 * agent reads and the SessionStart hook keeps a stable path; the bundled copy is the fallback.
 */
export function scriptsSkillDir() {
  if (process.env.FERRIS_SKILL_DIR) {
    const d = path.resolve(expandHome(process.env.FERRIS_SKILL_DIR));
    if (!fs.existsSync(path.join(d, "scripts", "engine.py"))) throw new CliError(`FERRIS_SKILL_DIR has no scripts/engine.py: ${d}`);
    return d;
  }
  for (const u of installedSkillDirs()) {
    if (readMarker(u) && fs.existsSync(path.join(u, "scripts", "engine.py"))) return u;
  }
  return bundledSkillDir();
}

/** User-level places ferris may have installed the skill: Claude Code, the shared ~/.agents (Codex, Cursor), Cursor. */
export function installedSkillDirs() {
  const cursor = process.env.CURSOR_HOME ? path.resolve(expandHome(process.env.CURSOR_HOME)) : path.join(os.homedir(), ".cursor");
  return [userSkillDir(), path.join(os.homedir(), ".agents", "skills", SKILL_NAME), path.join(cursor, "skills", SKILL_NAME)];
}

/** Path with the home folder shown as ~, for display. */
export function tilde(p) {
  const h = os.homedir();
  return p === h || p.startsWith(h + path.sep) ? `~${p.slice(h.length)}` : p;
}

export function slugify(name) {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function looksLikePath(arg) {
  return arg.includes("/") || arg.includes(path.sep) || arg.startsWith(".") || arg.startsWith("~");
}

/**
 * A workspace argument: a path, or a project slug under the state home.
 * With mustExist=false (setup), a bare slug that matches nothing names a new workspace under the state home.
 */
export function resolveWs(arg, { mustExist = true } = {}) {
  if (!arg) throw new CliError("a workspace path or project slug is required");
  const asPath = path.resolve(expandHome(arg));
  if (looksLikePath(arg) || fs.existsSync(asPath)) {
    if (mustExist && !fs.existsSync(path.join(asPath, "config.json"))) {
      throw new CliError(`no workspace at ${asPath} (config.json missing)`);
    }
    return asPath;
  }
  const bySlug = path.join(projectsDir(), arg);
  if (fs.existsSync(path.join(bySlug, "config.json")) || !mustExist) return bySlug;
  throw new CliError(`no workspace named "${arg}": not a folder here and not in ${tilde(projectsDir())} (see: ferris state list)`);
}

/** Nearest folder at or above p that holds .git, or null. Walks up from the nearest existing ancestor. */
export function gitRootOf(p) {
  let d = path.resolve(p);
  while (!fs.existsSync(d)) d = path.dirname(d);
  for (;;) {
    if (fs.existsSync(path.join(d, ".git"))) return d;
    const up = path.dirname(d);
    if (up === d) return null;
    d = up;
  }
}

export function syncedFolderHint(p) {
  const s = path.resolve(p);
  for (const marker of ["/Dropbox/", "/Mobile Documents/", "/Google Drive/", "/GoogleDrive", "/OneDrive", "/Box/"]) {
    if ((s + "/").includes(marker)) return marker.replace(/\//g, "");
  }
  return null;
}
