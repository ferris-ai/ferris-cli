import { AGENT_IDS, AGENTS, detectAgents, hookFileFor, joinLabels, skillTargets } from "./agents.js";
import { createWorkspace, nextCard, promptNew } from "./new.js";
import { CliError, pkg, readMarker, tilde } from "./paths.js";
import { findPython, runScriptAsync } from "./python.js";
import { installSkill } from "./skill.js";
import { banner, box, chooseMany, closePrompts, confirm, fail, info, line, ok, spin, stepBar, ui, warn } from "./ui.js";

const STEPS = ["Check", "Agents", "Project", "Reminders"];

/**
 * Install the skill for the given agents with one spinner per folder. On a folder ferris did not make, ask before
 * moving it aside (only when ask is true; otherwise the error stands).
 */
export async function installForAgents(agentIds, { dir, force = false, ask = ui.interactive } = {}) {
  const { version } = pkg();
  const results = [];
  for (const t of skillTargets(agentIds, { dir })) {
    const covers = t.agent === "claude" && agentIds.includes("cursor") ? ["claude", "cursor"]
      : t.agent === "codex" && agentIds.includes("cursor") && !agentIds.includes("claude") ? ["codex", "cursor"] : [t.agent];
    const run = (f) =>
      spin(`Installing for ${joinLabels(covers)}`, async () => installSkill({ target: t.dir, force: f, log: () => {} }), {
        done: (r) =>
          `${joinLabels(covers)} ${r.previous ? `${ui.dim(r.previous)} → ${ui.bold(r.version)}` : ui.bold(`v${r.version}`)} ${ui.dim(tilde(r.target))}`,
      });
    try {
      results.push(await run(force));
    } catch (e) {
      if (!(e instanceof CliError) || !/not installed by ferris/.test(e.message) || !ask) throw e;
      warn(`${tilde(t.dir)} already holds a project-state skill that ferris did not install.`);
      if (!(await confirm(`Move it aside (kept as a .bak folder) and install v${version}?`, { yes: false }))) {
        info(`Left ${tilde(t.dir)} as it is.`);
        continue;
      }
      results.push(await run(true));
    }
  }
  return results;
}

function upToDate(agentIds) {
  const { version } = pkg();
  return skillTargets(agentIds).every((t) => readMarker(t.dir)?.version === version);
}

export async function runSetup(args) {
  const yes = args.includes("--yes") || args.includes("-y");
  const talk = ui.interactive && !yes;
  const { version } = pkg();
  line(banner({ version }));

  try {
    // ---- 1. machine
    line(stepBar(STEPS, 0));
    line("");
    const m = await spin("Checking your machine", async () => ({ py: findPython({ required: false }), agents: await detectAgents() }), {
      done: null,
      minMs: 600,
    });
    ok(`Node ${process.versions.node}`);
    if (!m.py) {
      fail(`Python 3.9 or newer not found ${ui.dim("(the state engine is Python, standard library only)")}`);
      info(`Install it from ${ui.accent("https://www.python.org/downloads/")} or point ${ui.bold("FERRIS_PYTHON")} at one, then run ${ui.accent("ferris setup")} again.`);
      return 1;
    }
    ok(`Python ${m.py.version}`);
    const found = Object.keys(m.agents);
    for (const id of AGENT_IDS) {
      if (m.agents[id]) ok(`${AGENTS[id].label}${m.agents[id].version ? ` ${m.agents[id].version}` : ""}`);
    }
    if (!found.length) warn(`No coding agent found ${ui.dim("· ferris works with Claude Code, Codex and Cursor; installing for Claude Code")}`);
    line("");

    // ---- 2. agents
    line(stepBar(STEPS, 1));
    line("");
    let agents = found.length ? found : ["claude"];
    if (talk) {
      agents = await chooseMany(
        "Which agents should get Ferris?",
        AGENT_IDS.map((id) => ({
          label: AGENTS[id].label,
          value: id,
          checked: found.includes(id) || (!found.length && id === "claude"),
          hint: m.agents[id] ? "found" : "not found",
        })),
      );
      line("");
    }
    if (!agents.length) {
      info(`No agent chosen. Install later with ${ui.accent("ferris install skill --agent claude|codex|cursor")}`);
    } else if (upToDate(agents)) {
      ok(`${joinLabels(agents)} already have v${version}`);
    } else {
      line(`  Ferris will add to ${joinLabels(agents)}:`);
      for (const t of skillTargets(agents)) {
        line(`    ${ui.bold("project-state skill")}   ${ui.dim(tilde(t.dir))}`);
      }
      if (agents.includes("cursor") && (agents.includes("claude") || agents.includes("codex"))) {
        line(`    ${" ".repeat(22)}${ui.dim("Cursor reads it from there too")}`);
      }
      line(`    ${ui.bold("ferris commands")}       ${ui.dim("the agent runs ferris state … · nothing runs in the background")}`);
      line(`    ${ui.bold("your data")}             ${ui.dim("stays in .ferris in your workspace (git-ignored)")}`);
      line("");
      if (!talk || (await confirm("Install?"))) await installForAgents(agents, { ask: talk });
      else info("Skipped. Install later with ferris install skill");
    }
    line("");

    // ---- 3. project
    line(stepBar(STEPS, 2));
    line("");
    let result = null;
    if (talk && (await confirm("Start tracking a project in this workspace?"))) {
      line("");
      const o = await promptNew({ aliases: null });
      line("");
      result = await createWorkspace(o);
    } else {
      info(`Start one any time from your workspace: ${ui.accent("ferris new project-state")}`);
    }
    line("");

    // ---- 4. reminders
    line(stepBar(STEPS, 3));
    line("");
    if (result?.cfg.sources && agents.length) {
      line(`  When a ${joinLabels(agents, "or")} session starts in this workspace, Ferris can check`);
      line(`  ${ui.bold(tilde(result.cfg.sources[0].path))} and offer an update when new documents landed.`);
      if (agents.includes("claude")) line(`  In Claude Code it also notices documents that land during a session.`);
      line(ui.dim("  File names and dates only; nothing runs between sessions."));
      line(ui.dim(`  Changes: hook entries in ${agents.map((id) => tilde(hookFileFor(id))).join(", ")}`));
      line(ui.dim("  (this workspace only, kept out of git; Codex has one hook file for all workspaces)"));
      line(ui.dim("  (each backed up first) and one line in .ferris/registry.json"));
      line("");
      if (await confirm("Turn on update reminders?")) {
        await spin("Turning on reminders", async () => {
          for (const a of [["install-hook", ...agents.flatMap((id) => ["--agent", id])], ["register", result.ws]]) {
            const r = await runScriptAsync("watch.py", a);
            if (r.status !== 0) throw new Error((r.stderr || r.stdout).trim());
          }
        }, { done: `Reminders on for ${joinLabels(agents)} ${ui.dim("· weekly cadence, asks before updating")}` });
        if (agents.includes("codex")) info(`Codex runs a new hook only after you trust it: type ${ui.accent("/hooks")} in Codex once`);
      } else {
        info(`Off. Turn on later: ${ui.accent("ferris watch install-hook --agent <agent>")}`);
      }
    } else {
      info(`Reminders come with a project that has a documents folder ${ui.dim("· ferris watch install-hook")}`);
    }
    line("");

    // ---- done
    const agentName = agents.length ? joinLabels(agents.filter((a) => a !== "cursor" || agents.length === 1)) : "your agent";
    line(
      result
        ? nextCard(result, agents)
        : box(
            [
              `${ui.dim("Create a project:")}  ${ui.accent("ferris new project-state")} ${ui.dim("(in your workspace)")}`,
              `${ui.dim("Check things:    ")}  ${ui.accent("ferris doctor")}`,
              `${ui.dim("Then tell")} ${agentName}${ui.dim(":")} ${ui.bold("start tracking <project> with the project-state skill")}`,
            ],
            { title: "You're set" },
          ),
    );
    line("");
    line(ui.dim(`  Start a new agent session so it loads the skill.  ·  ferris --help for everything`));
    line("");
    return 0;
  } finally {
    closePrompts();
  }
}
