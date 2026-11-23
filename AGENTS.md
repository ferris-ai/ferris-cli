# Working on ferris

This repository is `@ferris-ai/cli`: the `ferris` command (Node, `bin/` and `src/`) and the **project-state** skill it installs into Claude Code, Codex and Cursor (`skill/project-state/`: instructions the agent reads, plus Python scripts the CLI runs). Read [README.md](README.md) for what the tool does and [CONTRIBUTING.md](CONTRIBUTING.md) for the contribution process; this file is what a coding agent needs on top of them.

## Two kinds of user

Every change affects one or both:

- **People** run `ferris setup`, `install`, `new`, `profiles`, `doctor` on a terminal. They get colors, spinners, boxes and prompts.
- **Agents** run `ferris state …` and `ferris watch …`, and read `skill/project-state/` as instructions. They parse the output and follow the text literally.

The skill's text is code: a changed sentence in `SKILL.md` or `references/` changes what every user's agent does. Review it the way you'd review a change to a script.

## Invariants

Don't break these unless the task says to, and then say so in the pull request and the changelog.

- **Agent-facing output is plain.** `ferris state` and `ferris watch` print one JSON object or plain lines, with no color, spinner or box, on a terminal or not. Errors go to stderr as `ferris: <message>` with a non-zero exit code.
- **People-facing output degrades off a terminal.** Colors, spinners and prompts only appear on a TTY (`src/ui.js` decides; `NO_COLOR`, `FORCE_COLOR`, `CI` and `TERM=dumb` are respected). Off a terminal, `ferris setup` doesn't prompt and acts as `--yes`.
- **Command contracts.** Commands, flags, defaults, exit codes, JSON fields, and the files a workspace holds (`config.json`, `store/`, `history.jsonl`, …) are relied on by the skill and by existing workspaces. Old workspaces must keep working, or `ferris doctor` must say what to do.
- **No network calls and no runtime dependencies.** Node uses only built-in modules; the scripts use only Python's standard library and must run on **Python 3.9** (no `match`, no `X | Y` type unions at runtime, no 3.10+ library calls).
- **Client data stays in the workspace.** Project data is written only inside the workspace (`.ferris/projects/<slug>/`), never to a temp folder, a log or anywhere shared. `.ferris` keeps its own `.gitignore` of `*`.
- **Other people's settings are left alone.** Installing hooks edits only ferris's own entries in the agent's settings file, keeps a backup, upgrades older ferris entries in place and never touches the user's own hooks. `ferris install skill` refuses a skill folder ferris didn't install unless `--force`, which moves it aside.
- **Reminder hooks are quiet and fast.** They run at every agent session start: silent outside a workspace with a `.ferris`, read only file names and dates, and never fail the session.
- **The skill's evidence rule holds.** Every fact in the state cites a word-for-word quote from a document; the scripts reject changes that don't. Don't weaken the checks in `engine.py` / `flatstore.py`.

## Where things are

| Path | What it is | Read first when |
|---|---|---|
| `bin/ferris.js` | entry point; prints `CliError`s | changing error handling |
| `src/cli.js` | command parsing and `ferris --help` | adding or changing a command or flag |
| `src/setup.js`, `src/doctor.js`, `src/new.js`, `src/profiles.js` | the people-facing commands | changing those commands |
| `src/ui.js` | terminal output: colors, `spin`, `box`, prompts | changing anything people see |
| `src/skill.js` | install/uninstall; the text rewrites applied to the installed copy (`REWRITES`, `CLI_SECTION`) | editing the skill's docs or install |
| `src/agents.js` | where each agent reads skills and hooks | adding or changing agent support |
| `src/paths.js` | `CliError`, finding `.ferris`, workspaces and folders; mirrors `scripts/ferris_home.py` | changing locations or environment variables |
| `src/state.js`, `src/python.js` | `ferris state` / `ferris watch`: run the Python scripts | changing how the scripts are called |
| `skill/project-state/SKILL.md` | the skill's entry point for the agent | any skill change |
| `skill/project-state/references/` | the flows: onboarding, daily use, one batch, workspace, input format, folder ingest | changing a flow |
| `skill/project-state/scripts/` | `engine.py` (batch steps), `flatstore.py` / `flatprompts.py` (the store and prompts), `render.py` (report), `watch.py` (hooks, reminders, tips), `ingest_folder.py`, `profiles.py`, `ferris_home.py` | changing behavior the agent drives |
| `skill/project-state/profiles/` | built-in profiles (`delivery`, `general`) | changing prompt wording |
| `skill/project-state/assets/example/` | the fictional sample project used by tests and the README | changing the input format |
| `test/cli.test.js` | the whole test suite | always |

## Things that are easy to get wrong

- **Skill text and `REWRITES` move together.** On install, `src/skill.js` rewrites `python3 SKILL_DIR/scripts/<script>.py …` in the docs to `ferris state …` / `ferris watch …`. If you change one of those sentences in the skill, update `REWRITES`; a test fails when a rewrite no longer matches.
- **`ferris_home.py` and `src/paths.js` must agree** on where `.ferris` is and which environment variables count. Change both.
- **New `ferris state` subcommands** need an entry in `STATE` in `src/state.js`; a new `ferris watch` subcommand that takes a workspace goes in `WATCH_WS`, so a project slug is resolved to a path. Add them to the help text and to the table in `CLI_SECTION` too.
- **A new agent or settings location** touches `src/agents.js`, `watch.py` (hooks), `ferris doctor`, the README's agents table and the tests.
- **`render.py` output is a self-contained HTML file** opened from disk: it loads no scripts, fonts or other resources from the network (plain links are fine).
- **Python scripts are run by `ferris` and directly** (`python3 SKILL_DIR/scripts/…` is the fallback when `ferris` isn't on PATH). Keep their command-line arguments working both ways.

## Develop and check

```bash
npm test                         # the whole suite, about 20 seconds
node bin/ferris.js --help        # run the checkout, not an installed ferris
```

- **Never run the CLI against the real home folder.** `setup`, `install` and the hook commands write to `~/.claude`, `~/.agents`, `~/.cursor` and `~/.codex`. Use a scratch home: `export HOME=$(mktemp -d)`, plus `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `CURSOR_HOME` and `PROJECT_STATE_HOME` inside it if they're set in your environment.
- **Tests use `sandbox()`** in `test/cli.test.js`, which gives each test its own home and runs the CLI as a child process. Add new tests the same way, and test both terminal and non-terminal output when you touch output.
- **Check Python changes on 3.9:** run the tests with a Python 3.9 interpreter (`FERRIS_PYTHON=python3.9 npm test`; macOS's own `python3` is 3.9), or at least `python3.9 -m py_compile skill/project-state/scripts/*.py`.
- **Check the package** when you add or move files: `npm pack --dry-run` should list only `bin`, `src`, `skill`, `README.md`, `LICENSE` and `package.json`.
- A test that fails after your change shows a contract you changed. Fix the code, or if the change is intended, update the test and say why in the pull request.

## Fixtures and data

Never put real emails, chat, meeting notes, client names or project state in tests, fixtures, examples, issues or commits. Use the sample project (`assets/example/`, the fictional Northwind Dispatch) or made-up data.

## Writing

Help text, error messages, skill text and docs use plain words and short sentences. An error says what went wrong and what to run next. Don't coin names for things; use the ones the README already uses (workspace, batch, unit, profile, overlay, reminder, project context).

## Before you finish

- `npm test` passes.
- README updated if a command, flag, file or behavior changed; a line under "Unreleased" in [CHANGELOG.md](CHANGELOG.md).
- Commit messages start with `feat:`, `fix:`, `docs:` or `chore:`.

## Security

Don't put vulnerability details, exploits or proofs of concept in public issues, pull requests, commits or comments. Follow [SECURITY.md](SECURITY.md) to report privately.
