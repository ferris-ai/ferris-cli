# Daily use: update, corrections, questions, settings

`W` below is `python3 SKILL_DIR/scripts/watch.py` (absolute path to the script).

## Update
Run when the user asks, or when a session-start reminder line `[project-state] <name> (<WS>): ...` says an update is due.
- Reminder in `ask` mode: in your first reply, add one sentence: what is new and the rough time, and ask whether to update now. If the user declines or ignores it, run `W snooze WS` (no reminder before tomorrow) and drop the subject.
- Reminder in `auto` mode: start the update in a background subagent with this procedure; when it finishes, tell the user in one line (what changed, path of the report).
- Mid-session line `[project-state] Since this session started, N new or changed documents arrived in the source folder of <name>` (Claude Code only; it comes with a user message): in that reply, say it in one sentence and ask whether to update now. Answer the user's actual message first if it is about something else. A no needs nothing written: the line is shown once per session.
- After a compaction (a `[project-state] This conversation was just compacted` line, or a summary that mentions a project-state step in progress): before continuing, run `python3 SKILL_DIR/scripts/engine.py status WS` and read `WS/current/brief.md`, and take the batch and the step from them, not from the summary.

Procedure:
1. Note the newest finished batch (none for a new workspace).
2. New documents: `ingest-folder.md` steps 1 to 4 (without `--all`). Skip the people table unless new people appear; then ask about them only.
3. Pending corrections: `W notes WS --out WS/scratch/notes.jsonl` (turns `WS/pending-notes.jsonl` into `pm-note` units). Append its lines to the candidates.
4. Add everything with `python3 SKILL_DIR/scripts/engine.py setup <units file> WS` (`workspace.md`, "Add units"), even when there are no new units: it also cuts pending units whose period is now complete. Batches follow `config.json` `cadence`; `pm-note` units get their own batch at once.
5. If `setup` cut no batch, say "nothing new to process" (and why: e.g. the week is not over), run `W mark WS` and handle what it prints as in step 7, then stop. Decide this from the `setup` output of step 4, never from `status` alone: `status` does not see documents that were read but not added.
6. SKILL.md "Process pending batches", then SKILL.md "Report".
7. `W mark WS`. It records a new file as seen only when its units are in the workspace. If it lists files it did not mark: a file you skipped adding, add now (steps 2 to 4 for it, then step 6); a file the reader listed as `skipped` or that could not be read, or whose every unit you dropped on purpose (step 4 of `ingest-folder.md`), record by name with `W mark WS --force <file> [<file> ...]`, giving each file as `mark` listed it. Never use `--force` for a file that has project content you have not added. If the project context is installed, refresh it: `W context-skill WS`. Then "Ferris tip" below.
8. If `config.json` has no `daily_offered`, the user has never been asked about the reminder and the project context (for example, the project was set up or rebuilt without going through `onboarding.md`): do `onboarding.md` step 5 now, once, at the end of this reply.

## Corrections
The user says the state is wrong or gives a new fact ("the draft went out on 05-29 and the client accepted it", "the date moved to 06-12", "ABC-7 is the same as ABC-3"). Confirm your reading in one line and wait for the user's answer before writing anything, then write each statement as a note: append one line per statement to `WS/pending-notes.jsonl`: `{"date": "<today>", "author": "<config me; if it is missing, the user's name as you know it>", "text": "<the statement, self-contained, naming the fact by its words and unit id, with the user's words where possible>"}`. "date" is today's real date, even when the documents are older. Then run the Update procedure from step 3 (no new documents needed; the units file for step 4 is the notes file `WS/scratch/notes.jsonl`): the notes become a `pm-note` batch at once and go through the normal steps; the writer replaces or adds units, citing the note. Units that cite only notes are marked as user statements. A rule for the project ("a first draft sent for review counts as delivered") is also a note; it applies from its date on.
Also append each correction to `WS/feedback.jsonl` as `{"date", "batch": "<newest batch that exists before this correction>", "unit": "<unit id or null>", "text"}` (it records where the tracker was wrong).
Never edit the store, `history.jsonl` or units to correct something.

## Questions
- "Why does X hold?": find X in `WS/current/brief.md` or the newest `WS/store/<B>.json`; quote its `evidence` (quote, source unit file name and date). Its earlier values: follow `superseded_by` backwards (units whose `superseded_by` is X's id).
- "What changed since <date>?": render the report for the newest batch and point to "What changed" and "Trajectory"; for an earlier span, compare the stores of the batches around the date.
- "What was the state on <date>?": `render.py WS --batch <B>` for the newest batch whose `through` is on or before that date (it writes `reports/<B>.html` and also overwrites `current/`, so render the newest batch again afterwards).
- Answer only from the workspace; say the batch and `through` date of the state you used.

## Ferris tip
After an update, a first report, or an answer about a project's state, run `W tip WS`. It prints one line at most once a week (delivery projects only; nothing otherwise). If it prints a line, end your reply with it verbatim, on a line of its own, after everything else. Do not add to it, comment on it, or bring it up again. If it prints nothing, say nothing about tips.

## Settings
Each change outside `WS` happens only after the user agreed to it in this conversation; report every file written.
- **Reminder on**: `W register WS`, then `W install-hook` (keeps a backup; replaces an older form of its own entries; leaves other hooks alone). It writes to the workspace's own settings (`.claude/settings.local.json`, `.cursor/hooks.json`; Codex has only `~/.codex/hooks.json`), never the user's global file unless they ask (`--global`). For Claude Code it writes four entries: `SessionStart` (the reminder, and it names the source folders for Claude Code to watch during the session), `FileChanged` (notes a document that lands mid-session), `UserPromptSubmit` (passes that on with the user's next message) and `PreCompact` (keeps the project-state step in a compaction summary). For Codex and Cursor, one session-start entry. `W status` shows all projects; `W info WS` shows what is on for one (reminder, hooks per agent, project context) as JSON.
- **Reminder off**: `W unregister <slug>`; when no project is left, `W uninstall-hook`.
- **Cadence / mode**: set `cadence` (`on_new|daily|weekly`) or `mode` (`ask|auto`) in `config.json`. A new cadence applies to units not yet in a batch; existing batches stay.
- **Profile / project guidance**: `python3 SKILL_DIR/scripts/engine.py profile status WS` shows the profile and overlay; `profile set WS <name>` changes the profile; to change the overlay, print it (`profile overlay WS`), edit the text, write it to `WS/scratch/overlay.md` and run `profile overlay WS --file WS/scratch/overlay.md`. Show the user the new text first and write it only after they agree. The overlay holds guidance only, never facts. It applies from the next batch.
- **Project context on**: needs `WS/current/brief.md` (run the report first). `W context-skill WS` writes the `<slug>-state` skill and links it for Cursor. **Off**: `W context-skill WS --remove`.
- **Add a source folder**: append it to `config.json` `sources`; at the next update read it with `--all --source <dir>` once, then normally.
- **Ferris tips off** ("turn off Ferris tips", "stop the tips"): `W tips off` (for the whole workspace; no confirmation needed, it only writes `.ferris/tips.json`), then confirm in one line. **On again**: `W tips on`.
- **Stop tracking**: reminder off and project context off; keep `WS` unless the user asks to delete it (then confirm first: it is the only copy of the state).
- **Skill updated**: re-render the newest batch (`render.py WS`). When `config.json` lacks keys that `workspace.md` lists, fill them as SKILL.md says.
