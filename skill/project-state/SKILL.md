---
name: project-state
description: Tracks the state of a project (rules, facts, commitments, dates, statuses and how they change) from its documents, with a verbatim quote as evidence for every fact. Sets a project up from a folder of emails, meeting notes, chat exports and files, keeps it updated as new documents arrive, shows the current state and how each fact changed in an HTML trajectory report, takes the user's corrections, and can install a reminder for new documents plus a per-project context skill so any later conversation gets the latest state. Use when asked to track, set up, update or report on a project's state or status, to process new project documents or a units.jsonl export, to correct the tracked state, or to turn update reminders or the project context on or off.
---

# Project state

Turn a stream of project documents into a store of facts ("units"): each unit is one sentence of content with a priority (hard / soft), a type and scope label, verbatim evidence quotes, and links to the units it depends on. Later documents replace units; the old values stay as a version chain. You write the state (you are the writer); every fixed-rule step is done by the scripts in `scripts/` (Python 3, standard library only): run them, never re-implement them. The method is `flat:v4`; `references/batch.md` describes the steps.

Paths: `SKILL_DIR` is the directory holding this file; `references/...`, `assets/...` and `scripts/...` are relative to it. `WS` is the project's workspace directory. Run scripts as `python3 SKILL_DIR/scripts/<name>.py ...` with absolute paths.

Talk to the user in their language. Keep chat replies short: the report holds the detail.

## Which flow
| The user wants to | Follow |
|---|---|
| start tracking a project (documents in a folder, or a units.jsonl) | `references/onboarding.md` |
| update a tracked project (new documents, a reminder said it is due) | `references/daily.md`, "Update" |
| correct the state | `references/daily.md`, "Corrections" |
| ask why a fact holds, what changed, how something evolved | `references/daily.md`, "Questions" |
| turn the reminder or the project context on/off, change cadence or mode, add a source, change the profile or the project guidance, stop tracking, turn Ferris tips off/on | `references/daily.md`, "Settings" |

**Every reply that reports on a project** (its state, an update, a first report, what changed) ends with one check: run `python3 SKILL_DIR/scripts/watch.py tip WS`. It prints a line at most once a week, for delivery projects; most of the time it prints nothing. If it prints a line, put that line verbatim as the last line of your reply, on its own, without comment. If it prints nothing, say nothing about it.

A workspace exists when `WS/config.json` exists. A workspace with `events.jsonl` and `snapshots/` was made by the earlier event-log version of this skill and cannot be continued: tell the user to start a new workspace for the project (`references/workspace.md`). A `config.json` with no `project` or no `people`: ask `references/onboarding.md` step 1 questions 1 and 2, show the people table of step 2 built from the authors in `WS/input/units.jsonl` (one message), and record `project`, `parties`, `people`, `me` (you decide it, `references/onboarding.md` step 2 "Who the user is"; do not ask) and the missing defaults (`cadence: "weekly"`, `mode: "ask"`) in `config.json`. Then offer the daily-use features of `onboarding.md` step 5 once, as that step says (it checks `watch.py info WS` and offers only what is off, and never again once `daily_offered` is set). Whatever flow produced the first report (onboarding, a rebuild, a units file), end it with `onboarding.md` step 5 when `config.json` has no `daily_offered`.
A user who only has a `units.jsonl` and wants the state (no folder, no reminders) gets the short path: `python3 SKILL_DIR/scripts/engine.py setup <units.jsonl> WS`, then "Process pending batches" below, then the report.

## Process pending batches (every flow ends here)
`python3 SKILL_DIR/scripts/engine.py status WS` lists the pending batches and the next step for each.

For each pending batch B, in order (details and the reply formats: `references/batch.md`):
1. **STAGE**: `python3 SKILL_DIR/scripts/engine.py stage WS B`. Writes `WS/workdir/write_prompt.md`.
2. **WRITE**: read `WS/workdir/write_prompt.md` and do what it asks: write the JSON reply to `WS/reply/B.json`. Follow the prompt exactly; read nothing else for the state. In every `evidence` entry, `unit_id` is the `unit_id` printed in the batch listing in brackets (for example `harbor-03cf609a1f`), never a file name or a `doc_id`.
3. **CHECK**: `python3 SKILL_DIR/scripts/engine.py check WS B`. Fix what the documents let you fix honestly, drop what they do not support, check again (at most twice).
4. **APPLY**: `python3 SKILL_DIR/scripts/engine.py apply WS B`. If it prints `"next": "recompute"`:
5. **RECOMPUTE** (at most 3 rounds): read `WS/workdir/recompute_prompt.md`, write `WS/reply/B.recompute.<n>.json` (n as printed), run `engine.py check WS B`, then `engine.py recompute WS B`; repeat while it prints `"next": "recompute"`.
6. **FOLD**: `python3 SKILL_DIR/scripts/engine.py fold WS B`. The batch is done when `WS/store/B.json` exists.

If a command prints an `error`, report it to the user instead of working around it. Tell the user one line per batch: `B3 2026-05-18..05-24 accepted=7 rejected=1 still_flagged=0`.

## Report (after processing)
1. Write `WS/reports/<B>.summary.md` for the newest batch B (setup creates `WS/reports/`; `mkdir -p` it for a workspace made earlier): 3 to 5 lines, each starting with `- `, most important first. Lead with what needs the reader's attention: an approval or decision still missing, something blocked or at risk and who it waits on, a date that moved. Then what changed. One sentence per line, written for the project's people, not about the tracking: name the people, documents and dates (write dates as "18 Dec"), and leave out internal words such as unit, scope, batch or store. End each line with the unit ids it rests on in square brackets, e.g. "[U-0023, U-0031]"; the report turns them into links. Example: "- Android beta is blocked: Google Play needs the Data safety form, which Jordan owns. [U-0060]". No facts beyond the store. Write it in English, like every other part of the report and the brief, even when you talk to the user in another language: the report is shared with people and agents that expect one language, and it quotes English sources.
2. `python3 SKILL_DIR/scripts/render.py WS [--batch B]`. It writes `WS/reports/<B>.html`, `WS/current/report.html` and `WS/current/brief.md`. The report shows a version timeline (one row per fact, each bar a version, grouped by scope; click a row for the quotes and source documents), activity per batch, what the newest batch changed, dependencies, and the current state by scope (filterable).
3. Open the report for the user (`open WS/current/report.html` on macOS, `xdg-open` on Linux) and reply in the user's language with at most 8 lines: the state in a few bullets, what changed, and any units still flagged for recheck. The report contains client data: never upload or publish it.

## Boundaries
- Facts come only from the documents. No outside knowledge about the companies or people.
- Every unit and every supersede cites at least one verbatim quote from a document of the current batch.
- Do not write a status as settled (accepted, approved, paid) unless a document states it.
- `WS/store/*.json` and `WS/history.jsonl` are written only by the scripts. Corrections enter as new units (`pm-note`), never as edits.
- Write project data only inside `WS`. Any file you create for your own use goes in `WS/scratch/`, never in `/tmp` or another system temp directory: project text must not leave the workspace. Outside `WS`, write only what `references/daily.md` "Settings" lists, and only after the user agreed to it.
- What you learn about the project, and who the user is in it, belongs in `WS/config.json` (`people`, `me`) and the state, not in your own memory: the scripts and later sessions read it from there.
- When a rule is ambiguous, apply the stricter reading and mention it in the report summary or your reply.

`assets/example/` holds a small fictional project (units and a batch plan) for trying the flow.
