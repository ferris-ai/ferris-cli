#!/usr/bin/env python3
"""Flat state store after StateMem (arXiv 2608.19652, sections 5.1-5.3, appendix E), python3 stdlib only, zero LLM.

    st = FlatStore()
    acc, rej, changed = st.apply_write(out, batch_units, batch_id, allow_supersede=True)   # out = {"add": [...], "supersede": [...]}
    st.recheck(changed)                       # deterministic Rechecker: direct dependents of changed units -> needs_recheck
    acc, rej, changed = st.apply_recompute(out, batch_id)   # out = {"recompute": [{"unit_id", "action": update|confirm, ...}]}
    st.view()        compact view for the write prompt (active + needs_recheck units)
    st.render(T)     answer-time block (E.3 layout: BINDING / PREFERENCES / NEEDS RECHECK), with source message ids in brackets
    st.render(T, hide_ids=True, history=True)   the `flat` arm's block: no unit ids, plus HISTORY (superseded values with dates)

Unit: {id, content, priority hard|soft, type, scope, source_type, evidence [{unit_id, quote}], derived_from [ids], coupled_with [ids],
triggers [...], status active|superseded|needs_recheck, batch, event_time, superseded_by, recheck_reason, origin write|recompute,
supersede_kind value|ended|progress, supersede_evidence, valid_from, valid_to, recheck_by [changed ids], trigger_evidence}.
The last six fields (plan 2026-11-11-flat-full-default-plan.html claims 3-4) are recorded for every arm; only render(hide_ids,
history) and recheck(coupled="no_progress") read them, so the frozen flat_f0..f3 render and flag exactly as before.
Deviations from the paper (plan section 1): every unit carries verbatim evidence checked here; one write per batch, so an add may
carry a local `key` that other adds of the SAME output can name in derived_from / coupled_with; a supersede also needs evidence.
When a unit is superseded with a replacement, dependents' dep pointers are moved to the replacement (the paper does not say), so a
later change of the replacement still reaches them. The Rechecker follows both edge kinds, one hop, as the paper describes.
`apply_write(..., follow_superseded=True)` (only `flat:v2` sets it) resolves a dependency that names a superseded unit to its live
successor, drops one that has none or does not exist, and notes it in `dep_notes`; without it such an op is rejected (unknown_dep).
`write_reply_problem(reply, out)` says whether a write reply is unusable as a whole (flat:v2 carries such a batch to the next write).
"""
import json, re

PRIORITIES = ("hard", "soft")
SOURCE_TYPES = ("document", "action", "tool_result", "user", "recompute")
QUOTES = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "ʼ": "'",
                        "“": '"', "”": '"', "„": '"', "‟": '"'})


def qnorm(t):
    """Same quote comparison as project-state engine.py: whitespace collapsed, curly quotes made straight."""
    return re.sub(r"\s+", " ", str(t or "")).strip().translate(QUOTES)


UNIT_ID_RE = re.compile(r"\bU-\d{3,}\b")
DEPS_RE = re.compile(r"\s*\((?:derived_from|coupled_with)\b[^)]*\)")
SUPERSEDE_KINDS = ("value", "ended", "progress")


def strip_unit_ids(text):
    """Content without internal unit ids: drops '(derived_from: U-..)' lists, bare U-ids and the empty brackets they leave."""
    t = DEPS_RE.sub("", str(text or ""))
    t = UNIT_ID_RE.sub("", t)
    t = re.sub(r"[(\[]\s*[,;\s]*[)\]]", "", t)
    return re.sub(r"[ \t]{2,}", " ", t).replace(" ,", ",").replace(" .", ".").replace(" )", ")").replace("( ", "(").strip()


def date_of(t):
    return str(t or "")[:10] or None


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


class FlatStore:
    def __init__(self):
        self.units, self.order, self.n = {}, [], 0
        self.log = []  # every op applied, in order (for inspection)
        self.follow_superseded = False   # set per apply_write call (flat:v2); not stored by dump()
        self.dep_notes = []              # dependency redirects / drops of the last apply_write with follow_superseded

    # ---------- reading ----------
    def live(self):
        return [self.units[i] for i in self.order if self.units[i]["status"] != "superseded"]

    def is_live(self, uid):
        return uid in self.units and self.units[uid]["status"] != "superseded"

    def flagged(self):
        return [u for u in self.live() if u["status"] == "needs_recheck"]

    @staticmethod
    def _line(u, with_src=True):
        deps = []
        if u["derived_from"]:
            deps.append("derived_from: " + ", ".join(u["derived_from"]))
        if u["coupled_with"]:
            deps.append("coupled_with: " + ", ".join(u["coupled_with"]))
        src = ""
        if with_src:
            src = " · " + ", ".join(sorted({e["unit_id"] for e in u["evidence"]}))
        head = f"[{u['id']} · {u['priority']} · {u['type'] or '-'} · scope: {u['scope'] or '-'} · {u['event_time'] or '-'}{src}]"
        return f"{head} {u['content']}" + (f" ({'; '.join(deps)})" if deps else "")

    @staticmethod
    def _sources(u, extra=()):
        return ", ".join(sorted({e["unit_id"] for e in list(u["evidence"]) + list(u.get("trigger_evidence") or []) + list(extra)}))

    @classmethod
    def _answer_line(cls, u):
        """Answer-time line for the `flat` arm: no unit ids, no dep ids; date = when the value took effect; source event ids last."""
        date = u.get("valid_from") or u["event_time"] or "-"
        return f"[{u['type'] or '-'} · {date}] {strip_unit_ids(u['content'])} ({cls._sources(u)})"

    @classmethod
    def _history_line(cls, u):
        start = u.get("valid_from") or u["event_time"] or "?"
        return f"[{u['type'] or '-'} · {start} → {u.get('valid_to') or '?'}] {strip_unit_ids(u['content'])} ({cls._sources(u)})"

    def _trigger_text(self, u):
        """Why a unit is flagged, without unit ids: the reason and date of each change, with its source events."""
        parts = []
        for cid in u.get("recheck_by") or []:
            c = self.units[cid]
            new = self.units.get(c.get("superseded_by") or "")
            src = ", ".join(sorted({e["unit_id"] for e in (c.get("supersede_evidence") or [])}))
            what = strip_unit_ids(c.get("supersede_reason") or "") or "an input changed"
            now = f"; now: {strip_unit_ids(new['content'])}" if new else "; it no longer holds"
            parts.append(f"{c.get('valid_to') or '?'}: {what}{now}" + (f" ({src})" if src else ""))
        return " | ".join(parts) or strip_unit_ids(u.get("recheck_reason") or "")

    def history(self):
        """Superseded units of kind 'value' (a write that replaced a value, or a recompute update), oldest first."""
        return [self.units[i] for i in self.order if self.units[i]["status"] == "superseded" and self.units[i].get("supersede_kind") == "value"]

    def view(self):
        """Compact rendering of every active unit for the write / recompute prompt."""
        lines = [self._line(u) + ("  !NEEDS RECHECK: " + u["recheck_reason"] if u["status"] == "needs_recheck" else "") for u in self.live()]
        return "\n".join(lines) if lines else "(no units yet)"

    def render(self, T, hide_ids=False, history=False):
        """Answer-time block, appendix E.3 layout. Superseded units are withheld, unless history=True: then superseded values (kind
        'value') follow under HISTORY with the dates they held. hide_ids=True prints no unit ids (only source event ids)."""
        line = self._answer_line if hide_ids else self._line
        hard = [u for u in self.live() if u["priority"] == "hard" and u["status"] != "needs_recheck"]
        soft = [u for u in self.live() if u["priority"] == "soft" and u["status"] != "needs_recheck"]
        flag = self.flagged()
        out = [f"today: {T}", "", "## BINDING — THIS PROJECT"] + ["- " + line(u) for u in hard]
        out += ["", "## PREFERENCES (yield if necessary)"] + ["- " + line(u) for u in soft]
        out += ["", "## ! NEEDS RECHECK — may be stale due to recent events"]
        for u in flag:
            out += ["- " + line(u), f"  -> trigger: {self._trigger_text(u) if hide_ids else u['recheck_reason']}"]
        if history:
            out += ["", "## HISTORY — superseded values, with the dates they held"]
            out += ["- " + (self._history_line(u) if hide_ids else self._line(u)) for u in self.history()]
        return "\n".join(out)

    def source_ids(self, history=False):
        """Every source event id the rendered block can show (render with the same `history`)."""
        units = self.live() + (self.history() if history else [])
        ids = {e["unit_id"] for u in units for e in list(u["evidence"]) + list(u.get("trigger_evidence") or [])}
        for u in self.flagged():
            for cid in u.get("recheck_by") or []:
                ids |= {e["unit_id"] for e in (self.units[cid].get("supersede_evidence") or [])}
        return sorted(ids)

    # ---------- validation ----------
    def _evidence(self, ev, batch_units):
        ev = as_list(ev)
        if not ev:
            return None, "missing_evidence"
        out = []
        for x in ev:
            if not isinstance(x, dict) or not x.get("unit_id") or not x.get("quote"):
                return None, "bad_evidence"
            if x["unit_id"] not in batch_units:
                return None, f"unknown_unit {x['unit_id']}"
            if qnorm(x["quote"]) not in qnorm(batch_units[x["unit_id"]]["text"]):
                return None, f"quote_not_found {x['unit_id']}: {qnorm(x['quote'])[:60]}"
            out.append({"unit_id": x["unit_id"], "quote": x["quote"]})
        return out, None

    def live_successor(self, uid):
        """(live unit id, None) for a live unit or the end of its superseded_by chain; (None, reason) when the id does not exist or
        the chain ends in a unit replaced by nothing (or loops)."""
        if uid not in self.units:
            return None, "unknown_id"
        seen = set()
        while uid and uid not in seen and uid in self.units:
            if self.units[uid]["status"] != "superseded":
                return uid, None
            seen.add(uid)
            uid = self.units[uid].get("superseded_by")
        return None, "no_live_successor"

    def _deps(self, ids, keys):
        out = []
        for d in as_list(ids):
            if self.follow_superseded:
                if not isinstance(d, str):
                    self.dep_notes.append({"dep": str(d)[:80], "action": "dropped", "why": "not_an_id"})
                    continue
                d = keys.get(d, d)
                live, why = self.live_successor(d)
                if live is None:
                    self.dep_notes.append({"dep": d, "action": "dropped", "why": why})
                    continue
                if live != d:
                    self.dep_notes.append({"dep": d, "action": "redirected", "to": live})
                d = live
            else:
                d = keys.get(d, d)
                if not self.is_live(d):
                    return None, f"unknown_dep {d}"
            if d not in out:
                out.append(d)
        return out, None

    def _unit(self, item, batch_units, batch_id, keys, origin="write", evidence=None):
        if not isinstance(item, dict):
            return None, "not_an_object"
        content = str(item.get("content") or "").strip()
        if not content:
            return None, "missing_content"
        pr = item.get("priority")
        if pr not in PRIORITIES:
            return None, f"bad_priority {pr!r}"
        if evidence is None:
            evidence, bad = self._evidence(item.get("evidence"), batch_units)
            if bad:
                return None, bad
        df, bad = self._deps(item.get("derived_from"), keys)
        if bad:
            return None, bad
        cw, bad = self._deps(item.get("coupled_with"), keys)
        if bad:
            return None, bad
        st = item.get("source_type") if item.get("source_type") in SOURCE_TYPES else "document"
        times = [str(batch_units[e["unit_id"]].get("event_time") or "")[:10] for e in evidence if e["unit_id"] in batch_units]
        return {"content": content, "priority": pr, "type": str(item.get("type") or ""), "scope": str(item.get("scope") or ""),
                "source_type": st, "evidence": evidence, "derived_from": df, "coupled_with": cw,
                "triggers": [t for t in as_list(item.get("triggers")) if isinstance(t, dict)],
                "status": "active", "batch": batch_id, "event_time": max(times) if times else item.get("event_time"),
                "superseded_by": None, "recheck_reason": None, "origin": origin}, None

    def _new_id(self):
        self.n += 1
        return f"U-{self.n:04d}"

    def _store(self, u):
        u["id"] = self._new_id()
        self.units[u["id"]] = u
        self.order.append(u["id"])
        return u

    def _supersede(self, old_id, new_id, reason, kind=None, at=None, evidence=None):
        """kind: value | ended | progress (None = the writer gave none: 'value' with a replacement, else 'ended').
        at: the date of the superseding evidence = the end of the old unit's validity."""
        old = self.units[old_id]
        old["status"], old["superseded_by"] = "superseded", new_id
        old["supersede_reason"] = reason
        old["supersede_kind"] = kind if kind in SUPERSEDE_KINDS else ("value" if new_id else "ended")
        old["supersede_kind_given"] = kind in SUPERSEDE_KINDS
        old["valid_to"], old["supersede_evidence"] = at, list(evidence or [])
        if new_id:  # move dependents' pointers to the replacement
            for u in self.live():
                for k in ("derived_from", "coupled_with"):
                    if old_id in u[k]:
                        u[k] = [new_id if d == old_id else d for d in u[k]]
                        u[k] = list(dict.fromkeys(u[k]))

    # ---------- write ----------
    def apply_write(self, out, batch_units, batch_id, allow_supersede=True, follow_superseded=False):
        """Apply one write output. Adds first (in order; a `key` lets later adds of this output depend on it), then supersedes.
        Returns (accepted ops, rejects, changed unit ids = units that just became superseded).
        follow_superseded: a dependency (derived_from / coupled_with) on a superseded unit is moved to its live successor, and one
        on an unknown id or a unit replaced by nothing is dropped; every such step is noted in self.dep_notes. The op is accepted."""
        acc, rej, changed, keys = [], [], [], {}
        self.follow_superseded, self.dep_notes = follow_superseded, []
        adds = as_list((out or {}).get("add"))
        sups = as_list((out or {}).get("supersede"))
        for item in adds:
            u, bad = self._unit(item, batch_units, batch_id, keys)
            if bad:
                rej.append({"op": "add", "item": item, "reason": bad})
                continue
            self._store(u)
            if isinstance(item, dict) and item.get("key"):
                keys[str(item["key"])] = u["id"]
            acc.append({"op": "add", "id": u["id"]})
        for item in sups:
            if not allow_supersede:
                rej.append({"op": "supersede", "item": item, "reason": "supersede_disabled"})
                continue
            if not isinstance(item, dict) or not self.is_live(keys.get(item.get("unit_id"), item.get("unit_id"))):
                rej.append({"op": "supersede", "item": item, "reason": f"unknown_unit_id {item.get('unit_id') if isinstance(item, dict) else item}"})
                continue
            target = keys.get(item["unit_id"], item["unit_id"])
            rep = item.get("replacement")
            ev, bad = self._evidence(item.get("evidence") or (rep or {}).get("evidence"), batch_units)
            if bad:
                rej.append({"op": "supersede", "item": item, "reason": bad})
                continue
            new = None
            if rep:
                new, bad = self._unit(rep, batch_units, batch_id, keys, evidence=None if rep.get("evidence") else ev)
                if bad:
                    rej.append({"op": "supersede", "item": item, "reason": "replacement " + bad})
                    continue
                if target in new["derived_from"] or target in new["coupled_with"]:
                    new["derived_from"] = [d for d in new["derived_from"] if d != target]
                    new["coupled_with"] = [d for d in new["coupled_with"] if d != target]
                self._store(new)
            times = [date_of(batch_units[e["unit_id"]].get("event_time")) for e in ev if e["unit_id"] in batch_units]
            at = max([t for t in times if t] or [None]) if any(times) else (new or {}).get("event_time")
            self._supersede(target, new["id"] if new else None, str(item.get("reason") or ""), kind=item.get("kind"), at=at, evidence=ev)
            changed.append(target)
            acc.append({"op": "supersede", "id": target, "replacement": new["id"] if new else None})
        self.log.append({"batch": batch_id, "phase": "write", "accepted": acc, "rejected": rej,
                         **({"dep_notes": list(self.dep_notes)} if self.dep_notes else {})})
        self.follow_superseded = False
        return acc, rej, changed

    # ---------- Rechecker ----------
    def recheck(self, changed, coupled="always"):
        """Direct dependents (derived_from or coupled_with, one hop) of units whose status just changed -> needs_recheck.
        Pointers were already moved to replacements, so match both the old id and its replacement.
        coupled: "always" (paper, flat_f2/f3) follows coupled_with on every change; "no_progress" (flat) skips coupled_with when the
        change is of kind 'progress' (completed, verified: the thing stays valid), and still follows derived_from."""
        if coupled not in ("always", "no_progress"):
            raise ValueError(f"coupled must be always | no_progress, not {coupled!r}")
        flagged = []
        for cid in changed:
            c = self.units[cid]
            ids = {cid} | ({c["superseded_by"]} if c.get("superseded_by") else set())
            why = f"{cid} superseded" + (f" by {c['superseded_by']}" if c.get("superseded_by") else "") + (f": {c.get('supersede_reason')}" if c.get("supersede_reason") else "")
            use_coupled = coupled == "always" or c.get("supersede_kind") != "progress"
            for u in self.live():
                if u["id"] in ids:
                    continue
                if ids & (set(u["derived_from"]) | (set(u["coupled_with"]) if use_coupled else set())):
                    if u["status"] != "needs_recheck":
                        flagged.append(u["id"])
                    u["status"] = "needs_recheck"
                    u["recheck_reason"] = (u["recheck_reason"] + "; " if u["recheck_reason"] else "") + why
                    u["recheck_by"] = list(dict.fromkeys((u.get("recheck_by") or []) + [cid]))
        self.log.append({"phase": "recheck", "changed": list(changed), "flagged": flagged, "coupled": coupled})
        return flagged

    # ---------- recompute ----------
    def apply_recompute(self, out, batch_id):
        """update -> the flagged unit is superseded by a recomputed copy (same priority/type/scope/deps, the old unit's evidence,
        origin recompute); confirm -> flag cleared. Unknown or unflagged ids are rejected. Returns (accepted, rejects, changed)."""
        acc, rej, changed = [], [], []
        for item in as_list((out or {}).get("recompute")):
            uid = item.get("unit_id") if isinstance(item, dict) else None
            if not uid or not self.is_live(uid) or self.units[uid]["status"] != "needs_recheck":
                rej.append({"op": "recompute", "item": item, "reason": f"not_flagged {uid}"})
                continue
            u = self.units[uid]
            act = item.get("action")
            if act == "confirm":
                u["status"], u["recheck_reason"], u["recheck_by"] = "active", None, []
                acc.append({"op": "confirm", "id": uid})
            elif act == "update":
                content = str(item.get("content") or "").strip()
                if not content:
                    rej.append({"op": "recompute", "item": item, "reason": "missing_content"})
                    continue
                # the copy keeps the old unit's evidence and event_time (what flat_f3 renders); it also records the change that
                # triggered it: trigger_evidence = the superseding evidence of each changed input, valid_from = the latest such date
                trig = [self.units[c] for c in (u.get("recheck_by") or [])]
                tev = list({(e["unit_id"], e["quote"]): e for c in trig for e in (c.get("supersede_evidence") or [])}.values())
                at = max([c["valid_to"] for c in trig if c.get("valid_to")] or [None])
                new = {**{k: (list(v) if isinstance(v, list) else v) for k, v in u.items() if k != "id"},
                       "content": content, "status": "active", "batch": batch_id, "superseded_by": None,
                       "recheck_reason": None, "recheck_by": [], "origin": "recompute", "source_type": "recompute",
                       "recompute_reason": str(item.get("reason") or ""), "trigger_evidence": tev, "valid_from": at}
                for k in ("supersede_kind", "supersede_kind_given", "valid_to", "supersede_evidence", "supersede_reason"):
                    new.pop(k, None)
                self._store(new)
                self._supersede(uid, new["id"], "recomputed: " + str(item.get("reason") or ""), kind="value", at=at, evidence=tev)
                changed.append(uid)
                acc.append({"op": "update", "id": uid, "replacement": new["id"]})
            else:
                rej.append({"op": "recompute", "item": item, "reason": f"bad_action {act!r}"})
        self.log.append({"batch": batch_id, "phase": "recompute", "accepted": acc, "rejected": rej})
        return acc, rej, changed

    def dump(self):
        return {"units": [self.units[i] for i in self.order], "log": self.log}

    @classmethod
    def load(cls, d):
        """The inverse of dump(): a store that continues with the same unit ids."""
        s = cls()
        for u in d.get("units") or []:
            s.units[u["id"]] = u
            s.order.append(u["id"])
        s.log = list(d.get("log") or [])
        s.n = max((int(i.split("-")[1]) for i in s.order), default=0)
        return s


def write_reply_problem(reply, out):
    """Why a write reply is unusable as a whole (flat:v2 carries its batch to the next write), or None when it is usable.
    `reply` = the model's text (None: the call failed), `out` = parse_json_object(reply). A usable reply is a JSON object with an
    "add" key (the output format always has one; `{"add": [], "supersede": []}` means nothing to record), or one whose only key is
    "supersede" (a supersede-only reply). Anything else that parses (`{"updates": [...]}`, `{"supersedes": [], "supersede": [...]}`,
    `{}`) is a format error, not "no updates"."""
    if reply is None:
        return "no_reply"
    if out is None:
        return "parse_failed"
    if "add" in out or set(out) == {"supersede"}:
        return None
    return "no_add_key: keys " + ",".join(sorted(map(str, out))[:6])


def parse_json_object(text):
    """First JSON object in a model reply (tolerates code fences and text around it). None if none parses."""
    t = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        v = json.loads(t)
        return v if isinstance(v, dict) else None
    except ValueError:
        pass
    i = t.find("{")
    while i != -1:
        depth, j, ins, esc = 0, i, False, False
        for j in range(i, len(t)):
            ch = t[j]
            if ins:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    ins = False
                continue
            if ch == '"':
                ins = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        v = json.loads(t[i:j + 1])
                        if isinstance(v, dict):
                            return v
                    except ValueError:
                        break
                    break
        i = t.find("{", i + 1)
    return None
