# Workspace

One directory `WS` per project. Everything the skill writes lives here.

```
WS/
  config.json                 settings (below)
  input/units.jsonl           all units received so far
  input/batches.json          the batch plan; existing entries never change
  store/<B>.json              the whole state store after batch B (units with evidence, supersede chains, dependencies); the batch is done when it exists
  history.jsonl               one line per finished batch: counts, what was rejected, recompute rounds, units still flagged
  reply/<B>.json              your write reply for B; reply/<B>.recompute.<n>.json your recompute replies
  workdir/                    rebuilt for every batch: write_prompt.md, recompute_prompt.md, step.json, store.json (working copy), applied.json
  profile.md                  optional project overlay: guidance for this project, added to the write prompt (below)
  scratch/                    your helper notes (never project data outside WS)
  sources.manifest.json       source files already seen (written by scripts/watch.py mark)
  pending-notes.jsonl         corrections recorded from other conversations, applied at the next update
  reports/<B>.html            report per batch; reports/<B>.summary.md your summary lines for it
  current/report.html         newest report; current/brief.md the short state summary other agents read
```

`config.json`:
```json
{
 "created": "2026-03-23T10:00:00Z",
 "excluded_authors": ["ferris", "ferris ai", "ferris agent"],
 "excluded_provider_prefixes": ["ferris"],
 "cadence": "weekly",
 "mode": "ask",
 "profile": "delivery",
 "project": {"name": "Northwind dispatch", "slug": "northwind-dispatch", "aliases": ["Northwind"]},
 "parties": {"vendor": "Acme Build", "client": "Northwind Freight"},
 "people": [{"name": "Dana Reyes", "side": "vendor", "role": "PM"}, {"name": "Sam Okafor", "side": "client", "role": "operations director"}],
 "sources": [{"type": "folder", "path": "/absolute/path/to/inbox"}],
 "last_update": null
}
```
- The two `excluded_*` lists name units written by the tracking tool itself (see `input.md`). Keep the defaults unless the user says otherwise. Such units stay in `input/units.jsonl` but are not shown to the writer and cannot be evidence.
- `cadence`: how often new units become batches. `weekly` (default): Monday-Sunday weeks, cut once the week is over. `daily`: one batch per
  day, cut once the day is over. `on_new`: everything dated up to today is cut into one batch at each update. Shorter cadences give fresher
  state but split related messages across batches.
- `mode`: `ask` (default) or `auto`, used by the update reminder (`SKILL.md`, "Daily use").
- `profile`: the domain wording of the write prompt, `delivery` or `general` or a custom profile (below). Without the key a workspace uses `delivery`, the wording it was made with.
- `tips`: `false` hides the Ferris link in this project's report footer and the occasional Ferris tip shown to the user at session start (delivery projects only; `ferris watch tips off` hides the tips for the whole workspace).
- `daily_offered`: the date the user was asked about the reminder and the project context (`onboarding.md` step 5); while it is set they are not asked again.
- `me`: the user's name as it appears in the documents, decided by the agent from what it knows of the user (onboarding.md step 2); the author of the user's corrections. Not asked of the user.
- `parties`: leave out when the project has no vendor and client (for example under the `general` profile); then give everyone in `people` the side `other`.
- `project`, `parties`, `people`, `sources`, `last_update`: filled during onboarding and later updates. `people[].side` is `vendor`, `client` or `other`; it is used for ingest and the reminder, not by the writer prompt.

## Initialise
`scripts/engine.py setup <units.jsonl> <WS> [<batches.json>] [--today YYYY-MM-DD]`, with the user's units file, the workspace path and, if the user has one, their `batches.json`.
A workspace is new while `WS/input/batches.json` does not exist; a `config.json` written before that (onboarding does this) is kept and only missing defaults are added. It checks the units, copies them to `WS/input/`, writes `config.json` and an empty `history.jsonl`, creates the folders and, without a given `batches.json`, cuts batches by `cadence` (weekly for a new workspace). It prints a summary; if it stops with an error, report the error to the user instead of working around it. `--today` defaults to the real date; give it only to reproduce a run.

A workspace made by the earlier event-log version of this skill (it has `events.jsonl` and `snapshots/`) is not supported: tell the user to start a new workspace for the project. The old workspace stays as it is (the user may delete it); the new one starts from the same project name and the same source folder or units file, so nothing is lost.

## Add units
For new units arriving later, run the same command with the new file and the existing `WS` (no `batches.json`). Units whose `unit_id` is already present with identical content are skipped; the same id with different content is an error. Every new unit is stored in `input/units.jsonl`; batches are cut from the units that are in no batch yet ("pending"):
- Only complete periods are cut (weekly: the week's Sunday is before today; daily: the day is before today; on_new: everything dated up to today). The rest stays pending and is cut by a later run of the command, even one with an empty units file.
- New batches are appended after the existing ones; existing batches never change. A unit dated on or before the last existing batch's `through` counts as dated the day after it, so it joins the first new batch.
- Units with `provider: "pm-note"` (corrections from the user, see `SKILL.md`) never wait: all pending ones form one extra batch with `through` = the newest note's date (or the last `through`, if later), cut after the regular ones.

## Profile and overlay
A **profile** is the domain wording of the write prompt (`profiles/<name>.json`): the intro, what to track, what to add, and the supersede examples. `delivery` is the original flat:v4 wording (a vendor-client project); `general` assumes no domain. A custom profile is `<state home>/profiles/<name>.json` (the `.ferris` that holds the workspace, else `PROJECT_STATE_HOME`); it may name another profile in `extends` and leaves out the slots it does not change. Built-in names cannot be overridden.

The **overlay** `WS/profile.md` (at most 6000 characters, no `{{`) is guidance for this one project: its terms and naming, what matters most, how its statuses are worded. It is added to the task context of the write prompt under "Project guidance". It holds no facts about the project (those enter only as units with a verbatim quote), and it cannot change the output format, the evidence rules or the checks.

`scripts/engine.py profile list [WS]`, `profile show <name> [WS]`, `profile status WS`, `profile set WS <name>`, `profile overlay WS [--file F | --clear]` (without options it prints the overlay). A change applies from the next `stage`; batches already folded keep what they were written with. `history.jsonl` records the profile name and the overlay's hash for every batch.
