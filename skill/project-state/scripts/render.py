#!/usr/bin/env python3
"""render.py: turn a project-state workspace (flat:v4 store) into a static HTML state-trajectory report and a compact brief.md.

Usage:
    render.py <WS> [--batch B] [--generated TEXT]

Reads   WS/config.json (optional), WS/input/batches.json, WS/input/units.jsonl, WS/store/<B>.json (every finished batch),
        WS/history.jsonl, WS/reports/<B>.summary.md (optional).
Writes  WS/reports/<B>.html, WS/current/report.html (copy), WS/current/brief.md.

The report uses only the generic record fields of a unit (content, priority, type, scope, evidence, supersede chain, derived_from /
coupled_with, triggers, batch, dates). It assumes no delivery semantics. Sections: how each fact evolved (a version timeline, one row
per fact, grouped by scope), activity per batch, what the newest batch changed, dependencies, current state (filterable). Clicking a
fact opens its versions with the verbatim quotes and the source documents.

Python 3 standard library only. Deterministic: the same inputs give byte-identical outputs (the only time-like text is --generated).
"""
import argparse
import datetime
import difflib
import html
import json
import os
import re
import sys

BRIEF_LIMIT = 8000   # characters
SOURCE_TEXT_LIMIT = 1500
LIVE = ("active", "needs_recheck")


def esc(x):
    return html.escape("" if x is None else str(x), quote=True)


def read_json(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read_jsonl(p):
    try:
        with open(p, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except (OSError, ValueError):
        return []


def write_text(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(s)


def day(t):
    return str(t or "")[:10]


def uid_key(i):
    m = re.match(r"U-(\d+)$", str(i))
    return (0, int(m.group(1))) if m else (1, str(i))


# ---------------------------------------------------------------- data
class Data:
    def __init__(self, ws, batch=None):
        self.ws = ws
        self.cfg = read_json(os.path.join(ws, "config.json"), {}) or {}
        self.batches = read_json(os.path.join(ws, "input", "batches.json"), []) or []
        self.srcs = {}
        for u in read_jsonl(os.path.join(ws, "input", "units.jsonl")):
            self.srcs[u["unit_id"]] = u
        self.done = [b["id"] for b in self.batches if os.path.exists(os.path.join(ws, "store", b["id"] + ".json"))]
        if not self.done:
            raise SystemExit("no finished batch in %s/store" % ws)
        self.batch = batch or self.done[-1]
        if self.batch not in self.done:
            raise SystemExit("batch %s has no store" % self.batch)
        self.through = {b["id"]: b["through"] for b in self.batches}
        upto = self.done[: self.done.index(self.batch) + 1]
        self.stores = {B: self.units_of(B) for B in upto}
        self.units = self.stores[self.batch]               # id -> unit, every unit ever written up to the batch
        self.order = sorted(self.units, key=uid_key)
        self.prev_batch = upto[-2] if len(upto) > 1 else None
        self.prev = self.stores[self.prev_batch] if self.prev_batch else {}
        self.hist = {h["batch"]: h for h in read_jsonl(os.path.join(ws, "history.jsonl")) if "batch" in h}
        self.pred = {}
        for i in self.order:
            n = self.units[i].get("superseded_by")
            if n and n in self.units:
                self.pred[n] = i
        self.chains = self.build_chains()
        self.chain_of = {i: c for c in self.chains for i in c}
        self.dependents = {}
        for i in self.order:
            for k in ("derived_from", "coupled_with"):
                for d in self.units[i].get(k) or []:
                    self.dependents.setdefault(d, []).append((i, k))

    def units_of(self, B):
        d = read_json(os.path.join(self.ws, "store", B + ".json"), {}) or {}
        return {u["id"]: u for u in d.get("units") or []}

    def build_chains(self):
        chains = []
        for i in self.order:
            if i in self.pred:
                continue
            c, seen = [i], {i}
            while self.units[c[-1]].get("superseded_by") in self.units and self.units[c[-1]]["superseded_by"] not in seen:
                c.append(self.units[c[-1]]["superseded_by"])
                seen.add(c[-1])
            chains.append(c)
        return chains

    def live(self):
        return [self.units[i] for i in self.order if self.units[i]["status"] in LIVE]

    def project(self):
        p = self.cfg.get("project") if isinstance(self.cfg.get("project"), dict) else {}
        return p.get("name") or os.path.basename(self.ws.rstrip("/")) or "Project"

    def changes(self):
        """What the newest batch did, from the store diff and the batch's history row."""
        B, h = self.batch, self.hist.get(self.batch, {})
        added = [i for i in self.order if i not in self.prev and self.units[i].get("origin") != "recompute"]
        added = [i for i in added if self.units[i].get("batch") == B]
        superseded = [i for i in self.order if self.units[i]["status"] == "superseded" and (i not in self.prev or self.prev[i]["status"] != "superseded")
                      and not str(self.units[i].get("supersede_reason") or "").startswith("recomputed:")]
        recomputed = [i for i in self.order if self.units[i]["status"] == "superseded" and (i not in self.prev or self.prev[i]["status"] != "superseded")
                      and str(self.units[i].get("supersede_reason") or "").startswith("recomputed:")]
        confirmed = []
        for r in h.get("recompute") or []:
            for a in r.get("accepted") or []:
                if a.get("op") == "confirm":
                    confirmed.append(a["id"])
        flagged = [i for i in self.order if self.units[i]["status"] == "needs_recheck"]
        return {"added": added, "superseded": superseded, "recomputed": recomputed, "confirmed": confirmed, "flagged": flagged}


def short(t, n=220):
    t = re.sub(r"\s+", " ", str(t or "")).strip()
    return t if len(t) <= n else t[: n - 1] + "…"


# ---------------------------------------------------------------- html
CSS = """
:root{--bg:#f7f6f3;--surf:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--mut:#8a8984;--line:#e3e1db;--grid:#ecebe6;
--cur:#2a78d6;--cur-soft:#9ec5f4;--old:#c9c7bf;--old2:#a9a69c;--user:#6250d6;--warn:#c98500;--end:#e34948;--add:#1baf7a;--chg:#eb6834;--rc:#6250d6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141413;--surf:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--mut:#8b8a83;--line:#33322f;--grid:#262624;
--cur:#3987e5;--cur-soft:#1c5cab;--old:#4a4944;--old2:#6b6a63;--user:#9085e9;--warn:#e0a020;--end:#e66767;--add:#199e70;--chg:#d95926;--rc:#9085e9}}
:root[data-theme=dark]{--bg:#141413;--surf:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--mut:#8b8a83;--line:#33322f;--grid:#262624;
--cur:#3987e5;--cur-soft:#1c5cab;--old:#4a4944;--old2:#6b6a63;--user:#9085e9;--warn:#e0a020;--end:#e66767;--add:#199e70;--chg:#d95926;--rc:#9085e9}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1120px;margin:0 auto;padding:22px 16px 24px}
h1{font-size:22px;margin:0}h2{font-size:15px;margin:0 0 2px}h3{font-size:13px;margin:12px 0 4px;color:var(--ink2)}.sub{color:var(--ink2);font-size:13px;margin:0}
nav{display:flex;gap:14px;flex-wrap:wrap;margin:10px 0 0;font-size:13px}a{color:var(--cur)}
.summary{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:14px 18px 6px;margin:14px 0}.summary h2{margin-bottom:6px}.summary ul{margin:0;padding:0;list-style:none}.summary li{position:relative;padding:7px 0 7px 16px;border-top:1px solid var(--grid);font-size:14px;line-height:1.5}.summary li:first-child{border-top:0}.summary li:before{content:'';position:absolute;left:2px;top:15px;width:6px;height:6px;border-radius:50%;background:var(--cur)}.ref{display:inline-block;font:11px ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--mut);border:1px solid var(--line);border-radius:4px;padding:0 4px;margin-left:2px;text-decoration:none;vertical-align:1px}a.ref:hover{color:var(--cur);border-color:var(--cur)}
.sec{background:var(--surf);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:16px 0}
.sec>.hd{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:14px 0}
.kpi{background:var(--surf);border:1px solid var(--line);border-radius:10px;padding:10px 14px}.kpi b{display:block;font-size:26px;line-height:1.1;font-variant-numeric:tabular-nums}.kpi span{color:var(--ink2);font-size:12px}.kpi.warn b{color:var(--warn)}
.tabs{display:flex;gap:4px;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:2px}.tabs button,.btn{font:inherit;font-size:12.5px;border:0;background:none;color:var(--ink2);padding:4px 10px;border-radius:6px;cursor:pointer}
.tabs button[aria-pressed=true]{background:var(--surf);color:var(--ink);box-shadow:0 0 0 1px var(--line)}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--ink2);margin:6px 0 4px}.legend i{display:inline-block;width:14px;height:8px;border-radius:2px;margin-right:5px;vertical-align:middle}
svg{display:block;width:100%;height:auto}svg text{fill:var(--ink2);font:11px system-ui,sans-serif}svg text.t1{fill:var(--ink)}svg .grid{stroke:var(--grid)}svg .rowline{stroke:var(--grid)}svg .ax{stroke:var(--line)}
svg text.ax1{font-size:10px;fill:var(--mut);text-transform:uppercase;letter-spacing:.04em}svg text.ax2{font-size:11px;fill:var(--ink2)}svg text.dt{font-size:10px;fill:var(--ink2)}svg text.sc{font-weight:600}
svg .row{cursor:pointer}svg .row:hover .bg{fill:var(--bg)}svg .bar rect.v:hover{opacity:.85}
svg.axis{position:sticky;top:0;z-index:3;background:var(--surf)}svg.lanes{margin-top:2px}#tip{position:fixed;z-index:9;pointer-events:none;background:var(--ink);color:var(--bg);padding:6px 9px;border-radius:7px;font-size:12px;max-width:320px;display:none;box-shadow:0 4px 14px #0003}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}
.dc{border:1px solid var(--line);border-radius:10px;padding:10px 12px;background:var(--surf)}.dc .k{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}
.dc .txt{font-size:13.5px;line-height:1.55}.dc del{color:var(--end);text-decoration:line-through;text-decoration-thickness:1.5px;opacity:.85}.dc ins{text-decoration:none;background:rgba(27,175,122,.22);background:color-mix(in srgb,var(--add) 22%,transparent);color:var(--ink);border-radius:3px;padding:0 2px}.dc .why{font-size:12.5px;color:var(--ink2);margin:2px 0 6px}.dc details.was{margin-top:6px;font-size:12.5px;color:var(--mut)}.dc details.was summary{cursor:pointer}.dc.end{border-left:3px solid var(--end)}.dc.add{border-left:3px solid var(--add)}.dc.chg{border-left:3px solid var(--chg)}.dc.rc{border-left:3px solid var(--rc)}.dc.flag{border-left:3px solid var(--warn)}
.chip{display:inline-block;border-radius:10px;padding:0 8px;font-size:11.5px;background:var(--bg);border:1px solid var(--line);color:var(--ink2);margin-right:4px}.chip.u{color:var(--user);border-color:var(--user)}.chip.f{color:var(--warn);border-color:var(--warn)}
table{border-collapse:collapse;width:100%;font-size:13px}th{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);text-align:left;padding:4px 8px;border-bottom:1px solid var(--line)}
td{padding:6px 8px;border-bottom:1px solid var(--grid);vertical-align:top}tr.scope td{background:var(--bg);font-weight:600;font-size:12px;color:var(--ink2)}tr.item{cursor:pointer}tr.item:hover td{background:var(--bg)}
.bar-h{display:inline-block;height:6px;border-radius:3px;background:var(--cur);vertical-align:middle}.bar-h.soft{background:var(--cur-soft)}.bar-h.f{background:var(--warn)}
.filters{display:flex;gap:8px;flex-wrap:wrap}.filters select,.filters input{background:var(--surf);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:4px 8px;font:inherit;font-size:12.5px}
#drawer{position:fixed;top:0;right:0;height:100%;width:min(460px,100%);background:var(--surf);border-left:1px solid var(--line);box-shadow:-8px 0 24px #0002;transform:translateX(102%);transition:transform .18s;z-index:8;overflow:auto;padding:16px 18px}
#drawer.open{transform:none}#drawer .dh{display:flex;align-items:flex-start;gap:12px;margin-bottom:4px}#drawer h3{margin:0;font-size:15px;color:var(--ink);flex:1;min-width:0}#drawer .x{flex:none}
.ver{border-left:2px solid var(--line);margin:0 0 0 5px;padding:0 0 12px 14px;position:relative}.ver:before{content:"";position:absolute;left:-6px;top:4px;width:10px;height:10px;border-radius:50%;background:var(--cur)}.ver.old:before{background:var(--old)}.ver.old .c{color:var(--mut)}
blockquote{margin:5px 0;padding:3px 10px;border-left:3px solid var(--line);font-size:12.5px;color:var(--ink2)}blockquote details{margin-top:3px}.srct{white-space:pre-wrap;background:var(--bg);border-radius:6px;padding:6px 8px;font-size:12px;max-height:220px;overflow:auto}
.note{font-size:12px;color:var(--mut);margin-top:4px}.hidden{display:none}
:root{--l1:#2a78d6;--l2:#1baf7a;--l3:#8a5cf6;--l4:#d9822b;--l5:#d6457a;--l6:#0f9bb8;--card:#ffffff;--lane:#f3f2ee}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--l1:#5b9cf0;--l2:#3cc79a;--l3:#a98bfa;--l4:#eda04f;--l5:#ec6f9c;--l6:#3cc0db;--card:#211f1d;--lane:#1d1c1a}}
:root[data-theme=dark]{--l1:#5b9cf0;--l2:#3cc79a;--l3:#a98bfa;--l4:#eda04f;--l5:#ec6f9c;--l6:#3cc0db;--card:#211f1d;--lane:#1d1c1a}
svg.dg{width:auto;max-width:none}svg.dg .lane{fill:var(--lane);stroke:var(--line)}svg.dg .card{fill:var(--card);stroke:var(--line)}svg.dg .card.f{stroke:var(--warn);stroke-width:2}
svg.dg text.meta{font:10.5px ui-monospace,SFMono-Regular,Menlo,monospace;fill:var(--mut)}svg.dg text.ttl{font-size:12px;fill:var(--ink)}svg.dg text.ln{font-size:12.5px;font-weight:650}svg.dg text.lns{font-size:11.5px;fill:var(--mut)}
svg.dg .e{fill:none;stroke:var(--mut);stroke-width:1.4;opacity:.75}svg.dg .row:hover .card{stroke:var(--cur)}
footer.brand{max-width:1120px;margin:0 auto;padding:6px 16px 28px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--mut)}
footer.brand a{display:inline-flex;align-items:center;gap:7px;color:var(--ink2);text-decoration:none;font-weight:600}footer.brand a:hover{color:var(--ink)}footer.brand svg{width:16px;height:16px;display:inline-block}
@media(max-width:640px){main{padding:14px 10px 60px}.scroll{overflow-x:auto}.scroll svg{min-width:760px}}
"""

JS = """
(function(){
var D=JSON.parse(document.getElementById('detail').textContent),S=JSON.parse(document.getElementById('srcs').textContent);
var tip=document.getElementById('tip'),dr=document.getElementById('drawer');
function fd(s){var m=/^(\\d{4})-(\\d{2})-(\\d{2})/.exec(s||'');return m?['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][+m[2]-1]+' '+(+m[3])+', '+m[1]:(s||'')}
function esc(s){return String(s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})}
document.addEventListener('mousemove',function(e){var t=e.target.closest&&e.target.closest('[data-tip]');if(!t){tip.style.display='none';return}tip.innerHTML=t.getAttribute('data-tip');tip.style.display='block';tip.style.left=Math.max(4,Math.min(e.clientX+14,innerWidth-340))+'px';tip.style.top=(e.clientY+14)+'px'});
function open_(id){var v=D[id];if(!v)return;var h=v[v.length-1];
 dr.innerHTML='<div class=dh><h3>'+esc(h.content)+'</h3><button class="btn x" id="dx">close ✕</button></div><p class=sub>'+v.length+' version'+(v.length>1?'s':'')+' · newest last</p><div style="margin-top:12px">'+v.map(function(x){return '<div class="ver '+(x.status=='superseded'?'old':'')+'"><div class=c>'+esc(x.content)+'</div><div class=note>'+x.id+' · '+fd(x.from)+' → '+(x.to?fd(x.to):'now')+' · batch '+x.batch+(x.user?' · user statement':'')+(x.why?'<br>replaced: '+esc(x.why):'')+(x.rc?'<br>flagged: '+esc(x.rc):'')+'</div>'+x.ev.map(function(e){return '<blockquote>“'+esc(e.q)+'”<div class=note>'+esc(e.u)+' · '+esc(e.src)+'</div>'+(S[e.u]?'<details><summary>source document</summary><div class=srct>'+esc(S[e.u])+'</div></details>':'')+'</blockquote>'}).join('')+'</div>'}).join('')+'</div>';
 dr.classList.add('open');document.getElementById('dx').onclick=function(){dr.classList.remove('open')}}
document.addEventListener('click',function(e){var t=e.target.closest&&e.target.closest('[data-chain]');if(t){e.preventDefault();open_(t.getAttribute('data-chain'))}});
document.addEventListener('keydown',function(e){if(e.key=='Escape')dr.classList.remove('open')});
var tb=document.querySelectorAll('[data-view]');
tb.forEach(function(b){b.addEventListener('click',function(){tb.forEach(function(x){x.setAttribute('aria-pressed',x===b)});document.getElementById('v-changed').classList.toggle('hidden',b.dataset.view!='changed');document.getElementById('v-all').classList.toggle('hidden',b.dataset.view!='all')})});
var fs=document.getElementById('fs'),fp=document.getElementById('fp'),fq=document.getElementById('fq');
function filt(){document.querySelectorAll('tr.item').forEach(function(r){var ok=(!fs.value||r.dataset.s==fs.value)&&(!fp.value||r.dataset.p==fp.value)&&(!fq.value||r.textContent.toLowerCase().indexOf(fq.value.toLowerCase())>=0);r.classList.toggle('hidden',!ok)});
 document.querySelectorAll('tr.scope').forEach(function(s){var n=s.nextElementSibling,any=false;while(n&&!n.classList.contains('scope')){if(!n.classList.contains('hidden'))any=true;n=n.nextElementSibling}s.classList.toggle('hidden',!any)})}
[fs,fp,fq].forEach(function(e){e&&e.addEventListener('input',filt)});
})();
"""

# the Ferris mark: nine squares (x, y, width, height) in a 991 x 992 box
FERRIS_MARK = [(628, 10, 105, 106), (266, 10, 105, 106), (10, 261, 109, 105), (628, 876, 105, 106), (878, 261, 102, 105),
               (10, 623, 109, 106), (266, 876, 105, 106), (878, 623, 102, 106), (447, 439, 109, 108)]
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MAX_ROWS_ALL = 150       # "All facts" view is capped at this many rows


def fmt_long(s):
    return ("%s, %s" % (fmt_day(s), str(s)[:4])) if s else ""


def fmt_day(s):
    if not s:
        return ""
    d = datetime.date.fromisoformat(str(s)[:10])
    return "%s %d" % (MON[d.month - 1], d.day)


def wrap2(t, n=40):
    words, lines, cur = re.sub(r"\s+", " ", str(t or "")).split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    if len(lines) > 2:
        lines = lines[:2]
        lines[1] = lines[1][: n - 1] + "…"
    return lines


class Page:
    def __init__(self, d):
        self.d = d
        self.upto = d.done[: d.done.index(d.batch) + 1]
        dates = []
        for u in d.units.values():
            dates.append(day(u.get("valid_from") or u.get("event_time")) or d.through.get(d.batch))
            if u.get("valid_to"):
                dates.append(day(u["valid_to"]))
        self.end = datetime.date.fromisoformat(d.through.get(d.batch) or max(dates))
        first = datetime.date.fromisoformat(min(x for x in dates if x))
        self.start = first - datetime.timedelta(days=first.weekday())     # a Monday: a regular weekly grid
        self.end1 = self.end + datetime.timedelta(days=1)
        self.span = max((self.end1 - self.start).days, 1)

    # -- geometry
    LW, TW, ROWH = 300, 1000, 44

    def X(self, s):
        return self.LW + (datetime.date.fromisoformat(s) - self.start).days / self.span * (self.TW - 16 - self.LW)

    # -- time axis: ticks every `step` weeks so labels stay at least ~36px apart
    def week_step(self):
        per_week = (self.TW - 16 - self.LW) / self.span * 7
        return max(1, int(-(-36 // per_week)))

    def ticks(self):
        t, k, step = self.start, 0, self.week_step()
        while t < self.end1:
            if self.X(t.isoformat()) < self.X(self.end1.isoformat()) - 30:    # no tick label squeezed against the as-of line
                yield t
            t += datetime.timedelta(days=7 * step)

    def axis_svg(self):
        """Sticky header: month, day, and the as-of marker, on the same x scale as the lanes."""
        out, last = ["<svg class=axis viewBox='0 0 %d 44' role=img aria-label='Time axis'>" % self.TW, "<text x=10 y=38 class=ax1>Fact</text>"], None
        for t in self.ticks():
            x, m = self.X(t.isoformat()), MON[t.month - 1]
            out.append("<line class=ax x1=%.1f x2=%.1f y1=28 y2=44 /><text x=%.1f y=26 class=ax1>%s</text><text x=%.1f y=40 class=ax2>%d</text>" % (x, x, x + 3, m if m != last else "", x + 3, t.day))
            last = m
        xe = self.X(self.end1.isoformat())
        out.append("<text x=%.1f y=10 text-anchor=end class=ax1 style='fill:var(--ink)'>as of %s</text><line x1=%.1f x2=%.1f y1=14 y2=44 stroke=var(--ink) stroke-dasharray='4 3' /><line class=ax x1=0 x2=%d y1=43 y2=43 /></svg>" % (xe + 6, fmt_day(self.end.isoformat()), xe, xe, self.TW))
        return "".join(out)

    # -- trajectory: one svg for all facts, grouped by scope bands
    def lanes_svg(self, groups):
        d, y, body = self.d, 4, []
        for sc, chains in groups:
            body.append("<rect x=0 y=%d width=%d height=22 rx=6 fill=var(--bg) /><rect x=0 y=%d width=3 height=22 rx=1.5 fill=var(--cur) /><text class='t1 sc' x=12 y=%d>%s<tspan class=n dx=8 style='font-weight:400;fill:var(--mut)'>%d fact%s</tspan></text>" % (y, self.TW, y, y + 15, esc(sc), len(chains), "" if len(chains) == 1 else "s"))
            y += 26
            for c in chains:
                head = d.units[c[-1]]
                n = len(c) - 1
                st0 = day(d.units[c[0]].get("valid_from") or d.units[c[0]].get("event_time")) or self.end.isoformat()
                stn = day(head.get("valid_from") or head.get("event_time")) or self.end.isoformat()
                meta = ["changed %d\u00d7 \u00b7 last %s" % (n, fmt_day(stn)) if n else "since %s" % fmt_day(st0)]
                if head["status"] == "needs_recheck":
                    meta.append("needs recheck")
                elif head.get("source_type") == "user":
                    meta.append("your statement")
                elif head["status"] == "superseded" and not head.get("superseded_by"):
                    meta.append("ended %s" % fmt_day(day(head.get("valid_to")) or self.end.isoformat()))
                if head.get("priority") == "soft":
                    meta.append("soft")
                body.append("<g class=row data-chain='%s'><rect class=bg x=0 y=%d width=%d height=%d fill=transparent />" % (esc(c[0]), y - 2, self.TW, self.ROWH))
                body.append("<text x=12 y=%d class=t1>%s</text><text x=12 y=%d class=dt>%s</text>" % (y + 15, esc(short(head["content"], 50)), y + 30, esc(" \u00b7 ".join(meta))))
                body.append("<line class=rowline x1=0 x2=%d y1=%d y2=%d />" % (self.TW, y + self.ROWH - 2, y + self.ROWH - 2))
                cy = y + 19
                x0 = self.X(st0)
                body.append("<line x1=%.1f x2=%.1f y1=%d y2=%d stroke=var(--grid) stroke-width=2 />" % (x0, self.X(self.end1.isoformat()), cy, cy))
                for k, i in enumerate(c):
                    u = d.units[i]
                    old = u["status"] == "superseded"
                    st = day(u.get("valid_from") or u.get("event_time")) or self.end.isoformat()
                    en = day(u.get("valid_to")) or self.end.isoformat()
                    x1 = self.X(st)
                    x2 = self.X(en) if old else self.X(self.end1.isoformat())
                    if old:   # earlier versions: thin, in alternating shades so each one shows
                        fill, bh = ("var(--old)" if (n - k) % 2 else "var(--old2)"), 6
                    elif u["status"] == "needs_recheck":
                        fill, bh = "var(--warn)", 10
                    elif u.get("source_type") == "user":
                        fill, bh = "var(--user)", 10
                    else:
                        fill, bh = "var(--cur)", 10
                    w = max(x2 - x1 - (2 if old and u.get("superseded_by") else 0), 4)
                    tip = "<b>%s</b> \u00b7 %s \u00b7 %s \u2192 %s<br>%s" % (esc(i), "earlier version" if old else ("needs recheck" if u["status"] == "needs_recheck" else "current"),
                                                                          fmt_day(st), fmt_day(en) if old else "now", esc(short(u["content"], 220)))
                    body.append("<rect class=v data-tip=\"%s\" x=%.1f y=%.1f width=%.1f height=%d rx=%.1f fill='%s' />" % (esc(tip), x1, cy - bh / 2, w, bh, bh / 2, fill))
                    if k:   # a change: the day the new version took effect
                        body.append("<circle data-tip=\"%s\" cx=%.1f cy=%d r=4.5 fill=var(--surf) stroke='%s' stroke-width=2 />" % (esc("changed %s" % fmt_day(st)), x1, cy, fill if not old else "var(--mut)"))
                    if old and not u.get("superseded_by"):
                        body.append("<path d='M%.1f %d l8 8 m0 -8 l-8 8' stroke=var(--end) stroke-width=2 fill=none />" % (x2 - 4, cy - 4))
                body.append("</g>")
                y += self.ROWH + 2
            y += 4
        H = y + 26
        out = ["<svg class=lanes viewBox='0 0 %d %d' role=img aria-label='Version timeline of the facts, grouped by scope'>" % (self.TW, H)]
        for t in self.ticks():
            x = self.X(t.isoformat())
            out.append("<line class=grid x1=%.1f x2=%.1f y1=0 y2=%d />" % (x, x, H - 26))
        xe = self.X(self.end1.isoformat())
        out.append("<line x1=%.1f x2=%.1f y1=0 y2=%d stroke=var(--ink) stroke-dasharray='4 3' />" % (xe, xe, H - 26))
        out += body
        out.append("<line class=ax x1=0 x2=%d y1=%d y2=%d /><text x=10 y=%d class=ax1>Batch cut-off</text>" % (self.TW, H - 26, H - 26, H - 8))
        per = (self.TW - 16 - self.LW) / self.span
        k = max(1, int(-(-26 // max(per * (self.span / max(len(self.upto), 1)), 1))))
        for n, bb in enumerate(self.upto):
            x = self.X(self.d.through[bb]) + per * 0.5
            out.append("<line x1=%.1f x2=%.1f y1=%d y2=%d stroke=var(--mut) />" % (x, x, H - 26, H - 20))
            if n % k == 0 or n == len(self.upto) - 1:
                out.append("<text x=%.1f y=%d text-anchor=middle style='font-size:10px'>%s</text>" % (x, H - 8, esc(bb)))
        out.append("</svg>")
        return "".join(out)

    def lanes(self, only_changed):
        d = self.d
        scopes = {}
        for c in d.chains:
            if only_changed and not (len(c) > 1 or d.units[c[-1]]["status"] == "superseded"):
                continue
            scopes.setdefault(d.units[c[-1]].get("scope") or "(no scope)", []).append(c)
        total, groups, shown = sum(len(v) for v in scopes.values()), [], 0
        for sc in sorted(scopes, key=lambda s: (s == "(no scope)", s.lower())):
            cs = scopes[sc][: max(MAX_ROWS_ALL - shown, 0)]
            if cs:
                groups.append((sc, cs))
                shown += len(cs)
        if not groups:
            return "<p class=sub>Nothing has changed yet.</p>"
        note = "<p class=note>%d more facts are not drawn: see the current-state list below.</p>" % (total - shown) if total > shown else ""
        return "%s%s" % (self.lanes_svg(groups), note)

    # -- activity per batch
    def activity(self):
        d = self.d
        rows = []
        for n, B in enumerate(self.upto):
            cur, prv = d.stores[B], (d.stores[self.upto[n - 1]] if n else {})
            a = r = c = 0
            for i, u in cur.items():
                if i not in prv and u.get("origin") != "recompute":
                    a += 1
                if u["status"] == "superseded" and (i not in prv or prv[i]["status"] != "superseded"):
                    if str(u.get("supersede_reason") or "").startswith("recomputed"):
                        c += 1
                    else:
                        r += 1
            rows.append((B, a, r, c))
        W, H, pl, pb = 1000, 170, 36, 30
        mx = max(max(a + r + c for _, a, r, c in rows), 1)
        bw = (W - pl - 10) / max(len(rows), 1)
        step = max(1, mx // 4)
        svg = ["<svg viewBox='0 0 %d %d' role=img aria-label='Units added, replaced and recomputed per batch'>" % (W, H)]
        for k in range(0, mx + 1, step):
            y = H - pb - (H - pb - 14) * k / mx
            svg.append("<line class=grid x1=%d x2=%d y1=%.1f y2=%.1f /><text x=%d y=%.1f text-anchor=end>%d</text>" % (pl, W, y, y, pl - 6, y + 4, k))
        for n, (B, a, r, c) in enumerate(rows):
            x, w, y0 = pl + n * bw + bw * .3, bw * .4, H - pb
            tip = "%s · documents to %s<br>+%d added · %d replaced · %d recomputed" % (esc(B), esc(d.through.get(B)), a, r, c)
            svg.append("<g class=bar data-tip=\"%s\">" % tip)
            for v, col in ((a, "var(--add)"), (r, "var(--chg)"), (c, "var(--rc)")):
                if v:
                    h = (H - pb - 14) * v / mx
                    y0 -= h
                    svg.append("<rect class=v x=%.1f y=%.1f width=%.1f height=%.1f rx=4 fill='%s' />" % (x, y0, w, max(h - 2, 2), col))
            if a + r + c:
                svg.append("<text class=t1 x=%.1f y=%.1f text-anchor=middle style='font-size:11px;font-weight:600'>%d</text>" % (x + w / 2, y0 - 5, a + r + c))
            svg.append("<rect x=%.1f y=0 width=%.1f height=%d fill=transparent /></g>" % (x - 4, w + 8, H - pb))
            svg.append("<text class=t1 x=%.1f y=%d text-anchor=middle>%s</text><text x=%.1f y=%d text-anchor=middle>%s</text>"
                       % (x + w / 2, H - pb + 14, esc(B), x + w / 2, H - pb + 26, esc(str(d.through.get(B))[5:])))
        svg.append("</svg>")
        return "".join(svg)

    # -- this batch
    @staticmethod
    def diff_html(old, new):
        """The new wording with removed words struck and added words marked, or None when too little is shared to read as a diff."""
        a, b = re.findall(r"\S+\s*", old or ""), re.findall(r"\S+\s*", new or "")
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        if sm.ratio() < 0.45:
            return None
        out = []
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                out.append(esc("".join(b[j1:j2])))
                continue
            if i2 > i1:
                out.append("<del>%s</del> " % esc("".join(a[i1:i2]).strip()))
            if j2 > j1:
                out.append("<ins>%s</ins> " % esc("".join(b[j1:j2]).strip()))
        return "".join(out)

    @staticmethod
    def reason(u):
        r = str(u.get("supersede_reason") or "").strip()
        if not r:
            return ""
        r = re.sub(r"^(value|scope|status|correction|refinement)\s*:\s*", "", r, flags=re.I)
        return "<div class=why>%s</div>" % esc(r[:1].upper() + r[1:])

    def changes(self):
        d, c = self.d, self.d.changes()
        h = d.hist.get(d.batch, {})
        cards = []
        for i in c["superseded"]:
            u = d.units[i]
            new = d.units.get(u.get("superseded_by") or "")
            if new:
                dh = self.diff_html(u["content"], new["content"])
                body = ("<div class=txt>%s</div>" % dh) if dh else ("<div class=txt>%s</div><details class=was><summary>was</summary>%s</details>" % (esc(new["content"]), esc(u["content"])))
                cards.append("<div class='dc chg'><div class=k>Changed \u00b7 %s</div>%s%s<div class=note>%s \u00b7 from %s</div></div>"
                             % (esc(new.get("scope") or u.get("scope")), self.reason(u), body, esc(new["id"]), esc(fmt_day(new.get("valid_from") or day(new.get("event_time"))))))
            else:
                cards.append("<div class='dc end'><div class=k>Ended \u00b7 %s</div>%s<div class=txt><del>%s</del></div></div>" % (esc(u.get("scope")), self.reason(u), esc(u["content"])))
        shown = {d.units[i].get("superseded_by") for i in c["superseded"] + c["recomputed"]}   # replacements already shown on their "changed" card
        for i in c["added"]:
            if i in shown:
                continue
            u = d.units[i]
            cards.append("<div class='dc add'><div class=k>New \u00b7 %s</div><div class=txt>%s</div><div class=note>%s \u00b7 from %s</div></div>"
                         % (esc(u.get("scope")), esc(u["content"]), esc(i), esc(fmt_day(u.get("valid_from") or day(u.get("event_time"))))))
        for i in c["recomputed"]:
            u = d.units[i]
            new = d.units[u["superseded_by"]]
            dh = self.diff_html(u["content"], new["content"])
            cards.append("<div class='dc rc'><div class=k>Rechecked \u00b7 %s</div>%s<div class=txt>%s</div></div>"
                         % (esc(u.get("scope")), self.reason(u), dh or esc(new["content"])))
        for i in c["confirmed"]:
            if i in d.units:
                cards.append("<div class='dc'><div class=k>Confirmed after recheck \u00b7 %s</div><div class=txt>%s</div></div>" % (esc(d.units[i].get("scope")), esc(d.units[i]["content"])))
        for i in c["flagged"]:
            u = d.units[i]
            cards.append("<div class='dc flag'><div class=k>Still flagged for recheck \u00b7 %s</div><div class=txt>%s</div><div class=note>%s</div></div>" % (esc(u.get("scope")), esc(u["content"]), esc(u.get("recheck_reason"))))
        sub = "%d document(s) \u00b7 %d accepted, %d rejected \u00b7 %d recompute rounds" % (h.get("units_in_batch", 0), h.get("accepted", 0) or 0, h.get("rejected", 0) or 0, h.get("recompute_rounds", 0) or 0)
        return ("<section id=changes class=sec><div class=hd><div><h2>What changed in batch %s</h2><p class=sub>%s. Struck words were removed, highlighted words added.</p></div></div><div class=cards>%s</div></section>"
                % (esc(d.batch), esc(sub), "".join(cards) or "<p class=sub>No change in this batch.</p>"))

    # -- dependencies: one lane per scope, a column per step (a fact sits right of what it is derived from)
    CW, CH, CG, RG, PAD, LH = 236, 54, 54, 10, 16, 30
    MAX_DEP_NODES = 80

    def deps(self):
        d = self.d
        edges = [(i, x, k) for i in d.order if d.units[i]["status"] in LIVE for k in ("derived_from", "coupled_with")
                 for x in d.units[i].get(k) or [] if x in d.units and d.units[x]["status"] in LIVE]
        if not edges:
            return ""
        nodes = sorted({e[0] for e in edges} | {e[1] for e in edges}, key=uid_key)
        more = len(nodes) - self.MAX_DEP_NODES
        nodes = nodes[: self.MAX_DEP_NODES]
        keep = set(nodes)
        edges = [e for e in edges if e[0] in keep and e[1] in keep]
        pairs = set(edges)   # a coupling listed on both facts is drawn and counted once
        edges = [e for e in edges if not (e[2] == "coupled_with" and (e[1], e[0], e[2]) in pairs and uid_key(e[0]) < uid_key(e[1]))]
        # columns: longest path along the links (a fact sits right of the facts it names), ignoring links that close a loop
        out_of = {n: [] for n in nodes}
        for a, b, k in edges:
            out_of[b].append(a)
        state, back = {}, set()
        for root in nodes:
            if root in state:
                continue
            stack = [(root, iter(out_of[root]))]
            state[root] = 1
            while stack:
                n, it = stack[-1]
                nxt = next(it, None)
                if nxt is None:
                    state[n] = 2
                    stack.pop()
                elif state.get(nxt) == 1:
                    back.add((n, nxt))
                elif nxt not in state:
                    state[nxt] = 1
                    stack.append((nxt, iter(out_of[nxt])))
        depth = {n: 0 for n in nodes}
        for _ in range(len(nodes)):
            moved = False
            for a, b, k in edges:
                if (b, a) not in back and depth[a] < depth[b] + 1:
                    depth[a], moved = depth[b] + 1, True
            if not moved:
                break
        lanes = {}
        for n in nodes:
            lanes.setdefault(d.units[n].get("scope") or "(no scope)", []).append(n)
        order = sorted(lanes, key=lambda sc: (min(depth[n] for n in lanes[sc]), sc == "(no scope)", sc.lower()))
        cols = max(depth.values()) + 1
        W = self.PAD * 2 + 14 + cols * (self.CW + self.CG) - self.CG + 40
        pos, y, out = {}, self.PAD, []
        for li, sc in enumerate(order):
            slots, rows = {}, 1
            for n in sorted(lanes[sc], key=lambda n: (depth[n], uid_key(n))):
                c = depth[n]
                r = slots.get(c, 0)
                slots[c] = r + 1
                rows = max(rows, r + 1)
                pos[n] = (self.PAD + 14 + c * (self.CW + self.CG), y + self.LH + r * (self.CH + self.RG))
            h = self.LH + rows * (self.CH + self.RG) + 6
            col = "var(--l%d)" % (li % 6 + 1)
            out.append("<rect class=lane x=%d y=%d width=%d height=%d rx=10 /><rect x=%d y=%d width=3 height=%d rx=1.5 fill='%s' />"
                       "<text class=ln x=%d y=%d style='fill:%s'>%s</text><text class=lns x=%d y=%d text-anchor=end>%d fact%s</text>"
                       % (self.PAD, y, W - 2 * self.PAD, h, self.PAD, y, h, col, self.PAD + 14, y + 20, col, esc(short(sc, max(12, int((W - 2 * self.PAD - 90) / 7.6)))),
                          W - self.PAD - 12, y + 20, len(lanes[sc]), "" if len(lanes[sc]) == 1 else "s"))
            for n in lanes[sc]:
                pos[n] = pos[n] + (col,)
            y += h + 12
        H = y + self.PAD - 12
        links = []
        for a, b, k in edges:
            (x1, y1, _), (x2, y2, _) = pos[b], pos[a]
            y1, y2 = y1 + self.CH / 2, y2 + self.CH / 2
            dash = " stroke-dasharray='5 4'" if k == "coupled_with" else ""
            if x2 > x1:   # left to right, from the source's right edge into the target's left edge
                x1 += self.CW
                dx = max(30, (x2 - x1) / 2)
                links.append("<path class=e d='M%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f'%s marker-end=url(#dga) />" % (x1, y1, x1 + dx, y1, x2 - dx, y2, x2 - 2, y2, dash))
            elif x2 < x1:  # a link back to the left (one that closes a loop): from the source's left edge into the target's right edge
                x2 += self.CW
                dx = max(30, (x1 - x2) / 2)
                links.append("<path class=e d='M%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f'%s marker-end=url(#dga) />" % (x1, y1, x1 - dx, y1, x2 + dx, y2, x2 + 2, y2, dash))
            else:         # same column (coupled facts): a loop out of the right edges
                x1, x2 = x1 + self.CW, x2 + self.CW
                links.append("<path class=e d='M%.1f %.1f C%.1f %.1f %.1f %.1f %.1f %.1f'%s marker-end=url(#dga) />" % (x1, y1, x1 + 34, y1, x2 + 34, y2, x2 + 2, y2, dash))
        cards = []
        for n in nodes:
            x, yy, col = pos[n]
            u = d.units[n]
            f = u["status"] == "needs_recheck"
            meta = " · ".join(t for t in (n, u.get("type") or "", "needs recheck" if f else ("your statement" if u.get("source_type") == "user" else "")) if t)
            lines = wrap2(u["content"], 36)
            cards.append("<g class=row data-chain='%s' data-tip=\"%s\"><rect class='card%s' x=%.1f y=%.1f width=%d height=%d rx=8 /><rect x=%.1f y=%.1f width=4 height=%d rx=2 fill='%s' />"
                         "<text class=meta x=%.1f y=%.1f>%s</text>%s</g>"
                         % (esc(d.chain_of[n][0]), esc(esc(short(u["content"], 260))), " f" if f else "", x, yy, self.CW, self.CH, x + 7, yy + 7, self.CH - 14, col,
                            x + 18, yy + 16, esc(short(meta, 40)),
                            "".join("<text class=ttl x=%.1f y=%.1f>%s</text>" % (x + 18, yy + 32 + k * 14, esc(t)) for k, t in enumerate(lines))))
        svg = ("<svg class=dg width=%d viewBox='0 0 %d %d' role=img aria-label='Dependency links between active facts, by scope'>"
               "<defs><marker id=dga viewBox='0 0 8 8' refX=7 refY=4 markerWidth=7 markerHeight=7 orient=auto><path d='M0 0L8 4L0 8z' fill=var(--mut) /></marker></defs>%s%s%s</svg>"
               % (W, W, H, "".join(out), "".join(links), "".join(cards)))
        legend = ("<div class=legend><span><svg width=26 height=8 style='display:inline;width:26px;vertical-align:middle'><path d='M0 4H26' stroke=var(--mut) stroke-width=1.6 /></svg> derived from</span>"
                  "<span><svg width=26 height=8 style='display:inline;width:26px;vertical-align:middle'><path d='M0 4H26' stroke=var(--mut) stroke-width=1.6 stroke-dasharray='5 4' /></svg> coupled with</span>"
                  "<span><i style='background:none;border:2px solid var(--warn)'></i>needs recheck</span></div>")
        return ("<section id=deps class=sec><div class=hd><div><h2>What depends on what</h2><p class=sub>Each lane is a scope. A fact sits to the right of the facts it is derived from: when one changes, the facts its arrows reach are flagged for recheck. Click a card for its versions and quotes.%s</p></div>%s</div><div class=scroll style='overflow-x:auto'>%s</div></section>"
                % (" %d more linked facts are not drawn." % more if more > 0 else "", legend, svg))

    # -- current state
    def state(self):
        d, live = self.d, self.d.live()
        rows = []
        scopes = sorted({u.get("scope") or "(no scope)" for u in live}, key=lambda s: (s == "(no scope)", s.lower()))
        for sc in scopes:
            rows.append("<tr class=scope><td colspan=4>%s</td></tr>" % esc(sc))
            for u in sorted([u for u in live if (u.get("scope") or "(no scope)") == sc], key=lambda u: (u["priority"] != "hard", uid_key(u["id"]))):
                n = len(d.chain_of[u["id"]]) - 1
                chips = ("<span class=chip>changed %d×</span>" % n if n else "") + ("<span class='chip u'>user statement</span>" if u.get("source_type") == "user" else "") + ("<span class='chip f'>needs recheck</span>" if u["status"] == "needs_recheck" else "")
                rows.append("<tr class=item data-s='%s' data-p=%s data-chain='%s'><td><span class='bar-h %s' style='width:%dpx'></span></td><td>%s<div class=note>%s · since %s %s</div></td><td>%s</td><td>%s</td></tr>"
                            % (esc(sc), esc(u["priority"]), esc(d.chain_of[u["id"]][0]), "f" if u["status"] == "needs_recheck" else ("soft" if u["priority"] == "soft" else ""), 22 if u["priority"] == "hard" else 12,
                               esc(u["content"]), esc(u["id"]), esc(fmt_day(u.get("valid_from") or day(u.get("event_time")))), chips, esc(u.get("type") or ""), esc(fmt_long(day(u.get("event_time"))))))
        opts = "".join("<option>%s</option>" % esc(s) for s in scopes)
        return ("<section id=state class=sec><div class=hd><div><h2>Current state</h2><p class=sub>%d facts, grouped by scope. Click one for its versions and quotes.</p></div>"
                "<div class=filters><select id=fs><option value=''>all scopes</option>%s</select><select id=fp><option value=''>any priority</option><option>hard</option><option>soft</option></select><input id=fq type=search placeholder=search></div></div>"
                "<table><tr><th></th><th>Fact</th><th>Type</th><th>Source date</th></tr>%s</table></section>" % (len(live), opts, "".join(rows)))

    # -- data for the drawer
    def detail(self):
        d, det, srcs = self.d, {}, {}
        for c in d.chains:
            vers = []
            for i in c:
                u = d.units[i]
                ev = []
                for e in u.get("evidence") or []:
                    s = d.srcs.get(e["unit_id"]) or {}
                    ev.append({"q": e.get("quote"), "u": e["unit_id"], "src": " · ".join(x for x in (fmt_long(day(s.get("event_time"))), s.get("provider"), s.get("filename") or s.get("author")) if x)})
                    if s.get("text"):
                        srcs[e["unit_id"]] = s["text"][:SOURCE_TEXT_LIMIT] + ("…" if len(s["text"]) > SOURCE_TEXT_LIMIT else "")
                vers.append({"id": i, "status": u["status"], "content": u["content"], "from": u.get("valid_from") or day(u.get("event_time")), "to": day(u.get("valid_to")) or None,
                             "why": u.get("supersede_reason"), "batch": u.get("batch"), "ev": ev, "user": u.get("source_type") == "user",
                             "rc": u.get("recheck_reason") if u["status"] == "needs_recheck" else None})
            det[c[0]] = vers
        j = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
        return "<script type=application/json id=detail>%s</script><script type=application/json id=srcs>%s</script>" % (j(det), j(srcs))

    def render(self):
        d = self.d
        live = d.live()
        flag = sum(1 for u in live if u["status"] == "needs_recheck")
        nchg = sum(1 for c in d.chains if len(c) > 1 or d.units[c[-1]]["status"] == "superseded")
        summ = ""
        p = os.path.join(d.ws, "reports", d.batch + ".summary.md")
        if os.path.exists(p):
            summ = self.summary(open(p, encoding="utf-8").read())
        gen = " · %s" % esc(d.generated) if getattr(d, "generated", None) else ""
        kp = [("", len(live), "facts holding now"), ("", nchg, "facts that changed"), ("warn" if flag else "", flag, "need recheck"),
              ("", len({u.get("scope") or "" for u in live}), "scopes"), ("", sum(len(u.get("evidence") or []) for u in d.units.values()), "quotes backing them")]
        head = ("<header><h1>%s</h1><p class=sub>State as of batch %s · documents up to %s · %d batches%s</p>"
                "<nav><a href='#trajectory'>How facts evolved</a><a href='#timeline'>Activity</a><a href='#changes'>This batch</a><a href='#deps'>Dependencies</a><a href='#state'>Current state</a></nav></header>%s"
                "<div class=kpis>%s</div>" % (esc(d.project()), esc(d.batch), esc(fmt_long(d.through.get(d.batch))), len(self.upto), gen, summ,
                                              "".join("<div class='kpi %s'><b>%d</b><span>%s</span></div>" % (c, n, esc(l)) for c, n, l in kp)))
        legend = ("<div class=legend><span><i style='background:var(--cur)'></i>current</span><span><i style='background:var(--old);height:5px'></i><i style='background:var(--old2);height:5px;margin-left:-3px'></i>earlier versions</span>"
                  "<span><i style='background:var(--user)'></i>your statement</span><span><i style='background:var(--warn)'></i>needs recheck</span>"
                  "<span>\u25cb a change</span><span><b style='color:var(--end)'>\u2715</b> ended, nothing replaced it</span></div>")
        traj = ("<section id=trajectory class=sec><div class=hd><div><h2>How each fact evolved</h2><p class=sub>One line per fact. Each dot is a change; the thick bar is the version that holds now. "
                "Hover for dates, click a row for every version and its quotes.</p></div>"
                "<div class=tabs><button data-view=changed aria-pressed=true>Changed only</button><button data-view=all aria-pressed=false>All facts</button></div></div>%s"
                "<div class=scroll>%s<div id=v-changed>%s</div><div id=v-all class=hidden>%s</div></div></section>" % (legend, self.axis_svg(), self.lanes(True), self.lanes(False)))
        act = ("<section id=timeline class=sec><div class=hd><div><h2>Activity per batch</h2><p class=sub>How much each batch moved the state.</p></div>"
               "<div class=legend><span><i style='background:var(--add)'></i>added</span><span><i style='background:var(--chg)'></i>replaced</span><span><i style='background:var(--rc)'></i>recomputed</span></div></div><div class=scroll>%s</div></section>" % self.activity())
        return ("<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>%s state</title><style>%s</style></head><body><main>%s%s%s%s%s%s</main>%s"
                "<div id=tip></div><aside id=drawer></aside>%s<script>%s</script></body></html>\n"
                % (esc(d.project()), CSS, head, traj, act, self.changes(), self.deps(), self.state(), self.footer(), self.detail(), JS))

    REF = re.compile(r"[\(\[]\s*((?:U-\d+)(?:\s*,\s*U-\d+)*)\s*[\)\]]")

    def summary(self, text):
        """The agent's summary lines as a list; [U-0023] references become links to the fact's versions and quotes."""
        d = self.d

        def refs(m):
            ids = [x.strip() for x in m.group(1).split(",")]
            return " ".join("<a class=ref href='#' data-chain='%s'>%s</a>" % (esc(d.chain_of[i][0]), esc(i)) if i in d.chain_of else "<span class=ref>%s</span>" % esc(i) for i in ids)
        marker = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])(?:\s+|$)")
        lines, open_ = [], False
        for raw in text.strip().splitlines():
            if not raw.strip():
                open_ = False
            elif raw.lstrip().startswith("#"):
                open_ = False
            elif marker.match(raw) or not open_:
                t = marker.sub("", raw).strip()
                open_ = bool(t)
                if t:
                    lines.append(t)
            else:   # a wrapped line, or a paragraph written under the earlier wording: part of the item above
                lines[-1] += " " + raw.strip()
        items = []
        for l in lines:
            parts, last = [], 0
            for m in self.REF.finditer(l):
                parts.append(esc(l[last:m.start()]).rstrip() + " " + refs(m))
                last = m.end()
            parts.append(esc(l[last:]))
            items.append("<li>%s</li>" % "".join(parts).strip())
        return "<section class=summary><h2>Where things stand</h2><ul>%s</ul></section>" % "".join(items) if items else ""

    def footer(self):
        # ferris CLI only: a link line under delivery reports (off with "tips": false in config.json)
        cfg = self.d.cfg
        if (cfg.get("profile") or "delivery") != "delivery" or cfg.get("tips") is False:
            return ""
        mark = "".join("<rect x=%d y=%d width=%d height=%d />" % r for r in FERRIS_MARK)
        return ("<footer class=brand><a href='https://tryferris.ai' target='_blank' rel='noopener'><svg viewBox='0 0 991 992' fill='#D95400' aria-hidden=true>%s</svg>Made with Ferris</a>"
                "<span>·</span><span>track every delivery project, at team scale, at <a href='https://tryferris.ai' target='_blank' rel='noopener' style='font-weight:400;text-decoration:underline'>tryferris.ai</a></span></footer>" % mark)


# ---------------------------------------------------------------- brief
def brief(d):
    c = d.changes()
    live = d.live()
    lines = ["# %s: current state" % d.project(), "",
             "As of batch %s, documents up to %s. %d active units (%d need recheck). Each line: [id · priority · type · since] content. "
             "Evidence (verbatim quotes with source units) is in store/%s.json under `evidence`." % (d.batch, d.through.get(d.batch), len(live), len(c["flagged"]), d.batch), ""]
    scopes = {}
    for u in live:
        scopes.setdefault(u.get("scope") or "", []).append(u)
    body, used, cut = [], sum(len(x) + 1 for x in lines), 0
    order = sorted(live, key=lambda u: (u["priority"] != "hard", uid_key(u["id"])))
    keep = set()
    for u in order:                                     # hard units first; soft ones only while there is room
        line = "- [%s · %s · %s · %s] %s%s" % (u["id"], u["priority"], u.get("type") or "-", u.get("valid_from") or day(u.get("event_time")) or "?", short(u["content"], 240),
                                                 "  (NEEDS RECHECK: %s)" % short(u.get("recheck_reason"), 120) if u["status"] == "needs_recheck" else "")
        if used + len(line) + 1 > BRIEF_LIMIT - 1200:
            cut += 1
            continue
        used += len(line) + 1
        keep.add(u["id"])
    for sc in sorted(scopes, key=lambda s: (s == "", s.lower())):
        us = [u for u in sorted(scopes[sc], key=lambda u: (u["priority"] != "hard", uid_key(u["id"]))) if u["id"] in keep]
        if us:
            body += ["## %s" % (sc or "(no scope)")] + ["- [%s · %s · %s · %s] %s%s" % (u["id"], u["priority"], u.get("type") or "-", u.get("valid_from") or day(u.get("event_time")) or "?", short(u["content"], 240),
                                                                                       "  (NEEDS RECHECK: %s)" % short(u.get("recheck_reason"), 120) if u["status"] == "needs_recheck" else "") for u in us] + [""]
    if cut:
        body.append("(%d more units not shown: see store/%s.json)" % (cut, d.batch))
        body.append("")
    chg = ["## Changed in batch %s" % d.batch]
    for i in c["added"][:12]:
        chg.append("- added: " + short(d.units[i]["content"], 160))
    for i in (c["superseded"] + c["recomputed"])[:12]:
        u = d.units[i]
        new = d.units.get(u.get("superseded_by") or "")
        chg.append("- %s: %s → %s" % ("replaced" if i in c["superseded"] else "recomputed", short(u["content"], 100), short(new["content"], 120) if new else "no longer holds"))
    if len(chg) == 1:
        chg.append("- nothing")
    return "\n".join(lines + body + chg).rstrip() + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Render project-state HTML report and brief.md")
    ap.add_argument("ws")
    ap.add_argument("--batch")
    ap.add_argument("--generated", help="text shown in the header (e.g. a timestamp); omitted if not given")
    a = ap.parse_args(argv)
    ws = os.path.abspath(a.ws)
    d = Data(ws, a.batch)
    d.generated = a.generated or None
    page = Page(d).render()
    rep = os.path.join(ws, "reports", d.batch + ".html")
    write_text(rep, page)
    write_text(os.path.join(ws, "current", "report.html"), page)
    write_text(os.path.join(ws, "current", "brief.md"), brief(d))
    print(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
