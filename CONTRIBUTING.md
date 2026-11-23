# Contributing to ferris

Thanks for your interest in improving ferris. Bug reports, ideas, docs fixes and code are all welcome.

This project follows a [code of conduct](CODE_OF_CONDUCT.md); by taking part you agree to it.

## Before you start

- **Found a bug?** Search the [issues](https://github.com/ferris-ai/ferris-cli/issues) first, then open one with the bug report form. The output of `ferris doctor` and the agent you use (Claude Code, Codex or Cursor) help a lot.
- **Have an idea or a larger change?** Open an issue to talk it through before writing code, so we can agree on the approach first.
- **Small fixes** (typos, docs, a clear bug with a test) can go straight to a pull request.
- **Security issues** go through [SECURITY.md](SECURITY.md), not public issues.

Never put real client documents, emails or project state in an issue, a test or a fixture. Use the fictional sample project or make up your own.

## Development setup

You need Node.js 18+ and Python 3.9+. There's nothing to install: the CLI has no runtime dependencies, and the engine's scripts use only Python's standard library.

```bash
git clone https://github.com/ferris-ai/ferris-cli.git
cd ferris-cli
npm test                     # the whole suite (node:test)
node bin/ferris.js --help    # run the CLI from the checkout
npm link                     # put this checkout on your PATH as `ferris`
```

`ferris` writes to your agents' folders (`~/.claude`, `~/.agents`, `~/.cursor`, `~/.codex`). To try a change without touching your own setup, give it a scratch home:

```bash
export HOME=$(mktemp -d) && mkdir -p "$HOME/.claude"
node bin/ferris.js setup
```

The tests do the same, so `npm test` never touches your real folders.

## Project layout

| Path | What's there |
|---|---|
| `bin/ferris.js` | entry point and error handling |
| `src/cli.js` | command parsing and help |
| `src/setup.js`, `src/doctor.js`, `src/new.js`, `src/profiles.js` | the user-facing commands |
| `src/skill.js`, `src/agents.js`, `src/paths.js` | installing the skill for each agent, and where things live |
| `src/state.js`, `src/python.js` | `ferris state` / `ferris watch`: run the engine's scripts |
| `src/ui.js` | terminal output (colors, spinners, boxes), only on a terminal |
| `skill/project-state/` | the skill itself: `SKILL.md`, its references, profiles and Python scripts |
| `test/cli.test.js` | the test suite |

### About `skill/project-state`

The skill's method (its prompts, the checks in `engine.py` and `flatstore.py`, the steps in `references/`) decides what every project's state looks like. For a change to how the state is written or checked, open an issue first so we can agree on it before you write code. Changes to the CLI-only parts (`ferris_home.py`, the reminder hooks in `watch.py`, the report in `render.py`) are fine as pull requests.

## Making a change

1. Fork the repo and create a branch from `main`.
2. Make your change, with a test in `test/cli.test.js` for any change in behavior.
3. Run `npm test`. CI runs it on every pull request, on Node 18, 20 and 22, on Linux and macOS, with Python 3.9.
4. Update the README if a command, flag or behavior changes, and add a line under "Unreleased" in [CHANGELOG.md](CHANGELOG.md).
5. Open a pull request and fill in the template.

### Conventions

- **Agent-facing output stays plain.** `ferris state` and `ferris watch` print JSON or plain lines that the agent reads; colors, spinners and boxes appear only for people, on a terminal. Test both when you touch output.
- **No network calls, no new dependencies.** The CLI and the scripts work offline and without installs. A change that needs either should start as an issue.
- **Errors say what to do.** An error message names the problem and the command or step that fixes it.
- **Write for people.** Help text, messages and docs use plain words and short sentences.
- **Commit messages** start with a type: `feat:`, `fix:`, `docs:` or `chore:`, followed by what the change does, in the present tense (`fix: show a spinner error once on a terminal`).

## Review

A maintainer reviews every pull request. We may ask for changes or suggest a different approach; the conversation stays on the pull request. When it's approved, a maintainer merges it, and it ships in the next release.

## License

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE), like the rest of the project.
