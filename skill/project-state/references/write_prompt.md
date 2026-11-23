{{INTRO}}
## Current project state (from prior batches):
{{CURRENT_STATE}}
## Latest batch (batch {{BATCH}}, documents up to {{THROUGH}}):
{{BATCH_DOCS}}
{{TASK_CONTEXT}}
## Your job:
Analyze the latest batch and determine what state updates are needed. You can:
1. ADD new units: New constraints, commitments, or facts that emerged in this batch.
{{ADD_SOURCES}}
{{SUPERSEDE_SECTION}}
3. TRACK PROGRESS: If a document lists multiple items to complete, track what has been completed and what remains.
  - Add a progress unit like: "Completed 2 of 5 reconciliations. Remaining: A, B, C"
  - Update this unit each batch as more items are completed.
  - Mark as "hard" priority so the agent doesn't forget remaining items.
4. LINK CASCADE / DERIVED dependencies: When a new unit's validity depends on another unit's status or value, surface that link.
  - Use 'coupled_with: [unit_id]' when this unit's validity depends on the STATUS of another unit (active/closed/superseded).
    Example: "Netflix autopay on Card 3" is coupled_with the unit declaring Card 3's status — if Card 3 closes, the Netflix routing must be re-evaluated.
  - Use 'derived_from: [unit_id]' when this unit's VALUE was computed from another unit's value.
    Example: "monthly savings $1,800" is derived_from the unit "take-home pay $6,000" via a savings-rate rule — if take-home changes, savings derives differently.
  - Use 'triggers: [{"kind": "date_passed", "payload": {"date": "YYYY-MM-DD"}}]' when this unit becomes operationally relevant on a specific date or condition.
    Allowed kinds: "date_passed", "entity_closed", "supersession_announced", "cascade".
  - A dependency may point to an existing unit id (U-....) or to the "key" of a unit you add in this same output (give that unit a "key" such as "k1").
5. NO CHANGE: If the batch contains no new state-relevant information, or if the state already captures everything.
## Rules:
- Only create units for information RELEVANT to future decisions
- Do NOT duplicate information already in an existing unit — check the current state first
- Priority "hard" for: verified facts, binding commitments, remaining obligations, things that constrain future actions
- Priority "soft" for: preferences, nice-to-haves, things that can yield
- Keep content concise — one clear sentence per unit, in the language of the documents{{NAME_RULE}}
- Type and scope are free-form labels you assign
- For coupled_with / derived_from / triggers: only include when there is a REAL dependency. Leave empty otherwise.
- Evidence: every added unit{{SUPERSEDE_EVIDENCE}} cites at least one document of THIS batch: "evidence": [{"unit_id": "<document id>", "quote": "<verbatim substring of that document's text>"}]. Copy the quote exactly; do not cite documents of earlier batches.
## Output format:
Return a JSON object:
{{OUTPUT_FORMAT}}
Output ONLY the JSON object, no other text.
