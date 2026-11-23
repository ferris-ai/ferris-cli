# One batch, step by step (flat:v4)

The state is a store of free-text units (`WS/store/<B>.json`, the whole store after batch B). You write the changes; `scripts/engine.py` does everything that follows fixed rules. The prompts are the ones of the `flat:v4` method, word for word: write them as the prompt says.

| Step | Command | What happens |
|---|---|---|
| STAGE | `engine.py stage WS B` | Writes `WS/workdir/write_prompt.md`: the current state (every active unit with its id) and the batch's documents. Units written by the tracking tool itself are left out. The prompt header says "documents up to <date>": that is the date of the newest document in the batch, which can be earlier than the batch's `through` date (the cut date). |
| WRITE | you | Follow the prompt. Write the JSON reply to `WS/reply/B.json`: `{"add": [...], "supersede": [...]}`. |
| CHECK | `engine.py check WS B` | Dry run. Lists every problem: reply shape, unknown top-level keys, quotes that are not verbatim in a document of this batch (`quote_not_found`), unknown document or unit ids, bad priority, missing evidence. Changes nothing. |
| APPLY | `engine.py apply WS B` | Adds the units, replaces the superseded ones, flags the units that depend on a replaced one (`needs_recheck`). When some are flagged it writes `WS/workdir/recompute_prompt.md`. |
| RECOMPUTE | you, then `engine.py recompute WS B` | Only when APPLY says `"next": "recompute"`. Follow the prompt, write `WS/reply/B.recompute.<n>.json` (`<n>` = the round APPLY or RECOMPUTE printed): `{"recompute": [{"unit_id", "action": "update"|"confirm", ...}]}`. At most 3 rounds; flagged units left after that stay flagged and are shown as such. |
| FOLD | `engine.py fold WS B` | Saves `WS/store/B.json` and one line in `WS/history.jsonl`. The batch is done only when `store/B.json` exists. |

## Writing the reply
- Read only the prompt (it holds the state and the documents). Copy quotes exactly from the documents; do not paraphrase inside `quote`.
- Reply with the JSON object only, written to the file with a file-writing tool (never a program that generates it).
- A dependency (`derived_from`, `coupled_with`) names an existing unit id (`U-....`) or the `key` of a unit added in the same reply.
- Run CHECK. Fix every problem you can fix honestly from the documents (a quote you mistyped, a missing evidence quote), drop what you cannot support, check again. At most two fix rounds; APPLY then takes the reply as it is and records what it rejected in `WS/workdir/applied.json` and `history.jsonl`.
- A correction from the user arrives as a unit with provider `pm-note` and is one more document of its batch: units that cite only such notes are marked `source_type: "user"`.

## What APPLY does with a reply
- Adds in order, then supersedes. An op that fails (bad quote, unknown id) is rejected alone; the rest applies.
- A dependency on a replaced unit is moved to its live successor; one on a unit that does not exist or was replaced by nothing is dropped, and says so in the warnings.
- Replacing a unit moves the pointers of its dependents to the replacement; the dependents of the replaced unit are flagged (one hop, both edge kinds).
