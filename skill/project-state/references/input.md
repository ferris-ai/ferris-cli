# Input format

## units.jsonl
One JSON object per line, one message-level piece of evidence (one email in a thread, one chat message, one meeting's notes or transcript, one document part).

| field | type | meaning |
|---|---|---|
| `unit_id` | string, unique | id used in evidence citations |
| `doc_id` | string | the source document the unit was cut from (several units can share it) |
| `provider` | string | source system, e.g. `gmail`, `slack`, `glyphic` (meeting notes), `drive` |
| `filename` | string | source document title or channel name |
| `event_time` | string `YYYY-MM-DD` or `YYYY-MM-DD HH:MM` | when the message was written or the meeting held |
| `author` | string or null | the writer's name as shown in the source; null for transcripts, AI notes and files |
| `text` | string | the unit's full text; quotes are checked against it |

Other keys are allowed and ignored. Example: `assets/example/units.jsonl`.

## batches.json (optional)
A JSON list, in processing order:

| field | meaning |
|---|---|
| `id` | batch id, `B1`, `B2`, ... |
| `after` | `through` of the previous batch (`0000-00-00` for the first) |
| `through` | last date (`YYYY-MM-DD`) this batch covers; every unit in it has `event_time` on or before this date |
| `unit_ids` | the unit ids of this batch, each unit in exactly one batch |

Other keys are ignored. Without a `batches.json`, the workspace setup cuts one batch per calendar week (Monday to Sunday) of `event_time`, see `workspace.md`. Example: `assets/example/batches.json`.

## Units that are never evidence
Units written by the tracking tool itself are summaries of other units, not evidence. `config.json` lists them (`excluded_authors`, compared case-insensitively after collapsing whitespace, and `excluded_provider_prefixes`, matched case-insensitively at the start of `provider`). They stay in the input, but they are not shown to the writer, so nothing can cite them.
