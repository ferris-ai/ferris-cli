import { spawn, spawnSync } from "node:child_process";
import path from "node:path";
import { CliError, scriptsSkillDir, stateHome } from "./paths.js";

let found;

/** The scripts find the same state home as the CLI: it is passed down explicitly. */
const scriptEnv = () => ({ ...process.env, PROJECT_STATE_HOME: stateHome() });

/** First of $FERRIS_PYTHON, python3, python that is Python 3.9+. */
export function findPython({ required = true } = {}) {
  if (found) return found;
  const candidates = process.env.FERRIS_PYTHON ? [process.env.FERRIS_PYTHON] : ["python3", "python"];
  for (const cmd of candidates) {
    const r = spawnSync(cmd, ["-c", "import sys; print('%d.%d.%d' % sys.version_info[:3])"], { encoding: "utf8" });
    if (r.status !== 0 || !r.stdout) continue;
    const version = r.stdout.trim();
    const [major, minor] = version.split(".").map(Number);
    if (major === 3 && minor >= 9) return (found = { cmd, version });
  }
  if (!required) return null;
  throw new CliError(
    "Python 3.9 or newer is required to run the state engine and was not found as python3 or python. " +
      "Install it, or point FERRIS_PYTHON at an interpreter.",
  );
}

/**
 * Run one of the skill's scripts. Output goes straight to the terminal unless capture is set.
 * Returns the exit status (or {status, stdout, stderr} with capture).
 */
export function runScript(script, args, { capture = false, skillDir } = {}) {
  const py = findPython();
  const file = path.join(skillDir ?? scriptsSkillDir(), "scripts", script);
  const r = spawnSync(py.cmd, [file, ...args], {
    stdio: capture ? ["ignore", "pipe", "pipe"] : "inherit",
    encoding: "utf8",
    env: scriptEnv(),
  });
  if (r.error) throw r.error;
  const status = r.status ?? 1;
  return capture ? { status, stdout: r.stdout, stderr: r.stderr } : status;
}

/** Async, captured run of any command (so a spinner can animate meanwhile). Resolves {status, stdout, stderr}. */
export function runAsync(cmd, args) {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let child;
    try {
      child = spawn(cmd, args, { stdio: ["ignore", "pipe", "pipe"], env: scriptEnv() });
    } catch (e) {
      resolve({ status: 127, stdout, stderr: String(e.message) });
      return;
    }
    child.stdout.on("data", (d) => (stdout += d));
    child.stderr.on("data", (d) => (stderr += d));
    child.on("error", (e) => resolve({ status: 127, stdout, stderr: String(e.message) }));
    child.on("close", (status) => resolve({ status: status ?? 1, stdout, stderr }));
  });
}

/** Async run of one of the skill's scripts, output captured. */
export function runScriptAsync(script, args, { skillDir } = {}) {
  const py = findPython();
  return runAsync(py.cmd, [path.join(skillDir ?? scriptsSkillDir(), "scripts", script), ...args]);
}

/** Claude Code's version, or null when the `claude` command is not on PATH. */
export async function claudeCodeVersion() {
  const r = await runAsync("claude", ["--version"]);
  if (r.status !== 0) return null;
  return (r.stdout.match(/\d+\.\d+(\.\d+)?/) ?? [r.stdout.trim()])[0] || null;
}
