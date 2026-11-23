"""Write and recompute prompts of flat:v4, rendered for the agent that writes the state.

The templates are references/write_prompt.md and references/recompute_prompt.md. Python 3 stdlib only.
"""
from pathlib import Path

import profiles as PR

REFS = Path(__file__).resolve().parent.parent / "references"

# flat:v4 = task "delivery" + supersede + keep_names; kind False, plain recompute False
# The domain wording (intro, role, task context, what to add, supersede examples) comes from the workspace's profile (profiles.py,
# profiles/<name>.json). The profile "delivery" holds the engine's flat:v4 wording byte for byte; tests/test_parity.py checks it.
OUT_ADD = '{"key": "k1 (optional, for same-output dependencies)", "content": "string", "priority": "hard"|"soft", "source_type": "document", "type": "string (free-form category label)", "scope": "string (what this applies to)", "coupled_with": ["unit_id_a","unit_id_b"], "derived_from": ["unit_id_c"], "triggers": [{"kind":"date_passed","payload":{"date":"2026-06-15"},"description":"tax deadline"}], "evidence": [{"unit_id": "<document id>", "quote": "<verbatim>"}]}'
OUT_REP = '{"content": "string (new version)", "priority": "hard"|"soft", "source_type": "document", "type": "string", "scope": "string", "coupled_with": [], "derived_from": [], "triggers": []}'
NAME_RULE_WRITE = """
- Keep names with identifiers: when a document refers to something by an identifier (a code, number or id) and also gives its name or title, write both in the unit, e.g. "PRJ-12 (Harbor Migration)". When one unit lists several such items, keep each item's name next to its identifier."""
NAME_RULE_RECOMPUTE = """
 - Keep every name or title that the flagged unit gives next to an identifier."""


def fill(template, **kw):
    """Sequential {{NAME}} replacement (engine text.fill)."""
    for k, v in kw.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template


def body_of(u):
    t, s = u.get("text") or "", u.get("filename") or ""
    return t[len(s) + 1:] if s and t.startswith(s + "\n") else t


def as_message(u):
    return {"id": u["unit_id"], "ts": u.get("event_time"), "channel": u.get("provider"), "author": u.get("author"),
            "to": u.get("to"), "subject": u.get("filename") or None, "body": body_of(u)}


def fmt_msg(m):
    head = f"[{m['id']} · {m['ts']} · {m['channel']} · {m['author']}" + (f" → {m['to']}" if m.get("to") else "") + "]"
    return f"{head} {m.get('subject') or ''}".rstrip() + f"\n{m.get('body') or ''}"


def fmt_msgs(msgs):
    return "\n\n".join(fmt_msg(m) for m in msgs)


def task_context(P, overlay):
    g = PR.guidance_block(P, overlay)
    return P["task_context"] + ("\n\n" + g if g else "")


def write_prompt(store, units, batch_no, profile=None, overlay=None):
    """The flat:v4 write prompt for batch number `batch_no` over `units` (unit dicts of input/units.jsonl), against `store`.
    `profile`: a resolved profile (profiles.load; default delivery); `overlay`: the project guidance text or None."""
    P = profile or PR.load()
    msgs = [as_message(u) for u in units]
    out = ('{\n "add": [ ' + OUT_ADD + ' ]'
           ',\n "supersede": [ {"unit_id": "string (ID of existing unit to supersede)", "reason": "string (why it\'s being superseded)", '
           '"evidence": [{"unit_id": "<document id>", "quote": "<verbatim>"}], "replacement": ' + OUT_REP + ' } ]\n}\n'
           'If no updates are needed, return: {"add": [], "supersede": []}')
    return fill((REFS / "write_prompt.md").read_text(encoding="utf-8"),
                INTRO=P["intro"], TASK_CONTEXT=task_context(P, overlay), ADD_SOURCES=P["add_sources"],
                CURRENT_STATE=store.view(), BATCH=batch_no, THROUGH=max(str(m.get("ts") or "")[:10] for m in msgs),
                BATCH_DOCS=fmt_msgs(msgs), SUPERSEDE_SECTION=P["supersede_section"],
                SUPERSEDE_EVIDENCE=" and every supersede", OUTPUT_FORMAT=out, NAME_RULE=NAME_RULE_WRITE)


def recompute_prompt(store, profile=None):
    P = profile or PR.load()
    flagged = "\n".join(store._line(u) + f"\n  -> trigger: {u['recheck_reason']}" for u in store.flagged())
    return fill((REFS / "recompute_prompt.md").read_text(encoding="utf-8"), ROLE=P["role"], EXTRA_RULES=NAME_RULE_RECOMPUTE,
                CURRENT_STATE=store.view(), FLAGGED=flagged)
