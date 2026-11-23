#!/usr/bin/env python3
"""Turn raw files in a folder into candidate units (see references/input.md).

usage: ingest_folder.py <WS> --out <file.jsonl> [--source <dir>] [--all]

Only files new or changed versus WS/sources.manifest.json are read (--all ignores the manifest).
The manifest is never written here. Prints a JSON report to stdout.
Stdlib only, Python 3.9+.
"""
import argparse, datetime, email, hashlib, json, mailbox, os, re, sys, time
from email import policy
from email.utils import parseaddr, parsedate_to_datetime

TEXT_NEEDED = {".pdf", ".docx", ".pptx", ".xlsx"}
MEETING_RE = re.compile(r"meeting|call|sync|notes", re.I)
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?$")
HEADING_RE = re.compile(r"^##\s+(.+?)\s+[—–-]+\s+(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?)\s*$")
HDR_RE = re.compile(r"^(date|author|from|source|subject|to|cc|title)\s*:\s*(.*)$", re.I)


def sha1(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def norm_time(s):
    """Normalise a date string to YYYY-MM-DD or YYYY-MM-DD HH:MM, else None."""
    if not s:
        return None
    s = s.strip().replace("T", " ")
    m = re.match(r"^(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}))?", s)
    if m:
        return m.group(1) + (" " + m.group(2) if m.group(2) else "")
    try:
        dt = parsedate_to_datetime(s)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def date_from_name(name):
    m = DATE_RE.search(name)
    if not m:
        return None
    try:
        datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    return m.group(0)


def read_text(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- email

def strip_quotes(body):
    lines = body.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        s = ln.strip()
        if s.startswith(">"):
            i += 1
            continue
        if re.match(r"^-{2,}\s*(original message|forwarded message)\s*-{2,}$", s, re.I):
            break
        if re.match(r"^_{10,}$", s) and i + 1 < len(lines) and lines[i + 1].lower().startswith("from:"):
            break
        # "On ... wrote:" possibly wrapped over two or three lines
        if re.match(r"^on\b", s, re.I):
            joined = s
            j = i
            while not joined.rstrip().endswith(":") and j + 1 < len(lines) and j - i < 2 and lines[j + 1].strip():
                j += 1
                joined += " " + lines[j].strip()
            if re.search(r"\bwrote:$", joined.rstrip()):
                i = j + 1
                continue
        out.append(ln.rstrip())
        i += 1
    return "\n".join(out).strip()


def html_to_text(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", h)
    h = re.sub(r"<[^>]+>", "", h)
    import html
    return re.sub(r"\n{3,}", "\n\n", html.unescape(h)).strip()


def msg_body(msg):
    part = msg.get_body(preferencelist=("plain",))
    if part is not None:
        try:
            return part.get_content()
        except Exception:
            pass
    part = msg.get_body(preferencelist=("html",))
    if part is not None:
        try:
            return html_to_text(part.get_content())
        except Exception:
            pass
    if not msg.is_multipart():
        try:
            return msg.get_content()
        except Exception:
            return ""
    return ""


def norm_subject(s):
    s = re.sub(r"^\s*((re|fwd?|aw)\s*:\s*)+", "", s or "", flags=re.I)
    return re.sub(r"\s+", " ", s).strip().lower()


def thread_key(msg):
    refs = (msg.get("References") or "").split()
    if refs:
        return refs[0].strip()
    irt = (msg.get("In-Reply-To") or "").strip()
    if irt:
        return irt.split()[0]
    mid = (msg.get("Message-ID") or "").strip()
    if mid:
        return mid
    subj = norm_subject(msg.get("Subject"))
    if subj:
        return "subject:" + subj
    return None


def email_unit(msg):
    name, addr = parseaddr(str(msg.get("From") or ""))
    author = name.strip().strip('"') or (addr.split("@")[0] if addr else None) or None
    et = None
    d = msg.get("Date")
    if d:
        try:
            et = parsedate_to_datetime(str(d)).strftime("%Y-%m-%d %H:%M")
        except Exception:
            et = None
    return {"author": author, "event_time": et, "filename": str(msg.get("Subject") or "").strip(),
            "text": strip_quotes(msg_body(msg)), "key": thread_key(msg)}


def parse_email_file(path, ext):
    if ext == ".eml":
        with open(path, "rb") as f:
            return [email.message_from_binary_file(f, policy=policy.default)]
    box = mailbox.mbox(path, factory=lambda f: email.message_from_binary_file(f, policy=policy.default))
    try:
        return [m for m in box]
    finally:
        box.close()


# ---------------------------------------------------------------- slack

def slack_units(path, data):
    stem = os.path.splitext(os.path.basename(path))[0]
    chan = os.path.basename(os.path.dirname(path)) if DATE_RE.fullmatch(stem) else stem
    chan = "#" + chan.lstrip("#")
    res = []
    for m in data:
        if not isinstance(m, dict):
            continue
        text = m.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        author = m.get("user_name") or (m.get("user_profile") or {}).get("real_name") or m.get("real_name") or None
        et = None
        try:
            et = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(m.get("ts"))))
        except (TypeError, ValueError):
            pass
        res.append({"author": author, "event_time": et, "filename": chan, "text": text.strip()})
    return res


# ---------------------------------------------------------------- transcripts

def transcript_text(raw):
    out = []
    for blk in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [l.strip() for l in blk.split("\n") if l.strip()]
        if not lines:
            continue
        if lines[0].upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        keep = []
        for l in lines:
            if re.fullmatch(r"\d+", l):
                continue
            if "-->" in l:
                continue
            l = re.sub(r"<v\s+([^>]+)>", r"\1: ", l)
            l = re.sub(r"</?[^>]+>", "", l).strip()
            if l:
                keep.append(l)
        out.extend(keep)
    return "\n".join(out).strip()


# ---------------------------------------------------------------- md / txt

def split_header(text):
    """Return (headers dict lower-case, body). Handles --- front matter and leading Key: value lines."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    hdr, i = {}, 0
    if lines and lines[0].strip() == "---":
        j = 1
        while j < len(lines) and lines[j].strip() != "---":
            m = re.match(r"^([A-Za-z_ -]+?)\s*:\s*(.*)$", lines[j])
            if m:
                hdr[m.group(1).strip().lower()] = m.group(2).strip().strip("'\"")
            j += 1
        if j < len(lines):
            return hdr, "\n".join(lines[j + 1:]).strip()
        return {}, text.strip()
    while i < len(lines):
        m = HDR_RE.match(lines[i])
        if not m:
            break
        hdr[m.group(1).lower()] = m.group(2).strip()
        i += 1
    if not hdr:
        return {}, text.strip()
    return hdr, "\n".join(lines[i:]).strip()


def hdr_author(h):
    a = h.get("author") or h.get("from")
    if not a:
        return None
    if "@" not in a:  # a plain name: parseaddr would keep only its first word
        return a.strip().strip('"') or None
    name, addr = parseaddr(a)
    return name.strip().strip('"') or (addr.split("@")[0] if addr else a.strip()) or None


def thread_messages(text):
    """Split a thread export into [{author,event_time,text,provider}], or None when it is not one."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    heads = [i for i, l in enumerate(lines) if HEADING_RE.match(l)]
    if len(heads) >= 2:
        out = []
        for k, i in enumerate(heads):
            m = HEADING_RE.match(lines[i])
            end = heads[k + 1] if k + 1 < len(heads) else len(lines)
            out.append({"author": m.group(1).strip(), "event_time": norm_time(m.group(2)),
                        "text": "\n".join(lines[i + 1:end]).strip(), "provider": None})
        return out
    froms = [i for i, l in enumerate(lines) if re.match(r"^from\s*:", l, re.I)]
    if len(froms) >= 2:
        out = []
        for k, i in enumerate(froms):
            end = froms[k + 1] if k + 1 < len(froms) else len(lines)
            blk = "\n".join(lines[i:end])
            h, body = split_header(blk)
            out.append({"author": hdr_author(h), "event_time": norm_time(h.get("date")), "text": body,
                        "provider": h.get("source")})
        return out
    return None


def first_heading(text):
    for l in text.split("\n"):
        if l.startswith("#"):
            return l.lstrip("#").strip()
    return ""


LONG = 12000  # a document longer than this (characters) is split into sections at its headings
TINY = 300    # a section shorter than this is merged into the next one


def split_sections(text, level=2):
    """[(heading or None, text)]: one part per `## ` heading (the text before the first heading is its own part);
    a part still longer than 2 * LONG is split again at `### `. Short or heading-less text stays whole."""
    if len(text) <= LONG or level > 3:
        return [(None, text)]
    mark = re.compile(r"^%s (?!#)(.*)$" % ("#" * level), re.M)
    hits = list(mark.finditer(text))
    if not hits:
        return split_sections(text, level + 1) if level == 2 else [(None, text)]
    parts, starts = [], [m.start() for m in hits] + [len(text)]
    pre = text[:starts[0]].strip()
    if pre:
        parts.append((None, pre))
    for m, end in zip(hits, starts[1:]):
        chunk = text[m.start():end].strip()
        sub = split_sections(chunk, level + 1) if len(chunk) > 2 * LONG and level == 2 else [(None, chunk)]
        for h, t in sub:
            parts.append((m.group(1).strip() + (" / " + h if h else ""), t))
    merged = []  # a part of a few lines (a bare heading, a title block) joins the part after it
    for h, t in parts:
        if merged and len(merged[-1][1]) < TINY:
            mh, mt = merged.pop()
            h, t = (mh if mh and not h else h if not mh else mh if h.startswith(mh) else h), mt + "\n\n" + t
        merged.append((h, t))
    return merged


def doc_units(path, raw):
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    hdr, body = split_header(raw)
    msgs = thread_messages(body if raw.lstrip().startswith('---') else raw)
    fname_date = date_from_name(base)
    heading = hdr.get("title") or hdr.get("subject") or first_heading(body) or stem
    provider = hdr.get("source")
    if msgs:
        res = []
        for m in msgs:
            et = m["event_time"] or norm_time(hdr.get("date")) or fname_date
            res.append({"author": m["author"], "event_time": et, "filename": heading if (hdr.get("title") or hdr.get("subject")) else stem,
                        "text": m["text"], "provider": m["provider"] or provider})
        return res
    meeting = bool(MEETING_RE.search(base) or MEETING_RE.search(first_heading(body)))
    author = hdr_author(hdr)
    if author is None and meeting:
        author = None
    et = norm_time(hdr.get("date")) or fname_date
    return [{"author": author, "event_time": et, "filename": stem + (" § " + h if h else ""), "text": t, "provider": provider,
             "_author_expected": not meeting} for h, t in split_sections(body)]


# ---------------------------------------------------------------- driver

def load_manifest(ws):
    try:
        with open(os.path.join(ws, "sources.manifest.json"), encoding="utf-8") as f:
            m = json.load(f)
        return m if isinstance(m, dict) else {}
    except (OSError, ValueError):
        return {}


def is_changed(path, manifest):
    e = manifest.get(path)
    if not isinstance(e, dict):
        return True
    st = os.stat(path)
    if e.get("size") == st.st_size and e.get("mtime_ns") == st.st_mtime_ns:
        return False
    if e.get("size") == st.st_size and e.get("sha1") and e["sha1"] == file_sha1(path):
        return False
    return True


def walk(root):
    for dp, dns, fns in os.walk(root):
        dns[:] = sorted(d for d in dns if not d.startswith("."))
        for fn in sorted(fns):
            yield os.path.abspath(os.path.join(dp, fn))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("ws")
    ap.add_argument("--out", required=True)
    ap.add_argument("--source")
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    ws = os.path.abspath(a.ws)
    try:
        with open(os.path.join(ws, "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    slug = ((cfg.get("project") or {}).get("slug")) or "u"
    sources = [a.source] if a.source else [s.get("path") for s in cfg.get("sources", [])
                                           if isinstance(s, dict) and s.get("type") == "folder" and s.get("path")]
    if not sources:
        print("no source folder: pass --source or add a folder source to config.json", file=sys.stderr)
        return 2
    manifest = {} if a.all else load_manifest(ws)

    units, needs_review, needs_text, skipped = [], [], [], []
    files = unchanged = 0
    for src in sources:
        src = os.path.abspath(os.path.expanduser(src))
        if not os.path.isdir(src):
            skipped.append({"path": src, "reason": "source folder not found"})
            continue
        for path in walk(src):
            if os.path.basename(path).startswith("."):
                continue
            if not a.all and not is_changed(path, manifest):
                unchanged += 1
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext in TEXT_NEEDED:
                files += 1
                needs_text.append({"path": path, "ext": ext})
                continue
            parts = None
            try:
                if ext in (".eml", ".mbox"):
                    parts = []
                    for m in parse_email_file(path, ext):
                        u = email_unit(m)
                        u["provider"] = "email"
                        parts.append(u)
                elif ext == ".json":
                    data = json.loads(read_text(path))
                    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data) and any("ts" in x and "text" in x for x in data):
                        parts = slack_units(path, data)
                        for u in parts:
                            u["provider"] = "slack"
                    else:
                        skipped.append({"path": path, "reason": "json is not a Slack export"})
                        continue
                elif ext in (".vtt", ".srt"):
                    parts = [{"author": None, "event_time": date_from_name(os.path.basename(path)),
                              "filename": os.path.splitext(os.path.basename(path))[0],
                              "text": transcript_text(read_text(path)), "provider": "transcript"}]
                elif ext in (".md", ".txt"):
                    parts = doc_units(path, read_text(path))
                    for u in parts:
                        u["provider"] = u.get("provider") or "files"
                else:
                    skipped.append({"path": path, "reason": "unsupported type " + (ext or "(none)")})
                    continue
            except Exception as e:  # keep going; report the file
                skipped.append({"path": path, "reason": "unreadable: %s" % e})
                continue
            if not any(p["text"] for p in parts):
                skipped.append({"path": path, "reason": "no text"})
                continue
            files += 1
            for idx, p in enumerate(parts):
                if not p["text"]:
                    continue
                uid = "%s-%s" % (slug, sha1(path + "#" + str(idx))[:10])
                key = p.get("key")
                doc_id = sha1(key)[:10] if key else sha1(path)[:10]
                u = {"unit_id": uid, "doc_id": doc_id, "provider": p["provider"], "filename": p["filename"],
                     "event_time": p["event_time"], "author": p["author"], "text": p["text"], "source_path": path}
                units.append(u)
                missing = []
                if not u["event_time"]:
                    missing.append("event_time")
                if p.get("_author_expected") and not u["author"]:
                    missing.append("author")
                if ext in (".eml", ".mbox") and not u["author"]:
                    missing.append("author")
                if missing:
                    prev = needs_review[-1] if needs_review else None
                    if prev and prev["path"] == path and prev["missing"] == missing:
                        prev["unit_ids"].append(uid)  # one entry per file: the sections of a split document share date and author
                    else:
                        needs_review.append({"unit_id": uid, "unit_ids": [uid], "path": path, "missing": missing})

    with open(a.out, "w", encoding="utf-8") as f:
        for u in units:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    print(json.dumps({"files": files, "units": len(units), "unchanged": unchanged, "needs_review": needs_review,
                      "needs_text": needs_text, "skipped": skipped}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
