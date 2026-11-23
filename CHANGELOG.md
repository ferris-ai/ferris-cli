# Changelog

All notable changes to this project are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [semantic versioning](https://semver.org).

## [Unreleased]

## [0.1.0]

First public release.

### Added

- `ferris setup`: a guided first run that checks Node and Python, finds Claude Code, Codex and Cursor, installs the skill, starts a project and turns on reminders.
- `ferris install skill` / `ferris uninstall skill` for Claude Code, Codex and Cursor, in user or project folders.
- `ferris new project-state`: create a project workspace from a folder of documents or a `units.jsonl`.
- `ferris profiles`, with the built-in `delivery` and `general` profiles and custom ones.
- `ferris doctor`: checks Node, Python, each agent's skill and reminders, profiles and the workspace.
- `ferris state` and `ferris watch`: the engine steps, reminder hooks, project context skill and tips the agent runs.
- The project-state skill: evidence-checked state with version history, an HTML report and a `brief.md` per project.
- Reminders: at session start the agent hears when a project has new documents, or that a new project isn't tracked yet. An update records a new document as seen only once it is in the workspace; `ferris watch mark --force FILE` records a file with nothing to track.
- The HTML report: "Where things stand" (the agent's summary, with links to the facts behind each line), a timeline of how each fact changed, what the latest batch changed with removed and added words marked, a dependency graph by scope, and the current state with filters and search.

[Unreleased]: https://github.com/ferris-ai/ferris-cli/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/ferris-ai/ferris-cli/releases/tag/v0.1.0
