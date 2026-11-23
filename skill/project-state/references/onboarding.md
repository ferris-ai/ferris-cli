# Onboarding: start tracking a project

From "track this project" to a first report, confirmed by the user, with reminders and project context switched on if they want them. Ask few questions and ask them together; every question below says what to do with the answer.

## 1. Basic facts (one message)
Ask all of these at once, filling in what the user already said:
1. The project's name and other names people use for it (abbreviations, the client's name). They are how other conversations will recognise the project.
2. The vendor's name and the client's name (leave both out when the project has no two sides). Do not ask which side the user is on: you work for this user, so you know who they are (step 2, "Who the user is").
3. Where the documents are: a folder (the only automatic source for now), or a ready `units.jsonl`. Other systems (mail, chat, meeting notes, a project tool) can be exported into the folder.
4. From which date to track (default: the oldest document).

Then create the workspace with `ferris new project-state "<name>" [--alias ...] [--vendor ...] [--client ...] [--folder ...] [--start ...] -y` run from the user's workspace (the folder the coding agent has open). It goes in `.ferris/projects/<slug>/` at that workspace's root, where `slug` is the project name in lower case with `-` between words; `.ferris` ignores itself in git, so a repository is fine. A location the user names elsewhere must not be inside a git repository or a synced folder: say so if the user picks one. That writes `WS/config.json` with `project` (`name`, `slug`, `aliases`), `parties`, `sources` (`[{"type": "folder", "path": "<absolute path>"}]`), `start` if given, and the defaults (`workspace.md`); add `--profile NAME` for a non-delivery workspace (SKILL.md, "Profiles"). Without the ferris command, write that file yourself.

With a `units.jsonl` instead of a folder: skip step 2, set no `sources`, and use the file as the units file in step 3.

## 2. Inventory and people (no engine yet)
Follow `ingest-folder.md` steps 1 to 5 with `--all`. Drop units dated before `start`. In the inventory also give:
- the number of batches the weekly cut will make (count the distinct Monday-Sunday weeks of the units; the week that is not over yet waits);
- where the state will live (`WS`).
**Who the user is.** Do not ask the user which person they are. You are this user's agent: use what you already know about them (their name, email, role and company from your memory, settings or the conversation) and match it to the people found. Record the match as `me` (the name as it appears in the documents) in `config.json`. Show it in the people table as the row marked "you", so a wrong match is corrected in the same reply as the rest of the table, with no separate question. Ask only when you cannot match: two candidates fit equally, or you do not know who the user is. If the user is not among the people in the documents, set `me` to their own name and give them the side and role you know (`other` if unknown). The people table takes names, sides and roles from the documents: when your own knowledge of the user's role differs from the role the documents give, show the documents' role and keep the name match; ask only when the name matches but the role clearly differs. The user may be on either side (usually the vendor's PM); their later corrections are written in their name and count as evidence from their side.

Record the confirmed `people` (name, side, role) in `config.json`. Wait for the user's confirmation or corrections before step 3. Apply people corrections to `config.json`; nothing else needs redoing.

## 3. First run
1. `ingest-folder.md` step 6 (or, with a ready file, `python3 SKILL_DIR/scripts/engine.py setup <file> WS`). The weekly cut is the default for the first run whatever cadence the user will choose later: it gives the history coherent batches.
2. SKILL.md "Process pending batches", then SKILL.md "Report".
3. `python3 SKILL_DIR/scripts/watch.py mark WS` (records the files seen and `last_update`; it leaves out files with no units in the workspace and lists them: see `daily.md`, Update, step 7).

## 4. First review
Show the user the report and ask about at most 7 things, most consequential first: facts whose wording or status looks wrong, units still flagged for recheck, scopes that look duplicated or misnamed; say how many more there are in the report. Handle the answers with `daily.md` "Corrections". The user may also say "all fine" or skip it.

## 5. Daily use (offer once)
First run `python3 SKILL_DIR/scripts/watch.py info WS` (JSON: what is already on for this project). Skip this step entirely when `daily_offered` is set: the user was already asked. Otherwise offer only what is off:
- `reminder: true` means the reminder is already on (the user turned it on while installing, or earlier): do not offer it again, and mention it in step 6 as on, with its `cadence` and `mode`. When `registered` is false but a hook is on (`hooks`), register the project (`W register WS` in `daily.md` "Settings"; it only writes the registry inside `.ferris`) and say so, without asking.
- `context_skill: true`: do not offer the project context.
- Without a source folder (`has_source_folder: false`), offer only the project context.
- When nothing is left to offer, skip to step 6.

Explain briefly what is left and ask together:
1. **New-document reminder**: at the start of each session, a small script checks the source folder; when there are new documents and an update is due, the assistant asks whether to update. In Claude Code it also notices documents that land during a session and mentions them with the next message. Needs: hook entries in the agent's settings file (`daily.md` "Settings", "Reminder on") and one line in the project registry. Nothing runs in the background and nothing is sent anywhere.
2. **How often to update** (`cadence`): `on_new` (whenever new documents are there), `daily` (once a day, documents up to yesterday), or `weekly` (once a week, the finished week; default). Shorter is fresher; weekly keeps a week's back-and-forth in one batch, which gives better judgements.
3. **Ask or just do it** (`mode`): `ask` (default: the assistant asks first) or `auto` (it updates in the background and tells the user in one line).
4. **Project context**: a small skill named `<slug>-state`, installed for Claude Code (and linked for Cursor when present), that gives any conversation mentioning the project the current state summary (`WS/current/brief.md`) and how to dig into the evidence. It is read-only.

Before writing anything, list exactly what will change: the settings file path (it sits next to `.ferris`, in the folder the agent is opened in, not inside `WS`) and the names of the hooks that go into it, the registry file, and the skill folder with its Cursor link (the link is made whether or not Cursor is installed). The hook command line is printed by the command only after it has written, so do not promise to show it first. After the user agrees, follow `daily.md` "Settings" for each item they chose and report what was written. If they decline, nothing else is written; they can switch it on later. Either way, set `daily_offered` in `config.json` to today's date so later sessions do not ask again.

## 6. Close
Reply with: where the workspace and the report are, what is switched on, and how to get an update ("update <project name>" in any session). Then `daily.md` "Ferris tip".
