# Ingest a folder

Turn the raw files of a folder (emails, chat exports, meeting notes, transcripts, documents) into units in the format of `input.md`, then add them to the workspace. Nothing here calls the state engine, and nothing may be invented: every unit's text comes from the source file.

## 1. Run the script
```
python3 scripts/ingest_folder.py <WS> --out <WS>/scratch/candidates.jsonl [--source <dir>] [--all]
```
- Sources come from `WS/config.json` `sources` (type `folder`), or `--source` for a one-off folder.
- By default only files that are new or changed against `WS/sources.manifest.json` are read. Use `--all` for the first ingest of a folder or when the user asks to re-read everything. The script never writes the manifest; it is updated by the update step after the units are accepted.
- It prints a JSON report: `files`, `units`, `unchanged`, `needs_review`, `needs_text`, `skipped`.

What the script does by type:
| files | result |
|---|---|
| `.eml`, `.mbox` | one unit per message; author from `From`, time from `Date` (as written in the header), filename = subject, provider `email`, quoted reply history removed, one `doc_id` per thread |
| Slack export `.json` | one unit per message, provider `slack`, filename = channel |
| `.vtt`, `.srt` | one unit per file, author null, provider `transcript`, time cues and numbers removed; date taken from the filename |
| `.md`, `.txt` | one unit per file, or one per message when the file is a thread export (`## Name - date` headings, or repeated `From:`/`Date:` blocks). A long document (over about 12,000 characters) becomes one unit per `## ` section (very long sections again per `### `), all with the file's `doc_id`, date and author; `filename` is `<file> § <heading>`. Header lines or front matter `Date:`, `Author:`/`From:`, `Source:` are used only when they are the first lines of the file (a title line above them hides them, and the author then shows up as missing); otherwise a `YYYY-MM-DD` in the filename. Meeting-style files (meeting, call, sync or notes in the name or first heading) get no author |
| `.pdf`, `.docx`, `.pptx`, `.xlsx` | listed in `needs_text`; the script does not read them |
| anything else | listed in `skipped` with a reason |

## 2. Fill `needs_review`
Each entry is one file: its `path`, the `missing` fields (`event_time`, `author`) and `unit_ids` (all sections of a split document; `unit_id` is the first). Find the value once and write it into every listed unit.
- Look in the unit's text for the date (a header, a "Date:" line, "on 12 May", a signature) or the writer (sign-off, "From" line). Write the value into the unit in the candidates file with the exact format of `input.md`.
- If the date is relative or a year is missing ("last Tuesday"), use other dates in the same file or the file's neighbours only when that is unambiguous.
- If it cannot be determined from the files, ask the user once for all such units together (list file names). Do not guess a date and do not leave `event_time` empty: a unit without a date cannot be added.
- A null author is correct for transcripts, AI meeting notes and plain documents; only fill it when the text names the writer.

## 3. Handle `needs_text`
For each file, extract its text with whatever reading tool is available (PDF, Word, slides, spreadsheet skill or reader). Then cut units by the same rules as above:
- a document or deck: one unit per file, or one per section when it has clearly dated parts (for example meeting minutes with several dated entries); split a very long document into parts at headings, each part keeping the file as `doc_id`;
- a spreadsheet: one unit per sheet, text as plain rows, only the rows that carry project information;
- `source_path` = the file's absolute path (`mark` uses it to know the file was added), `provider` = `drive` (or `files`), `filename` = the title, `author` null unless the document names its writer, `event_time` from the document date or the file name; ask the user if there is none.
- Give each unit a `unit_id` of the form `<slug>-<10 hex>` that is unique in the workspace; use the first 10 hex of the sha1 of `<abs path>#<part index>`.
If a file cannot be read (scanned image, password), say so in the inventory instead of dropping it silently.

## 4. Check the candidates
- Every unit has `unit_id`, `doc_id`, `provider`, `filename`, `event_time`, `text`, and `author` (possibly null).
- Remove units whose text is only a signature, an auto-reply or a calendar notice; list them as dropped in the inventory. When that drops every unit of a file, `mark` will list the file as not marked: record it with `--force <file>`, giving the name as `mark` listed it (`daily.md`, Update, step 7). The same goes for any other file `mark` lists as not marked: a file in `skipped` (a `.json` that is not a Slack export, a file with no text) or one that cannot be read. Files of types the reader never reads, such as images, are recorded without `--force`.
- Units written by the tracking tool itself stay in, but make sure their author or provider is covered by `excluded_authors` / `excluded_provider_prefixes` in `config.json` (see `input.md`).
- Extra keys such as `source_path` are allowed and ignored.

## 5. Show the inventory and confirm
Show the user a short summary, no more than this:
- counts: files read (by type), units produced, files skipped or unreadable, files unchanged;
- date range of the units (earliest to latest `event_time`);
- the people found, one row each: name, your guess of side (`vendor` / `client` / `other`) and role, and the evidence (email signature, only appears in chat, named in notes). Mark a guess as `?` when the evidence is thin; do not infer a side from an email domain alone unless the project parties make it clear. Compare with `config.json` `people`: only list new or uncertain people, and name tool or bot authors that will be excluded;
- anything you filled in yourself (dates, authors) and anything still unresolved.
In an update, show this summary only when the inventory lists new or uncertain people or something you filled in yourself; otherwise report the unit count in one line. When you show it, ask the user to correct the people table. Record the confirmed people in `config.json` `people` (name, side, role).

## 6. Add the units
Write the final units to `WS/scratch/new_units.jsonl` and run `python3 SKILL_DIR/scripts/engine.py setup WS/scratch/new_units.jsonl WS` (`workspace.md`, "Add units"). Report its summary (units added, batches created) and the unit count that was skipped as already present. Then continue with the normal update flow.
