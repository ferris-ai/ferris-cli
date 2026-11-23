import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { REWRITES, transformText } from "../src/skill.js";

const CLI = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const BIN = path.join(CLI, "bin", "ferris.js");
const SKILL = path.join(CLI, "skill", "project-state");
const EXAMPLE = path.join(SKILL, "assets", "example");

function sandbox() {
  // Outside any git repository, like a real workspace.
  const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "ferris-test-")));
  const env = {
    ...process.env,
    HOME: root,
    CLAUDE_CONFIG_DIR: path.join(root, "claude"),
    PROJECT_STATE_HOME: path.join(root, "ps"),
    CURSOR_HOME: path.join(root, "cursor"),
    CODEX_HOME: path.join(root, "codex"),
  };
  delete env.FERRIS_SKILL_DIR;
  const run = (...args) => spawnSync(process.execPath, [BIN, ...args], { env, encoding: "utf8", stdin: "ignore" });
  return { root, env, run };
}

function mdFiles(dir) {
  return fs.readdirSync(dir, { recursive: true }).filter((f) => f.endsWith(".md")).map((f) => path.join(dir, f));
}

test("every rewrite still matches the skill's docs", () => {
  const all = mdFiles(SKILL).map((f) => fs.readFileSync(f, "utf8")).join("\n");
  for (const [from] of REWRITES) assert.ok(all.includes(from), `no longer in the skill docs: ${from}`);
});

test("install rewrites every python3 script call to ferris and stamps a marker", () => {
  const { env, run } = sandbox();
  const r = run("install", "skill");
  assert.equal(r.status, 0, r.stderr);
  const target = path.join(env.CLAUDE_CONFIG_DIR, "skills", "project-state");
  for (const f of mdFiles(target)) {
    const left = fs
      .readFileSync(f, "utf8")
      .split("\n")
      .filter((l) => /python3 (SKILL_DIR\/)?scripts\//.test(l) && !l.includes("If `ferris` is not found"));
    assert.deepEqual(left, [], `${f} still calls python3 directly`);
  }
  const skillMd = fs.readFileSync(path.join(target, "SKILL.md"), "utf8");
  assert.ok(skillMd.indexOf("## Installed with the ferris CLI") < skillMd.indexOf("## Which flow"));
  assert.ok(fs.existsSync(path.join(target, "scripts", "engine.py")));
  assert.equal(JSON.parse(fs.readFileSync(path.join(target, ".ferris.json"), "utf8")).package, "@ferris-ai/cli");
  // Re-installing over our own copy is an update, not an error.
  assert.equal(run("install", "skill").status, 0);
});

test("transform is idempotent", () => {
  const src = fs.readFileSync(path.join(SKILL, "SKILL.md"), "utf8");
  const once = transformText(src, "SKILL.md");
  assert.equal(transformText(once, "SKILL.md"), once);
});

test("install refuses a foreign skill folder unless --force, which keeps a backup", () => {
  const { env, run } = sandbox();
  const target = path.join(env.CLAUDE_CONFIG_DIR, "skills", "project-state");
  fs.mkdirSync(target, { recursive: true });
  fs.writeFileSync(path.join(target, "SKILL.md"), "hand-made\n");
  const r = run("install", "skill");
  assert.equal(r.status, 1);
  assert.match(r.stderr, /not installed by ferris/);
  assert.equal(run("install", "skill", "--force").status, 0);
  const backups = fs.readdirSync(path.dirname(target)).filter((n) => n.startsWith("project-state.bak-"));
  assert.equal(backups.length, 1);
  assert.equal(fs.readFileSync(path.join(path.dirname(target), backups[0], "SKILL.md"), "utf8"), "hand-made\n");
});

test("an error inside a spinner step still reaches stderr off a terminal", () => {
  // On a terminal the spinner's ✗ line is the only copy; scripts and agents read stderr.
  const { env, run } = sandbox();
  const target = path.join(env.CLAUDE_CONFIG_DIR, "skills", "project-state");
  fs.mkdirSync(target, { recursive: true });
  fs.writeFileSync(path.join(target, "SKILL.md"), "hand-made\n");
  const r = run("setup", "--yes", "--agent", "claude");
  assert.equal(r.status, 1);
  assert.equal(r.stderr.match(/^ferris: .*not installed by ferris/gm)?.length, 1, r.stderr);
});

test("install --dir puts the skill in the project's .claude folder; uninstall removes it", () => {
  const { root, run } = sandbox();
  const proj = path.join(root, "proj");
  fs.mkdirSync(proj);
  assert.equal(run("install", "skill", "--dir", proj).status, 0);
  assert.ok(fs.existsSync(path.join(proj, ".claude", "skills", "project-state", "SKILL.md")));
  assert.equal(run("uninstall", "skill", "--dir", proj).status, 0);
  assert.ok(!fs.existsSync(path.join(proj, ".claude", "skills", "project-state")));
});

test("new project-state writes the onboarding config", () => {
  const { root, env, run } = sandbox();
  const docs = path.join(root, "docs");
  fs.mkdirSync(docs);
  const r = run("new", "project-state", "Northwind Dispatch", "--alias", "Northwind", "--vendor", "Acme Build",
    "--client", "Northwind Freight", "--folder", docs, "--start", "2026-03-01", "-y");
  assert.equal(r.status, 0, r.stderr);
  const cfg = JSON.parse(fs.readFileSync(path.join(env.PROJECT_STATE_HOME, "projects", "northwind-dispatch", "config.json"), "utf8"));
  assert.deepEqual(cfg.project, { name: "Northwind Dispatch", slug: "northwind-dispatch", aliases: ["Northwind"] });
  assert.deepEqual(cfg.parties, { vendor: "Acme Build", client: "Northwind Freight" });
  assert.deepEqual(cfg.sources, [{ type: "folder", path: docs }]);
  assert.equal(cfg.start, "2026-03-01");
  assert.equal(cfg.created_by, "ferris new project-state");
  assert.equal(cfg.people, undefined);
  // A second run does not overwrite it.
  const again = run("new", "project-state", "Northwind Dispatch", "-y");
  assert.equal(again.status, 1);
  assert.match(again.stderr, /already exists/);
});

test("new project-state refuses a workspace inside a git repository", () => {
  const { root, run } = sandbox();
  fs.mkdirSync(path.join(root, "repo", ".git"), { recursive: true });
  const r = run("new", "project-state", "X", "--ws", path.join(root, "repo", "ws"), "-y");
  assert.equal(r.status, 1);
  assert.match(r.stderr, /inside a git repository/);
  assert.ok(!fs.existsSync(path.join(root, "repo", "ws")));
});

/** The agent's write reply for batch B: one added unit that quotes the first line of the batch's first document. */
function writeReply(ws, B, run, slug) {
  const units = fs.readFileSync(path.join(ws, "input", "units.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
  const ids = JSON.parse(fs.readFileSync(path.join(ws, "input", "batches.json"), "utf8")).find((b) => b.id === B).unit_ids;
  const u = units.find((x) => x.unit_id === ids[0]);
  const body = u.text.startsWith(u.filename + "\n") ? u.text.slice(u.filename.length + 1) : u.text;
  const quote = body.split("\n").find((l) => l.trim().length > 15).trim();
  const reply = { add: [{ content: `Fact from ${B}`, priority: "hard", type: "fact", scope: "project", source_type: "document",
    evidence: [{ unit_id: u.unit_id, quote }] }], supersede: [] };
  fs.mkdirSync(path.join(ws, "reply"), { recursive: true });
  fs.writeFileSync(path.join(ws, "reply", `${B}.json`), JSON.stringify(reply));
}

test("the engine loop through ferris state: stage, write, check, apply, fold, report", () => {
  const { env, run } = sandbox();
  let r = run("new", "project-state", "Example", "--units", path.join(EXAMPLE, "units.jsonl"),
    "--batches", path.join(EXAMPLE, "batches.json"), "-y");
  assert.equal(r.status, 0, r.stderr);
  const ws = path.join(env.PROJECT_STATE_HOME, "projects", "example");
  assert.equal(JSON.parse(fs.readFileSync(path.join(ws, "config.json"), "utf8")).profile, "delivery");
  r = run("state", "status", "example");
  assert.deepEqual(JSON.parse(r.stdout).pending.map((p) => p.batch), ["B1", "B2", "B3"]);
  for (const B of ["B1", "B2", "B3"]) {
    r = run("state", "stage", "example", B);
    assert.equal(r.status, 0, r.stdout + r.stderr);
    assert.match(fs.readFileSync(path.join(ws, "workdir", "write_prompt.md"), "utf8"), /vendor-client/);
    writeReply(ws, B, run, "example");
    assert.deepEqual(JSON.parse(run("state", "check", "example", B).stdout).problems, []);
    const applied = JSON.parse(run("state", "apply", "example", B).stdout);
    assert.equal(applied.accepted, 1, JSON.stringify(applied));
    assert.equal(applied.next, "fold");
    assert.equal(run("state", "fold", "example", B).status, 0);
    assert.ok(fs.existsSync(path.join(ws, "store", `${B}.json`)));
  }
  const history = fs.readFileSync(path.join(ws, "history.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
  assert.deepEqual(history.map((h) => [h.batch, h.profile]), [["B1", "delivery"], ["B2", "delivery"], ["B3", "delivery"]]);
  const list = JSON.parse(run("state", "list").stdout);
  assert.deepEqual(list.workspaces.map((w) => [w.slug, w.done, w.pending]), [["example", "B3", []]]);
  r = run("state", "report", "example", "--batch", "B3");
  assert.equal(r.status, 0, r.stderr + r.stdout);
  assert.ok(fs.existsSync(path.join(ws, "current", "brief.md")));
  assert.match(fs.readFileSync(path.join(ws, "current", "report.html"), "utf8"), /tryferris\.ai/, "delivery reports carry the footer link");
});

test("unknown workspaces and commands fail with a message", () => {
  const { run } = sandbox();
  let r = run("state", "status", "nope");
  assert.equal(r.status, 1);
  assert.match(r.stderr, /no workspace named "nope"/);
  r = run("frobnicate");
  assert.equal(r.status, 1);
  assert.match(r.stderr, /unknown command/);
});

// ---------------------------------------------------------------- profiles

function writeUnits(file, units) {
  fs.writeFileSync(file, units.map((u) => JSON.stringify({ doc_id: u.unit_id, provider: "email", filename: "trip", author: "Ana", ...u }) + "\n").join(""));
}

const TRIP = [{ unit_id: "t-1", event_time: "2026-01-12", text: "Trip to Pell booked. Ana's tier is Tier-2." }];

test("profiles: built-ins are listed; a custom one extends a built-in and its guidance reaches the prompt", () => {
  const { root, env, run } = sandbox();
  let r = run("profiles");
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /delivery/);
  assert.match(r.stdout, /general/);
  assert.doesNotMatch(r.stdout, /\x1b\[/, "piped output must stay plain");
  r = run("profiles", "new", "trips", "--from", "general");
  assert.equal(r.status, 0, r.stderr);
  const file = path.join(env.PROJECT_STATE_HOME, "profiles", "trips.json");
  const d = JSON.parse(fs.readFileSync(file, "utf8"));
  assert.equal(d.extends, "general");
  fs.writeFileSync(file, JSON.stringify({ ...d, guidance: "CUSTOM-PROFILE-MARKER: a trip is booked only on a booking confirmation." }));
  assert.equal(run("profiles", "new", "general").status, 1, "a built-in name cannot be reused");
  r = run("profiles", "show", "trips");
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /CUSTOM-PROFILE-MARKER/);
  assert.match(r.stdout, /project or body of work/, "slots it leaves out come from general");

  const units = path.join(root, "u.jsonl");
  writeUnits(units, TRIP);
  r = run("new", "project-state", "Pell Trip", "--profile", "trips", "--units", units, "-y");
  assert.equal(r.status, 0, r.stderr + r.stdout);
  const ws = path.join(env.PROJECT_STATE_HOME, "projects", "pell-trip");
  assert.equal(JSON.parse(fs.readFileSync(path.join(ws, "config.json"), "utf8")).profile, "trips");
  assert.equal(run("state", "stage", "pell-trip", "B1").status, 0);
  const prompt = fs.readFileSync(path.join(ws, "workdir", "write_prompt.md"), "utf8");
  assert.match(prompt, /CUSTOM-PROFILE-MARKER/);
  assert.doesNotMatch(prompt, /vendor-client/);
});

test("general profile: the whole loop runs through ferris state; project guidance reaches the prompt", () => {
  const { root, env, run } = sandbox();
  const units = path.join(root, "u.jsonl");
  writeUnits(units, TRIP);
  let r = run("new", "project-state", "Travel desk", "--profile", "general", "--units", units, "-y");
  assert.equal(r.status, 0, r.stderr + r.stdout);
  const ws = path.join(env.PROJECT_STATE_HOME, "projects", "travel-desk");
  assert.deepEqual(JSON.parse(run("state", "profile", "status", "travel-desk").stdout), { profile: "general", builtin: true, overlay: null, overlay_chars: 0 });
  const g = path.join(root, "guidance.md");
  fs.writeFileSync(g, "Trips are named by city. Count a trip as booked only on a confirmation email.\n");
  r = run("state", "profile", "overlay", "travel-desk", "--file", g);
  assert.equal(r.status, 0, r.stderr + r.stdout);
  assert.equal(fs.readFileSync(path.join(ws, "profile.md"), "utf8").trim(), fs.readFileSync(g, "utf8").trim());
  assert.equal(run("state", "stage", "travel-desk", "B1").status, 0);
  const prompt = fs.readFileSync(path.join(ws, "workdir", "write_prompt.md"), "utf8");
  assert.match(prompt, /## Project guidance:\nTrips are named by city/);
  assert.doesNotMatch(prompt, /vendor-client/);
  writeReply(ws, "B1", run, "travel-desk");
  assert.deepEqual(JSON.parse(run("state", "check", "travel-desk", "B1").stdout).problems, []);
  assert.equal(JSON.parse(run("state", "apply", "travel-desk", "B1").stdout).accepted, 1);
  assert.equal(run("state", "fold", "travel-desk", "B1").status, 0);
  const h = JSON.parse(fs.readFileSync(path.join(ws, "history.jsonl"), "utf8").trim());
  assert.equal(h.profile, "general");
  assert.match(h.overlay, /^[0-9a-f]{12}$/);
  r = run("state", "report", "travel-desk", "--batch", "B1");
  assert.equal(r.status, 0, r.stderr + r.stdout);
  assert.doesNotMatch(fs.readFileSync(path.join(ws, "current", "report.html"), "utf8"), /tryferris\.ai/, "no footer link outside delivery");
  assert.equal(run("watch", "context-skill", "travel-desk").status, 0);
  const skill = fs.readFileSync(path.join(env.CLAUDE_CONFIG_DIR, "skills", "travel-desk-state", "SKILL.md"), "utf8");
  assert.match(skill, /rules, facts, commitments/);
});

test("doctor flags a workspace made by the earlier event-log version of the skill", () => {
  const { env, run } = sandbox();
  const ws = path.join(env.PROJECT_STATE_HOME, "projects", "old");
  fs.mkdirSync(ws, { recursive: true });
  fs.writeFileSync(path.join(ws, "config.json"), "{}");
  fs.writeFileSync(path.join(ws, "events.jsonl"), "");
  assert.match(run("doctor").stdout, /old: made by the earlier event-log version/);
});

test("an unknown profile is refused before anything is written", () => {
  const { env, run } = sandbox();
  const r = run("new", "project-state", "X", "--profile", "nope", "-y");
  assert.equal(r.status, 1);
  assert.match(r.stderr, /unknown profile "nope"/);
  assert.ok(!fs.existsSync(path.join(env.PROJECT_STATE_HOME, "projects", "x")));
});

test("help and setup degrade to plain text off a terminal", () => {
  const { root, env, run } = sandbox();
  const help = run("--help");
  assert.equal(help.status, 0);
  assert.match(help.stdout, /ferris setup/);
  assert.doesNotMatch(help.stdout, /\x1b\[|███/);
  const setup = run("setup", "--yes");
  assert.equal(setup.status, 0, setup.stderr);
  assert.doesNotMatch(setup.stdout, /\x1b\[/);
  assert.match(setup.stdout, /skills\/project-state/);
  const anyInstalled = [path.join(env.CLAUDE_CONFIG_DIR, "skills"), path.join(root, ".agents", "skills"), path.join(root, "cursor", "skills")]
    .some((d) => fs.existsSync(path.join(d, "project-state", ".ferris.json")));
  assert.ok(anyInstalled, "setup --yes installs for at least one agent");
});

// ---------------------------------------------------------------- .ferris in the workspace

function workspaceSandbox() {
  const s = sandbox();
  delete s.env.PROJECT_STATE_HOME;
  const runIn = (cwd, ...args) => spawnSync(process.execPath, [BIN, ...args], { env: s.env, cwd, encoding: "utf8", stdin: "ignore" });
  return { ...s, runIn };
}

test(".ferris goes at the workspace root, ignores itself in git, and is found from any subfolder", (t) => {
  const git = spawnSync("git", ["--version"]);
  if (git.status !== 0) return t.skip("git not installed");
  const { root, env, runIn } = workspaceSandbox();
  const repo = path.join(root, "acme-repo");
  const deep = path.join(repo, "src", "deep");
  fs.mkdirSync(deep, { recursive: true });
  spawnSync("git", ["init", "-q", repo]);
  fs.writeFileSync(path.join(repo, "README.md"), "x\n");
  spawnSync("git", ["-C", repo, "add", "README.md"]);
  spawnSync("git", ["-C", repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"]);

  const r = runIn(path.join(repo, "src"), "new", "project-state", "Example", "--units", path.join(EXAMPLE, "units.jsonl"), "-y");
  assert.equal(r.status, 0, r.stderr + r.stdout);
  const home = path.join(repo, ".ferris");
  assert.ok(fs.existsSync(path.join(home, "projects", "example", "config.json")), "created at the repo root");
  assert.ok(!fs.existsSync(path.join(repo, "src", ".ferris")), "not in the subfolder");
  assert.match(fs.readFileSync(path.join(home, ".gitignore"), "utf8"), /^\*$/m);
  assert.equal(spawnSync("git", ["-C", repo, "status", "--porcelain"], { encoding: "utf8" }).stdout, "", "git sees nothing");
  assert.ok(!fs.existsSync(path.join(env.HOME, ".project-state")), "nothing in the home folder");

  const list = JSON.parse(runIn(deep, "state", "list").stdout);
  assert.equal(list.state_home, home);
  assert.deepEqual(list.workspaces.map((w) => w.slug), ["example"]);
  assert.equal(runIn(deep, "state", "status", "example").status, 0);

  assert.equal(runIn(deep, "watch", "register", "example").status, 0);
  assert.ok(fs.existsSync(path.join(home, "registry.json")), "the reminder registry lives in the workspace's .ferris");

  // Outside the workspace there is no project of that name.
  const elsewhere = path.join(root, "elsewhere");
  fs.mkdirSync(elsewhere);
  assert.equal(runIn(elsewhere, "state", "status", "example").status, 1);
});

test("the engine finds a workspace's custom profile from the workspace path alone", () => {
  const { root, runIn } = workspaceSandbox();
  const proj = path.join(root, "proj");
  fs.mkdirSync(proj);
  assert.equal(runIn(proj, "profiles", "new", "trips").status, 0);
  const units = path.join(root, "u.jsonl");
  writeUnits(units, [{ unit_id: "t-1", event_time: "2026-01-12", text: "Trip to Pell booked." }]);
  assert.equal(runIn(proj, "new", "project-state", "Pell", "--profile", "trips", "--units", units, "-y").status, 0);
  // Run the script directly from an unrelated folder, without PROJECT_STATE_HOME: the profile resolves via the workspace.
  const r = spawnSync("python3", [path.join(SKILL, "scripts", "engine.py"), "stage", path.join(proj, ".ferris", "projects", "pell"), "B1"],
    { cwd: root, encoding: "utf8", env: { ...process.env, PROJECT_STATE_HOME: "" } });
  assert.equal(r.status, 0, r.stderr + r.stdout);
});

// ---------------------------------------------------------------- Claude Code, Codex, Cursor

const marker = (d) => fs.existsSync(path.join(d, "project-state", ".ferris.json"));

test("install skill puts one copy where each agent reads it", () => {
  const { root, env, run } = sandbox();
  const claude = path.join(env.CLAUDE_CONFIG_DIR, "skills");
  const shared = path.join(root, ".agents", "skills");
  const cursor = path.join(env.CURSOR_HOME, "skills");

  let r = run("install", "skill", "--agent", "claude", "--agent", "codex", "--agent", "cursor");
  assert.equal(r.status, 0, r.stderr);
  assert.ok(marker(claude) && marker(shared), "Claude Code and the shared ~/.agents folder (Codex, Cursor)");
  assert.ok(!marker(cursor), "no third copy: Cursor reads ~/.claude/skills and ~/.agents/skills");
  const fm = fs.readFileSync(path.join(shared, "project-state", "SKILL.md"), "utf8").split("---")[1];
  assert.match(fm, /^name: project-state$/m, "Codex and Cursor need name + description; Cursor needs name = folder");
  assert.match(fm, /^description: /m);

  r = run("uninstall", "skill");
  assert.equal(r.status, 0, r.stderr);
  assert.ok(!marker(claude) && !marker(shared));

  r = run("install", "skill", "--agent", "cursor");
  assert.equal(r.status, 0, r.stderr);
  assert.ok(marker(cursor) && !marker(claude) && !marker(shared), "Cursor alone gets ~/.cursor/skills");
  assert.equal(run("install", "skill", "--agent", "vim").status, 1);
});

function hookCmds(file, key) {
  const s = JSON.parse(fs.readFileSync(file, "utf8"));
  return (s.hooks?.[key] ?? []).flatMap((e) => (e.hooks ? e.hooks.map((h) => h.command) : [e.command]));
}

test("reminder hooks are written in each agent's own format and removed cleanly", () => {
  const { env, run, root } = sandbox();
  // PROJECT_STATE_HOME is root/ps, so the workspace is root: Claude Code and Cursor get workspace files, Codex the user file
  const claudeFile = path.join(root, ".claude", "settings.local.json");
  const codexFile = path.join(env.CODEX_HOME, "hooks.json");
  const cursorFile = path.join(root, ".cursor", "hooks.json");
  fs.mkdirSync(path.dirname(cursorFile), { recursive: true });
  fs.writeFileSync(cursorFile, JSON.stringify({ version: 1, hooks: { sessionStart: [{ command: "./mine.sh" }] } }));

  let r = run("watch", "install-hook", "--agent", "claude", "--agent", "codex", "--agent", "cursor");
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stdout, /trust it once with \/hooks/);
  assert.equal(hookCmds(claudeFile, "SessionStart").filter((c) => /watch\.py'? check$/.test(c)).length, 1);
  const claude = JSON.parse(fs.readFileSync(claudeFile, "utf8"));
  assert.equal(claude.hooks.SessionStart[0].matcher, "startup|resume|compact");
  assert.match(hookCmds(claudeFile, "FileChanged")[0], /watch\.py'? file-changed$/);
  assert.equal(claude.hooks.FileChanged[0].matcher, undefined, "no matcher: runs for every watched folder");
  assert.match(hookCmds(claudeFile, "UserPromptSubmit")[0], /watch\.py'? prompt$/);
  assert.match(hookCmds(claudeFile, "PreCompact")[0], /watch\.py'? precompact$/);
  const codex = JSON.parse(fs.readFileSync(codexFile, "utf8"));
  assert.equal(codex.hooks.SessionStart[0].hooks[0].type, "command");
  assert.match(codex.hooks.SessionStart[0].hooks[0].command, /watch\.py'? check --format codex$/);
  const cur = JSON.parse(fs.readFileSync(cursorFile, "utf8"));
  assert.equal(cur.version, 1);
  assert.deepEqual(cur.hooks.sessionStart.map((h) => h.command.endsWith("--format cursor") || h.command), ["./mine.sh", true]);

  // idempotent, and doctor sees them
  run("watch", "install-hook", "--agent", "cursor");
  assert.equal(hookCmds(cursorFile, "sessionStart").length, 2);

  r = run("watch", "uninstall-hook", "--agent", "cursor", "--agent", "codex", "--agent", "claude");
  assert.equal(r.status, 0, r.stderr);
  assert.deepEqual(hookCmds(cursorFile, "sessionStart"), ["./mine.sh"], "the user's own hook stays");
  assert.deepEqual(hookCmds(codexFile, "SessionStart"), []);
  assert.ok(!fs.existsSync(path.join(env.CLAUDE_CONFIG_DIR, "settings.json")), "the user's own settings are never touched");
});

test("hooks go in the workspace's own settings, kept out of git; --global uses the user's", (t) => {
  if (spawnSync("git", ["--version"]).status !== 0) return t.skip("git not installed");
  const { root, env, runIn } = workspaceSandbox();
  const repo = path.join(root, "repo");
  fs.mkdirSync(repo);
  spawnSync("git", ["init", "-q", repo]);
  assert.equal(runIn(repo, "new", "project-state", "Acme", "-y").status, 0);
  const r = runIn(repo, "watch", "install-hook", "--agent", "claude", "--agent", "cursor");
  assert.equal(r.status, 0, r.stderr);
  assert.ok(fs.existsSync(path.join(repo, ".claude", "settings.local.json")));
  assert.ok(fs.existsSync(path.join(repo, ".cursor", "hooks.json")));
  assert.equal(spawnSync("git", ["-C", repo, "status", "--porcelain"], { encoding: "utf8" }).stdout, "", "nothing for git to commit");
  assert.ok(!fs.existsSync(path.join(env.CLAUDE_CONFIG_DIR, "settings.json")));
  // installing again changes nothing and does not add the exclude line twice
  runIn(repo, "watch", "install-hook", "--agent", "claude");
  const excl = fs.readFileSync(path.join(repo, ".git", "info", "exclude"), "utf8");
  assert.equal(excl.split("\n").filter((l) => l === "/.claude/settings.local.json").length, 1);
  // doctor and info see the workspace hook from inside the workspace only
  assert.equal(runIn(repo, "install", "skill", "--agent", "claude").status, 0);
  assert.match(runIn(repo, "doctor").stdout, /reminders on \(this workspace\)/);
  assert.match(runIn(root, "doctor").stdout, /reminders off here/, "another folder does not get them");
  assert.deepEqual(JSON.parse(runIn(repo, "watch", "info", "acme").stdout).hooks_where, { claude: ["workspace"], cursor: ["workspace"] });
  // --global writes the user's file; uninstall clears both
  assert.equal(runIn(repo, "watch", "install-hook", "--agent", "claude", "--global").status, 0);
  assert.equal(hookCmds(path.join(env.CLAUDE_CONFIG_DIR, "settings.json"), "SessionStart").length, 1);
  assert.equal(runIn(repo, "watch", "uninstall-hook", "--agent", "claude", "--agent", "cursor").status, 0);
  assert.deepEqual(hookCmds(path.join(repo, ".claude", "settings.local.json"), "SessionStart"), []);
  assert.deepEqual(hookCmds(path.join(env.CLAUDE_CONFIG_DIR, "settings.json"), "SessionStart"), []);
});

test("the reminder check finds the workspace from the agent's project folder and answers in its format", () => {
  const { root, env, runIn } = workspaceSandbox();
  const proj = path.join(root, "proj");
  const inbox = path.join(root, "inbox");
  fs.mkdirSync(proj);
  fs.mkdirSync(inbox);
  fs.writeFileSync(path.join(inbox, "kickoff.md"), "notes\n");
  assert.equal(runIn(proj, "new", "project-state", "Acme", "--folder", inbox, "-y").status, 0);
  const ws = path.join(proj, ".ferris", "projects", "acme");
  const cfg = JSON.parse(fs.readFileSync(path.join(ws, "config.json"), "utf8"));
  fs.writeFileSync(path.join(ws, "config.json"), JSON.stringify({ ...cfg, cadence: "on_new" }));
  assert.equal(runIn(proj, "watch", "register", "acme").status, 0);
  assert.equal(runIn(proj, "watch", "install-hook", "--agent", "cursor", "--agent", "claude").status, 0);

  const watch = path.join(SKILL, "scripts", "watch.py");
  const hookEnv = { ...env, PROJECT_STATE_HOME: "" };
  fs.mkdirSync(env.CURSOR_HOME, { recursive: true }); // Cursor runs user hooks from there
  // Cursor runs user hooks from ~/.cursor and names the project in CURSOR_PROJECT_DIR
  const asCursor = spawnSync("python3", [watch, "check", "--format", "cursor"],
    { cwd: env.CURSOR_HOME, encoding: "utf8", env: { ...hookEnv, CURSOR_PROJECT_DIR: proj } });
  assert.match(JSON.parse(asCursor.stdout).additional_context, /\[project-state\] Acme/);
  // The Claude Code hook that Cursor also imports stays quiet there, so the reminder shows once
  const imported = spawnSync("python3", [watch, "check"], { cwd: env.CURSOR_HOME, encoding: "utf8", env: { ...hookEnv, CURSOR_PROJECT_DIR: proj } });
  assert.equal(imported.stdout, "");
  // Claude Code and Codex run it in the session folder
  const asCodex = spawnSync("python3", [watch, "check"], { cwd: proj, encoding: "utf8", env: hookEnv });
  assert.match(JSON.parse(asCodex.stdout).hookSpecificOutput.additionalContext, /\[project-state\] Acme/);
  // Another workspace: nothing to say
  const other = spawnSync("python3", [watch, "check"], { cwd: root, encoding: "utf8", env: hookEnv });
  assert.equal(other.stdout, "");
});

test("watch info reports what is already on, so the skill offers only what is off", () => {
  const { env, root, run } = sandbox();
  assert.equal(run("new", "project-state", "Info Test", "--folder", root, "-y").status, 0);
  const info = () => {
    const r = run("watch", "info", "info-test");
    assert.equal(r.status, 0, r.stderr);
    return JSON.parse(r.stdout);
  };
  let i = info();
  assert.equal(i.reminder, false);
  assert.equal(i.registered, false);
  assert.equal(i.context_skill, false);
  assert.equal(i.has_source_folder, true);
  // A hook from another copy of the scripts (a hand-copied skill, an older install) still counts as on.
  fs.mkdirSync(env.CLAUDE_CONFIG_DIR, { recursive: true });
  fs.writeFileSync(
    path.join(env.CLAUDE_CONFIG_DIR, "settings.json"),
    JSON.stringify({ hooks: { SessionStart: [{ hooks: [{ type: "command", command: "python3 /elsewhere/project-state/scripts/watch.py check" }] }] } }),
  );
  assert.equal(info().hooks.claude, true);
  assert.equal(info().reminder, false, "a hook without the project registered is not a reminder for it");
  assert.equal(run("watch", "register", "info-test").status, 0);
  i = info();
  assert.equal(i.reminder, true);
  assert.deepEqual(i.hooks, { claude: true, codex: false, cursor: false });
});


test("install-hook upgrades an older reminder entry and keeps the user's own hooks", () => {
  const { env, run } = sandbox();
  const claudeFile = path.join(env.CLAUDE_CONFIG_DIR, "settings.json");
  const watch = path.join(SKILL, "scripts", "watch.py");
  fs.mkdirSync(env.CLAUDE_CONFIG_DIR, { recursive: true });
  fs.writeFileSync(claudeFile, JSON.stringify({
    model: "opus",
    hooks: {
      SessionStart: [{ matcher: "startup", hooks: [{ type: "command", command: `python3 ${watch} check` }] }],
      UserPromptSubmit: [{ hooks: [{ type: "command", command: "./mine.sh" }] }],
    },
  }));
  assert.equal(run("watch", "install-hook", "--agent", "claude", "--global").status, 0);
  const s = JSON.parse(fs.readFileSync(claudeFile, "utf8"));
  assert.equal(s.model, "opus");
  assert.equal(s.hooks.SessionStart.length, 1, "the old entry is replaced, not duplicated");
  assert.equal(s.hooks.SessionStart[0].matcher, "startup|resume|compact");
  assert.deepEqual(hookCmds(claudeFile, "UserPromptSubmit").map((c) => c.endsWith(" prompt") || c), ["./mine.sh", true]);
  assert.match(run("watch", "install-hook", "--agent", "claude", "--global").stdout, /no change/);
  assert.equal(run("watch", "uninstall-hook", "--agent", "claude").status, 0);
  const after = JSON.parse(fs.readFileSync(claudeFile, "utf8"));
  assert.deepEqual(after.hooks, { UserPromptSubmit: [{ hooks: [{ type: "command", command: "./mine.sh" }] }] });
});

test("Claude Code: watched source folders, documents that land mid-session, compaction and the weekly tip", () => {
  const { root, env, runIn } = workspaceSandbox();
  const proj = path.join(root, "proj");
  const inbox = path.join(root, "inbox");
  fs.mkdirSync(proj);
  fs.mkdirSync(inbox);
  fs.writeFileSync(path.join(inbox, "kickoff.md"), "notes\n");
  assert.equal(runIn(proj, "new", "project-state", "Acme", "--folder", inbox, "-y").status, 0);
  const ws = path.join(proj, ".ferris", "projects", "acme");
  assert.equal(runIn(proj, "watch", "register", "acme").status, 0);
  assert.equal(runIn(proj, "watch", "mark", "acme", "--force", "*").status, 0); // as after the first update (no units in this test)
  const watch = path.join(SKILL, "scripts", "watch.py");
  const hookEnv = { ...env, PROJECT_STATE_HOME: "", CLAUDE_PROJECT_DIR: proj };
  delete hookEnv.CURSOR_PROJECT_DIR;
  const hook = (sub, input, extra = []) =>
    spawnSync("python3", [watch, sub, ...extra], { cwd: root, encoding: "utf8", env: hookEnv, input: JSON.stringify(input) });
  const sid = "sess-1";

  // Session start: the source folder is watched; no tip there (the desktop app, Codex and Cursor would not show it)
  let r = hook("check", { session_id: sid, source: "startup" }, ["--today", "2026-11-12"]);
  let out = JSON.parse(r.stdout);
  assert.deepEqual(out.hookSpecificOutput.watchPaths, [inbox]);
  assert.equal(out.systemMessage, undefined);
  // The tip: the agent asks for it after an update; a delivery project that has been updated gets it at most weekly
  const tip = (today) => runIn(proj, "watch", "tip", "acme", "--today", today).stdout.trim();
  assert.match(tip("2026-11-12"), /^Ferris tip: .*https:\/\/tryferris\.ai/);
  assert.equal(tip("2026-11-15"), "", "not again within a week");
  assert.match(tip("2026-11-19"), /tryferris\.ai/);
  assert.equal(runIn(proj, "watch", "tips", "off").status, 0);
  assert.equal(tip("2026-12-30"), "", "tips off");
  assert.equal(runIn(proj, "watch", "tips", "on").status, 0);
  const cfgFile = path.join(ws, "config.json");
  fs.writeFileSync(cfgFile, JSON.stringify({ ...JSON.parse(fs.readFileSync(cfgFile, "utf8")), profile: "general" }));
  assert.equal(tip("2027-02-01"), "", "delivery projects only");
  // Resume: watch again, no reminder or tip
  out = JSON.parse(hook("check", { session_id: sid, source: "resume" }).stdout);
  assert.deepEqual(Object.keys(out.hookSpecificOutput).sort(), ["hookEventName", "watchPaths"]);

  // Nothing landed yet: the prompt hook says nothing
  assert.equal(hook("prompt", { session_id: sid, prompt: "hi" }).stdout, "");
  // A document lands; hidden files and other folders are ignored
  fs.writeFileSync(path.join(inbox, "minutes.md"), "x\n");
  fs.writeFileSync(path.join(inbox, ".DS_Store"), "x");
  for (const f of [path.join(inbox, "minutes.md"), path.join(inbox, ".DS_Store"), path.join(root, "elsewhere.md")]) {
    assert.equal(hook("file-changed", { session_id: sid, file_path: f, event: "add" }).status, 0);
  }
  r = hook("prompt", { session_id: sid, prompt: "what's next?" });
  const ctx = JSON.parse(r.stdout).hookSpecificOutput;
  assert.equal(ctx.hookEventName, "UserPromptSubmit");
  assert.match(ctx.additionalContext, /1 new or changed document arrived in the source folder of Acme .*: minutes\.md\./);
  assert.equal(hook("prompt", { session_id: sid, prompt: "again" }).stdout, "", "said once");
  // Another session does not see this session's documents; one already recorded by an update is not reported
  fs.writeFileSync(path.join(inbox, "later.md"), "y\n");
  hook("file-changed", { session_id: sid, file_path: path.join(inbox, "later.md"), event: "add" });
  assert.equal(hook("prompt", { session_id: "sess-2", prompt: "hi" }).stdout, "");
  assert.equal(runIn(proj, "watch", "mark", "acme", "--force", "*").status, 0);
  assert.equal(hook("prompt", { session_id: sid, prompt: "hi" }).stdout, "");

  // Compaction: the summary keeps the place, and Claude re-reads the state after it
  r = hook("precompact", { session_id: sid, trigger: "auto" });
  assert.match(r.stdout, /Acme \(.*acme\)/);
  assert.match(r.stdout, /engine\.py'? status <WS>/);
  out = JSON.parse(hook("check", { session_id: sid, source: "compact" }).stdout);
  assert.match(out.hookSpecificOutput.additionalContext, /just compacted.*re-read its state/);
  // A workspace with no projects: every hook stays silent
  const bare = { ...hookEnv, CLAUDE_PROJECT_DIR: root };
  for (const sub of ["check", "prompt", "precompact"]) {
    const q = spawnSync("python3", [watch, sub], { cwd: root, encoding: "utf8", env: bare, input: JSON.stringify({ session_id: sid, source: "startup" }) });
    assert.equal(q.stdout, "", sub);
  }
});

test("an update records a new file as seen only once its units are in the workspace", () => {
  const { root, runIn } = workspaceSandbox();
  const proj = path.join(root, "proj");
  const inbox = path.join(root, "inbox");
  fs.mkdirSync(proj);
  fs.mkdirSync(inbox);
  fs.writeFileSync(path.join(inbox, "kickoff-2026-11-02.md"), "Date: 2026-11-02\nAuthor: Dana Reyes <dana@acme.test>\n\nKickoff: handover on 2026-03-20.\n");
  assert.equal(runIn(proj, "new", "project-state", "Acme", "--folder", inbox, "-y").status, 0);
  const ws = path.join(proj, ".ferris", "projects", "acme");
  const manifest = () => Object.keys(JSON.parse(fs.readFileSync(path.join(ws, "sources.manifest.json"), "utf8"))).map((p) => path.basename(p));
  const cands = path.join(ws, "scratch", "c.jsonl");
  fs.mkdirSync(path.join(ws, "scratch"), { recursive: true });
  const update = () => {
    assert.equal(runIn(proj, "state", "ingest", "acme", "--out", cands).status, 0);
    assert.equal(runIn(proj, "state", "setup", cands, "acme", "--today", "2026-11-30").status, 0);
  };
  update();
  let r = runIn(proj, "watch", "mark", "acme");
  assert.equal(r.status, 0);
  assert.deepEqual(manifest(), ["kickoff-2026-11-02.md"]);
  assert.doesNotMatch(r.stdout, /not marked/);

  // A document arrives; the update reads it but skips adding it: mark must not hide it from the next update
  fs.writeFileSync(path.join(inbox, "status-2026-11-09.md"), "Date: 2026-11-09\nAuthor: Dana Reyes <dana@acme.test>\n\nHandover moves to 2026-03-27.\n");
  assert.equal(runIn(proj, "state", "ingest", "acme", "--out", cands).status, 0);
  r = runIn(proj, "watch", "mark", "acme");
  assert.equal(r.status, 0);
  assert.match(r.stdout, /not marked, no units in the workspace yet \(1\): status-2026-11-09\.md/);
  assert.deepEqual(manifest(), ["kickoff-2026-11-02.md"]);
  // The next update still sees it; once it is added, mark records it
  update();
  assert.match(fs.readFileSync(path.join(ws, "input", "units.jsonl"), "utf8"), /2026-03-27/);
  r = runIn(proj, "watch", "mark", "acme");
  assert.doesNotMatch(r.stdout, /not marked/);
  assert.deepEqual(manifest().sort(), ["kickoff-2026-11-02.md", "status-2026-11-09.md"]);

  // A file whose units were all dropped on purpose is recorded by name with --force; another one stays held
  fs.writeFileSync(path.join(inbox, "out-of-office-2026-11-10.md"), "Date: 2026-11-10\n\nI'm away until Monday.\n");
  fs.writeFileSync(path.join(inbox, "minutes-2026-11-11.md"), "Date: 2026-11-11\n\nHandover confirmed.\n");
  assert.match(runIn(proj, "watch", "mark", "acme").stdout, /not marked.*minutes-2026-11-11\.md; out-of-office-2026-11-10\.md/);
  r = runIn(proj, "watch", "mark", "acme", "--force", "out-of-office-2026-11-10.md");
  assert.match(r.stdout, /not marked.*\(1\): minutes-2026-11-11\.md/);
  assert.ok(manifest().includes("out-of-office-2026-11-10.md"));
  assert.ok(!manifest().includes("minutes-2026-11-11.md"), "--force FILE records only the files it names");
  // Two new files with the same name in different folders: a bare name is refused, a path picks one
  fs.mkdirSync(path.join(inbox, "a"));
  fs.mkdirSync(path.join(inbox, "b"));
  fs.writeFileSync(path.join(inbox, "a", "notes.txt"), "");
  fs.writeFileSync(path.join(inbox, "b", "notes.txt"), "");
  r = runIn(proj, "watch", "mark", "acme");
  assert.match(r.stdout, /a\/notes\.txt; b\/notes\.txt/, "held files are listed by their path in the source folder");
  r = runIn(proj, "watch", "mark", "acme", "--force", "notes.txt");
  assert.equal(r.status, 1);
  assert.match(r.stderr, /2 new files match notes\.txt.*a\/notes\.txt; b\/notes\.txt/);
  r = runIn(proj, "watch", "mark", "acme", "--force", "a/notes.txt");
  assert.equal(manifest().filter((n) => n === "notes.txt").length, 1, "only a/notes.txt recorded");
  assert.match(r.stdout, /b\/notes\.txt/);
  // A name that matches no held file is refused, and nothing is recorded
  r = runIn(proj, "watch", "mark", "acme", "--force", "nothing-like-this.md");
  assert.equal(r.status, 1);
  assert.match(r.stderr, /no new file without units matches nothing-like-this\.md/);
  // A readable file with no text is listed by the reader as skipped
  const empty = JSON.parse(runIn(proj, "state", "ingest", "acme", "--out", cands).stdout);
  assert.ok(empty.skipped.some((x) => x.path.endsWith(path.join("b", "notes.txt")) && x.reason === "no text"), JSON.stringify(empty.skipped));
  // A name already recorded (or of a type mark records itself) is noted, not refused; * records everything
  r = runIn(proj, "watch", "mark", "acme", "--force", "kickoff-2026-11-02.md", "b/notes.txt");
  assert.equal(r.status, 0, r.stderr);
  assert.match(r.stderr, /kickoff-2026-11-02\.md is already recorded/);
  assert.equal(manifest().filter((n) => n === "notes.txt").length, 2, "b/notes.txt recorded in the same call");
  fs.writeFileSync(path.join(inbox, "late-2026-11-12.md"), "Date: 2026-11-12\n\nLate.\n");
  r = runIn(proj, "watch", "mark", "acme", "--force", "*", "anything.md");
  assert.equal(r.status, 0, r.stderr);
  assert.ok(manifest().includes("late-2026-11-12.md"), "* records every held file");
  // A type the reader never turns into units (an image) is recorded as before, not held forever
  fs.writeFileSync(path.join(inbox, "diagram.png"), "png");
  assert.doesNotMatch(runIn(proj, "watch", "mark", "acme").stdout, /diagram\.png/);
  assert.ok(manifest().includes("diagram.png"));
});

test("an Author header keeps the whole name", () => {
  const { root, runIn } = workspaceSandbox();
  const proj = path.join(root, "proj");
  const inbox = path.join(root, "inbox");
  fs.mkdirSync(proj);
  fs.mkdirSync(inbox);
  fs.writeFileSync(path.join(inbox, "design.md"), "---\nDate: 2026-10-22\nAuthor: Marcus Chen\n---\n# Architecture\n\nSync is last-write-wins.\n");
  fs.writeFileSync(path.join(inbox, "plan.md"), "Date: 2026-10-23\nAuthor: Diego Alvarez <diego@acme.test>\n\nAPI on staging.\n");
  assert.equal(runIn(proj, "new", "project-state", "Acme", "--folder", inbox, "-y").status, 0);
  const out = path.join(root, "c.jsonl");
  assert.equal(runIn(proj, "state", "ingest", "acme", "--out", out, "--all").status, 0);
  const authors = fs.readFileSync(out, "utf8").trim().split("\n").map((l) => JSON.parse(l).author).sort();
  assert.deepEqual(authors, ["Diego Alvarez", "Marcus Chen"]);
});

test("before the first update, the reminder offers to start tracking, not to update", () => {
  const { root, env, runIn } = workspaceSandbox();
  const proj = path.join(root, "proj");
  const inbox = path.join(root, "inbox");
  fs.mkdirSync(proj);
  fs.mkdirSync(inbox);
  fs.writeFileSync(path.join(inbox, "kickoff-2026-11-02.md"), "Date: 2026-11-02\n\nKickoff.\n");
  fs.writeFileSync(path.join(inbox, "notes-2026-11-03.md"), "Date: 2026-11-03\n\nNotes.\n");
  assert.equal(runIn(proj, "new", "project-state", "Acme", "--folder", inbox, "-y").status, 0);
  assert.equal(runIn(proj, "watch", "register", "acme").status, 0);
  const watch = path.join(SKILL, "scripts", "watch.py");
  const hookEnv = { ...env, PROJECT_STATE_HOME: "", CLAUDE_PROJECT_DIR: proj };
  delete hookEnv.CURSOR_PROJECT_DIR;
  const check = () => {
    const r = spawnSync("python3", [watch, "check", "--today", "2026-11-30"], { cwd: root, encoding: "utf8", env: hookEnv, input: JSON.stringify({ session_id: "s", source: "startup" }) });
    return JSON.parse(r.stdout).hookSpecificOutput.additionalContext;
  };
  let ctx = check();
  assert.match(ctx, /Acme .*: not tracked yet, 2 documents waiting in its source folder\. Ask the user whether to start tracking it now/);
  assert.doesNotMatch(ctx, /update it now|since/);
  // a workspace with a processed batch is not "not tracked yet", even before its first mark
  const ws = path.join(proj, ".ferris", "projects", "acme");
  fs.mkdirSync(path.join(ws, "store"), { recursive: true });
  fs.writeFileSync(path.join(ws, "store", "B1.json"), "{}");
  assert.match(check(), /2 new files \(weekly\)\. Ask the user whether to update it now/);
  // after the first update it is an ordinary update reminder
  assert.equal(runIn(proj, "watch", "mark", "acme", "--force", "*").status, 0);
  fs.writeFileSync(path.join(inbox, "status-2026-11-20.md"), "Date: 2026-11-20\n\nStatus.\n");
  ctx = check();
  assert.match(ctx, /1 new file since \d{4}-\d{2}-\d{2} \(weekly\)\. Ask the user whether to update it now/);
});

test("report: summary links and escaping, word-level changes, one arrow per link, loops stay narrow", () => {
  const code = String.raw`
import json, re, sys
sys.path.insert(0, sys.argv[1])
import render
P = render.Page.__new__(render.Page)
class D: pass
d = D()
u = lambda i, **k: dict({"id": i, "status": "active", "content": "fact " + i, "scope": "s", "type": "t", "priority": "hard"}, **k)
d.units = {
  "U-0001": u("U-0001", coupled_with=["U-0002"]), "U-0002": u("U-0002", coupled_with=["U-0001"]),   # one coupling, listed on both
  "U-0003": u("U-0003", coupled_with=["U-0004"]), "U-0004": u("U-0004", coupled_with=["U-0005"]), "U-0005": u("U-0005", coupled_with=["U-0003"]),  # a loop
}
d.order = list(d.units); d.chain_of = {i: [i] for i in d.units}
P.d = d
out = {}
deps = P.deps()
out["paths"] = len(re.findall(r"<path class=e ", deps))
out["width"] = int(re.search(r"<svg class=dg width=(\d+)", deps).group(1))
s = P.summary("- Beta moved <b>late</b> [U-0001, U-9999]\n- \n-\n- Second line\nwrapped here (U-0002)\n\n# A heading\n")
out["items"] = s.count("<li>")
out["link"] = "data-chain='U-0001'" in s
out["unknown_plain"] = "<span class=ref>U-9999</span>" in s
out["escaped"] = "&lt;b&gt;late&lt;/b&gt;" in s and "<b>late" not in s
out["heading_skipped"] = "heading" not in s
out["wrapped_joined"] = "Second line wrapped here" in s and "<li></li>" not in s and " -<" not in s
dh = render.Page.diff_html("Crash-free sessions were 99.3% over 3 days", "Crash-free sessions were 99.6% over 7 days")
out["diff"] = "<del>99.3%</del>" in dh and "<ins>99.6%</ins>" in dh and "<ins>7</ins>" in dh
out["rewrite"] = render.Page.diff_html("alpha beta gamma", "one two three four") is None
print(json.dumps(out))
`;
  const r = spawnSync("python3", ["-c", code, path.join(SKILL, "scripts")], { encoding: "utf8" });
  assert.equal(r.status, 0, r.stderr);
  const o = JSON.parse(r.stdout);
  assert.equal(o.paths, 4, "a coupling listed on both facts is drawn once; the loop's three links are drawn");
  assert.ok(o.width < 1000, `a loop of links stays narrow (width ${o.width})`);
  assert.equal(o.items, 2);
  assert.ok(o.link && o.unknown_plain && o.escaped && o.heading_skipped && o.wrapped_joined, JSON.stringify(o));
  assert.ok(o.diff, "removed and added words are marked");
  assert.ok(o.rewrite, "a full rewrite is not shown as a word diff");
});
