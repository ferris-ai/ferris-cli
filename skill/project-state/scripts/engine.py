#!/usr/bin/env python3
"""project-state engine (flat:v4): the deterministic steps of the skill (Python 3, standard library only).

The agent in the conversation writes the state; this script builds the prompts, checks and applies the replies, and keeps the store.
Usage (WS = workspace directory):
  engine.py setup <units.jsonl> <WS> [<batches.json>] [--today YYYY-MM-DD] [--profile NAME]   create a workspace / add units, cut batches
  engine.py status <WS>                  pending batches and the next step for each
  engine.py stage <WS> <B>               write WS/workdir/write_prompt.md for batch B
  engine.py check <WS> <B>               dry-run of the agent's reply for the current step; changes nothing
  engine.py apply <WS> <B>               apply reply/<B>.json (write); flags dependents; writes recompute_prompt.md when some are flagged
  engine.py recompute <WS> <B>           apply reply/<B>.recompute.<n>.json; at most 3 rounds
  engine.py fold <WS> <B>                save store/<B>.json and a line in history.jsonl; the batch is done
  engine.py profile list [<WS>]          the profiles (built-in and custom)
  engine.py profile show <name> [<WS>]   one profile's slots
  engine.py profile set <WS> <name>      use this profile from the next batch on
  engine.py profile overlay <WS> [--file F | --clear]   show, replace or remove the project guidance (WS/profile.md)
  engine.py profile status <WS>          the workspace's profile and overlay
Every command prints one JSON object. See references/workspace.md and references/batch.md.
"""
import copy, datetime, json, os, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import flatprompts as FP  # noqa: E402
import profiles as PR  # noqa: E402
from flatstore import FlatStore, parse_json_object, write_reply_problem  # noqa: E402

RECOMPUTE_ROUNDS = 3
WRITE_KEYS = ("add", "supersede")


def ws_text(t):
    return re.sub(r"\s+", " ", str(t or "")).strip()


def read_jsonl(p):
    p = Path(p)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def write_json(p, o):
    Path(p).write_text(json.dumps(o, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def die(msg):
    print(json.dumps({"error": msg}))
    sys.exit(2)


def out(o):
    print(json.dumps(o, ensure_ascii=False))


class Workspace:
    def __init__(self, d):
        self.dir = Path(d).expanduser().resolve()
        if not (self.dir / "config.json").exists():
            die(f"no config.json in {self.dir}")
        self.cfg = json.loads((self.dir / "config.json").read_text(encoding="utf-8"))
        if (self.dir / "events.jsonl").exists() and not (self.dir / "history.jsonl").exists():
            die("this workspace was made by the earlier event-log version of the skill and cannot be continued; start a new workspace")
        self._prof = None
        self.ulist = read_jsonl(self.dir / "input" / "units.jsonl")
        self.units = {u["unit_id"]: u for u in self.ulist}
        bp = self.dir / "input" / "batches.json"
        self.batches = json.loads(bp.read_text(encoding="utf-8")) if bp.exists() else []
        self.rank = {b["id"]: i for i, b in enumerate(self.batches)}
        self.bat = {b["id"]: b for b in self.batches}
        self.wd = self.dir / "workdir"

    def need_batch(self, B):
        if B not in self.rank:
            die(f"unknown batch {B}")

    def units_of(self, B):
        miss = [i for i in self.bat[B]["unit_ids"] if i not in self.units]
        if miss:
            die(f"{B}: unit ids not in units.jsonl: {miss[:5]}")
        return [self.units[i] for i in self.bat[B]["unit_ids"]]

    def excluded(self, u):
        ea = {ws_text(a).casefold() for a in self.cfg.get("excluded_authors", [])}
        ep = tuple(str(x).lower() for x in self.cfg.get("excluded_provider_prefixes", []))
        return bool((ep and str(u.get("provider") or "").lower().startswith(ep)) or ws_text(u.get("author")).casefold() in ea)

    def docs_of(self, B):
        """The documents shown to the writer: the batch's units without those written by the tracking tool itself."""
        return [u for u in self.units_of(B) if not self.excluded(u)]

    def store_path(self, B):
        return self.dir / "store" / f"{B}.json"

    def done(self, B):
        return self.store_path(B).exists()

    def prev_store(self, B):
        i = self.rank[B]
        if i == 0:
            return FlatStore()
        p = self.store_path(self.batches[i - 1]["id"])
        if not p.exists():
            die(f"previous store missing: {p}")
        return FlatStore.load(json.loads(p.read_text(encoding="utf-8")))

    def step(self):
        p = self.wd / "step.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def profile(self):
        """(resolved profile, overlay text or None) of this workspace; stops with an error when either is unusable."""
        if self._prof is None:
            try:
                self._prof = (PR.load(self.cfg.get("profile"), self.dir), PR.read_overlay(self.dir))
            except PR.ProfileError as e:
                die(str(e))
        return self._prof

    def profile_stats(self):
        p, ov = self.profile()
        return {"profile": p["name"], "overlay": PR.overlay_sha(ov)}

    def set_step(self, **kw):
        write_json(self.wd / "step.json", kw)

    def work_store(self):
        return FlatStore.load(json.loads((self.wd / "store.json").read_text(encoding="utf-8")))

    def save_work(self, st):
        write_json(self.wd / "store.json", st.dump())


def cmd_setup(argv):
    """setup <units.jsonl> <WS> [<batches.json>] [--today YYYY-MM-DD]: create or extend a workspace (references/workspace.md)."""
    args, today = list(argv), datetime.date.today()
    if "--today" in args:
        i = args.index("--today"); today = datetime.date.fromisoformat(args[i + 1]); del args[i:i + 2]
    prof = None
    if "--profile" in args:
        i = args.index("--profile"); prof = args[i + 1]; del args[i:i + 2]
        try:
            PR.load(prof, args[1])
        except PR.ProfileError as e:
            die(str(e))
    src, ws = args[0], args[1]
    given = args[2] if len(args) > 2 else None
    REQ = ("unit_id", "doc_id", "provider", "filename", "event_time", "author", "text")
    units = []
    for n, line in enumerate(open(src, encoding="utf-8"), 1):
        if not line.strip():
            continue
        u = json.loads(line)
        miss = [k for k in REQ if k not in u]
        if miss:
            sys.exit(f"line {n}: missing {miss}")
        datetime.date.fromisoformat(str(u["event_time"])[:10])
        units.append(u)
    inp = os.path.join(ws, "input")
    upath, bpath = os.path.join(inp, "units.jsonl"), os.path.join(inp, "batches.json")
    fresh = not os.path.exists(bpath)
    old = {}
    if not fresh:
        for line in open(upath, encoding="utf-8"):
            if line.strip():
                u = json.loads(line); old[u["unit_id"]] = u
    new, seen = [], set()
    for u in units:
        if u["unit_id"] in seen:
            sys.exit(f"duplicate unit_id {u['unit_id']} in {src}")
        seen.add(u["unit_id"])
        if u["unit_id"] in old:
            if old[u["unit_id"]] != u:
                sys.exit(f"unit_id {u['unit_id']} already exists with different content")
            continue
        new.append(u)
    if fresh:
        for d in ("input", "store", "reply", "workdir", "scratch", "reports"):
            os.makedirs(os.path.join(ws, d), exist_ok=True)
        cpath = os.path.join(ws, "config.json")
        cfg = json.load(open(cpath, encoding="utf-8")) if os.path.exists(cpath) else {}
        for k, v in (("created", datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")),
                     ("excluded_authors", ["ferris", "ferris ai", "ferris agent"]), ("excluded_provider_prefixes", ["ferris"]),
                     ("cadence", "weekly"), ("mode", "ask")):
            cfg.setdefault(k, v)
        if prof:
            cfg["profile"] = prof
        open(cpath, "w", encoding="utf-8").write(json.dumps(cfg, indent=1, ensure_ascii=False) + "\n")
        open(os.path.join(ws, "history.jsonl"), "w").close()
        batches = []
    else:
        batches = json.load(open(bpath, encoding="utf-8"))
    if given:
        if not fresh:
            sys.exit("a batches.json can only be given when the workspace is created")
        batches = json.load(open(given, encoding="utf-8"))
        ids = [i for b in batches for i in b["unit_ids"]]
        if sorted(ids) != sorted(seen):
            sys.exit("batches.json must list every unit exactly once")
        for b in batches:
            for i in b["unit_ids"]:
                u = next(x for x in units if x["unit_id"] == i)
                if str(u["event_time"])[:10] > b["through"]:
                    sys.exit(f"unit {i} is dated after the through of {b['id']}")
    else:
        cadence = json.load(open(os.path.join(ws, "config.json"), encoding="utf-8")).get("cadence", "weekly")
        if cadence not in ("weekly", "daily", "on_new"):
            sys.exit(f"config.json cadence must be weekly, daily or on_new, not {cadence!r}")
        done = {i for b in batches for i in b["unit_ids"]}
        pending = [u for u in list(old.values()) + new if u["unit_id"] not in done]
        last = batches[-1]["through"] if batches else "0000-00-00"
        floor = None if last == "0000-00-00" else datetime.date.fromisoformat(last)
        groups = {}
        for u in sorted(pending, key=lambda u: (u["event_time"], u["unit_id"])):
            if str(u.get("provider") or "").lower() == "pm-note":
                continue
            d = datetime.date.fromisoformat(str(u["event_time"])[:10])
            if floor and d <= floor:
                d = floor + datetime.timedelta(days=1)
            if cadence == "weekly":
                end = d + datetime.timedelta(days=6 - d.weekday())
                ok = end < today
            elif cadence == "daily":
                end, ok = d, d < today
            else:
                end, ok = None, d <= today
            if ok:
                groups.setdefault(end, []).append(u)
        if cadence == "on_new" and groups:
            groups = {max(datetime.date.fromisoformat(str(u["event_time"])[:10]) for u in groups[None]): groups[None]}
            if floor:
                groups = {max(k, floor): v for k, v in groups.items()}
        after = last
        for end, us in sorted(groups.items()):
            batches.append({"id": f"B{len(batches) + 1}", "after": after, "through": end.isoformat(), "unit_ids": [u["unit_id"] for u in us]})
            after = end.isoformat()
        notes = [u for u in sorted(pending, key=lambda u: (u["event_time"], u["unit_id"])) if str(u.get("provider") or "").lower() == "pm-note"]
        if notes:
            end = max(after, max(str(u["event_time"])[:10] for u in notes))
            batches.append({"id": f"B{len(batches) + 1}", "after": after, "through": end, "unit_ids": [u["unit_id"] for u in notes]})
    with open(upath, "a" if not fresh else "w", encoding="utf-8") as f:
        for u in new:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    open(bpath, "w", encoding="utf-8").write(json.dumps(batches, indent=1, ensure_ascii=False) + "\n")
    cut = {i for b in batches for i in b["unit_ids"]}
    print(json.dumps({"workspace": ws, "new_units": len(new), "skipped": len(units) - len(new),
                      "pending": len([1 for i in list(old) + [u["unit_id"] for u in new] if i not in cut]),
                      "batches": [[b["id"], b["through"], len(b["unit_ids"])] for b in batches]}))



# ---------- commands ----------
def next_step(w, B):
    s = w.step()
    if s and s.get("batch") == B:
        return {"write": "write reply/%s.json, then check and apply" % B,
                "recompute": "write reply/%s.recompute.%d.json, then check and recompute" % (B, s["round"]),
                "fold": "fold"}.get(s["phase"], "stage")
    return "stage"


def cmd_status(a):
    w = Workspace(a[0])
    pending = []
    for b in w.batches:
        B = b["id"]
        if w.done(B):
            continue
        pending.append({"batch": B, "through": b["through"], "units": len(b["unit_ids"]), "next": next_step(w, B)})
    done = [b["id"] for b in w.batches if w.done(b["id"])]
    out({"workspace": str(w.dir), "batches": len(w.batches), "done": done, "pending": pending})


def cmd_stage(a):
    w = Workspace(a[0])
    B = a[1]
    w.need_batch(B)
    st = w.prev_store(B)
    docs = w.docs_of(B)
    if w.wd.exists():
        for f in w.wd.iterdir():
            f.unlink()
    w.wd.mkdir(exist_ok=True)
    skipped = len(w.units_of(B)) - len(docs)
    if not docs:   # nothing but the tool's own units: an empty write
        w.save_work(st)
        w.set_step(batch=B, phase="fold", round=0, stats={"batch": B, "accepted": 0, "rejected": 0, "excluded_units": skipped, **w.profile_stats()})
        out({"batch": B, "documents": 0, "excluded_units": skipped, "next": "fold"})
        return
    (w.wd / "write_prompt.md").write_text(FP.write_prompt(st, docs, int(B[1:]), *w.profile()), encoding="utf-8")
    w.save_work(st)
    w.set_step(batch=B, phase="write", round=0, stats={"batch": B, "excluded_units": skipped, **w.profile_stats()})
    out({"batch": B, "documents": len(docs), "excluded_units": skipped, "through": w.bat[B]["through"],
         "units_in_store": len(st.live()), "prompt": str(w.wd / "write_prompt.md"), "reply_to": str(w.dir / "reply" / f"{B}.json")})


def _need_step(w, B, phases):
    s = w.step()
    if not s or s.get("batch") != B:
        die(f"{B} is not staged: run stage first")
    if s["phase"] not in phases:
        die(f"{B} is at phase {s['phase']}, not {'/'.join(phases)}")
    return s


def _reply_path(w, B, s):
    return w.dir / "reply" / (f"{B}.json" if s["phase"] == "write" else f"{B}.recompute.{s['round']}.json")


def _read_reply(p):
    if not p.exists():
        die(f"missing reply file {p}")
    text = p.read_text(encoding="utf-8")
    return text, parse_json_object(text)


def _batch_units(w, B):
    return {u["unit_id"]: {"text": u["text"], "event_time": u.get("event_time")} for u in w.docs_of(B)}


def _reject_view(rej):
    return [{"op": r["op"], "reason": r["reason"], "item": r["item"] if isinstance(r["item"], dict) else str(r["item"])[:200]} for r in rej]


def _dry(w, B, s):
    """What applying the current reply would do, on a copy of the store: (problems, summary)."""
    text, o = _read_reply(_reply_path(w, B, s))
    st = copy.deepcopy(w.work_store())
    if s["phase"] == "write":
        bad = write_reply_problem(text, o)
        if bad:
            return [{"reason": bad, "detail": "the reply must be one JSON object with an \"add\" list (and optionally \"supersede\")"}], {}
        extra = sorted(str(k) for k in o if k not in WRITE_KEYS)
        acc, rej, changed = st.apply_write(o, _batch_units(w, B), B, allow_supersede=True, follow_superseded=True)
        probs = [{"reason": r["reason"], "op": r["op"], "item": _reject_view(rej)[i]["item"]} for i, r in enumerate(rej)]
        probs += [{"reason": "unknown_key", "detail": f"top-level key {k!r} is not applied"} for k in extra]
        notes = [f"dependency {n['dep']} " + (f"redirected to {n['to']}" if n["action"] == "redirected" else f"dropped ({n['why']})") for n in st.dep_notes]
        return probs, {"would_accept": len(acc), "would_reject": len(rej), "superseded": changed, "warnings": notes}
    if o is None or "recompute" not in o:
        return [{"reason": "bad_reply", "detail": "the reply must be one JSON object with a \"recompute\" list"}], {}
    acc, rej, changed = st.apply_recompute(o, B)
    return [{"reason": r["reason"], "op": r["op"], "item": _reject_view([r])[0]["item"]} for r in rej], {"would_accept": len(acc), "would_reject": len(rej)}


def cmd_check(a):
    w = Workspace(a[0])
    B = a[1]
    w.need_batch(B)
    s = _need_step(w, B, ("write", "recompute"))
    probs, summ = _dry(w, B, s)
    out({"batch": B, "phase": s["phase"], "round": s["round"], **summ, "problems": probs})


def _flag_next(w, B, s, st, stats, rnd, more):
    """After a write (rnd 0) or a recompute round: another round when flagged units remain and the last step changed something."""
    if more and st.flagged() and rnd < RECOMPUTE_ROUNDS:
        (w.wd / "recompute_prompt.md").write_text(FP.recompute_prompt(st, w.profile()[0]), encoding="utf-8")
        w.set_step(batch=B, phase="recompute", round=rnd + 1, stats=stats)
        return {"next": "recompute", "round": rnd + 1, "flagged": [u["id"] for u in st.flagged()],
                "prompt": str(w.wd / "recompute_prompt.md"), "reply_to": str(w.dir / "reply" / f"{B}.recompute.{rnd + 1}.json")}
    stats["still_flagged"] = [u["id"] for u in st.flagged()]
    w.set_step(batch=B, phase="fold", round=rnd, stats=stats)
    return {"next": "fold", "still_flagged": stats["still_flagged"]}


def cmd_apply(a):
    w = Workspace(a[0])
    B = a[1]
    w.need_batch(B)
    s = _need_step(w, B, ("write",))
    text, o = _read_reply(_reply_path(w, B, s))
    bad = write_reply_problem(text, o)
    if bad:
        die(f"unusable write reply ({bad}); fix reply/{B}.json and run check")
    st = w.work_store()
    before = set(st.order)
    acc, rej, changed = st.apply_write(o, _batch_units(w, B), B, allow_supersede=True, follow_superseded=True)
    for uid in st.order:   # a unit that cites only corrections from the user is a user statement
        if uid not in before and st.units[uid]["evidence"] and all(
                str(w.units[e["unit_id"]].get("provider") or "").lower() == "pm-note" for e in st.units[uid]["evidence"]):
            st.units[uid]["source_type"] = "user"
    flagged = st.recheck(changed, coupled="always") if changed else []
    stats = dict(s["stats"], accepted=len(acc), rejected=len(rej), rejects=[r["reason"] for r in rej], superseded=changed, flagged=flagged,
                 warnings=[f"dependency {n['dep']} " + (f"redirected to {n['to']}" if n["action"] == "redirected" else f"dropped ({n['why']})") for n in st.dep_notes],
                 added=[x["id"] for x in acc if x["op"] == "add"], recompute_rounds=0)
    w.save_work(st)
    write_json(w.wd / "applied.json", {"accepted": acc, "rejected": _reject_view(rej)})
    out({"batch": B, "accepted": len(acc), "rejected": len(rej), "by_reason": {r["reason"].split(" ")[0]: sum(1 for x in rej if x["reason"].split(" ")[0] == r["reason"].split(" ")[0]) for r in rej},
         **_flag_next(w, B, s, st, stats, 0, True)})


def cmd_recompute(a):
    w = Workspace(a[0])
    B = a[1]
    w.need_batch(B)
    s = _need_step(w, B, ("recompute",))
    text, o = _read_reply(_reply_path(w, B, s))
    if o is None or "recompute" not in o:
        die(f"unusable recompute reply; fix {_reply_path(w, B, s).name} and run check")
    st = w.work_store()
    racc, rrej, rch = st.apply_recompute(o, B)
    stats = dict(s["stats"])
    stats.setdefault("recompute", []).append({"round": s["round"], "accepted": racc, "rejected": [x["reason"] for x in rrej]})
    if rch:
        stats["flagged"] = list(stats.get("flagged") or []) + st.recheck(rch, coupled="always")
    stats["recompute_rounds"] = s["round"]
    w.save_work(st)
    out({"batch": B, "round": s["round"], "accepted": len(racc), "rejected": len(rrej), "rejects": [x["reason"] for x in rrej],
         **_flag_next(w, B, s, st, stats, s["round"], bool(racc))})


def cmd_fold(a):
    w = Workspace(a[0])
    B = a[1]
    w.need_batch(B)
    s = _need_step(w, B, ("fold",))
    st = w.work_store()
    stats = dict(s["stats"], still_flagged=[u["id"] for u in st.flagged()], through=w.bat[B]["through"], units_in_batch=len(w.bat[B]["unit_ids"]),
                 live=len(st.live()))
    (w.dir / "store").mkdir(exist_ok=True)
    write_json(w.store_path(B), st.dump())
    with open(w.dir / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(stats, ensure_ascii=False, sort_keys=True) + "\n")
    out({"batch": B, "folded": True, **{k: stats.get(k) for k in ("accepted", "rejected", "still_flagged", "live")}})


def cmd_profile(a):
    sub = a[0] if a else ""
    try:
        if sub == "list":
            return out({"profiles": PR.list_profiles(a[1] if len(a) > 1 else None)})
        if sub == "show" and len(a) > 1:
            return out(PR.load(a[1], a[2] if len(a) > 2 else None))
        if sub in ("set", "overlay", "status") and len(a) > 1:
            ws = Path(a[1]).expanduser().resolve()
            w = Workspace(ws)
            if sub == "set":
                if len(a) < 3:
                    die("profile set <WS> <name>")
                PR.load(a[2], ws)
                w.cfg["profile"] = a[2]
                (ws / "config.json").write_text(json.dumps(w.cfg, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                return out({"profile": a[2], "applies_from": "the next stage"})
            if sub == "overlay":
                op = PR.overlay_path(ws)
                if "--clear" in a:
                    op.unlink(missing_ok=True)
                    return out({"overlay": None})
                if "--file" in a:
                    fp = Path(a[a.index("--file") + 1]).expanduser()
                    if not fp.is_file():
                        die(f"no such file: {fp} (a relative path is resolved from the folder you run the command in)")
                    text = fp.read_text(encoding="utf-8").strip()
                    bad = PR.overlay_problem(text)
                    if bad:
                        die(bad)
                    op.write_text(text + "\n", encoding="utf-8")
                return out({"overlay": PR.read_overlay(ws), "path": str(op)})
            p, ov = w.profile()
            return out({"profile": p["name"], "builtin": p["builtin"], "overlay": PR.overlay_sha(ov), "overlay_chars": len(ov or "")})
    except PR.ProfileError as e:
        die(str(e))
    sys.exit(__doc__)


COMMANDS = {"setup": cmd_setup, "status": cmd_status, "stage": cmd_stage, "check": cmd_check, "apply": cmd_apply,
            "recompute": cmd_recompute, "fold": cmd_fold, "profile": cmd_profile}


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    if not a or a[0] not in COMMANDS or (a[0] != "setup" and len(a) < 2) or (a[0] not in ("setup", "status", "profile") and len(a) < 3):
        sys.exit(__doc__)
    COMMANDS[a[0]](a[1:])


if __name__ == "__main__":
    main()
