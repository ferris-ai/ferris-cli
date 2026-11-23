"""Profiles of the project-state skill: the domain wording of the write and recompute prompts (Python 3 standard library only).

A profile is a JSON file with the prompt slots below. Two built-in profiles ship in profiles/: `delivery` (the original flat:v4 wording,
byte for byte) and `general` (no domain assumed). A custom profile lives in <state home>/profiles/<name>.json and may name another
profile in `extends`; slots it leaves out come from there (default: general). Built-in names cannot be overridden.

On top of the profile a project may have an overlay, WS/profile.md: guidance for this one project, drafted during onboarding and
confirmed by the user. It is added to the task context of the write prompt. It carries no facts and cannot change the output format,
the evidence rules or the checks; those belong to the engine.
"""
import hashlib, json, os, re
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
BUILTIN = SKILL / "profiles"
DEFAULT = "delivery"          # a workspace without a `profile` key keeps the wording it was made with
FALLBACK = "general"          # base for custom profiles without `extends`
SLOTS = ("intro", "role", "task_context", "add_sources", "supersede_section")
OPTIONAL = ("about", "guidance")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
MAX_OVERLAY = 6000
OVERLAY_FILE = "profile.md"


class ProfileError(Exception):
    pass


def custom_dir(ws=None):
    """<state home>/profiles: the .ferris that holds the workspace, else PROJECT_STATE_HOME; None if neither is known."""
    if ws is not None:
        p = Path(ws).expanduser().resolve()
        if p.parent.name == "projects":
            return p.parent.parent / "profiles"
    h = os.environ.get("PROJECT_STATE_HOME")
    return Path(h).expanduser() / "profiles" if h else None


def _file(name, ws=None):
    if not NAME_RE.match(str(name)):
        raise ProfileError(f"bad profile name {name!r}: lower-case letters, digits, - and _ only")
    cd = custom_dir(ws)
    for c in (BUILTIN / f"{name}.json", cd / f"{name}.json" if cd else None):   # built-in wins
        if c and c.exists():
            return c
    raise ProfileError(f"unknown profile {name!r}: not in {BUILTIN} or {cd or 'a .ferris/profiles folder'}")


def load(name=None, ws=None, _seen=()):
    """The resolved profile: a dict with every slot (and `guidance`, `about`)."""
    name = name or DEFAULT
    if name in _seen:
        raise ProfileError(f"profile {name!r} extends itself")
    p = _file(name, ws)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProfileError(f"profile file {p} is not valid JSON: {e}")
    if d.get("name") != name:
        raise ProfileError(f"profile file {p} names itself {d.get('name')!r}, not {name!r}")
    base = {}
    if any(k not in d for k in SLOTS):
        base = load(d.get("extends") or FALLBACK, ws, _seen + (name,))
    out = {}
    for k in SLOTS + OPTIONAL:
        v = d.get(k, base.get(k, ""))
        if not isinstance(v, str):
            raise ProfileError(f"profile {name!r}: {k} must be a string")
        out[k] = v
    for k in SLOTS:
        if not out[k].strip():
            raise ProfileError(f"profile {name!r}: slot {k} is empty")
        if "{{" in out[k] or "{{" in out["guidance"]:
            raise ProfileError(f"profile {name!r}: slots cannot contain '{{{{'")
    out["name"] = name
    out["builtin"] = (BUILTIN / f"{name}.json").exists()
    return out


def list_profiles(ws=None):
    rows, seen = [], set()
    for d, kind in ((BUILTIN, "built-in"), (custom_dir(ws), "custom")):
        if not d or not d.exists():
            continue
        for f in sorted(d.glob("*.json")):
            if f.stem in seen:
                continue
            seen.add(f.stem)
            try:
                rows.append({"name": f.stem, "kind": kind, "about": load(f.stem, ws)["about"]})
            except ProfileError as e:
                rows.append({"name": f.stem, "kind": kind, "error": str(e)})
    return rows


def overlay_path(ws):
    return Path(ws) / OVERLAY_FILE


def overlay_problem(text):
    if len(text) > MAX_OVERLAY:
        return f"overlay is {len(text)} characters; the limit is {MAX_OVERLAY}"
    if "{{" in text:
        return "overlay cannot contain '{{'"
    return None


def read_overlay(ws):
    """The overlay text of a workspace, stripped; None when there is none. Raises ProfileError when it is not usable."""
    p = overlay_path(ws)
    if not p.exists():
        return None
    t = p.read_text(encoding="utf-8").strip()
    bad = overlay_problem(t)
    if bad:
        raise ProfileError(bad)
    return t or None


def overlay_sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12] if text else None


def guidance_block(profile, overlay):
    """Text appended to the task context: the profile's guidance, then the project overlay; '' when there is neither."""
    parts = []
    if profile.get("guidance", "").strip():
        parts.append(profile["guidance"].strip())
    if overlay:
        parts.append("## Project guidance:\n" + overlay + "\nThis guidance says what to look for and how to label it. It adds no facts: "
                     "every unit still needs a verbatim quote from a document of this batch, and the output format and evidence rules "
                     "below are unchanged.")
    return "\n\n".join(parts)
