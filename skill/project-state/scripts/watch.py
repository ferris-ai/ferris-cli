#!/usr/bin/env python3
"""watch.py - new-source watcher for the project-state skill (stdlib only, Python 3.9+).

Subcommands:
  check [--today YYYY-MM-DD]   SessionStart hook entry; silent unless an update is due. For Claude Code it also names the
                               source folders to watch during the session, and now and then shows a one-line Ferris tip
  file-changed                 FileChanged hook (Claude Code): note a new or changed source file for this session
  prompt                       UserPromptSubmit hook (Claude Code): tell Claude about documents that landed this session
  precompact                   PreCompact hook (Claude Code): keep the project-state place in the compaction summary
  tip [WS] [--today D]         the Ferris tip line when one is due (at most weekly, delivery projects), else nothing
  tips on|off                  turn that tip on or off for the workspace (also off with FERRIS_NO_TIPS=1)
  register <WS>                add a workspace to the registry
  unregister <slug|WS>         remove it from the registry (WS is not deleted)
  scan <WS> [--json]           list new / changed source files vs sources.manifest.json
  mark <WS>                    record manifest + config.last_update after a successful update (clears a snooze)
  snooze <WS> [--until D]      no reminder before D (default: tomorrow)
  notes <WS> --out FILE        turn pending-notes.jsonl into pm-note units (archives the notes file)
  status [--today YYYY-MM-DD]  human-readable list of registered projects
  install-hook [--agent claude|codex|cursor]... [--global]
                               add the hooks (default claude; repeatable) to this workspace's own settings
                               (.claude/settings.local.json, .cursor/hooks.json); --global: the user's files. Codex: user only
  uninstall-hook [--agent ...]...  remove them from the workspace and user files
  context-skill <WS> [--remove]   write <slug>-state skill (Claude Code) + symlink for Cursor

State home: the nearest .ferris folder walking up from the current directory (ferris_home.py); PROJECT_STATE_HOME
overrides it. Environment (all optional): PROJECT_STATE_HOME, CLAUDE_CONFIG_DIR (~/.claude),
CURSOR_HOME (~/.cursor).
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import sys

MANIFEST = "sources.manifest.json"
NOTES = "pending-notes.jsonl"
SKIP_DIRS = {".git", "node_modules"}


# ---------------------------------------------------------------- paths
def _home(env, default):
    return os.path.abspath(os.path.expanduser(os.environ.get(env) or default))


def ps_home():
    import ferris_home
    return ferris_home.home()


def registry_path():
    return os.path.join(ps_home(), "registry.json")


def claude_dir():
    return _home("CLAUDE_CONFIG_DIR", "~/.claude")


def settings_path():
    return os.path.join(claude_dir(), "settings.json")


def claude_skills():
    return os.path.join(claude_dir(), "skills")


def cursor_skills():
    return os.path.join(_home("CURSOR_HOME", "~/.cursor"), "skills")


# ---------------------------------------------------------------- json io
def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp-%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def load_registry():
    reg = read_json(registry_path(), {})
    if not isinstance(reg, dict) or not isinstance(reg.get("projects"), dict):
        return {"projects": {}}
    return reg


def load_config(ws):
    cfg = read_json(os.path.join(ws, "config.json"), {})
    return cfg if isinstance(cfg, dict) else {}


# ---------------------------------------------------------------- files / manifest
def _ignored(name):
    return name.startswith(".") or name.startswith("~$")


def iter_files(root):
    """Yield (abs_path, DirEntry-stat) for regular files under root."""
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                name = e.name
                if _ignored(name):
                    continue
                try:
                    if e.is_dir(follow_symlinks=False):
                        if name not in SKIP_DIRS:
                            stack.append(e.path)
                    elif e.is_file():
                        yield os.path.abspath(e.path), e.stat()
                except OSError:
                    continue


def sha1_of(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def folder_sources(cfg):
    """Return (folder paths, notes about ignored sources)."""
    paths, notes = [], []
    srcs = cfg.get("sources")
    for s in srcs if isinstance(srcs, list) else []:
        if isinstance(s, dict) and s.get("type") == "folder" and s.get("path"):
            paths.append(os.path.abspath(os.path.expanduser(str(s["path"]))))
        else:
            t = s.get("type") if isinstance(s, dict) else None
            notes.append("source type %r ignored (only 'folder' is supported)" % (t,))
    return paths, notes


def load_manifest(ws):
    m = read_json(os.path.join(ws, MANIFEST), {})
    return m if isinstance(m, dict) else {}


def scan_ws(ws, cfg=None):
    """Compare current source files with the manifest.
    Returns dict(new=[...], changed=[...], unchanged=int, notes=[...]).
    Hashes only files whose size/mtime differ from the manifest."""
    cfg = cfg if cfg is not None else load_config(ws)
    manifest = load_manifest(ws)
    folders, notes = folder_sources(cfg)
    new, changed, unchanged = [], [], 0
    seen = set()
    for folder in folders:
        if not os.path.isdir(folder):
            notes.append("source folder missing: %s" % folder)
            continue
        for path, st in iter_files(folder):
            if path in seen:
                continue
            seen.add(path)
            old = manifest.get(path)
            if not isinstance(old, dict):
                new.append(path)
            elif old.get("size") == st.st_size and old.get("mtime_ns") == st.st_mtime_ns:
                unchanged += 1
            else:
                try:
                    same = sha1_of(path) == old.get("sha1")
                except OSError:
                    continue
                if same:
                    unchanged += 1
                else:
                    changed.append(path)
    new.sort()
    changed.sort()
    return {"new": new, "changed": changed, "unchanged": unchanged, "notes": notes}


def build_manifest(ws, cfg=None):
    cfg = cfg if cfg is not None else load_config(ws)
    old = load_manifest(ws)
    folders, _ = folder_sources(cfg)
    out = {}
    for folder in folders:
        if not os.path.isdir(folder):
            continue
        for path, st in iter_files(folder):
            o = old.get(path)
            if isinstance(o, dict) and o.get("size") == st.st_size and o.get("mtime_ns") == st.st_mtime_ns and o.get("sha1"):
                sha = o["sha1"]
            else:
                try:
                    sha = sha1_of(path)
                except OSError:
                    continue
            out[path] = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha1": sha}
    return out


# ---------------------------------------------------------------- due rule
def parse_date(s):
    return datetime.date.fromisoformat(s)


def last_update_date(cfg):
    """Local date of config.last_update, or None."""
    lu = cfg.get("last_update")
    if not lu or not isinstance(lu, str):
        return None
    try:
        if len(lu) == 10:
            return datetime.date.fromisoformat(lu)
        dt = datetime.datetime.fromisoformat(lu.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone().date()
    except Exception:
        return None


def cadence_ok(cadence, last, today):
    if last is None:
        return True
    if cadence == "on_new":
        return True
    if cadence == "daily":
        return last < today
    if cadence == "weekly":
        monday = today - datetime.timedelta(days=today.weekday())
        return last < monday
    return last < today  # unknown cadence -> treat as daily


def count_notes(ws):
    try:
        with open(os.path.join(ws, NOTES), encoding="utf-8") as f:
            return sum(1 for ln in f if ln.strip())
    except Exception:
        return 0


def project_name(cfg, ws):
    p = cfg.get("project")
    if isinstance(p, dict) and p.get("name"):
        return str(p["name"])
    return os.path.basename(ws.rstrip("/"))


def project_slug(cfg, ws):
    p = cfg.get("project")
    if isinstance(p, dict) and p.get("slug"):
        return str(p["slug"])
    return slugify(project_name(cfg, ws))


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "project"


def count_pending(ws):
    """Units already in input/units.jsonl but in no batch yet (their period was incomplete at the last update)."""
    try:
        with open(os.path.join(ws, "input", "batches.json"), encoding="utf-8") as f:
            done = {i for b in json.load(f) for i in b.get("unit_ids", [])}
        n = 0
        with open(os.path.join(ws, "input", "units.jsonl"), encoding="utf-8") as f:
            for ln in f:
                if ln.strip() and json.loads(ln).get("unit_id") not in done:
                    n += 1
        return n
    except Exception:
        return 0


def due_info(ws, today):
    """None if not due, else dict(name, ws, n_new, n_changed, notes, pending, since, cadence, mode)."""
    cfg = load_config(ws)
    if not cfg:
        return None
    snooze = cfg.get("snoozed_until")
    try:
        if snooze and today < parse_date(str(snooze)[:10]):
            return None
    except Exception:
        pass
    sc = scan_ws(ws, cfg)
    notes = count_notes(ws)
    # on_new cuts everything up to today, so units still pending after an update are future-dated: not a reason to ask
    pending = 0 if (cfg.get("cadence") or "weekly") == "on_new" else count_pending(ws)
    if not (sc["new"] or sc["changed"] or notes or pending):
        return None
    cadence = cfg.get("cadence") or "weekly"
    last = last_update_date(cfg)
    if not cadence_ok(cadence, last, today):
        return None
    try:
        tracked = any(n.endswith(".json") for n in os.listdir(os.path.join(ws, "store")))
    except OSError:
        tracked = False
    return {"name": project_name(cfg, ws), "ws": ws, "n_new": len(sc["new"]), "tracked": tracked,
            "n_changed": len(sc["changed"]), "notes": notes, "pending": pending, "since": last.isoformat() if last else None,
            "cadence": cadence, "mode": cfg.get("mode") or "ask"}


def due_line(d):
    parts = []
    nn, nc = d["n_new"], d["n_changed"]
    if nn:
        parts.append("%d new file%s" % (nn, "" if nn == 1 else "s"))
    if nc:
        parts.append("%d changed file%s" % (nc, "" if nc == 1 else "s"))
    if d["notes"]:
        parts.append("%d pending correction note%s in %s" % (d["notes"], "" if d["notes"] == 1 else "s", NOTES))
    if d.get("pending"):
        parts.append("%d unit%s waiting for a complete period" % (d["pending"], "" if d["pending"] == 1 else "s"))
    if not parts:
        parts.append("no new files")
    what = " and ".join([", ".join(parts[:-1]), parts[-1]] if len(parts) > 2 else parts)
    minutes = max(1, min(30, 1 + (nn + nc) // 2))
    snooze = "if they decline, run `python3 '%s' snooze '%s'` so they are not asked again today." % (os.path.abspath(__file__), d["ws"])
    if not d["since"] and not d.get("tracked"):
        # never updated: the workspace was created but nothing has been tracked yet
        n = nn + nc
        docs = "%d document%s" % (n, "" if n == 1 else "s") if n else what
        return ("[project-state] %s (%s): not tracked yet, %s waiting in its source folder. Ask the user whether to start "
                "tracking it now with the project-state skill (about %d min); %s" % (d["name"], d["ws"], docs, minutes, snooze))
    head = "[project-state] %s (%s): %s%s (%s)." % (d["name"], d["ws"], what, " since %s" % d["since"] if d["since"] else "", d["cadence"])
    if d["mode"] == "auto":
        tail = "Update it now with the project-state skill in a background subagent and tell the user in one line when done."
    else:
        tail = "Ask the user whether to update it now (about %d min); %s" % (minutes, snooze)
    return head + " " + tail


# ---------------------------------------------------------------- commands
def read_hook_input():
    """The hook's JSON on stdin, or {}. Never blocks waiting for it."""
    try:
        import select
        if not sys.stdin.isatty() and select.select([sys.stdin], [], [], 0.05)[0]:
            d = json.loads(sys.stdin.read(1 << 20) or "{}")
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def enter_project_dir():
    """Hooks may run elsewhere (Cursor runs user hooks from ~/.cursor); agents name the open project in these variables."""
    proj = os.environ.get("CURSOR_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
    if proj and os.path.isdir(proj):
        os.chdir(proj)


def registered():
    """[(slug, ws, cfg)] for the projects in this workspace's registry whose config is readable."""
    out = []
    for slug, ent in sorted(load_registry()["projects"].items()):
        ws = ent.get("ws") if isinstance(ent, dict) else None
        cfg = load_config(ws) if ws else None
        if cfg:
            out.append((slug, ws, cfg))
    return out


def all_projects():
    """[(slug, ws, cfg)] for every project in this workspace's .ferris, registered or not."""
    seen, out = set(), []
    for slug, ws, cfg in registered():
        seen.add(os.path.abspath(ws))
        out.append((slug, ws, cfg))
    pdir = os.path.join(ps_home(), "projects")
    try:
        names = sorted(os.listdir(pdir))
    except OSError:
        names = []
    for n in names:
        ws = os.path.join(pdir, n)
        cfg = load_config(ws) if os.path.isfile(os.path.join(ws, "config.json")) else None
        if cfg and os.path.abspath(ws) not in seen:
            out.append((project_slug(cfg, ws), ws, cfg))
    return out


def scripts_cmd(script):
    import shlex
    return "python3 %s" % shlex.quote(os.path.join(os.path.dirname(os.path.abspath(__file__)), script))


def compact_note(projects):
    """Context after a compaction: re-read the state from the workspace instead of trusting the summary."""
    if not projects:
        return []
    names = "; ".join("%s (%s)" % (project_name(cfg, ws), ws) for _, ws, cfg in projects)
    return ["[project-state] This conversation was just compacted. Projects tracked in this workspace: %s. If the "
            "conversation was tracking, updating or correcting one of them, re-read its state before going on: run "
            "`%s status <WS>` and read `<WS>/current/brief.md`. Take batch numbers, statuses and what is still pending "
            "from those, not from the summary." % (names, scripts_cmd("engine.py"))]


# ---- the occasional Ferris tip: the agent asks for it after an update or a state answer (`tip`), and adds the line to
# its reply. Not a session-start systemMessage: the Claude desktop app, Codex and Cursor do not show one.
TIPS = [
    "Want to learn more about running delivery projects, and how to scale it across a team? → https://tryferris.ai",
    "Running more than one client project? Ferris keeps every project's state current for your whole team → https://tryferris.ai",
    "Scaling delivery? See every engagement's status, risks and next steps in one place with Ferris → https://tryferris.ai",
]
TIP_EVERY_DAYS = 7


def tips_path():
    return os.path.join(ps_home(), "tips.json")


def tip_for(projects, today):
    """A tip line, at most once a week, only in a workspace with a delivery project; None otherwise. Records the date."""
    if os.environ.get("FERRIS_NO_TIPS"):
        return None
    st = read_json(tips_path(), {})
    st = st if isinstance(st, dict) else {}
    if st.get("off"):
        return None
    delivery = [cfg for _, _, cfg in projects
                if (cfg.get("profile") or "delivery") == "delivery" and cfg.get("tips") is not False and cfg.get("last_update")]
    if not delivery:
        return None
    try:
        if st.get("last_shown") and (today - parse_date(str(st["last_shown"])[:10])).days < TIP_EVERY_DAYS:
            return None
    except Exception:
        pass
    n = int(st.get("shown") or 0)
    st.update(last_shown=today.isoformat(), shown=n + 1)
    try:
        write_json(tips_path(), st)
    except OSError:
        return None
    return "Ferris tip: %s (Tell me to turn off Ferris tips to stop these.)" % TIPS[n % len(TIPS)]


def cmd_tip(args):
    """Print the tip line when one is due (and record it), else nothing."""
    today = parse_date(args.today) if args.today else datetime.date.today()
    if args.ws:
        ws = abs_ws(args.ws)
        cfg = load_config(ws) if os.path.isfile(os.path.join(ws, "config.json")) else None
        projects = [(project_slug(cfg, ws), ws, cfg)] if cfg else []
    else:
        projects = all_projects()
    line = tip_for(projects, today)
    if line:
        print(line)
    return 0


def cmd_tips(args):
    st = read_json(tips_path(), {})
    st = st if isinstance(st, dict) else {}
    st["off"] = args.state == "off"
    write_json(tips_path(), st)
    print("Ferris tips %s for %s" % ("off" if st["off"] else "on", ps_home()))
    return 0


def cmd_check(args):
    try:
        inp = read_hook_input()
        source = inp.get("source") or "startup"
        in_cursor = bool(os.environ.get("CURSOR_PROJECT_DIR"))
        enter_project_dir()
        fmt = getattr(args, "format", None) or "claude"
        # Cursor also runs Claude Code hooks (Third-Party Imports); with our native Cursor hook present, that copy stays quiet
        if fmt != "cursor" and in_cursor and cursor_hook_installed():
            return 0
        today = parse_date(args.today) if args.today else datetime.date.today()
        projects = registered()
        lines = []
        _prune_sessions()
        if source == "startup":
            for _, ws, _ in projects:
                try:
                    d = due_info(ws, today)
                    if d:
                        lines.append(due_line(d))
                except Exception:
                    continue
        elif source == "compact":
            lines = compact_note(all_projects())
        if fmt == "cursor":
            if lines:
                sys.stdout.write(json.dumps({"additional_context": "\n".join(lines)}, ensure_ascii=False) + "\n")
            return 0
        out, hso = {}, {"hookEventName": "SessionStart"}
        if lines:
            hso["additionalContext"] = "\n".join(lines)
        if fmt == "claude" and not in_cursor:
            # Claude Code watches these folders for the session; FileChanged then fires for each new file
            watch = sorted({f for _, _, cfg in projects for f in folder_sources(cfg)[0] if os.path.isdir(f)})
            if watch:
                hso["watchPaths"] = watch
        if len(hso) > 1:
            out["hookSpecificOutput"] = hso
        if out:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return 0


# ---- documents that land during a session (Claude Code: FileChanged, then UserPromptSubmit)
def _session_file(inp):
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(inp.get("session_id") or ""))[:80]
    return os.path.join(ps_home(), "sessions", sid + ".json") if sid else None


def _in_folder(path, folder):
    rel = os.path.relpath(path, folder)
    if rel.startswith(os.pardir) or os.path.isabs(rel):
        return False
    parts = rel.split(os.sep)
    return not any(_ignored(x) for x in parts) and not any(x in SKIP_DIRS for x in parts[:-1])


def _prune_sessions(days=7):
    """Drop notes of sessions that ended without another message."""
    d = os.path.join(ps_home(), "sessions")
    try:
        cutoff = datetime.datetime.now().timestamp() - days * 86400
        for n in os.listdir(d):
            f = os.path.join(d, n)
            if os.path.getmtime(f) < cutoff:
                os.remove(f)
    except OSError:
        pass


def cmd_file_changed(args):
    try:
        inp = read_hook_input()
        path, event = inp.get("file_path"), inp.get("event")
        sf = _session_file(inp)
        if not path or event not in ("add", "change") or not sf:
            return 0
        enter_project_dir()
        path = os.path.abspath(path)
        for slug, _, cfg in registered():
            if any(_in_folder(path, f) for f in folder_sources(cfg)[0]):
                st = read_json(sf, {})
                st = st if isinstance(st, dict) else {}
                files = st.setdefault("files", {}).setdefault(slug, [])
                if path not in files and len(files) < 1000:
                    files.append(path)
                    write_json(sf, st)
                break
    except Exception:
        pass
    return 0


def _still_new(ws, paths):
    """The paths not yet recorded by an update (missing from the manifest, or different from it)."""
    man = load_manifest(ws)
    out = []
    for p in paths:
        try:
            st = os.stat(p)
        except OSError:
            continue
        old = man.get(p)
        if not (isinstance(old, dict) and old.get("size") == st.st_size and old.get("mtime_ns") == st.st_mtime_ns):
            out.append(p)
    return out


def arrival_line(name, ws, folder_of, paths):
    n = len(paths)
    shown = ", ".join(os.path.relpath(p, folder_of(p)) for p in paths[:5]) + (", and %d more" % (n - 5) if n > 5 else "")
    return ("[project-state] Since this session started, %d new or changed document%s arrived in the source folder of "
            "%s (%s): %s. Mention this to the user in one sentence at the start of your reply and ask whether to update "
            "%s now with the project-state skill; start an update only if they say yes." % (
                n, "" if n == 1 else "s", name, ws, shown, name))


def cmd_prompt(args):
    try:
        inp = read_hook_input()
        sf = _session_file(inp)
        if not sf or not os.path.exists(sf):
            return 0
        enter_project_dir()
        st = read_json(sf, {})
        try:
            os.remove(sf)
        except OSError:
            pass
        today = datetime.date.today()
        lines = []
        for slug, ws, cfg in registered():
            paths = ((st or {}).get("files") or {}).get(slug) or []
            try:
                snooze = cfg.get("snoozed_until")
                if snooze and today < parse_date(str(snooze)[:10]):
                    continue
            except Exception:
                pass
            paths = _still_new(ws, paths)
            if paths:
                folders = folder_sources(cfg)[0]
                folder_of = lambda p: next((f for f in folders if _in_folder(p, f)), os.path.dirname(p))
                lines.append(arrival_line(project_name(cfg, ws), ws, folder_of, paths))
        if lines:
            sys.stdout.write(json.dumps({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit", "additionalContext": "\n".join(lines)}}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return 0


def cmd_precompact(args):
    """PreCompact: what the summary must keep, so a long update does not lose its place."""
    try:
        read_hook_input()
        enter_project_dir()
        projects = all_projects()
        if not projects:
            return 0
        names = "; ".join("%s (%s)" % (project_name(cfg, ws), ws) for _, ws, cfg in projects)
        sys.stdout.write(
            "If this conversation used the project-state skill on one of these projects: %s, the summary must keep: "
            "the project name and workspace path; the step in progress (setup, stage, propose, check, apply, fold, "
            "report, corrections) and the batch it was on; the user's answers, confirmations and corrections that are "
            "not yet written to the workspace; and what the user asked to do next. The summary must also say that, "
            "before continuing, Claude re-reads the project's state from the workspace (`%s status <WS>` and "
            "`<WS>/current/brief.md`) instead of relying on the summary for batch numbers or statuses.\n"
            % (names, scripts_cmd("engine.py")))
    except Exception:
        pass
    return 0


def abs_ws(p):
    return os.path.abspath(os.path.expanduser(p))


def cmd_register(args):
    ws = abs_ws(args.ws)
    if not os.path.isfile(os.path.join(ws, "config.json")):
        print("error: %s has no config.json" % ws, file=sys.stderr)
        return 1
    cfg = load_config(ws)
    slug = project_slug(cfg, ws)
    reg = load_registry()
    prev = reg["projects"].get(slug)
    reg["projects"][slug] = {"ws": ws}
    write_json(registry_path(), reg)
    print("%s %s -> %s" % ("updated" if prev else "registered", slug, ws))
    return 0


def cmd_unregister(args):
    reg = load_registry()
    key = args.slug
    if key not in reg["projects"]:
        ws = abs_ws(key)
        match = [s for s, e in reg["projects"].items() if isinstance(e, dict) and e.get("ws") == ws]
        key = match[0] if match else None
    if key is None:
        print("not registered: %s" % args.slug)
        return 1
    ws = reg["projects"].pop(key).get("ws")
    write_json(registry_path(), reg)
    print("unregistered %s (workspace %s kept)" % (key, ws))
    return 0


def cmd_scan(args):
    ws = abs_ws(args.ws)
    cfg = load_config(ws)
    sc = scan_ws(ws, cfg)
    if args.json:
        print(json.dumps(sc, indent=1, ensure_ascii=False))
        return 0
    print("project: %s (%s)" % (project_name(cfg, ws), ws))
    for n in sc["notes"]:
        print("note: " + n)
    for p in sc["new"]:
        print("new: " + p)
    for p in sc["changed"]:
        print("changed: " + p)
    print("summary: %d new, %d changed, %d unchanged" % (len(sc["new"]), len(sc["changed"]), sc["unchanged"]))
    return 0


def unit_source_paths(ws):
    """Source files that have at least one unit in the workspace (the units' source_path)."""
    out = set()
    try:
        with open(os.path.join(ws, "input", "units.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    p = json.loads(line).get("source_path")
                except (ValueError, AttributeError):
                    continue
                if isinstance(p, str) and p:
                    out.add(os.path.abspath(p))
    except OSError:
        pass
    return out


# file types the folder reader turns into units, or that the agent reads itself (ingest_folder.py)
READ_TYPES = {".eml", ".mbox", ".json", ".vtt", ".srt", ".md", ".txt", ".pdf", ".docx", ".pptx", ".xlsx"}


def cmd_mark(args):
    ws = abs_ws(args.ws)
    cfg = load_config(ws)
    if not cfg:
        print("error: cannot read %s/config.json" % ws, file=sys.stderr)
        return 1
    old = load_manifest(ws)
    manifest = build_manifest(ws, cfg)
    # A new file is recorded as seen only once its units are in the workspace: marking it before would hide it from
    # every later update. --force FILE records named files anyway (skipped by the reader, or every unit dropped on purpose).
    folders, _ = folder_sources(cfg)

    def rel(path):   # a held file as the user sees it: relative to its source folder
        for f in folders:
            if path.startswith(f.rstrip(os.sep) + os.sep):
                return os.path.relpath(path, f)
        return path

    candidates = []
    have = unit_source_paths(ws)
    for path in sorted(manifest):
        if path in old or os.path.abspath(path) in have:
            continue
        if os.path.splitext(path)[1].lower() not in READ_TYPES:
            continue   # a type the reader never turns into units (an image, an archive): recorded, as before
        candidates.append(path)
    # every held file gets a name that picks out only it: its path in its source folder, or the full path when two share one
    short_names = {}
    for p in candidates:
        short_names.setdefault(rel(p), []).append(p)

    def name(p):
        return rel(p) if len(short_names[rel(p)]) == 1 else p

    force = args.force or []
    picked = set(candidates) if "*" in force else set()
    for f in ([] if "*" in force else force):
        full = os.path.abspath(os.path.expanduser(f))
        hits = [p for p in candidates if p == full] or [p for p in candidates if rel(p) == os.path.normpath(f)]
        if not hits and os.sep not in f:
            hits = [p for p in candidates if os.path.basename(p) == f]
        if len(hits) > 1:
            print("error: %d new files match %s; name one of them as listed: %s" % (len(hits), f, "; ".join(name(p) for p in hits)), file=sys.stderr)
            return 1
        if not hits:
            if full in manifest or (os.sep not in f and any(os.path.basename(p) == f for p in manifest)):
                print("note: %s is already recorded; nothing to force" % f, file=sys.stderr)
                continue
            print("error: no new file without units matches %s; files not marked: %s"
                  % (f, "; ".join(name(p) for p in candidates) or "none"), file=sys.stderr)
            return 1
        picked.update(hits)
    held = [p for p in candidates if p not in picked]
    for p in held:
        del manifest[p]
    write_json(os.path.join(ws, MANIFEST), manifest)
    cfg["last_update"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cfg.pop("snoozed_until", None)
    write_json(os.path.join(ws, "config.json"), cfg)
    print("marked %s: %d files in manifest, last_update=%s" % (ws, len(manifest), cfg["last_update"]))
    if held:
        print("not marked, no units in the workspace yet (%d): %s" % (len(held), "; ".join(name(p) for p in held)))
        print("If the update skipped adding them, add them now (ingest, then setup). For a file the reader skipped or could "
              "not read, or whose every unit you dropped on purpose, run mark again with --force FILE (each such file).")
    return 0


def cmd_status(args):
    today = parse_date(args.today) if args.today else datetime.date.today()
    projects = load_registry()["projects"]
    if not projects:
        print("no registered projects (registry: %s)" % registry_path())
        return 0
    for slug, ent in sorted(projects.items()):
        ws = ent.get("ws", "?") if isinstance(ent, dict) else "?"
        cfg = load_config(ws)
        if not cfg:
            print("%s: workspace unreadable (%s)" % (slug, ws))
            continue
        sc = scan_ws(ws, cfg)
        notes = count_notes(ws)
        cadence = cfg.get("cadence") or "weekly"
        last = last_update_date(cfg)
        due = bool(sc["new"] or sc["changed"] or notes) and cadence_ok(cadence, last, today)
        print("%s (%s)" % (slug, ws))
        print("  cadence: %s  mode: %s  last_update: %s" % (cadence, cfg.get("mode") or "ask", cfg.get("last_update") or "never"))
        print("  pending: %d new files, %d changed files, %d notes  due: %s" % (len(sc["new"]), len(sc["changed"]), notes, "yes" if due else "no"))
        for n in sc["notes"]:
            print("  note: " + n)
    return 0


# ---- hook install (Claude Code, Codex, Cursor)
AGENTS = ("claude", "codex", "cursor")


def our_command(agent="claude", sub="check"):
    import shlex
    cmd = "python3 %s %s" % (shlex.quote(os.path.abspath(__file__)), sub)
    if sub == "check" and agent in ("cursor", "codex"):
        cmd += " --format " + agent
    return cmd


def our_hooks(agent):
    """[(event key, matcher or None, command)] that install-hook writes for the agent."""
    if agent == "cursor":
        return [("sessionStart", None, our_command("cursor"))]
    if agent == "codex":
        return [("SessionStart", "startup", our_command("codex"))]
    return [
        ("SessionStart", "startup|resume|compact", our_command("claude")),
        ("FileChanged", None, our_command("claude", "file-changed")),
        ("UserPromptSubmit", None, our_command("claude", "prompt")),
        ("PreCompact", None, our_command("claude", "precompact")),
    ]


def our_commands(agent):
    """Every command this copy of the scripts may have written for the agent, current and older forms."""
    base = our_command("claude", "check")
    cmds = {c for _, _, c in our_hooks(agent)} | {base}
    if agent == "cursor":
        cmds.add(base + " --format cursor")
    return cmds


def workspace_root():
    """The folder that holds .ferris: the workspace the agent is opened in."""
    return os.path.dirname(ps_home())


def hook_scopes(agent):
    """Where the agent reads hooks: the workspace first (Claude Code, Cursor), then the user's own file."""
    return ["user"] if agent == "codex" else ["workspace", "user"]


def hook_file(agent, scope="user"):
    """The agent's hook file. workspace: <workspace>/.claude/settings.local.json (Claude Code reads it only for sessions
    in that workspace and it is personal, not shared) or <workspace>/.cursor/hooks.json. Codex reads only the user file."""
    if scope == "workspace" and agent == "claude":
        return os.path.join(workspace_root(), ".claude", "settings.local.json")
    if scope == "workspace" and agent == "cursor":
        return os.path.join(workspace_root(), ".cursor", "hooks.json")
    if agent == "codex":
        return os.path.join(_home("CODEX_HOME", "~/.codex"), "hooks.json")
    if agent == "cursor":
        return os.path.join(_home("CURSOR_HOME", "~/.cursor"), "hooks.json")
    return settings_path()


def keep_out_of_git(path):
    """Add a workspace hook file to the repository's local exclude list (.git/info/exclude), unless git ignores it already.
    The file holds absolute paths on this machine; the user's .gitignore is not touched."""
    import subprocess
    try:
        d = os.path.dirname(path)
        top = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if top.returncode != 0:
            return None
        if subprocess.run(["git", "-C", d, "check-ignore", "-q", path], timeout=5).returncode == 0:
            return None
        excl = subprocess.run(["git", "-C", d, "rev-parse", "--git-path", "info/exclude"], capture_output=True, text=True, timeout=5)
        if excl.returncode != 0:
            return None
        ex = excl.stdout.strip()
        ex = ex if os.path.isabs(ex) else os.path.join(d, ex)
        rel = "/" + os.path.relpath(os.path.realpath(path), os.path.realpath(top.stdout.strip())).replace(os.sep, "/")
        os.makedirs(os.path.dirname(ex), exist_ok=True)
        with open(ex, "a", encoding="utf-8") as f:
            f.write("%s\n" % rel)
        return ex
    except Exception:
        return None


def _load_settings(p=None):
    p = p or settings_path()
    if not os.path.exists(p):
        return p, {}
    try:
        with open(p, encoding="utf-8") as f:
            s = json.load(f)
        if not isinstance(s, dict):
            raise ValueError("top level is not an object")
    except Exception as e:
        print("error: cannot parse %s (%s); not touching it" % (p, e), file=sys.stderr)
        return p, None
    return p, s


def _backup(p):
    if os.path.exists(p):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        b = "%s.bak-%s" % (p, stamp)
        shutil.copy2(p, b)
        return b
    return None


# Our hook commands from any copy of the scripts (this one, an older install, the skill copied by hand)
_ANY_OURS = re.compile(r"python3 .*watch\.py'? (check( --format \w+)?|file-changed|prompt|precompact)$")


def _is_ours(h, cmd):
    cmds = cmd if isinstance(cmd, (set, frozenset, list, tuple)) else {cmd}
    if not (isinstance(h, dict) and h.get("type", "command") == "command"):
        return False
    c = h.get("command")
    return c in cmds or bool(_ANY_OURS.match(str(c or "")))


def _event_list(s, agent, create, key=None):
    """An event's list in a parsed hook file, or an error string. Claude Code and Codex: hooks.<Event>,
    entries {matcher, hooks: [{type, command}]}. Cursor: version 1, hooks.sessionStart, entries {command}."""
    key = key or ("sessionStart" if agent == "cursor" else "SessionStart")
    hooks = s.get("hooks")
    if hooks is None:
        if not create:
            return None
        hooks = s["hooks"] = {}
    if not isinstance(hooks, dict):
        return "hooks is not an object"
    if agent == "cursor" and create:
        s.setdefault("version", 1)
    ss = hooks.get(key)
    if ss is None:
        if not create:
            return None
        ss = hooks[key] = []
    if not isinstance(ss, list):
        return "hooks.%s is not a list" % key
    return ss


def _has_ours(ss, agent, cmd):
    if agent == "cursor":
        return any(_is_ours(e, cmd) for e in ss)
    return any(isinstance(e, dict) and any(_is_ours(h, cmd) for h in e.get("hooks") or []) for e in ss)


def _strip_ours(s, agent, key, cmds):
    """Remove our entries from one event list; returns how many were removed (or an error string)."""
    ss = _event_list(s, agent, create=False, key=key)
    if ss is None:
        return 0
    if isinstance(ss, str):
        return ss
    removed, kept = 0, []
    for entry in ss:
        if agent == "cursor":
            if _is_ours(entry, cmds):
                removed += 1
                continue
        elif isinstance(entry, dict) and isinstance(entry.get("hooks"), list):
            rest = [h for h in entry["hooks"] if not _is_ours(h, cmds)]
            removed += len(entry["hooks"]) - len(rest)
            if len(rest) != len(entry["hooks"]):
                if not rest:
                    continue
                entry = dict(entry, hooks=rest)
        kept.append(entry)
    if kept:
        s["hooks"][key] = kept
    else:
        del s["hooks"][key]
    return removed


def _event_keys(agent):
    return ["sessionStart"] if agent == "cursor" else ["SessionStart", "FileChanged", "UserPromptSubmit", "PreCompact"]


def install_hook(agent, scope="workspace"):
    """Write our hooks for the agent; replaces older forms of them (other entries in the file are kept)."""
    if scope not in hook_scopes(agent):
        print("%s: reads hooks only from %s, so the hook goes there" % (agent, hook_file(agent, "user")))
        scope = "user"
    p, s = _load_settings(hook_file(agent, scope))
    if s is None:
        return 1
    before = json.dumps(s, sort_keys=True)
    for key in _event_keys(agent):
        r = _strip_ours(s, agent, key, our_commands(agent))
        if isinstance(r, str):
            print("error: %s: %s" % (p, r), file=sys.stderr)
            return 1
    for key, matcher, cmd in our_hooks(agent):
        ss = _event_list(s, agent, create=True, key=key)
        if isinstance(ss, str):
            print("error: %s: %s" % (p, ss), file=sys.stderr)
            return 1
        if agent == "cursor":
            ss.append({"command": cmd})
        else:
            entry = {"hooks": [{"type": "command", "command": cmd}]}
            if matcher:
                entry = {"matcher": matcher, **entry}
            ss.append(entry)
    if json.dumps(s, sort_keys=True) == before:
        print("%s: hooks already installed in %s (no change)" % (agent, p))
        return 0
    b = _backup(p)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    write_json(p, s)
    events = ", ".join(k for k, _, _ in our_hooks(agent))
    print("%s: installed hooks (%s) in %s: %s" % (agent, events, p, our_command(agent)))
    if b:
        print("backup: " + b)
    if scope == "workspace":
        ex = keep_out_of_git(p)
        if ex:
            print("git: added %s to %s" % (os.path.basename(p), ex))
    if agent == "codex":
        print("codex: review and trust it once with /hooks in Codex; until then Codex does not run it")
    return 0


def uninstall_hook(agent):
    """Remove our hooks from every file the agent reads (workspace and user)."""
    found = False
    for scope in hook_scopes(agent):
        p = hook_file(agent, scope)
        if not os.path.exists(p):
            continue
        r = _uninstall_from(agent, p)
        if r == 1:
            return 1
        found = found or r == 0
    if not found:
        print("%s: hook not installed (no change)" % agent)
    return 0


def _uninstall_from(agent, p):
    """0 removed, 2 none there, 1 error."""
    p, s = _load_settings(p)
    if s is None:
        return 1
    if not isinstance(s.get("hooks"), dict):
        return 2
    removed = 0
    for key in _event_keys(agent):
        r = _strip_ours(s, agent, key, our_commands(agent))
        if isinstance(r, str):
            print("error: %s: %s" % (p, r), file=sys.stderr)
            return 1
        removed += r
    if not removed:
        return 2
    if not s["hooks"]:
        del s["hooks"]
    b = _backup(p)
    write_json(p, s)
    print("%s: removed %d hook entr%s from %s" % (agent, removed, "y" if removed == 1 else "ies", p))
    if b:
        print("backup: " + b)
    return 0


_ANY_CHECK = re.compile(r"watch\.py'? check( --format \w+)?$")


def _quiet_load(p):
    if not os.path.exists(p):
        return None
    try:
        s = read_json(p)
        return s if isinstance(s, dict) else None
    except Exception:
        return None


def hook_where(agent):
    """The scopes whose hook file runs a project-state `watch.py check`, from this copy of the scripts or another."""
    out = []
    for scope in hook_scopes(agent):
        s = _quiet_load(hook_file(agent, scope))
        ss = _event_list(s, agent, create=False) if s else None
        if not isinstance(ss, list):
            continue
        hs = ss if agent == "cursor" else [h for e in ss if isinstance(e, dict) for h in e.get("hooks") or []]
        if any(isinstance(h, dict) and _ANY_CHECK.search(str(h.get("command") or "")) for h in hs):
            out.append(scope)
    return out


def hook_installed(agent):
    return bool(hook_where(agent))


def cursor_hook_installed():
    for scope in hook_scopes("cursor"):
        s = _quiet_load(hook_file("cursor", scope))
        ss = _event_list(s, "cursor", create=False) if s else None
        if isinstance(ss, list) and _has_ours(ss, "cursor", our_commands("cursor")):
            return True
    return False


def cmd_info(args):
    """What is already switched on for one workspace (JSON), so the agent offers only what is off."""
    ws = abs_ws(args.ws)
    if not os.path.isfile(os.path.join(ws, "config.json")):
        print("error: %s has no config.json" % ws, file=sys.stderr)
        return 1
    cfg = load_config(ws)
    slug = project_slug(cfg, ws)
    import ferris_home
    home = ferris_home.find(ws)  # the registry next to this workspace, wherever the command runs from
    reg = read_json(os.path.join(home, "registry.json"), {}) if home else load_registry()
    ent = (reg.get("projects") or {}).get(slug) if isinstance(reg, dict) else None
    registered = isinstance(ent, dict) and os.path.abspath(ent.get("ws") or "") == ws
    hooks = {a: hook_installed(a) for a in AGENTS}
    hooks_where = {a: hook_where(a) for a in AGENTS if hook_where(a)}
    print(json.dumps({
        "slug": slug,
        "reminder": registered and any(hooks.values()),
        "registered": registered,
        "hooks": hooks,
        "hooks_where": hooks_where,
        "context_skill": os.path.isfile(os.path.join(claude_skills(), slug + "-state", "SKILL.md")),
        "cadence": cfg.get("cadence") or "weekly",
        "mode": cfg.get("mode") or "ask",
        "has_source_folder": bool(folder_sources(cfg)),
        "daily_offered": cfg.get("daily_offered"),
    }, indent=1))
    return 0


def _agents(args):
    return list(dict.fromkeys(args.agent or ["claude"]))


def cmd_install_hook(args):
    return max(install_hook(a, "user" if args.globally else "workspace") for a in _agents(args))


def cmd_uninstall_hook(args):
    return max(uninstall_hook(a) for a in _agents(args))


# ---- context skill
def newest_store(ws):
    d = os.path.join(ws, "store")
    try:
        names = [n for n in os.listdir(d) if n.endswith(".json")]
    except OSError:
        return None

    def key(n):
        m = re.match(r"[A-Za-z]*(\d+)", n)
        return (int(m.group(1)) if m else -1, n)
    for n in sorted(names, key=key, reverse=True):
        st = read_json(os.path.join(d, n))
        if isinstance(st, dict):
            return st
    return None


def _idkey(i):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", i)]


def active_titles(ws, limit=12):
    """Scopes of the active units, most units first: what the project context says it covers."""
    st = newest_store(ws)
    units = st.get("units") if isinstance(st, dict) else None
    if not isinstance(units, list):
        return []
    count = {}
    for u in units:
        if isinstance(u, dict) and u.get("status") in ("active", "needs_recheck") and u.get("scope"):
            count[str(u["scope"])] = count.get(str(u["scope"]), 0) + 1
    return [k for k, _ in sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]


def build_description(cfg, ws):
    name = project_name(cfg, ws)
    p = cfg.get("project") if isinstance(cfg.get("project"), dict) else {}
    aliases = [str(a) for a in (p.get("aliases") or []) if a]
    parties = cfg.get("parties") if isinstance(cfg.get("parties"), dict) else {}
    pp = ["%s (%s)" % (v, k) for k, v in parties.items() if v]
    titles = active_titles(ws)
    triggers = [name] + aliases

    def make(tl):
        s = "Latest tracked state of the %s project" % name
        if aliases:
            s += " (also called %s)" % ", ".join(aliases)
        if pp:
            s += ", between " + " and ".join(pp)
        s += ": rules, facts, commitments, dates, statuses and changes"
        if tl:
            s += ". Covers " + "; ".join(tl)
        s += ". Use when the conversation mentions %s, or asks about its status, rules, commitments, deadlines or what the other party agreed." % ", ".join(triggers)
        return s
    desc = make(titles)
    while len(desc) > 1000 and titles:
        titles = titles[:-1]
        desc = make(titles)
    if len(desc) > 1000:
        desc = desc[:997] + "..."
    return desc


def sh_quote(s):
    return "'" + s.replace("'", "'\\''") + "'"


def build_skill_md(cfg, ws, slug):
    name = project_name(cfg, ws)
    brief = os.path.join(ws, "current", "brief.md")
    desc = build_description(cfg, ws)
    return """---
name: %(slug)s-state
description: %(desc)s
---

# %(name)s: current state (read-only)

State summary, generated by the project-state skill:

!`cat %(brief_q)s`

If the state summary is not shown above, read %(brief)s with your file-reading tool.

## Rules
- This skill is read-only: never edit files under %(ws)s, except appending a correction as described below. To update the state, the user asks for an update with the project-state skill.
- The summary is only as new as the "as of" date in its header. Say so when you rely on it; newer documents are not included.
- Quote status, dates and acceptance wording from the summary; do not guess beyond it.

## Drill down
To see why a fact holds, find its id (for example U-0042) in the newest file of %(ws)s/store/ (one JSON per batch, newest last): each unit carries its verbatim evidence quotes with their source unit ids, and the facts it replaced stay in the file with status "superseded". %(ws)s/current/report.html shows the same as a state and trajectory report.

## Record a correction
If the user says the state is wrong or gives a new fact, do not change state files. Append one JSON line to %(ws)s/pending-notes.jsonl:
{"date": "YYYY-MM-DD", "author": "<who said it>", "text": "<the correction, in the user's words>"}
Then tell the user it will be applied at the next update.
""" % {"slug": slug, "desc": json.dumps(desc, ensure_ascii=False), "name": name,
       "brief_q": sh_quote(brief),
       "brief": brief, "ws": ws}


def cmd_context_skill(args):
    target = args.ws
    ws = abs_ws(target)
    if os.path.isfile(os.path.join(ws, "config.json")):
        cfg = load_config(ws)
        slug = project_slug(cfg, ws)
    elif args.remove:
        cfg, slug = {}, target  # allow removal by slug after the workspace is gone
    else:
        print("error: %s has no config.json" % ws, file=sys.stderr)
        return 1
    skill_name = slug + "-state"
    cdir = os.path.join(claude_skills(), skill_name)
    link = os.path.join(cursor_skills(), skill_name)
    link_is_real = os.path.lexists(link) and not os.path.islink(link)
    if args.remove:
        if link_is_real:
            print("error: %s is not a symlink; refusing to remove" % link, file=sys.stderr)
            return 1
        did = []
        if os.path.islink(link):
            os.unlink(link)
            did.append("removed " + link)
        if os.path.islink(cdir):
            os.unlink(cdir)
            did.append("removed " + cdir)
        elif os.path.isdir(cdir):
            shutil.rmtree(cdir)
            did.append("removed " + cdir)
        print("\n".join(did) if did else "nothing to remove for %s" % skill_name)
        return 0
    if link_is_real:
        print("error: %s exists and is not a symlink; refusing to overwrite" % link, file=sys.stderr)
        return 1
    if os.path.islink(cdir):
        print("error: %s is a symlink; refusing to write through it" % cdir, file=sys.stderr)
        return 1
    os.makedirs(cdir, exist_ok=True)
    skill_md = os.path.join(cdir, "SKILL.md")
    with open(skill_md, "w", encoding="utf-8") as f:
        f.write(build_skill_md(cfg, ws, slug))
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.islink(link):
        os.unlink(link)
    os.symlink(cdir, link)
    print("wrote %s" % skill_md)
    print("linked %s -> %s" % (link, cdir))
    return 0


def cmd_snooze(args):
    ws = abs_ws(args.ws)
    cfg = load_config(ws)
    if cfg is None:
        print("error: no config.json in %s" % ws, file=sys.stderr)
        return 1
    until = args.until or (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
    parse_date(until)
    cfg["snoozed_until"] = until
    write_json(os.path.join(ws, "config.json"), cfg)
    print("snoozed %s until %s" % (ws, until))
    return 0


def cmd_notes(args):
    """pending-notes.jsonl lines {date, author, text} -> pm-note units in --out; the notes file moves to notes-archive/."""
    ws = abs_ws(args.ws)
    src = os.path.join(ws, NOTES)
    rows = []
    if os.path.exists(src):
        with open(src, encoding="utf-8") as f:
            for n, ln in enumerate(f, 1):
                if not ln.strip():
                    continue
                try:
                    d = json.loads(ln)
                    text = str(d["text"]).strip()
                except Exception:
                    print("error: %s line %d is not a JSON object with text" % (src, n), file=sys.stderr)
                    return 1
                date = str(d.get("date") or datetime.date.today().isoformat())[:10]
                parse_date(date)
                uid = "pm-" + hashlib.sha1(ln.strip().encode("utf-8")).hexdigest()[:10]
                rows.append({"unit_id": uid, "doc_id": "pm-notes", "provider": "pm-note", "filename": "PM note",
                             "event_time": date, "author": d.get("author") or None, "text": text})
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if rows:
        arch = os.path.join(ws, "notes-archive")
        os.makedirs(arch, exist_ok=True)
        os.replace(src, os.path.join(arch, datetime.datetime.now().strftime("%Y%m%dT%H%M%S") + ".jsonl"))
    print(json.dumps({"notes": len(rows), "out": args.out}))
    return 0


# ---------------------------------------------------------------- main
def build_parser():
    ap = argparse.ArgumentParser(prog="watch.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check"); p.add_argument("--today"); p.add_argument("--format", choices=("claude", "codex", "cursor")); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("file-changed"); p.set_defaults(fn=cmd_file_changed)
    p = sub.add_parser("prompt"); p.set_defaults(fn=cmd_prompt)
    p = sub.add_parser("precompact"); p.set_defaults(fn=cmd_precompact)
    p = sub.add_parser("tips"); p.add_argument("state", choices=("on", "off")); p.set_defaults(fn=cmd_tips)
    p = sub.add_parser("tip"); p.add_argument("ws", nargs="?"); p.add_argument("--today"); p.set_defaults(fn=cmd_tip)
    p = sub.add_parser("register"); p.add_argument("ws"); p.set_defaults(fn=cmd_register)
    p = sub.add_parser("unregister"); p.add_argument("slug"); p.set_defaults(fn=cmd_unregister)
    p = sub.add_parser("scan"); p.add_argument("ws"); p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_scan)
    p = sub.add_parser("mark"); p.add_argument("ws")
    p.add_argument("--force", nargs="+", metavar="FILE", help="also record these new files, which have no units in the workspace (a file name or path; * for all)")
    p.set_defaults(fn=cmd_mark)
    p = sub.add_parser("snooze"); p.add_argument("ws"); p.add_argument("--until"); p.set_defaults(fn=cmd_snooze)
    p = sub.add_parser("notes"); p.add_argument("ws"); p.add_argument("--out", required=True); p.set_defaults(fn=cmd_notes)
    p = sub.add_parser("status"); p.add_argument("--today"); p.set_defaults(fn=cmd_status)
    p = sub.add_parser("info"); p.add_argument("ws"); p.set_defaults(fn=cmd_info)
    p = sub.add_parser("install-hook"); p.add_argument("--agent", action="append", choices=AGENTS)
    p.add_argument("--global", dest="globally", action="store_true", help="the user's own hook file instead of this workspace's")
    p.set_defaults(fn=cmd_install_hook)
    p = sub.add_parser("uninstall-hook"); p.add_argument("--agent", action="append", choices=AGENTS); p.set_defaults(fn=cmd_uninstall_hook)
    p = sub.add_parser("context-skill"); p.add_argument("ws"); p.add_argument("--remove", action="store_true"); p.set_defaults(fn=cmd_context_skill)
    return ap


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("check", "file-changed", "prompt", "precompact"):
        try:  # hooks must never fail loudly, even on bad arguments
            args = build_parser().parse_args(argv)
        except SystemExit:
            return 0
        return args.fn(args)
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
