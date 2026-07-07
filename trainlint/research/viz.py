#!/usr/bin/env python3
"""Visualize the research search — ANY TIME, on demand.

  python3 viz.py [project]

Emits ONE self-contained HTML report (zero external deps — no graphviz, no fonts, no JS
libs) to research/viz/<project>.html, and prints a compact ASCII summary + that path to
stdout (so the terminal and the SessionStart hook still get a one-glance answer).

The report weaves the layers the substrate already records, each on its natural axis
— it INVENTS no data, it only renders what plan.py / tree.py / surfacer already compute:

  1. STORY       — the whole project as ONE 5-beat narrative arc: 想做什么 (总分总: headline ·
                   core pillars · done-bar) · 遇到问题 · BOTTLENECK · 干了什么 · 要做什么.   (from plan.* + log)
  2. TIMELINE    — the dated story: experiment / wall / verdict / backtrack, in order, with
                   a wall linking to the paper it unlocks.            (from the annotation log)
  3. SPINE+TREE  — the phase-ordered DECISION spine (what we know) beside the SEARCH tree
                   (the directions explored), with knowledge-readiness edges off the walls.

PLANNING STAGE — a project with a plan but NO experiments yet (no log events, no search tree).
The mature arc above leans on the log, so before any run it renders empty boxes and reads as
broken. When `planning` is detected, the report instead tells a tight, all-English plan story —
MOTIVATION (from motivation.<name>.txt) · GOAL (总分总) · MAIN THREAD · NEXT — over a full-width
decision spine, and SUPPRESSES the timeline, search tree, and pipeline band (all empty/abstract
at this stage). A project graduates to the mature view automatically once it logs its first event.
"""
import difflib
import hashlib
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree   # noqa: E402
import plan   # noqa: E402
import paths  # noqa: E402  — per-project data lives outside the versioned plugin dir

ROOT = Path(__file__).resolve().parent

# status palettes (reused by ascii + html + svg) ---------------------------------------
TREE_ICON = {"open": "·", "deepening": "▸", "stalled": "⚠", "abandoned": "✗", "won": "★"}
TREE_FILL = {"open": "#e2e8f0", "deepening": "#bfdbfe", "stalled": "#fde68a",
             "abandoned": "#fecaca", "won": "#bbf7d0"}
TREE_EDGE = {"open": "#94a3b8", "deepening": "#3b82f6", "stalled": "#d97706",
             "abandoned": "#dc2626", "won": "#16a34a"}
DEC_ICON = {"verified": "✓", "decided": "◐", "open": "○"}
DEC_COLOR = {"verified": "#16a34a", "decided": "#d97706", "open": "#64748b"}


def _dec_glyph(n):
    """(glyph, color) for a decision in the spine. Splits `decided` into BUILT (◐) vs decided-on-
    PAPER (✎) via plan.artifact_exists, so the one-glance spine never paints an unbuilt choice the
    same as one that produced an artifact."""
    st = n.get("status", "open")
    if st == "decided" and not plan.artifact_exists(n):
        return ("✎", "#b45309")
    return (DEC_ICON.get(st, "?"), DEC_COLOR.get(st, "#64748b"))
KIND = {  # (glyph, color, label)
    "experiment": ("●", "#2563eb", "experiment"),
    "wall":       ("⚠", "#d97706", "wall"),
    "abandon":    ("↩", "#dc2626", "backtrack"),
    "verdict":    ("★", "#16a34a", "verdict"),
    "hypothesis": ("◆", "#7c3aed", "hypothesis"),
    "deadend":    ("✗", "#64748b", "dead end"),
    "trunk-check":("✓", "#0d9488", "trunk-check"),
    # milestone kinds the execute loop writes ("what we did" -> timeline)
    "build":      ("⬢", "#2563eb", "built"),
    "verify":     ("✓", "#16a34a", "verified"),
    "probe":      ("◆", "#0891b2", "probe"),
    "decide":     ("●", "#0d9488", "decided"),
    "note":       ("•", "#64748b", "note"),
}


def _e(s):
    return html.escape(str(s), quote=True)


def _ec(s):
    """Escape HTML, then show the DATA as code: (1) markdown `...` spans -> <code>, and
    (2) any bare <|...|> token (speaker/control markers) -> <code>, even without backticks —
    so data tokens never render as plain prose. The lookbehind avoids double-wrapping (1)'s output."""
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", _e(s))
    out = re.sub(r"(?<!<code>)(&lt;\|[^|]*\|&gt;)", r"<code>\1</code>", out)
    return out


def stable_line_id(kind, obj):
    """Deterministic id for a surprises/focus jsonl ROW. MUST stay byte-identical to
    chat_backend.stable_line_id (copied verbatim) so the id baked into data-e-id matches the id the
    /edit backend recomputes to locate the line. Contract:
        if the row already carries a non-empty "id" (focus rows do, and the backend PINS one into a
            surprise row on its first edit) -> that id wins, unchanged;
        else id = kind[0] + "-" + sha1( kind + ":" + canon ).hexdigest()[:12]
        where canon = json.dumps({k: v for k, v in obj.items() if k != "id"},
                                 sort_keys=True, ensure_ascii=False, separators=(",", ":")).
    Both sides parse the SAME jsonl line into the SAME dict, so sort_keys makes key order irrelevant
    and the hashes agree; once the backend pins an id both sides read it and the id stays stable even
    though the edit changed the hashed content."""
    if obj.get("id"):
        return str(obj["id"])
    canon = json.dumps({k: v for k, v in obj.items() if k != "id"},
                       sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return kind[:1] + "-" + hashlib.sha1((kind + ":" + canon).encode("utf-8")).hexdigest()[:12]


def _eattr(kind, field, prev, id="", type="", opts="", render=""):
    """The data-e-* hooks the inline editor (EDIT_JS) reads off an editable element. kind/id/field
    name the substrate target; data-e-prev is the RAW stored value — the optimistic-concurrency
    `prev` the backend checks — kept independent of any glossary/markdown decoration in the visible
    text. type='select' + opts drive a dropdown; render='glyph' re-paints a status glyph on save."""
    a = (f' data-e-kind="{_e(kind)}" data-e-field="{_e(field)}"'
         f' data-e-id="{_e(id)}" data-e-prev="{_e(prev)}"')
    if type:
        a += f' data-e-type="{_e(type)}"'
    if opts:
        a += f' data-e-opts="{_e(opts)}"'
    if render:
        a += f' data-e-render="{_e(render)}"'
    return a


def _natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def _trunc(s, n):
    s = str(s)
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


# --- data shaping ---------------------------------------------------------------------

def split_goal(text):
    """goal.<name>.txt -> (goal, bar). Splits on the 'bar for "done"' clause; drops a
    trailing 'Pillars: ...' sentence (pillars come from the plan, not the prose)."""
    text = " ".join((text or "").split())
    m = re.search(r"\bPillars?\s*:", text)
    if m:
        text = text[:m.start()].strip()
    i = text.lower().find("bar for")
    if i == -1:  # goals also phrase the criterion as 'DONE = …' — split there too
        m = re.search(r"\bDONE\s*=", text)
        i = m.start() if m else -1
    if i == -1:
        return text, ""
    return text[:i].rstrip(" ;.—-").strip(), text[i:].strip()


def wall_paper(wall, knowledge):
    """surfacer's rule: a wall unlocks a paper when one of its `match` keywords is a
    substring of the wall text (and it's not already read)."""
    for k in knowledge:
        if k.get("read"):
            continue
        if any(str(m).lower() in str(wall).lower() for m in k.get("match", [])):
            return k
    return None


def newly_done(name):
    """(set of decision-ids touched on the LATEST log date, that date) — powers the 🆕 NEW badge so
    a reader can track what changed THIS run. Derived from the dated log; no per-decision field."""
    try:
        lp = paths.resolve(f"log.{name}.jsonl")
        if not lp.exists():
            return set(), ""
        ev = []
        for line in lp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = e.get("ts") or e.get("date")
            d = e.get("direction") or e.get("decision")
            if ts and d and e.get("kind") in ("build", "verify", "decide", "probe"):
                ev.append((ts, d))
        if not ev:
            return set(), ""
        latest = max(t for t, _ in ev)
        return {d for t, d in ev if t == latest}, latest
    except Exception:
        return set(), ""


def newly_done_notes(name):
    """(list of (kind, plain-note) on the LATEST log date, that date) — powers the human-readable
    'Newly done' line so a reader sees WHAT changed in plain words, not raw kebab decision-ids."""
    try:
        lp = paths.resolve(f"log.{name}.jsonl")
        if not lp.exists():
            return [], ""
        ev = []
        for line in lp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = e.get("ts") or e.get("date")
            note = e.get("note") or ""
            if ts and note and e.get("kind") in ("build", "verify", "decide", "probe"):
                ev.append((ts, e.get("kind"), note))
        if not ev:
            return [], ""
        latest = max(t for t, _, _ in ev)
        return [(k, n) for t, k, n in ev if t == latest], latest
    except Exception:
        return [], ""


def timeline_rows(events, knowledge):
    """The dated story — annotation events that carry a ts, oldest first."""
    rows = []
    for e in events:
        # accept common aliases so a "what we did" entry still lands on the timeline:
        # ts<-date, note<-finding/text, direction<-decision. A milestone is never dropped
        # just because the writer used the natural field name.
        ts = e.get("ts") or e.get("date")
        if not ts:
            continue  # structured run-events have no date; they live in the tree, not the story
        kind = e.get("kind", "experiment")
        note = e.get("note") or e.get("finding") or e.get("text") or ""
        paper = wall_paper(note, knowledge) if kind == "wall" else None
        rows.append({"ts": ts, "kind": kind,
                     "direction": e.get("direction") or e.get("decision") or "?",
                     "note": note, "delta": e.get("delta"), "paper": paper})
    rows.sort(key=lambda r: (r["ts"], 0))
    return rows


def spine_groups(pl):
    """Decisions grouped by phase, phases in first-appearance order (no hard-coded list)."""
    phases, by = [], {}
    for n in pl:
        ph = n.get("phase", "")
        if ph not in by:
            phases.append(ph)
            by[ph] = []
        by[ph].append(n)
    return [(ph, by[ph]) for ph in phases]


# --- the search-tree SVG --------------------------------------------------------------

# A node's SEARCH STATUS, derived from the KINDS of events on a direction — meaningful even
# before any run lands. A wall you hit, a wall you closed by reasoning, and a wall you closed
# by an experiment are three DIFFERENT states; the old experiment-only status collapsed them
# all to "open / 0 run" (a resolved problem read as "nothing happened"). (glyph, fill, edge, label)
SS = {
    "tested":      ("◆", "#dbeafe", "#2563eb", "tested"),
    "stalled":     ("⚠", "#fde68a", "#d97706", "stalled"),
    "won":         ("★", "#bbf7d0", "#16a34a", "won"),
    "resolved":    ("✓", "#dcfce7", "#16a34a", "wall closed"),
    "open-wall":   ("⚠", "#fee2e2", "#dc2626", "open problem"),
    "backtracked": ("↩", "#f5d0fe", "#a21caf", "backtracked"),
    "decided":     ("●", "#cffafe", "#0891b2", "decided"),
    "checked":     ("✓", "#ccfbf1", "#0d9488", "checked"),
    "idea":        ("○", "#ede9fe", "#7c3aed", "idea"),
    "open":        ("·", "#e2e8f0", "#94a3b8", "open"),
}


def search_status(node, kinds):
    """Status from the kinds seen on this direction. Experiment-driven nodes keep the
    governor's stalled/won nuance; pre-experiment nodes get a wall-resolution state."""
    ks = set(kinds)
    if "abandon" in ks:
        return "backtracked"
    if node.get("spend", 0) > 0 or "experiment" in ks:
        bt = node.get("status")
        return bt if bt in ("stalled", "won") else "tested"
    if "wall" in ks and ("verdict" in ks or "trunk-check" in ks):
        return "resolved"
    if "wall" in ks:
        return "open-wall"
    if "verdict" in ks:
        return "decided"
    if "trunk-check" in ks:
        return "checked"
    if "hypothesis" in ks:
        return "idea"
    return "open"


def build_groups(nodes, id2phase, phase_order):
    """Choose the trunk axis. Real CHECKPOINT LINEAGE when run-parents exist (mature,
    experiment-driven search — e.g. an experiment-driven run prefix). Otherwise group by plan PHASE, so a
    pre-experiment project (walls + decisions, no runs yet) still reads as a real tree
    AND shares the spine's vocabulary. Returns (groups, axis-name)."""
    by_parent = {}
    for n in nodes.values():
        by_parent.setdefault(n.get("parent"), []).append(n)
    anchors = sorted([p for p in by_parent if p], key=_natkey)
    if anchors:
        groups = [(_trunc(a, 12), sorted(by_parent[a], key=lambda n: n["direction"])) for a in anchors]
        if None in by_parent:
            groups.append(("(roots)", sorted(by_parent[None], key=lambda n: n["direction"])))
        return groups, "checkpoint lineage"
    buckets = {}
    for n in nodes.values():
        buckets.setdefault(id2phase.get(n["direction"]) or "(exploratory)", []).append(n)
    order = [p for p in phase_order if p in buckets] + [p for p in buckets if p not in phase_order]
    return [(ph, sorted(buckets[ph], key=lambda n: n["direction"])) for ph in order], "phase"


def tree_svg(nodes, knowledge, kinds, id2phase, phase_order):
    """A real tree: a faint vertical TRUNK (phase, or checkpoint lineage when runs exist),
    each branch a direction card colored by its search status, dashed READ? edges off walls."""
    if not nodes:
        return ('<div class="empty">No directions explored yet — the search tree fills in '
                'as walls get hit and runs land. (The decision spine on the left is where '
                'the project stands today.)</div>')
    groups, axis = build_groups(nodes, id2phase, phase_order)

    PAD, TRUNK_X, CARD_X, CARD_W = 16, 120, 182, 250
    KN_X, KN_W = CARD_X + CARD_W + 78, 196
    WIDTH = KN_X + KN_W + PAD

    cards, anchor_pts = [], []
    y = PAD + 10
    for label, kids in groups:
        top, start = y, len(cards)
        for n in kids:
            walls = n.get("walls", [])[:2]
            h = 50 + 18 * len(walls)
            cards.append({"x": CARD_X, "y": y, "w": CARD_W, "h": h, "n": n, "walls": walls})
            y += h + 14
        ay = (top + (y - 14)) / 2
        anchor_pts.append({"y": ay, "label": _trunc(label, 12)})
        for c in cards[start:]:
            c["ay"] = ay
        y += 22

    kn_pos, ky = {}, PAD + 10
    for c in cards:
        for w in c["walls"]:
            p = wall_paper(w, knowledge)
            if p and p["id"] not in kn_pos:
                kn_pos[p["id"]] = {"x": KN_X, "y": ky, "w": KN_W, "h": 50, "p": p}
                ky += 64
    height = max(y, ky) + PAD

    S = [f'<svg viewBox="0 0 {WIDTH} {int(height)}" width="100%" '
         f'preserveAspectRatio="xMinYMin meet" font-family="-apple-system,Segoe UI,Roboto,sans-serif">']
    if anchor_pts:
        S.append(f'<line x1="{TRUNK_X}" y1="{anchor_pts[0]["y"]}" x2="{TRUNK_X}" '
                 f'y2="{anchor_pts[-1]["y"]}" stroke="#cbd5e1" stroke-width="3"/>')
    # connectors trunk -> card
    for c in cards:
        cy = c["y"] + c["h"] / 2
        S.append(f'<path d="M {TRUNK_X} {c["ay"]} C {TRUNK_X+34} {c["ay"]}, '
                 f'{CARD_X-34} {cy}, {CARD_X} {cy}" fill="none" stroke="#cbd5e1" stroke-width="2"/>')
    # trunk nodes + labels
    for ap in anchor_pts:
        S.append(f'<circle cx="{TRUNK_X}" cy="{ap["y"]}" r="7" fill="#fff" '
                 f'stroke="#94a3b8" stroke-width="2.5"/>')
        S.append(f'<text x="{TRUNK_X-13}" y="{ap["y"]+4}" text-anchor="end" '
                 f'font-size="11" font-weight="600" fill="#475569">{_e(ap["label"])}</text>')
    # knowledge dashed edges (card wall -> paper)
    for c in cards:
        for w in c["walls"]:
            p = wall_paper(w, knowledge)
            if not p:
                continue
            kp = kn_pos[p["id"]]
            S.append(f'<path d="M {c["x"]+c["w"]} {c["y"]+c["h"]-12} C '
                     f'{c["x"]+c["w"]+34} {c["y"]+c["h"]-12}, {KN_X-34} {kp["y"]+25}, '
                     f'{KN_X} {kp["y"]+25}" fill="none" stroke="#34d399" '
                     f'stroke-width="1.6" stroke-dasharray="5 4"/>')
    # cards
    for c in cards:
        n = c["n"]
        g, fill, edge, slabel = SS[search_status(n, kinds.get(n["direction"], []))]
        x, yy, w, h = c["x"], c["y"], c["w"], c["h"]
        S.append(f'<rect x="{x}" y="{yy}" width="{w}" height="{h}" rx="9" '
                 f'fill="{fill}" stroke="{edge}" stroke-width="1.5"/>')
        S.append(f'<rect x="{x}" y="{yy}" width="5" height="{h}" rx="2.5" fill="{edge}"/>')
        S.append(f'<text x="{x+14}" y="{yy+21}" font-size="14" font-weight="700" '
                 f'fill="#0f172a">{g} {_e(_trunc(n["direction"],24))}</text>')
        meta = slabel
        if n.get("spend", 0) > 0:
            meta += f' · {n["spend"]} run' + ("s" if n["spend"] != 1 else "")
        dz = n.get("deltas", [])[-3:]
        if dz:
            meta += " · Δ " + " ".join((f"+{d}" if d > 0 else f"{d}") for d in dz)
        S.append(f'<text x="{x+14}" y="{yy+39}" font-size="11.5">'
                 f'<tspan fill="{edge}" font-weight="700">{_e(slabel)}</tspan>'
                 f'<tspan fill="#475569">{_e(meta[len(slabel):])}</tspan></text>')
        for i, w_ in enumerate(c["walls"]):
            S.append(f'<text x="{x+14}" y="{yy+57+18*i}" font-size="11" fill="#b45309">'
                     f'⚠ {_e(_trunc(w_,32))}</text>')
    # knowledge cards
    for kp in kn_pos.values():
        x, yy, w, h = kp["x"], kp["y"], kp["w"], kp["h"]
        S.append(f'<rect id="kn-{_e(kp["p"]["id"])}" x="{x}" y="{yy}" width="{w}" height="{h}" '
                 f'rx="9" fill="#ecfdf5" stroke="#34d399" stroke-width="1.5" stroke-dasharray="5 4"/>')
        S.append(f'<text x="{x+12}" y="{yy+19}" font-size="10.5" font-weight="700" fill="#047857">📖 READ?</text>')
        S.append(f'<text x="{x+12}" y="{yy+37}" font-size="11" fill="#065f46">{_e(_trunc(kp["p"]["title"],28))}</text>')
    S.append("</svg>")
    return (f'<div class="treecap">trunk = <b>{_e(axis)}</b> · '
            f'card colour = search status (⚠ open problem · ✓ wall closed · ◆ tested)</div>'
            + "\n".join(S))


# --- HTML assembly --------------------------------------------------------------------

CSS = """
:root{--ink:#0f172a;--mut:#64748b;--line:#e2e8f0;--bg:#f1f5f9}
html{scroll-padding-top:58px}  /* anchor jumps must land below the sticky .rnav bar */
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,sans-serif;line-height:1.45}
.wrap{max-width:1120px;margin:0 auto;padding:22px}
/* --- section nav: the report body is TABBED — a sticky bar switches which section is on screen,
   so a reader lands on one view at a time instead of one endless scroll. Print shows everything.
   PROGRESSIVE: hiding is gated on html.js, which only NAV_JS sets — in a JS-stripped viewer
   (phone inline preview, sanitizing proxies) no script runs, the class never lands, and every
   section renders stacked instead of the whole body going blank. --- */
.rnav{position:sticky;top:0;z-index:60;display:none;gap:7px;overflow-x:auto;scrollbar-width:none;
  background:rgba(241,245,249,.93);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  padding:10px 2px 9px;margin:14px -2px 4px;border-bottom:1px solid var(--line)}
html.js .rnav{display:flex}  /* the tab bar is dead weight without its JS — show only when live */
.rnav::-webkit-scrollbar{display:none}
.rnav button{flex:0 0 auto;border:1px solid var(--line);background:#fff;color:#334155;border-radius:20px;
  padding:5px 13px;font-size:12.5px;font-weight:600;cursor:pointer;white-space:nowrap;transition:background .15s}
.rnav button:hover{background:#eef2f7}
.rnav button.on{background:#0f172a;color:#fff;border-color:#0f172a}
html.js .rsec{display:none}
html.js .rsec.on{display:block;animation:rsec-in .18s ease}
@keyframes rsec-in{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
@media print{.rnav{display:none}.rsec{display:block!important}}
.hdr{background:linear-gradient(135deg,#0f172a,#1e293b);color:#e2e8f0;border-radius:16px;padding:22px 26px}
.hdr h1{margin:0 0 2px;font-size:22px}
.hdr .sub{color:#94a3b8;font-size:13px;margin-bottom:14px}
.hdr .tldr{background:rgba(148,163,184,.12);border-left:3px solid #7dd3fc;border-radius:8px;padding:12px 15px;margin:0 0 15px;font-size:14px;line-height:1.55;color:#e2e8f0}
.hdr .tldr .tldr-tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.05em;color:#7dd3fc;margin-right:8px;vertical-align:1px}
.hdr .lead{font-size:15px;line-height:1.5;color:#e2e8f0;margin:0 0 15px}
.hdr .llm{font-size:14px;line-height:1.55;color:#e2e8f0}
.hdr .llm h4{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#7dd3fc;margin:14px 0 6px}
.hdr .llm ul{margin:4px 0;padding-left:20px;display:flex;flex-direction:column;gap:5px}
.hdr .llm p{margin:6px 0}
.hdr .tldr-list{margin:7px 0 0;padding-left:20px;display:flex;flex-direction:column;gap:5px}
.hdr .tldr-list li{font-size:14px;line-height:1.5;color:#e2e8f0}
.hdr .funnel{margin:2px 0 16px}
.hdr .funnel-title{font-size:11px;font-weight:700;letter-spacing:.05em;color:#7dd3fc;margin:0 0 9px;text-transform:uppercase}
.hdr .rung{display:flex;gap:11px;align-items:baseline;padding:5px 0 5px 12px;border-left:2px solid #334155}
.hdr .rung-l{font-size:10px;font-weight:700;letter-spacing:.04em;color:#94a3b8;text-transform:uppercase;min-width:104px;flex-shrink:0}
.hdr .rung-t{font-size:13.5px;color:#e2e8f0;line-height:1.45}
.hdr .rung-now{border-left-color:#c4b5fd;background:rgba(196,181,253,.10);border-radius:0 8px 8px 0}
.hdr .rung-now .rung-l{color:#c4b5fd}
.hdr .rung-now .rung-t{font-weight:700;color:#fff}
.kv{display:flex;gap:10px;margin:7px 0;font-size:14px}
.kv .k{flex:0 0 56px;color:#7dd3fc;font-weight:700;font-size:12px;letter-spacing:.04em;padding-top:1px}
.now{background:#0b1220;border:1px solid #334155;border-radius:10px;padding:10px 13px;margin-top:6px}
.now .k{color:#fbbf24}
.story{display:flex;flex-direction:column;gap:9px;margin:16px 0 4px}
.beat{display:grid;grid-template-columns:108px minmax(0,1fr);gap:13px;align-items:start}
.beat .bl{font-size:11.5px;font-weight:700;letter-spacing:.03em;padding-top:1px;white-space:nowrap}
.beat .bt{font-size:14px;color:#e2e8f0;line-height:1.4}
.beat .bt .sm{display:block;color:#94a3b8;font-size:12px;margin-top:2px}
.beat.want .bl{color:#7dd3fc}
.beat.prob .bl{color:#fca5a5}
.beat.neck .bl{color:#fbbf24}
.beat.did .bl{color:#86efac}
.beat.next .bl{color:#c4b5fd}
.beat.neck{background:#0b1220;border:1px solid #334155;border-radius:10px;padding:9px 13px}
.beat .blist{margin:7px 0 4px;padding-left:18px;display:flex;flex-direction:column;gap:4px}
.beat .blist li{font-size:13px;color:#cbd5e1;line-height:1.4}
.beat .blist li b{color:#bae6fd;font-weight:700}
.beat .tail{margin-top:7px;font-size:13px;color:#cbd5e1;border-top:1px solid #1e293b;padding-top:7px}
.beat .tail b{color:#fbbf24;font-weight:700}
@media(max-width:640px){.beat{grid-template-columns:1fr;gap:2px}.beat.neck{padding:9px 12px}}
.score{display:flex;align-items:center;gap:12px;margin-top:14px;flex-wrap:wrap}
.dots span{font-size:17px;letter-spacing:1px}
.score .lbl{font-size:13px;color:#cbd5e1}
.chips{display:flex;gap:7px;flex-wrap:wrap;margin-top:11px}
.chip{background:#1e293b;border:1px solid #475569;color:#e2e8f0;border-radius:999px;padding:3px 11px;font-size:12px}
.chip.pillar{border-color:#7dd3fc;color:#bae6fd}
.rej{margin-top:11px;font-size:12.5px;color:#fca5a5}
.rej b{color:#f87171}
.legend{display:flex;gap:18px;flex-wrap:wrap;background:#fff;border:1px solid var(--line);
  border-radius:12px;padding:11px 16px;margin:16px 0;font-size:12.5px;color:var(--mut)}
.legend b{color:var(--ink)}
h2.sec{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--mut);margin:22px 2px 10px}
.card{background:#fff;border:1px solid var(--line);border-radius:14px;padding:6px 4px}
/* timeline */
.tl{position:relative;padding:6px 8px}
.tl .row{display:grid;grid-template-columns:74px 26px 1fr;gap:8px;padding:9px 8px;border-bottom:1px solid #f1f5f9;align-items:start}
.tl .row:last-child{border-bottom:0}
.tl .date{font-size:12px;color:var(--mut);font-variant-numeric:tabular-nums;padding-top:2px}
.tl .mk{font-size:16px;text-align:center;line-height:1.2}
.tl .body{font-size:13.5px}
.tl .dir{font-weight:700}
.tl .knd{font-size:11px;color:var(--mut);margin-left:6px;text-transform:uppercase;letter-spacing:.03em}
.tl .delta{font-weight:700;margin-left:6px}
.tl .up{color:#16a34a}.tl .flat{color:#94a3b8}
.tl .note{color:#334155}
.tl a.read{display:inline-block;margin-top:3px;font-size:12px;color:#047857;text-decoration:none;
  background:#ecfdf5;border:1px solid #a7f3d0;border-radius:7px;padding:1px 8px}
/* two-column */
.cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.05fr);gap:18px;align-items:start}
@media(max-width:820px){.cols{grid-template-columns:1fr}}
.phase{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:#94a3b8;margin:12px 8px 4px;font-weight:700}
.implgrp{margin-top:6px;border-top:1px dashed #e2e8f0}
.implgrp>summary{cursor:pointer;list-style:none;user-select:none}
.implgrp>summary::-webkit-details-marker{display:none}
.implgrp>summary::before{content:'▸ ';color:#94a3b8}
.implgrp[open]>summary::before{content:'▾ '}
details.dec{border-bottom:1px solid #f1f5f9;padding:2px 8px}
details.dec:last-child{border-bottom:0}
details.dec>summary{list-style:none;cursor:pointer;display:flex;gap:9px;align-items:baseline;padding:8px 0}
details.dec>summary::-webkit-details-marker{display:none}
.gl{font-size:15px;flex:0 0 auto;line-height:1.2}
.dsum{flex:1 1 auto}
.dq{font-size:13.5px;font-weight:600}
.dch{font-size:12.5px;color:#475569}
.you{font-size:10.5px;font-weight:700;color:#b45309;background:#fef3c7;border-radius:6px;padding:1px 6px;margin-left:6px}
.pill-tag{font-size:10px;color:#0369a1;background:#e0f2fe;border-radius:6px;padding:1px 6px;margin-left:4px}
.dwhy{font-size:12.5px;color:#475569;padding:0 0 9px 24px}
.dwhy .pr{display:inline-block;background:#f1f5f9;border-radius:6px;padding:1px 7px;color:#334155;font-size:11.5px}
.draw{margin:2px 0 9px 24px}
.draw>summary{cursor:pointer;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#2563eb;user-select:none;list-style:none}
.draw>summary::-webkit-details-marker{display:none}
.draw>summary::before{content:'▸ ';color:#94a3b8}
.draw[open]>summary::before{content:'▾ '}
.dex{margin:6px 0 2px 6px}
.exitem{margin-bottom:10px}
.excap{font-size:11px;color:#64748b;margin-bottom:4px}
.excode{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px;color:#0f172a;background:#f1f5f9;border:1px solid #e2e8f0;border-radius:6px;padding:8px 11px;margin:0;white-space:pre;overflow-x:auto;line-height:1.55}
.dchfull{font-size:12px;color:#475569;padding:6px 0 2px 10px;margin-top:4px;white-space:pre-wrap;border-left:2px solid #e2e8f0}
.anch-tag{font-size:10px;font-weight:700;border-radius:6px;padding:1px 6px;margin-left:4px;white-space:nowrap}
.anch-tag.ok{color:#166534;background:#dcfce7}
.anch-tag.warn{color:#92400e;background:#fef3c7}
.anch-tag.miss{color:#b91c1c;background:#fee2e2}
.anch-red{color:#b91c1c}
.anccap{font-size:11px;color:#334155;margin-bottom:4px;line-height:1.9}
.anccap code{background:#f1f5f9;border-radius:4px;padding:0 4px;font-size:10.5px}
.anclink{display:inline-block;margin-left:8px;font-size:11px;font-weight:700;color:#fff;
  background:#2563eb;border-radius:14px;padding:2px 10px;text-decoration:none;white-space:nowrap}
.anclink:hover{background:#1d4ed8}
.ancbar{display:block;background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;color:#1e3a8a;
  font-size:13px;padding:11px 15px;margin:12px 0 4px;text-decoration:none}
.ancbar:hover{background:#dbeafe}
.ancbar .anch-red{color:#b91c1c}
.ancbar-go{float:right;font-weight:700;color:#2563eb}
.anccode{max-height:340px;overflow:auto}
.anccmd{font-size:10.5px;color:#94a3b8;margin-top:3px}
.anccmd code{user-select:all;background:#f8fafc;border-radius:4px;padding:0 4px}
.anch-stub{font-size:11.5px;color:#b91c1c;margin:2px 0 9px 24px}
.anch-stub code{background:#fef2f2;border-radius:4px;padding:0 4px;font-size:10.5px}
.dfa{color:#166534;background:#f0fdf4;display:block}
.dfr{color:#b91c1c;background:#fef2f2;display:block}
.surprises{margin:16px 0 4px}
.surp-title{font-size:14px;font-weight:800;color:#0f172a;margin:0 0 9px}
.surp{border-left:4px solid #94a3b8;background:#f8fafc;border-radius:0 9px 9px 0;padding:9px 13px;margin:0 0 8px}
.surp-h{display:flex;align-items:center;gap:8px;margin-bottom:3px}
.surp-badge{font-size:11px;font-weight:700;letter-spacing:.02em;text-transform:uppercase;color:#475569;background:#e2e8f0;border-radius:20px;padding:1px 9px}
.surp-dir{font-size:11px;color:#94a3b8;font-family:ui-monospace,monospace}
.surp-head{font-size:14px;font-weight:700;color:#0f172a;line-height:1.4}
.surp-d{font-size:12.5px;color:#475569;line-height:1.5;margin-top:2px}
.surp-hidden-bottleneck,.surp-metric-green-output-bad{border-left-color:#dc2626;background:#fef2f2}
.surp-hidden-bottleneck .surp-badge,.surp-metric-green-output-bad .surp-badge{background:#fee2e2;color:#b91c1c}
.surp-hard-turned-free{border-left-color:#16a34a;background:#f0fdf4}
.surp-hard-turned-free .surp-badge{background:#dcfce7;color:#15803d}
.surp-assumed-bottleneck-not{border-left-color:#2563eb;background:#eff6ff}
.surp-assumed-bottleneck-not .surp-badge{background:#dbeafe;color:#1d4ed8}
.surp-easy-turned-hard{border-left-color:#d97706;background:#fffbeb}
.surp-doubt{border-left-color:#7c3aed;background:#faf5ff}
.newbar{margin:14px 0 2px;padding:9px 13px;border-radius:9px;background:#f0fdf4;border:1px solid #bbf7d0;font-size:12.5px;color:#166534}
.newbar code{background:#dcfce7;color:#14532d;border-radius:4px;padding:1px 5px;font-size:11.5px}
.new-tag{background:#16a34a;color:#fff;font-size:9.5px;font-weight:700;letter-spacing:.03em;padding:1px 6px;border-radius:9px;margin-left:7px;vertical-align:middle}
.focussec{margin:16px 0 6px;border:1px solid #bfdbfe;border-radius:10px;padding:14px 16px;background:#eff6ff}
.fshdr{font-size:13px;font-weight:700;letter-spacing:.03em;color:#1e3a8a;margin-bottom:11px}
.fcard{background:#fff;border:1px solid #dbeafe;border-radius:8px;padding:10px 12px;margin-bottom:9px}
.fcard:last-child{margin-bottom:0}
.fhead{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:5px}
.fst{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#fff;padding:2px 7px;border-radius:11px}
.ftitle{font-size:12.5px;font-weight:600;color:#0f172a}
.fdec{font-size:11px;color:#64748b;font-family:ui-monospace,Menlo,monospace}
.ftry{font-size:12px;color:#334155;line-height:1.5}
.fnext{font-size:12px;color:#475569;margin-top:5px}
.datasec{margin:18px 0 6px;border:1px solid #e2e8f0;border-radius:10px;padding:14px 16px;background:#fbfcfe}
.dshdr{font-size:13px;font-weight:700;letter-spacing:.03em;color:#0f172a;margin-bottom:12px}
.dsblock{margin-bottom:14px;padding-left:12px;border-left:3px solid #2563eb}
.dsblock:last-child{margin-bottom:0}
.dstitle{font-size:12.5px;font-weight:600;color:#1e293b;margin-bottom:7px}
.treecap{font-size:11.5px;color:var(--mut);padding:2px 10px 10px}
.treecap b{color:#334155}
.empty{color:var(--mut);font-size:13px;padding:22px 16px;text-align:center}
.foot{color:#94a3b8;font-size:11.5px;text-align:center;margin:22px 0 6px}
.pp{display:flex;flex-wrap:wrap;align-items:stretch;gap:6px;margin:4px 0 6px}
.pp-stage{flex:1 1 130px;min-width:130px;background:#fff;border:1px solid var(--line,#e2e8f0);border-top:3px solid #2563eb;border-radius:9px;padding:8px 10px}
.pp-t{font-weight:700;font-size:12.5px}
.pp-note{color:var(--mut,#64748b);font-size:11px;margin-top:3px;line-height:1.4;font-family:ui-monospace,Menlo,monospace}
.pp-now{color:#2563eb;font-size:10px;font-weight:700;margin-left:5px}
.pp-s{color:var(--mut,#64748b);font-size:11px;margin-top:2px}
.pp-arr{align-self:center;color:#cbd5e1;font-size:12px}
@media(max-width:560px){.pp-arr{display:none}.pp-stage{flex-basis:100%}}
abbr.gl-term{text-decoration:underline dotted #94a3b8;text-underline-offset:2px;cursor:help}
.gl-box{margin:14px 0 4px;background:#fff;border:1px solid var(--line,#e2e8f0);border-radius:10px;padding:2px 14px}
.gl-box summary{cursor:pointer;font-weight:700;font-size:12.5px;padding:9px 0}
.gl-row{font-size:12px;color:#334155;padding:4px 0;border-top:1px solid var(--line,#eef2f7)}
.fb-row{display:flex;gap:9px;align-items:flex-start;font-size:12px;color:#334155;padding:6px 0;border-top:1px solid var(--line,#eef2f7)}
.fb-kind{flex:0 0 auto;color:#fff;font-size:10px;font-weight:700;letter-spacing:.03em;text-transform:uppercase;border-radius:9px;padding:2px 8px;margin-top:1px}
.fb-note{line-height:1.5}
.fb-act{color:#166534;margin-top:2px;line-height:1.45}
.fb-row.done{opacity:.55}
.fb-done{color:#16a34a;font-weight:700;font-size:11px;margin-top:2px}
"""


# --- per-action-item chatbot: CSS + JS (the only client-side, JS-bearing parts) ----
# A self-contained widget under every decision. It calls the Anthropic API straight from
# the browser (key in localStorage, never baked into the file), grounds each answer in that
# decision + the project glossary, and captures what the user didn't grok into localStorage
# — which `viz --absorb <blob.json>` later folds back into glossary.* + clarify.* on disk.
CHAT_CSS = """
.tl-chat{margin:6px 0 10px 24px;font-size:13px}
.tl-ask{cursor:pointer;border:1px solid #c7d2fe;background:#eef2ff;color:#3730a3;border-radius:8px;padding:3px 10px;font-size:12px;font-weight:600;transition:background .15s,transform .1s}
.tl-ask:hover{background:#e0e7ff}
.tl-ask:active{transform:scale(.96)}
.tl-panel{margin-top:0;border:1px solid var(--line);border-radius:10px;background:#fafbff;overflow:hidden;max-height:0;opacity:0;border-width:0;transition:max-height .28s cubic-bezier(.4,0,.2,1),opacity .2s,margin-top .2s,border-width .2s}
.tl-panel.open{max-height:520px;opacity:1;margin-top:8px;border-width:1px}
.tl-saved{padding:8px 10px;border-bottom:1px solid #eef2f7}
.tl-saved h5{margin:0 0 4px;font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:#94a3b8}
.tl-term{font-size:12.5px;margin:3px 0}.tl-term b{color:#3730a3}
.tl-unsv{font-size:9.5px;color:#b45309;background:#fef3c7;border-radius:5px;padding:0 5px;margin-left:5px}
.tl-faq{font-size:12.5px;margin:5px 0;color:#334155}
.tl-faq .q{font-weight:700;color:#0f172a}
.tl-log{padding:8px 10px;min-height:54px;max-height:340px;overflow-y:auto;resize:vertical}
.tl-msg{margin:6px 0;font-size:13px;line-height:1.45;white-space:pre-wrap;animation:tl-fade .2s ease}
.tl-msg.u b{color:#3730a3}.tl-msg.a b{color:#16a34a}.tl-msg.err{color:#b91c1c}
@keyframes tl-fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.tl-typing{display:inline-flex;gap:5px;vertical-align:middle;padding:2px 0}
.tl-typing i{width:7px;height:7px;border-radius:50%;background:#16a34a;display:inline-block;animation:tl-bounce 1.2s infinite cubic-bezier(.4,0,.2,1)}
.tl-typing i:nth-child(2){animation-delay:.16s}
.tl-typing i:nth-child(3){animation-delay:.32s}
@keyframes tl-bounce{0%,70%,100%{transform:translateY(0) scale(.6);opacity:.3}35%{transform:translateY(-5px) scale(1);opacity:1}}
.tl-skel{margin-top:5px;display:flex;flex-direction:column;gap:6px}
.tl-skel span{height:9px;border-radius:6px;background:linear-gradient(90deg,#e8edf5 8%,#f4f7fb 20%,#e8edf5 33%);background-size:800px 100%;animation:tl-shim 1.3s linear infinite}
.tl-skel span:nth-child(1){width:92%}.tl-skel span:nth-child(2){width:78%}.tl-skel span:nth-child(3){width:55%}
@keyframes tl-shim{0%{background-position:-380px 0}100%{background-position:380px 0}}
.tl-in{display:flex;gap:6px;padding:8px 10px;border-top:1px solid #eef2f7}
.tl-in textarea{flex:1;border:1px solid #cbd5e1;border-radius:8px;padding:6px 8px;font:inherit;font-size:13px;resize:vertical;min-height:36px}
.tl-in button{border:0;background:#4f46e5;color:#fff;border-radius:8px;padding:0 14px;font-weight:600;cursor:pointer}
.tl-in button:disabled{background:#a5b4fc}
.tl-bar{position:fixed;right:14px;bottom:14px;display:flex;flex-wrap:wrap;justify-content:flex-end;gap:8px;z-index:50;max-width:calc(100vw - 28px)}
.tl-bar button{border:1px solid #cbd5e1;background:#fff;border-radius:9px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;box-shadow:0 2px 8px rgba(15,23,42,.12)}
.tl-bar button:hover{background:#f8fafc}
.tl-bar .tl-digest{background:#4f46e5;color:#fff;border-color:#4338ca}
.tl-bar .tl-digest:hover{background:#4338ca}
.tl-bar .tl-digest:disabled{opacity:.6;cursor:default}
.tl-digbox{position:fixed;right:14px;bottom:58px;z-index:60;max-width:min(360px,92vw);background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:10px 13px;font-size:12.5px;line-height:1.45;color:#0f172a;box-shadow:0 8px 24px rgba(15,23,42,.18)}
.tl-digbox a{color:#4f46e5;font-weight:700}
@media print{.tl-bar,.tl-digbox{display:none}}
"""

# Plain string (NOT an f-string) — the JS keeps its own braces/backticks. It reads the
# embedded <script id="tl-data"> blob for grounding; nothing about the project is hard-coded.
CHAT_JS = r"""
(function(){
  var el=document.getElementById('tl-data'); if(!el) return;
  var DATA=JSON.parse(el.textContent);
  var LS='trainlint_mem_'+DATA.project, KK='trainlint_anthropic_key', MK='trainlint_model';
  function lsGet(k){try{return localStorage.getItem(k)}catch(e){return null}}
  function lsSet(k,v){try{localStorage.setItem(k,v)}catch(e){}}
  function mem(){try{return JSON.parse(lsGet(LS))||{faq:{},glossary:[]}}catch(e){return{faq:{},glossary:[]}}}
  function setMem(m){lsSet(LS,JSON.stringify(m))}
  function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
  function glossaryText(){return (DATA.glossary||[]).map(function(g){return '- '+g.term+': '+g.plain+(g.why?(' ('+g.why+')'):'')}).join('\n')}
  function globalCtx(){var c=DATA.context||{},d=(c.decisions||[]);return d.length?('\n\nFULL PLAN (for cross-reference only): '+d.join(' | ')):'';}
  var MEMTAIL='If the questions reveal concepts the user did not understand, append AT THE VERY END a fenced block exactly like:\n'+
      '```memory\n{"terms":[{"term":"...","plain":"one-line plain meaning","why":"why it matters here"}]}\n```\n'+
      'Only include genuinely-clarified concepts; omit the block entirely if none.';
  function sysPromptBlock(b){
    return 'You are a tutor embedded in a research-planning report for the project "'+DATA.project+'".\n'+
      'PROJECT GOAL: '+DATA.goal+'\n\n'+
      'You answer questions about ONE SECTION of the report:\n'+
      '  Section: '+b.title+'\n'+
      '  Content: '+b.text+'\n\n'+
      'PROJECT GLOSSARY:\n'+glossaryText()+globalCtx()+'\n\n'+
      'Answer clearly and concisely, grounded in THIS section; you may cross-reference the plan. '+MEMTAIL;
  }
  function sysPrompt(dec){
    return 'You are a tutor embedded in a research-planning report for the project "'+DATA.project+'".\n'+
      'PROJECT GOAL: '+DATA.goal+'\n\n'+
      'You answer questions about ONE decision in the plan:\n'+
      '  Decision: '+dec.decision+'\n'+
      '  Chosen: '+(dec.choice||'(still open)')+'\n'+
      '  Principle: '+(dec.principle||'')+'\n'+
      '  Why: '+(dec.why||'')+'\n'+
      (dec.code?('  THE CODE BEHIND THIS DECISION (pinned for review):\n'+dec.code+'\n'):'')+'\n'+
      'PROJECT GLOSSARY:\n'+glossaryText()+globalCtx()+'\n\n'+
      'Answer clearly and concisely, grounded in this context; define jargon in plain language. '+
      'If the questions reveal concepts the user did not understand, append AT THE VERY END a fenced block exactly like:\n'+
      '```memory\n{"terms":[{"term":"...","plain":"one-line plain meaning","why":"why it matters here"}]}\n```\n'+
      'Only include genuinely-clarified concepts; omit the block entirely if none.';
  }
  function parseMemory(text){
    var m=text.match(/```memory\s*([\s\S]*?)```/), clean=text, terms=[];
    if(m){clean=text.replace(m[0],'').trim();try{var o=JSON.parse(m[1].trim());if(o&&o.terms)terms=o.terms;}catch(e){}}
    return {clean:clean,terms:terms};
  }
  async function ask(sys,convo){
    var model=lsGet(MK)||DATA.model;
    if(/gemini/i.test(model)){  // Google Gemini: different endpoint + contents/parts shape (assistant->model)
      var gk=lsGet('trainlint_gemini_key');
      if(!gk) throw new Error('No Gemini API key — click "Set API key" and pick a gemini model.');
      var contents=convo.map(function(m){return {role:m.role==='assistant'?'model':'user',parts:[{text:m.content}]}});
      var gres=await fetch('https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent?key='+encodeURIComponent(gk),
        {method:'POST',headers:{'content-type':'application/json'},
         body:JSON.stringify({system_instruction:{parts:[{text:sys}]},contents:contents,generationConfig:{maxOutputTokens:1024}})});
      if(!gres.ok){var gt=await gres.text();throw new Error('Gemini '+gres.status+': '+gt.slice(0,200));}
      var gj=await gres.json();
      return ((((gj.candidates||[])[0]||{}).content||{}).parts||[]).map(function(p){return p.text||''}).join('');
    }
    var key=lsGet(KK);
    if(!key) throw new Error('No API key set — click "Set API key" (bottom-right).');
    var res=await fetch('https://api.anthropic.com/v1/messages',{method:'POST',
      headers:{'content-type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01',
        'anthropic-dangerous-direct-browser-access':'true'},
      body:JSON.stringify({model:model,max_tokens:1024,system:sys,messages:convo})});
    if(!res.ok){var t=await res.text();throw new Error('API '+res.status+': '+t.slice(0,200));}
    var j=await res.json();
    return (j.content||[]).filter(function(b){return b.type==='text'}).map(function(b){return b.text}).join('');
  }
  function renderSaved(box,decId){
    var dec=DATA.decisions[decId]||{}, m=mem();
    var sterms=dec.terms||[], sfaq=dec.faq||[];
    var seenT={}; sterms.forEach(function(t){seenT[(t.term||'').toLowerCase()]=1});
    var seenQ={}; sfaq.forEach(function(f){seenQ[f.q]=1});
    var lterms=(m.glossary||[]).filter(function(t){return t.dec===decId&&!seenT[(t.term||'').toLowerCase()]});
    var lfaq=((m.faq&&m.faq[decId])||[]).filter(function(f){return !seenQ[f.q]});
    var h='';
    var T=sterms.map(function(t){return{t:t,u:0}}).concat(lterms.map(function(t){return{t:t,u:1}}));
    var F=sfaq.map(function(f){return{f:f,u:0}}).concat(lfaq.map(function(f){return{f:f,u:1}}));
    if(T.length){h+="<div class='tl-saved'><h5>terms you asked about</h5>";
      T.forEach(function(x){h+="<div class='tl-term'><b>"+esc(x.t.term)+"</b> — "+esc(x.t.plain)+(x.u?" <span class='tl-unsv'>unsaved</span>":"")+"</div>"});h+="</div>";}
    if(F.length){h+="<div class='tl-saved'><h5>Q&amp;A</h5>";
      F.forEach(function(x){h+="<div class='tl-faq'><div class='q'>"+esc(x.f.q)+"</div><div>"+esc(x.f.a)+(x.u?" <span class='tl-unsv'>unsaved</span>":"")+"</div></div>"});h+="</div>";}
    box.innerHTML=h;
  }
  function initWidget(node){
    // one widget for BOTH decision blocks (data-dec -> DATA.decisions) and every other report
    // block (data-block -> DATA.blocks). Decisions are just one KIND of block (generic-widget).
    var decId=node.getAttribute('data-dec'), blockId=node.getAttribute('data-block');
    var focusText=node.getAttribute('data-focus');  // per-ITEM widget: its own text is the focus
    var id, sys, ph;
    if(decId){var dec=DATA.decisions[decId]; if(!dec) return; id=decId; sys=sysPrompt(dec); ph='Ask about this decision…';}
    else if(blockId){var b=(DATA.blocks||{})[blockId]; if(!b) return; id=blockId; sys=sysPromptBlock(b); focusText=focusText||(b.title+': '+b.text); ph='Ask about this section…';}
    else if(focusText){id='focus:'+focusText.slice(0,40); sys=sysPromptBlock({title:'Report item',text:focusText}); ph='Ask about this…';}
    else return;
    var convo=[];
    var btn=document.createElement('button'); btn.className='tl-ask'; btn.textContent='💬 Ask about this';
    var panel=document.createElement('div'); panel.className='tl-panel';
    var saved=document.createElement('div'), log=document.createElement('div'); log.className='tl-log';
    var inRow=document.createElement('div'); inRow.className='tl-in';
    var ta=document.createElement('textarea'); ta.placeholder=ph+' (Cmd/Ctrl+Enter to send)';
    var send=document.createElement('button'); send.textContent='Send';
    inRow.appendChild(ta); inRow.appendChild(send);
    panel.appendChild(saved); panel.appendChild(log); panel.appendChild(inRow);
    node.appendChild(btn); node.appendChild(panel);
    renderSaved(saved,id);
    btn.addEventListener('click',function(e){e.preventDefault();panel.classList.toggle('open');});
    function addMsg(cls,who,txt){var d=document.createElement('div');d.className='tl-msg '+cls;d.innerHTML='<b>'+who+'</b> '+esc(txt);log.appendChild(d);log.scrollTop=log.scrollHeight;return d;}
    async function askBackend(decId,question,conv){  // the LIVE local backend: full substrate + grep
      var res=await fetch('chat',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({project:DATA.project,question:question,decision_id:(decId&&decId.indexOf('focus:')<0?decId:null),focus:focusText||null,history:conv.slice(0,-1)})});
      if(!res.ok) throw new Error('chat backend '+res.status);
      var j=await res.json(); if(j.error) throw new Error(j.error); return j.answer||'';
    }
    async function go(){
      var q=ta.value.trim(); if(!q) return; ta.value='';
      addMsg('u','You:',q); convo.push({role:'user',content:q});
      send.disabled=true; var wait=addMsg('a','Assistant:','');
      wait.innerHTML="<b>Assistant:</b> <span class='tl-typing'><i></i><i></i><i></i></span>"+
        "<div class='tl-skel'><span></span><span></span><span></span></div>";
      try{
        var raw;
        try{ raw=await askBackend(id,q,convo); }        // served -> rich live context
        catch(e){ raw=await ask(sys,convo); }           // bare-file fallback -> baked context
        var pm=parseMemory(raw);
        wait.innerHTML="<b>Assistant:</b> "+esc(pm.clean); convo.push({role:'assistant',content:raw});
        var m=mem(); m.faq=m.faq||{}; m.faq[id]=m.faq[id]||[];
        var rec={q:q,a:pm.clean,ts:new Date().toISOString()};
        if(focusText)rec.focus=focusText.slice(0,300);  // keep WHAT the question was about
        m.faq[id].push(rec);
        m.glossary=m.glossary||[];
        pm.terms.forEach(function(t){if(t&&t.term)m.glossary.push({term:t.term,plain:t.plain||'',why:t.why||'',dec:id})});
        setMem(m); renderSaved(saved,id);
      }catch(err){wait.className='tl-msg err';wait.innerHTML='⚠ '+esc(err.message);}
      send.disabled=false; ta.focus();
    }
    send.addEventListener('click',go);
    ta.addEventListener('keydown',function(e){if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))go();});
  }
  function toolbar(){
    var bar=document.createElement('div'); bar.className='tl-bar';
    var k=document.createElement('button'); k.textContent='🔑 Set API key';
    k.onclick=function(){
      var mdl=prompt('Model — anthropic claude-* OR google gemini-* (e.g. gemini-2.5-flash):',lsGet(MK)||DATA.model);
      if(mdl!=null&&mdl.trim())lsSet(MK,mdl.trim());
      var isG=/gemini/i.test(lsGet(MK)||DATA.model);
      var slot=isG?'trainlint_gemini_key':KK;
      var v=prompt((isG?'Gemini':'Anthropic')+' API key (stored only in this browser):',lsGet(slot)||'');
      if(v!=null)lsSet(slot,v.trim());
    };
    var ex=document.createElement('button'); ex.textContent='⬇ Export memory';
    ex.onclick=function(){var m=mem(),blob={project:DATA.project,faq:m.faq||{},glossary:m.glossary||[],mastered:m.mastered||{},
      annotations:(function(){try{return JSON.parse(lsGet('trainlint_ann_'+DATA.project))||[]}catch(e){return[]}})()};
      var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(blob,null,2)],{type:'application/json'}));
      a.download='viz-memory.'+DATA.project+'.json';a.click();};
    bar.appendChild(k); bar.appendChild(ex); document.body.appendChild(bar);
  }
  document.querySelectorAll('.tl-chat').forEach(initWidget); toolbar();
  window.tlInitChat=initWidget;  // ANNOT_JS mounts ad-hoc chats (Ask-AI on a highlight note)
})();
"""


# --- per-decision QUIZ: CSS + JS (offline, no API key) -------------------------------
# A graded multiple-choice drill under every decision, mirroring the terminal /trainlint:quiz
# but in the browser: question + options come baked in the tl-data blob (from quiz.jsonl by
# principle), grading is client-side against the baked correct index, and a pass is recorded to
# the SAME localStorage memory the chatbot uses (`mastered` map) — so "Export memory" + `viz
# --absorb` clears the very gate the terminal quiz clears. Zero network, zero deps.
QUIZ_CSS = """
.tl-quiz{margin:6px 0 4px 24px}
.tl-qbox{border:1px solid #e2e8f0;border-radius:10px;background:#fcfdff;overflow:hidden}
.tl-qhead{padding:8px 10px;font-size:12.5px;border-bottom:1px solid #eef2f7;background:#f8fafc}
.tl-qmark{font-size:10.5px;font-weight:700;letter-spacing:.04em;color:#6366f1;margin-right:6px}
.tl-qmark.done{color:#16a34a}
.tl-qq{color:#0f172a;font-weight:600}
.tl-qopts{display:flex;flex-direction:column;gap:6px;padding:9px 10px}
.tl-qopt{text-align:left;border:1px solid #cbd5e1;background:#fff;border-radius:8px;padding:6px 10px;font:inherit;font-size:12.5px;color:#1e293b;cursor:pointer;line-height:1.4}
.tl-qopt:hover:not(:disabled){background:#eef2ff;border-color:#a5b4fc}
.tl-qopt:disabled{cursor:default;opacity:.92}
.tl-qopt.ok{border-color:#16a34a;background:#dcfce7;color:#14532d;font-weight:600}
.tl-qopt.bad{border-color:#dc2626;background:#fee2e2;color:#7f1d1d}
.tl-qwhy{padding:0 10px 9px;font-size:12.5px;line-height:1.5;color:#334155}
.tl-qwhy b{color:#0f172a}
.tl-qretry{margin:0 10px 10px;border:1px solid #cbd5e1;background:#fff;border-radius:8px;padding:3px 10px;font-size:11.5px;cursor:pointer;color:#475569}
.tl-qretry:hover{background:#f1f5f9}
"""

# Plain string (own braces/backticks). Reads the same <script id="tl-data"> blob as the chatbot.
# The tab switcher for the report's sectioned body (.rnav / .rsec). Remembers the last-open tab
# per project; honors #hash deep links; a click on a cross-section anchor (e.g. a timeline row's
# "📖 now readable" pointing into the tree) flips to the target's tab before the browser jumps.
NAV_JS = r"""
(function(){
  document.documentElement.classList.add('js');  // gates ALL tab-hiding CSS: no JS -> no hiding
  var secs=[].slice.call(document.querySelectorAll('.rsec'));
  if(!secs.length) return;
  var btns=[].slice.call(document.querySelectorAll('.rnav button'));
  if(!btns.length){secs.forEach(function(s){s.classList.add('on')});return;}
  var KEY='trainlint_tab_'+((document.title||'').split(' ')[0]);
  function show(id,save){
    if(id!=='all'&&!secs.some(function(s){return s.id===id})) id=secs[0].id;
    secs.forEach(function(s){s.classList.toggle('on',id==='all'||s.id===id)});
    btns.forEach(function(b){b.classList.toggle('on',b.getAttribute('data-sec')===id)});
    if(save){try{localStorage.setItem(KEY,id)}catch(e){}}
  }
  btns.forEach(function(b){b.addEventListener('click',function(){show(b.getAttribute('data-sec'),true)});});
  var start=null,h=(location.hash||'').slice(1);
  if(h){var el=document.getElementById(h); if(el&&el.closest){var s=el.closest('.rsec'); if(s) start=s.id;}}
  if(!start){try{start=localStorage.getItem(KEY)}catch(e){}}
  show(start||secs[0].id,false);
  if(h){var el2=document.getElementById(h); if(el2&&el2.scrollIntoView) el2.scrollIntoView();}
  document.addEventListener('click',function(e){
    var a=e.target&&e.target.closest?e.target.closest('a[href^="#"]'):null;
    if(!a) return;
    var el=document.getElementById(a.getAttribute('href').slice(1));
    if(!el||!el.closest) return;
    var s=el.closest('.rsec');
    if(s&&!s.classList.contains('on')) show(s.id,false);
  });
})();
"""

# Highlight-to-comment: select any text in the report, click the floating 🖍 button, write a
# note. Highlights live in localStorage (trainlint_ann_<project>) anchored by quote+context —
# they survive report regeneration as long as the quoted text still exists (else they show as
# ⚠ orphans in the Notes drawer). "Export memory" carries them; `viz.py <name> --absorb` folds
# them into comments.<name>.jsonl so the operator's margin notes reach the substrate.
ANNOT_CSS = """
mark.tl-hl{background:#fde68a;color:#1f2937;border-bottom:2px solid #f59e0b;border-radius:2px;cursor:pointer;padding:0 1px}
.ann-btn{position:fixed;z-index:90;border:1px solid #f59e0b;background:#fffbeb;color:#92400e;border-radius:18px;
  padding:5px 12px;font-size:12.5px;font-weight:700;cursor:pointer;box-shadow:0 4px 14px rgba(15,23,42,.18)}
.ann-pop{position:fixed;z-index:95;background:#fff;border:1px solid var(--line);border-radius:12px;padding:11px;
  width:min(340px,92vw);max-height:min(560px,82vh);overflow-y:auto;box-shadow:0 10px 30px rgba(15,23,42,.22);font-size:13px;color:#0f172a}
.ann-q{font-size:12px;color:#64748b;border-left:3px solid #fde68a;padding-left:8px;margin-bottom:8px;max-height:70px;overflow:hidden}
.ann-ta{width:100%;min-height:64px;border:1px solid #cbd5e1;border-radius:8px;padding:7px 9px;font:inherit;font-size:13px;resize:vertical}
.ann-row{display:flex;gap:7px;margin-top:8px}
.ann-row button{border:0;border-radius:8px;padding:5px 13px;font-weight:600;cursor:pointer;font-size:12.5px}
.ann-save{background:#4f46e5;color:#fff}
.ann-ai{background:#ecfdf5;color:#047857;border:1px solid #a7f3d0!important}
.ann-del{background:#fee2e2;color:#b91c1c}
.ann-x{background:#eef2f7;color:#334155}
.ann-pop.haschat{width:min(480px,94vw)}
.ann-chat{margin-top:4px}
.ann-chat .tl-chat{margin:0}
.ann-list{max-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:9px}
.ann-item{cursor:pointer;border:1px solid var(--line);border-radius:9px;padding:7px 9px}
.ann-item:hover{background:#f8fafc}
.ann-iq{color:#92400e;font-size:12px}
.ann-orph{color:#b91c1c;font-size:11px;font-weight:700}
@media print{.ann-btn,.ann-pop{display:none}}
"""

# --- in-report INLINE EDITOR: CSS + JS -----------------------------------------------
# Owner-only editing of every structured item straight in the live report. A toolbar toggle
# arms "edit mode" (off by default); each editable element then shows a ✎ pencil affordance and,
# on click, an inline textarea/select with Save/Cancel. Save POSTs to `edit` (relative, so the
# worker relays it to this operator's backend exactly like `chat`); on 200 the DOM updates in
# place, on 403 the report drops to read-only (someone else's substrate), on 409 it warns the
# item changed underneath. Coexists with NAV/ANNOT/CHAT/QUIZ — it only intercepts clicks that
# land on a [data-e-field] element while edit mode is on.
EDIT_CSS = """
.tl-editbtn.on{background:#0f172a!important;color:#fff!important;border-color:#0f172a!important}
.tl-editbtn:disabled{opacity:.7;cursor:default}
body.tl-editing [data-e-field]{cursor:pointer;border-radius:3px}
body.tl-editing [data-e-field]:hover{background:#fef9c3;outline:1px dashed #fbbf24;outline-offset:1px}
body.tl-editing [data-e-field]::after{content:'\\270e';font-size:10px;color:#b45309;margin-left:4px;opacity:.55;vertical-align:1px}
.tl-edit{display:block;margin:6px 0;border:1px solid #c7d2fe;background:#f5f7ff;border-radius:9px;padding:8px 10px}
.tl-edit textarea{display:block;width:100%;min-height:60px;border:1px solid #cbd5e1;border-radius:7px;padding:6px 8px;font:inherit;font-size:13px;line-height:1.45;resize:vertical;box-sizing:border-box}
.tl-edit select{border:1px solid #cbd5e1;border-radius:7px;padding:5px 8px;font:inherit;font-size:13px;background:#fff}
.tl-edit-row{display:flex;gap:7px;align-items:center;margin-top:7px}
.tl-edit-row button{border:0;border-radius:7px;padding:5px 13px;font-weight:600;font-size:12.5px;cursor:pointer}
.tl-edit-save{background:#4f46e5;color:#fff}
.tl-edit-save:disabled{background:#a5b4fc}
.tl-edit-cancel{background:#eef2f7;color:#334155}
.tl-edit-msg{font-size:11.5px;margin-left:2px;color:#64748b}
.tl-edit-msg.err{color:#b91c1c}
.tl-edit-note{position:fixed;left:50%;transform:translateX(-50%);bottom:64px;z-index:100;
  background:#7f1d1d;color:#fff;padding:7px 14px;border-radius:9px;font-size:12.5px;
  box-shadow:0 6px 20px rgba(15,23,42,.3);max-width:88vw;text-align:center}
.gl-why{color:#94a3b8;font-size:11px}
@media print{.tl-editbtn,.tl-edit,.tl-edit-note{display:none}
  body.tl-editing [data-e-field]::after{content:''}body.tl-editing [data-e-field]{background:none;outline:0}}
"""

# Plain string (own braces/backticks). Reads DATA.project from the shared <script id="tl-data">
# blob (same source CHAT_JS/QUIZ_JS read). No hashing here — Python already baked data-e-id.
EDIT_JS = r"""
(function(){
  var el=document.getElementById('tl-data'); var DATA={};
  try{DATA=JSON.parse(el.textContent)}catch(e){}
  var PROJECT=DATA.project||((document.title||'').split(' ')[0]);
  var GLYPH={open:'○',decided:'◐',verified:'✓'};
  var GCOL={open:'#64748b',decided:'#d97706',verified:'#16a34a'};
  var editing=false, readonly=false, openEd=null, btn=null;

  function toast(m){var t=document.createElement('div');t.className='tl-edit-note';t.textContent=m;
    document.body.appendChild(t);setTimeout(function(){try{t.remove()}catch(e){}},4200);}

  function closeEd(){ if(!openEd)return; var o=openEd; openEd=null;
    o.el.style.display=o.disp; try{o.box.remove()}catch(e){} }

  function setEditing(on){
    editing=on&&!readonly;
    document.body.classList.toggle('tl-editing',editing);
    if(btn){btn.classList.toggle('on',editing);
      btn.textContent=readonly?'✎ read-only':(editing?'✎ Editing — click an item':'✎ Edit');}
    if(!editing) closeEd();
  }

  function build(elm){
    closeEd();
    var kind=elm.getAttribute('data-e-kind'), id=elm.getAttribute('data-e-id')||'',
        field=elm.getAttribute('data-e-field')||'', prev=elm.getAttribute('data-e-prev')||'',
        type=elm.getAttribute('data-e-type')||'text', oset=elm.getAttribute('data-e-opts')||'',
        render=elm.getAttribute('data-e-render')||'';
    var box=document.createElement('div'); box.className='tl-edit';
    box.addEventListener('click',function(e){e.stopPropagation();});
    box.addEventListener('mousedown',function(e){e.stopPropagation();});
    var input;
    if(type==='select'){
      input=document.createElement('select');
      oset.split(',').forEach(function(o){o=o.trim(); if(!o)return;
        var op=document.createElement('option'); op.value=o; op.textContent=o;
        if(o===prev)op.selected=true; input.appendChild(op);});
    }else{ input=document.createElement('textarea'); input.value=prev; }
    box.appendChild(input);
    var row=document.createElement('div'); row.className='tl-edit-row';
    var save=document.createElement('button'); save.type='button'; save.className='tl-edit-save'; save.textContent='Save';
    var cancel=document.createElement('button'); cancel.type='button'; cancel.className='tl-edit-cancel'; cancel.textContent='Cancel';
    var msg=document.createElement('span'); msg.className='tl-edit-msg';
    row.appendChild(save); row.appendChild(cancel); row.appendChild(msg);
    box.appendChild(row);
    var disp=elm.style.display; elm.style.display='none';
    elm.parentNode.insertBefore(box,elm.nextSibling);
    openEd={el:elm,box:box,disp:disp};
    try{input.focus();}catch(e){}
    cancel.addEventListener('click',function(e){e.preventDefault();closeEd();});
    input.addEventListener('keydown',function(e){
      e.stopPropagation();
      if(e.key==='Escape'){e.preventDefault();closeEd();}
      else if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();save.click();}
    });
    save.addEventListener('click',function(e){
      e.preventDefault();
      var val=input.value;
      save.disabled=true; msg.className='tl-edit-msg'; msg.textContent='saving…';
      fetch('edit',{method:'POST',headers:{'content-type':'application/json'},
        body:JSON.stringify({project:PROJECT,kind:kind,id:id,field:field,value:val,prev:prev})})
      .then(function(res){ return res.text().then(function(txt){
        var j={}; try{j=JSON.parse(txt)}catch(e){}
        if(res.status===200&&j&&j.ok){
          var stored=(j.value!=null)?j.value:val;
          if(render==='glyph'){elm.textContent=GLYPH[stored]||stored; if(GCOL[stored])elm.style.color=GCOL[stored];}
          else{elm.textContent=stored;}
          elm.setAttribute('data-e-prev',stored);
          closeEd(); return;
        }
        save.disabled=false;
        if(res.status===403){
          msg.className='tl-edit-msg err'; msg.textContent='read-only';
          readonly=true; setEditing(false); if(btn)btn.disabled=true;
          closeEd(); toast('read-only — not your report');
        }else if(res.status===409){
          msg.className='tl-edit-msg err'; msg.textContent='changed underneath, reload';
          toast('changed underneath — reload the report');
        }else{
          msg.className='tl-edit-msg err'; msg.textContent=(j&&j.error)||('error '+res.status);
        }
      });})
      .catch(function(err){save.disabled=false; msg.className='tl-edit-msg err';
        msg.textContent=String((err&&err.message)||err);});
    });
  }

  // Capture-phase delegate: while armed, a click on any editable element opens its inline editor
  // (and is stopped from toggling a <details> summary or triggering ANNOT/NAV/CHAT handlers).
  document.addEventListener('click',function(e){
    if(!editing||readonly) return;
    if(openEd&&openEd.box.contains(e.target)) return;
    var t=e.target&&e.target.closest?e.target.closest('[data-e-field]'):null;
    if(!t) return;
    e.preventDefault(); e.stopPropagation();
    build(t);
  },true);

  var bar=document.querySelector('.tl-bar');
  if(!bar){bar=document.createElement('div'); bar.className='tl-bar'; document.body.appendChild(bar);}
  btn=document.createElement('button'); btn.type='button'; btn.className='tl-editbtn'; btn.textContent='✎ Edit';
  btn.addEventListener('click',function(){ if(readonly)return; setEditing(!editing); });
  bar.insertBefore(btn,bar.firstChild);
})();
"""

ANNOT_JS = r"""
(function(){
  var wrap=document.querySelector('.wrap'); if(!wrap||!window.getSelection) return;
  var proj=(document.title||'').split(' ')[0];
  var KEY='trainlint_ann_'+proj;
  function lsGet(k){try{return localStorage.getItem(k)}catch(e){return null}}
  function lsSet(k,v){try{localStorage.setItem(k,v)}catch(e){}}
  function load(){try{return JSON.parse(lsGet(KEY))||[]}catch(e){return[]}}
  function save(a){lsSet(KEY,JSON.stringify(a))}
  function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}

  // the text universe: every text node under .wrap in document order (same order Range.toString uses)
  function collect(){
    var nodes=[],text='',w=document.createTreeWalker(wrap,NodeFilter.SHOW_TEXT,null),n;
    while((n=w.nextNode())){nodes.push({n:n,start:text.length});text+=n.nodeValue;}
    return {nodes:nodes,text:text};
  }
  function rangeStart(r){var pre=r.cloneRange();pre.selectNodeContents(wrap);pre.setEnd(r.startContainer,r.startOffset);return pre.toString().length;}
  function wrapOffsets(s,e,id){
    var idx=collect();
    for(var k=0;k<idx.nodes.length;k++){
      var rec=idx.nodes[k],ns=rec.start,ne=ns+rec.n.nodeValue.length;
      if(ne<=s||ns>=e) continue;
      var node=rec.n,p=node.parentNode;
      if(!p||(p.closest&&p.closest('svg,script,style,textarea'))) continue;
      var from=Math.max(s-ns,0),to=Math.min(e-ns,node.nodeValue.length);
      if(to<=from) continue;
      if(to<node.nodeValue.length) node.splitText(to);
      if(from>0) node=node.splitText(from);
      var m=document.createElement('mark');m.className='tl-hl';m.setAttribute('data-ann',id);
      p.insertBefore(m,node);m.appendChild(node);
    }
  }
  function anchor(a){ // quote + saved context -> offsets in the current text; null if the text changed
    var hay=collect().text,best=-1,from=0;
    while(true){var i=hay.indexOf(a.quote,from);if(i<0)break;
      if(best<0)best=i;
      var p=hay.slice(Math.max(0,i-(a.prefix||'').length),i);
      var s=hay.slice(i+a.quote.length,i+a.quote.length+(a.suffix||'').length);
      if((!a.prefix||p===a.prefix)&&(!a.suffix||s===a.suffix)){best=i;break;}
      from=i+1;}
    return best<0?null:{s:best,e:best+a.quote.length};
  }
  function unwrap(m){var p=m.parentNode;while(m.firstChild)p.insertBefore(m.firstChild,m);p.removeChild(m);p.normalize();}

  // --- auto-sync: file the notes + chat Q&A back to the report server, so the machine can pull
  // and digest them with zero clicks. Only fires when served over http(s); a page that has no
  // /api/feedback (file://, plain loopback) just fails silently and keeps everything local. ---
  var lastSync='',synced=false,syncTimer=null,fbDead=false;
  function fbPayload(){  // the SYNC KEY — deterministic (no timestamp), so an unchanged report is
    var faq={};try{var m=JSON.parse(lsGet('trainlint_mem_'+proj))||{};faq=m.faq||{}}catch(e){}
    var anns=load();  // byte-identical between the on-load baseline and the dirty-check — no spam
    if(!anns.length&&!Object.keys(faq).length) return null;
    return JSON.stringify({project:proj,annotations:anns,faq:faq});
  }
  function fbSync(){  // returns the in-flight promise (or undefined when there is nothing to do),
    if(fbDead||location.protocol.indexOf('http')!==0||!window.fetch) return;  // so the digest
    var body=fbPayload(); if(!body||body===lastSync) return;                  // button can flush-then-run
    try{
      return fetch('/api/feedback?project='+encodeURIComponent(proj),
            {method:'POST',headers:{'content-type':'application/json'},body:body,credentials:'same-origin'})
        .then(function(r){return r.text().then(function(t){
          // a worker WITHOUT this route (or not signed in) answers 200 login-page HTML or 401 —
          // only the real endpoint's exact ack counts, else ☁ would lie until the next deploy;
          // a non-ack disables further tries this page-load so we don't re-POST every 20s forever
          if(r.ok&&t.indexOf('feedback stored')===0){lastSync=body;synced=true;refreshCount();}
          else{fbDead=true;}
        });})
        .catch(function(){fbDead=true;});
    }catch(e){fbDead=true;}
  }
  function scheduleSync(){fbDead=false;clearTimeout(syncTimer);syncTimer=setTimeout(fbSync,1500);}
  setInterval(function(){if(!fbDead)scheduleSync();},20000); // chat Q&A lands outside this file

  var btn=document.createElement('button');btn.className='ann-btn';btn.textContent='🖍 Comment';btn.style.display='none';document.body.appendChild(btn);
  var pop=document.createElement('div');pop.className='ann-pop';pop.style.display='none';document.body.appendChild(pop);
  var pending=null;
  var refreshCount=function(){};
  function hideUi(){btn.style.display='none';pop.style.display='none';}
  function place(el,rect){
    var x=Math.min(Math.max((rect.left||0)+(rect.width||0)/2-70,8),window.innerWidth-170);
    var y=Math.min((rect.bottom||60)+8,window.innerHeight-80);
    el.style.left=x+'px';el.style.top=y+'px';
  }
  function offer(){
    var sel=window.getSelection();
    if(!sel||sel.isCollapsed||!sel.rangeCount){btn.style.display='none';return;}
    var r=sel.getRangeAt(0);
    var ca=r.commonAncestorContainer; ca=ca.nodeType===3?ca.parentNode:ca;
    if(!wrap.contains(ca)||(ca.closest&&ca.closest('.ann-pop,.tl-panel,textarea,svg'))){btn.style.display='none';return;}
    var q=r.toString(); if(!q.trim()||q.length>1500){btn.style.display='none';return;}
    var s=rangeStart(r);
    pending={s:s,e:s+q.length,quote:q};
    place(btn,r.getBoundingClientRect());btn.style.display='block';
  }
  document.addEventListener('mouseup',function(e){if(e.target===btn||pop.contains(e.target))return;setTimeout(offer,10);});
  document.addEventListener('touchend',function(e){if(e.target===btn||pop.contains(e.target))return;setTimeout(offer,150);});
  btn.addEventListener('click',function(){if(pending)editPop(null,pending);});

  function editPop(existing,pend){
    var quote=existing?existing.quote:pend.quote;
    pop.innerHTML='<div class="ann-q">“'+esc(quote.length>160?quote.slice(0,160)+'…':quote)+'”</div>'+
      '<textarea class="ann-ta" placeholder="Your note…">'+esc(existing?existing.comment:'')+'</textarea>'+
      '<div class="ann-row"><button class="ann-save">Save</button>'+
      '<button class="ann-ai">💬 Ask AI</button>'+
      (existing?'<button class="ann-del">Delete</button>':'')+
      '<button class="ann-x">Cancel</button></div>'+
      '<div class="ann-chat"></div>';
    var rect;
    if(existing){var m0=document.querySelector('mark[data-ann="'+existing.id+'"]');
      rect=m0?m0.getBoundingClientRect():{left:window.innerWidth/2,width:0,bottom:window.innerHeight/3};}
    else rect={left:parseFloat(btn.style.left)||0,width:0,bottom:parseFloat(btn.style.top)||60};
    place(pop,rect);pop.style.display='block';btn.style.display='none';
    var ta=pop.querySelector('.ann-ta');ta.focus();
    pop.querySelector('.ann-x').onclick=hideUi;
    pop.querySelector('.ann-ai').onclick=function(){  // chat grounded in the highlight (+ the note)
      var host=pop.querySelector('.ann-chat');
      if(host.firstChild){host.innerHTML='';pop.classList.remove('haschat');return;}
      var note=ta.value.trim();
      var focus='HIGHLIGHTED PASSAGE: "'+quote+'"'+(note?('\nOPERATOR NOTE ON IT: '+note):'');
      var w=document.createElement('div');w.className='tl-chat';w.setAttribute('data-focus',focus);
      host.appendChild(w);
      if(window.tlInitChat){try{window.tlInitChat(w);var open=w.querySelector('.tl-ask');if(open)open.click();}catch(e){}}
      pop.classList.add('haschat');
    };
    if(existing){
      pop.querySelector('.ann-del').onclick=function(){
        save(load().filter(function(a){return a.id!==existing.id}));
        document.querySelectorAll('mark[data-ann="'+existing.id+'"]').forEach(unwrap);
        hideUi();refreshCount();scheduleSync();
      };
    }
    pop.querySelector('.ann-save').onclick=function(){
      var c=ta.value.trim();if(!c){hideUi();return;}
      if(existing){
        var anns=load();
        for(var i=0;i<anns.length;i++)if(anns[i].id===existing.id)anns[i].comment=c;
        save(anns);hideUi();refreshCount();scheduleSync();return;
      }
      var text=collect().text;
      var id='a'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);
      var a={id:id,quote:pend.quote,
             prefix:text.slice(Math.max(0,pend.s-32),pend.s),
             suffix:text.slice(pend.e,pend.e+32),
             comment:c,sec:'',ts:new Date().toISOString()};
      wrapOffsets(pend.s,pend.e,id);
      var m1=document.querySelector('mark[data-ann="'+id+'"]');
      if(m1&&m1.closest){var sc=m1.closest('.rsec');a.sec=sc?sc.id:'';}
      var anns2=load();anns2.push(a);save(anns2);
      try{window.getSelection().removeAllRanges()}catch(e){}
      pending=null;hideUi();refreshCount();scheduleSync();
    };
  }

  // click a highlight -> view/edit its note
  document.addEventListener('click',function(e){
    var m=e.target&&e.target.closest?e.target.closest('mark.tl-hl'):null;
    if(!m)return;
    var id=m.getAttribute('data-ann');
    var a=load().filter(function(x){return x.id===id})[0];
    if(a)editPop(a,null);
  });

  // --- "Deal with all requests": flush the pending feedback, then ask the operator machine
  // (via the relay: POST digest -> 202 + background job) to digest EVERYTHING queued — fold the
  // notes/Ask-AI into the substrate, classify, auto-apply, re-render + re-upload — and poll
  // digest/status until it lands. Relative URLs ride whatever prefix the page is served under
  // (/r/<ns>/, /<email>/, flat, loopback) exactly like the chat + edit endpoints do. ---
  // The button lives PERMANENTLY on the toolbar (digBtn, set in drawer()) — NOT inside the notes
  // popup, which only opens when there are notes, so a user with zero notes could never reach it.
  // Status shows in a floating box (digBox) just above the toolbar. Both are stable nodes, so no
  // stale-lookup dance; digestBusy gates re-entry.
  var digestBusy=false,digMsgHtml='',digBtn=null,digBox=null;
  var DIG_LABEL='🤖 Deal with all requests';
  function _digBox(){
    if(!digBox){digBox=document.createElement('div');digBox.className='tl-digbox';digBox.style.display='none';document.body.appendChild(digBox);}
    return digBox;
  }
  function setDst(html){digMsgHtml=html;var n=_digBox();if(html){n.innerHTML=html;n.style.display='block';}else{n.style.display='none';}}
  function setDb(label,disabled){if(digBtn){digBtn.textContent=label;digBtn.disabled=!!disabled;}}
  function endDigest(html){digestBusy=false;setDst(html);setDb(DIG_LABEL,false);}
  function pollDigest(n){
    if(!digestBusy)return;  // superseded (page reset) — stop the loop
    if(n>120){digestBusy=false;setDst('still running — reload the page later to see the updates.');return;}
    setTimeout(function(){
      if(!digestBusy)return;
      fetch('digest/status?project='+encodeURIComponent(proj),{credentials:'same-origin'})
        .then(function(r){return r.json()})
        .then(function(j){
          if(!digestBusy)return;
          if(j.state==='done'){
            endDigest('✅ done — <a href="javascript:location.reload()">reload the report</a>'+
              (j.summary?'<br>'+esc(String(j.summary)).slice(0,240):''));
          }else if(j.state==='error'){
            endDigest('✗ digest failed: '+esc(String(j.error||'unknown')).slice(0,200));
          }else{
            setDst('🤖 digesting on the operator machine… '+((n+1)*5)+'s (LLM pass + re-render can take a few minutes)');
            pollDigest(n+1);
          }
        }).catch(function(){pollDigest(n+1);});
    },5000);
  }
  function runDigest(){
    if(digestBusy)return;  // one loop only; the button is disabled while busy anyway
    digestBusy=true;setDb('🤖 working…',true);setDst('⇪ syncing your feedback to the server…');
    fbDead=false;
    var flush=null;try{flush=fbSync();}catch(e){}
    (flush||Promise.resolve()).then(function(){
      // be honest if the newest notes never reached the server (an ack sets lastSync=body, so a
      // still-dirty payload means the flush didn't land — offline/undeployed worker). Proceed anyway:
      // the digest works on whatever IS queued server-side; we just don't claim those notes are in.
      var caveat='';var dirty=fbPayload();
      if(dirty&&dirty!==lastSync)caveat='⚠ couldn’t sync your newest notes (server offline?) — digesting what’s already queued.<br>';
      setDst(caveat+'🤖 starting the digest…');
      return fetch('digest',{method:'POST',headers:{'content-type':'application/json'},
                             body:JSON.stringify({project:proj}),credentials:'same-origin'});
    }).then(function(r){return r.text().then(function(t){return {ok:r.ok,status:r.status,text:t};});})
    .then(function(r){
      var j=null;try{j=JSON.parse(r.text)}catch(e){}
      if(r.status===403){digestBusy=false;setDb(DIG_LABEL,false);
        setDst('owner-only — sign in as the report owner to run this.');return;}
      if(r.status===503){digestBusy=false;setDb(DIG_LABEL,false);
        setDst('operator machine is offline — your feedback is safely queued and will be digested on its next run.');return;}
      if(!r.ok||!j||!j.ok){digestBusy=false;setDb(DIG_LABEL,false);
        setDst('✗ '+esc(String((j&&j.error)||('error '+r.status))));return;}
      setDst(j.already?'🤖 a digest is already running — watching it…':'🤖 digest started…');
      pollDigest(0);
    }).catch(function(e){digestBusy=false;setDb(DIG_LABEL,false);
      setDst('✗ unreachable ('+esc(e&&e.message?e.message:'network')+') — your feedback stays queued.');});
  }

  // notes drawer button in the bottom-right toolbar (shared with the chat toolbar when present)
  function drawer(){
    var bar=document.querySelector('.tl-bar');
    if(!bar){bar=document.createElement('div');bar.className='tl-bar';document.body.appendChild(bar);}
    var b=document.createElement('button');bar.insertBefore(b,bar.firstChild);
    refreshCount=function(){b.textContent='🖍 Notes ('+load().length+')'+(synced?' ☁':'');};
    refreshCount();
    // the digest button lives on the toolbar itself (always visible, independent of whether there
    // are notes) — right after 🖍 Notes. This IS the "one button" that digests all queued feedback.
    digBtn=document.createElement('button');digBtn.type='button';digBtn.className='tl-digest';
    digBtn.textContent=DIG_LABEL;digBtn.onclick=runDigest;
    bar.insertBefore(digBtn,b.nextSibling);
    if(digestBusy){setDb('🤖 working…',true);}  // survive a toolbar rebuild mid-run
    b.onclick=function(){
      var anns=load();
      if(!anns.length){alert('No notes yet — select any text in the report, then click 🖍 Comment.');return;}
      pop.innerHTML='<div class="ann-list">'+anns.map(function(a){
        var live=document.querySelector('mark[data-ann="'+a.id+'"]');
        return '<div class="ann-item" data-ann="'+a.id+'">'+(live?'':'<span class="ann-orph">⚠ text changed</span> ')+
          '<span class="ann-iq">“'+esc(a.quote.slice(0,70))+(a.quote.length>70?'…':'')+'”</span><br>'+esc(a.comment)+'</div>';
      }).join('')+'</div><div class="ann-row"><button class="ann-x">Close</button></div>';
      place(pop,{left:window.innerWidth-360,width:0,bottom:Math.max(window.innerHeight-420,60)});
      pop.style.display='block';
      pop.querySelector('.ann-x').onclick=hideUi;
      pop.querySelectorAll('.ann-item').forEach(function(it){
        it.onclick=function(){
          var id=it.getAttribute('data-ann');
          var m=document.querySelector('mark[data-ann="'+id+'"]');if(!m)return;
          var s=m.closest?m.closest('.rsec'):null;
          if(s&&!s.classList.contains('on')){
            var nb=document.querySelector('.rnav button[data-sec="'+s.id+'"]');if(nb)nb.click();
          }
          hideUi();m.scrollIntoView({block:'center'});
        };
      });
    };
  }

  // restore saved highlights (quote+context re-anchoring; unfindable ones stay as drawer orphans)
  var anns=load();
  for(var i=0;i<anns.length;i++){
    try{var pos=anchor(anns[i]);if(pos)wrapOffsets(pos.s,pos.e,anns[i].id);}catch(e){}
  }
  try{drawer();}catch(e){}
  lastSync=fbPayload()||'';
})();
"""

QUIZ_JS = r"""
(function(){
  var el=document.getElementById('tl-data'); if(!el) return;
  var DATA=JSON.parse(el.textContent);
  var LS='trainlint_mem_'+DATA.project;
  function mem(){try{return JSON.parse(localStorage.getItem(LS))||{}}catch(e){return{}}}
  function setMem(m){try{localStorage.setItem(LS,JSON.stringify(m))}catch(e){}}
  function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
  function init(node){
    var decId=node.getAttribute('data-dec'), dec=DATA.decisions[decId]; if(!dec||!dec.quiz) return;
    var Q=dec.quiz, done=!!((mem().mastered||{})[decId]);
    node.innerHTML='';
    var box=document.createElement('div'); box.className='tl-qbox';
    var head=document.createElement('div'); head.className='tl-qhead';
    head.innerHTML="<span class='tl-qmark"+(done?" done":"")+"'>"+(done?'✓ MASTERED':'? QUIZ')+"</span><span class='tl-qq'>"+esc(Q.q)+"</span>";
    box.appendChild(head);
    var opts=document.createElement('div'); opts.className='tl-qopts'; var answered=false;
    Q.options.forEach(function(opt,i){
      var b=document.createElement('button'); b.className='tl-qopt'; b.type='button'; b.textContent=opt;
      b.addEventListener('click',function(e){
        e.preventDefault(); if(answered) return; answered=true;
        var ok=(i===Q.correct);
        opts.querySelectorAll('.tl-qopt').forEach(function(x,j){x.disabled=true;
          if(j===Q.correct)x.classList.add('ok'); else if(j===i)x.classList.add('bad');});
        var why=document.createElement('div'); why.className='tl-qwhy';
        why.innerHTML="<b>"+(ok?'✓ Right.':'✗ Not quite.')+"</b> "+esc(Q.why);
        box.appendChild(why);
        if(ok){var m=mem(); m.mastered=m.mastered||{}; m.mastered[decId]={ts:new Date().toISOString(),principle:Q.principle}; setMem(m);
          var mk=head.querySelector('.tl-qmark'); mk.textContent='✓ MASTERED'; mk.classList.add('done');}
        var rt=document.createElement('button'); rt.className='tl-qretry'; rt.type='button'; rt.textContent='try again';
        rt.addEventListener('click',function(){init(node);}); box.appendChild(rt);
      });
      opts.appendChild(b);
    });
    box.appendChild(opts); node.appendChild(box);
  }
  document.querySelectorAll('.tl-quiz').forEach(init);
})();
"""


def _dots(counts):
    order = [("verified", "#22c55e"), ("decided", "#fbbf24"), ("open", "#475569")]
    out = []
    for st, col in order:
        out.append(f'<span style="color:{col}">' + "●" * counts.get(st, 0) + "</span>")
    return "".join(out)


def _want_parts(goal, bar, pl):
    """The WANT beat as 总-分-总: headline sentence · the project's core pillars (the bullets) ·
    the done-bar. Headline = goal's first sentence with any trailing 'The N pillars…/DONE…' prose
    trimmed (those live in the bullets + tail). Bullets = plan.pillars() — the structured core
    dimensions, same source as the pillar chips. done = bar, else the 'DONE …' clause from goal."""
    g = " ".join((goal or "").split())
    head = g
    for marker in (r"\bThe\s+\w+\s+pillars?\b", r"\bPillars?\s*:", r"\bDONE\b"):
        m = re.search(marker, head, re.I)
        if m:
            head = head[:m.start()]
    head = head.strip().rstrip(".;—- ")
    headline = re.split(r"(?<=[.])\s+", head)[0] if head else (g or "— no goal set yet —")
    bullets = [(p.get("id", ""), _trunc(p.get("plain") or p.get("choice") or p.get("decision", ""), 130))
               for p in plan.pillars(pl)]
    done = bar
    if not done:
        m = re.search(r"\bDONE\b\s*[:=]?\s*(.+)", g, re.I)
        if m:
            done = m.group(1).strip()
    done = re.sub(r'^bar\s+for\s+["“]?done["”]?\s*:?\s*', "", done, flags=re.I).strip()
    return headline, bullets, done


def story_beats(goal, bar, pl, nodes, rows):
    """The whole project told as ONE narrative arc — the five beats every project report
    leads with: 想做什么 · 遇到问题 · bottleneck · 干了什么 · 要做什么. Every beat is FOLDED
    from plan + log + tree (goal text, tree walls/verdicts, the load-bearing open decision);
    nothing here is hand-written. Returns a list of (cls, label, headline, sub) — empty parts
    degrade to an honest 'nothing logged yet' line, never a fabricated story."""
    mt = plan.main_thread(pl)
    summ = plan.summary(pl)
    n_open = summ["counts"].get("open", 0)

    def _verdict(n):
        return next((s for t, s in n.get("notes", []) if t == "verdict"), "")

    def _abandon(n):
        return next((s for t, s in n.get("notes", []) if t == "abandon"), "")

    # recency: latest event ts per direction, so "did / problem" read newest-first
    last_ts = {}
    for r in rows:
        d = r["direction"]
        if r["ts"]:
            last_ts[d] = max(last_ts.get(d, ""), r["ts"])
    order = sorted(nodes, key=lambda d: last_ts.get(d, ""), reverse=True)

    # 2 · 遇到问题 — directions with a wall still standing (no verdict, not abandoned)
    probs = [(d, next((w for w in reversed(nodes[d].get("walls", [])) if w), ""))
             for d in order
             if nodes[d].get("walls") and not nodes[d].get("abandoned") and not _verdict(nodes[d])]
    # 4 · 干了什么 — walls closed by a verdict, or directions backtracked
    did = []
    for d in order:
        n = nodes[d]
        v = _verdict(n)
        if v:
            did.append(("✓", d, v))
        elif n.get("abandoned"):
            did.append(("↩", d, _abandon(n) or "backtracked"))

    def _join(items, fmt, cap, more_noun):
        head = "  ·  ".join(fmt(x) for x in items[:cap])
        if len(items) > cap:
            head += f"  (+{len(items) - cap} more {more_noun})"
        return head

    beats = []
    # 1 · 想做什么 — 总分总: headline sentence · the core pillars (bulleted) · the done-bar
    head, bullets, done = _want_parts(goal, bar, pl)
    beats.append({"cls": "want", "label": "🎯 WHAT WE WANT", "head": head,
                  "bullets": bullets, "tail": (f"<b>done</b> = {_e(done)}" if done else "")})
    # 2 · 遇到问题
    if probs:
        beats.append({"cls": "prob", "label": "⛰ THE PROBLEM",
                      "head": _join(probs, lambda p: f"[{p[0]}] {p[1]}", 2, "walls"),
                      "sub": f"{len(probs)} wall(s) still standing"})
    else:
        beats.append({"cls": "prob", "label": "⛰ THE PROBLEM",
                      "head": "every wall hit so far has been closed"})
    # 3 · bottleneck (the load-bearing open decision)
    if mt:
        beats.append({"cls": "neck", "label": "🔻 BOTTLENECK", "head": mt.get("decision", ""),
                      "sub": f"main thread · {mt.get('id', '')}"})
    else:
        beats.append({"cls": "neck", "label": "🔻 BOTTLENECK",
                      "head": "no open bottleneck — every decision is settled"})
    # 4 · 干了什么
    if did:
        beats.append({"cls": "did", "label": "🔧 WHAT WE DID",
                      "head": _join(did, lambda x: f"{x[0]} [{x[1]}] {x[2]}", 3, "moves"),
                      "sub": f"{len(did)} direction(s) resolved or backtracked"})
    else:
        beats.append({"cls": "did", "label": "🔧 WHAT WE DID",
                      "head": "no verdicts or backtracks logged yet"})
    # 5 · 要做什么
    if mt:
        beats.append({"cls": "next", "label": "➡️ WHAT'S NEXT",
                      "head": mt.get("plain") or mt.get("decision") or "drive the main thread to a verdict",
                      "sub": f"{n_open} decision(s) still open" if n_open else ""})
    else:
        beats.append({"cls": "next", "label": "➡️ WHAT'S NEXT",
                      "head": "harden, verify the unverified, and ship"})
    return beats


def _render_beats(beats):
    """Render a list of story beats to HTML. A beat is {cls,label,head} plus optional
    sub / bullets (总分总 pillar list) / tail (pre-escaped HTML). Shared by the mature
    five-beat arc and the planning-stage arc so they stay visually identical."""
    H = ["<div class='story'>"]
    for b in beats:
        body = [f"<div class='bt'>{_ec(b['head'])}"]
        if b.get("sub"):
            body.append(f"<span class='sm'>{_ec(b['sub'])}</span>")
        if b.get("bullets"):
            body.append("<ul class='blist'>")
            for bid, btext in b["bullets"]:
                body.append(f"<li><b>{_e(bid)}</b> — {_ec(btext)}</li>")
            body.append("</ul>")
        if b.get("tail"):
            body.append(f"<div class='tail'>{b['tail']}</div>")  # tail is pre-escaped HTML
        body.append("</div>")
        _bchat = "<div class='tl-chat' data-block='what-we-want'></div>" if b.get("cls") == "want" else ""
        H.append(f"<div class='beat {b['cls']}'><div class='bl'>{_e(b['label'])}</div>"
                 f"{''.join(body)}{_bchat}</div>")
    H.append("</div>")
    return "\n".join(H)


def story_html(goal, bar, pl, nodes, rows):
    """Render the five-beat narrative arc as the lead of a project report. The WANT beat may
    carry `bullets` (总分总: headline · bulleted core pillars · done-tail); others are head+sub."""
    return _render_beats(story_beats(goal, bar, pl, nodes, rows))


def planning_story_beats(motivation, goal, bar, pl):
    """The PLANNING-STAGE arc — for a project that has NOT run experiments yet (no log, no
    search tree). The mature five-beat arc leans on the log (timeline, walls hit, verdicts),
    so before any run it renders three empty boxes and reads as broken. This arc is fed
    ENTIRELY by the plan + goal, so every beat carries real content: why it matters · what
    we're building (总分总 headline+pillars+done) · the one open decision everything waits on ·
    the concrete next move. All-English (the mature arc keeps its bilingual labels untouched)."""
    mt = plan.main_thread(pl)
    n_open = plan.summary(pl)["counts"].get("open", 0)
    beats = []
    if motivation:
        beats.append({"cls": "prob", "label": "💡 MOTIVATION", "head": motivation})
    head, bullets, done = _want_parts(goal, bar, pl)
    beats.append({"cls": "want", "label": "🎯 GOAL", "head": head, "bullets": bullets,
                  "tail": (f"<b>done</b> = {_e(done)}" if done else "")})
    if mt:
        beats.append({"cls": "neck", "label": "🔻 MAIN THREAD", "head": mt.get("decision", ""),
                      "sub": f"the one open decision everything waits on · {mt.get('id','')}"})
        beats.append({"cls": "next", "label": "➡️ NEXT",
                      "head": mt.get("plain") or mt.get("decision") or "settle this decision next",
                      "sub": f"{n_open} decision(s) still open" if n_open else ""})
    return beats


def planning_story_html(motivation, goal, bar, pl):
    return _render_beats(planning_story_beats(motivation, goal, bar, pl))


# The report's HOOK: a report is a story of its SURPRISES (voice rule 6), not a did-list. This band
# leads with where reality broke from intuition. Fed by surprises.<name>.jsonl:
#   {ts, valence, headline, detail, direction?}. Empty file -> nothing renders.
SURPRISE_LABEL = {
    "assumed-bottleneck-not":  "looked like the bottleneck — wasn't",
    "hidden-bottleneck":       "the real wall, hidden in a 'done' decision",
    "hard-turned-free":        "looked hard — was free",
    "easy-turned-hard":        "looked trivial — was the wall",
    "metric-green-output-bad": "green metric, quietly-wrong output",
    "doubt":                   "a genuine doubt",
}


def surprises_html(surprises):
    """The 🎢 band — the intuition-vs-reality gaps, newest first. Each card = a valence badge +
    a headline + one detail line. This is the report's lead hook, not a footnote."""
    if not surprises:
        return ""
    rows = sorted((s for s in surprises if isinstance(s, dict) and s.get("headline")),
                  key=lambda s: s.get("ts", ""), reverse=True)
    if not rows:
        return ""
    cards = []
    for s in rows:
        val = s.get("valence", "")
        badge = _e(SURPRISE_LABEL.get(val, val or "surprise"))
        dirn = f" <span class='surp-dir'>{_e(s.get('direction'))}</span>" if s.get("direction") else ""
        _f = _e(f"SURPRISE ({val}) — {s.get('headline','')}: {s.get('detail','')}"
                + (f"  [direction: {s.get('direction')}]" if s.get("direction") else ""))
        sid = stable_line_id("surprise", s)  # matches chat_backend.stable_line_id exactly
        cards.append(
            f"<div class='surp surp-{_e(val)}'>"
            f"<div class='surp-h'><span class='surp-badge'>{badge}</span>{dirn}</div>"
            f"<div class='surp-head'{_eattr('surprise', 'headline', s.get('headline',''), id=sid)}>"
            f"{_ec(s.get('headline',''))}</div>"
            f"<div class='surp-d'{_eattr('surprise', 'detail', s.get('detail',''), id=sid)}>"
            f"{_ec(s.get('detail',''))}</div>"
            f"<div class='tl-chat' data-focus=\"{_f}\"></div></div>")
    return ("<div class='surprises'><div class='surp-title'>🎢 What surprised us — "
            "where reality broke from the plan</div>" + "".join(cards) + "</div>")


def purpose_funnel_html(purpose, mt=None, edit_attrs=""):
    """The report's OPENING line — one plain sentence of why this exists (the whole point, big→small).
    Fed by purpose.<name>.txt; any 'LABEL:' prefixes are stripped and the lines joined into one lead.
    Empty purpose -> nothing (the missing-purpose lint nags instead). `edit_attrs` (data-e-* from
    render_html) makes the lead the inline-editable purpose target."""
    if not purpose:
        return ""
    parts = []
    for line in purpose.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line and line.split(":", 1)[0].isupper():
            line = line.split(":", 1)[1].strip()
        parts.append(line)
    lead = " ".join(parts).strip()
    return f"<div class='lead'{edit_attrs}>{_ec(lead)}</div>" if lead else ""


def focus_section_html(name):
    """CURRENT FOCUS — the active trial-and-error work right now (distinct from the main thread,
    which is ONE decision, and pillars, which are settled core dimensions). Reads
    research/focus.<name>.jsonl: {id, title, decision?, status, trying, next?}. Empty file -> ''."""
    fp = paths.resolve(f"focus.{name}.jsonl")
    if not fp.exists():
        return ""
    items = []
    try:
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                items.append(json.loads(line))
    except Exception:
        return ""
    if not items:
        return ""
    color = {"trying": "#2563eb", "blocked": "#dc2626", "done": "#16a34a"}
    cards = []
    for it in items:
        st = str(it.get("status", "trying")).lower()
        fid = stable_line_id("focus", it)  # matches chat_backend.stable_line_id (honors jsonl "id")
        dec = f"<span class='fdec'>{_e(it.get('decision',''))}</span>" if it.get("decision") else ""
        nxt = (f"<div class='fnext'><b>next:</b> "
               f"<span{_eattr('focus', 'next', it.get('next',''), id=fid)}>{_ec(it.get('next',''))}</span></div>"
               ) if it.get("next") else ""
        _f = _e(f"FOCUS ITEM [{st}] {it.get('title','')} — trying: {it.get('trying','')}; next: {it.get('next','')}")
        cards.append(
            f"<div class='fcard'>"
            f"<div class='fhead'><span class='fst' style='background:{color.get(st,'#64748b')}'"
            f"{_eattr('focus', 'status', it.get('status','trying'), id=fid, type='select', opts='trying,blocked,done')}>"
            f"{_e(st)}</span>"
            f"<span class='ftitle'{_eattr('focus', 'title', it.get('title',''), id=fid)}>"
            f"{_ec(it.get('title',''))}</span>{dec}</div>"
            f"<div class='ftry'{_eattr('focus', 'trying', it.get('trying',''), id=fid)}>"
            f"{_ec(it.get('trying',''))}</div>{nxt}"
            f"<div class='tl-chat' data-focus=\"{_f}\"></div></div>")
    return ("<div class='focussec'><div class='fshdr'>🎯 CURRENT FOCUS — what we're actively trying now</div>"
            + "".join(cards) + "</div>")


def data_section_html(pl):
    """A dedicated, always-visible DATA panel: for each decision that carries `examples`, show a
    high-level title (its plain summary) then the real samples as code blocks. This is the one
    place that DEMONSTRATES what data the pipeline moves — so it isn't buried in the spine."""
    blocks = []
    for n in (pl or []):
        ex = n.get("examples") or []
        if not ex:
            continue
        title = _ec(n.get("plain") or n.get("decision", ""))
        rows = []
        for x in ex:
            if isinstance(x, dict):
                cap, code = _ec(x.get("cap", "")), _e(x.get("code", ""))
                rows.append((f"<div class='excap'>{cap}</div>" if cap else "")
                            + f"<pre class='excode'>{code}</pre>")
            else:
                rows.append(f"<pre class='excode'>{_e(str(x))}</pre>")
        blocks.append(f"<div class='dsblock'><div class='dstitle'>{title}</div>{''.join(rows)}</div>")
    if not blocks:
        return ""
    return ("<div class='datasec'><div class='dshdr'>DATA — what the rewriter reads &amp; writes</div>"
            + "".join(blocks) + "<div class='tl-chat' data-block='data-section'></div></div>")


def _quiz_lead(s, cap=180):
    """The sharp lead of a quiz answer — strip a leading 'Principle:' label and keep the first
    sentence, so the correct OPTION is one crisp line, not the full teaching paragraph."""
    s = re.sub(r"^\s*Principle:\s*", "", (s or "").strip(), flags=re.I)
    m = re.search(r"[.;](\s|$)", s)
    if m:
        s = s[: m.start() + 1]
    return _trunc(s, cap)


def _quiz_rows():
    """The teaching quiz bank (quiz.jsonl, repo root — sibling of research/). Each row is
    {principle, q, naive, a, why, ...}; we map it to per-decision multiple-choice by principle."""
    for p in (ROOT.parent / "quiz.jsonl", ROOT / "quiz.jsonl"):
        if p.exists():
            return tree._load_jsonl(p)
    return []


def _decision_quiz(pl, qrows=None, name=None):
    """ONE offline multiple-choice question per decision, built from the quiz row whose
    `principle` matches the decision's principle: correct = that row's answer (sharp lead),
    trap = its `naive`, + up to two distractor `naive`s from OTHER principles. Graded entirely
    in the browser (no API key), so the report stays self-contained. Option order is shuffled
    deterministically (hashed by decision id) for reproducible builds. Decisions whose principle
    has no quiz row are omitted — their widget simply renders nothing.

    The decision's principle is resolved through the per-project adapter FIRST
    (plan.canonical_principle), so a project's local id (proposals' `proposal-addressing`, kimi's
    `tool-name-mapping`) lands on the single canonical rule (keys-must-be-canonical-...) and shares
    its ONE quiz row — rules stay a single copy, the project only carries an adapter.
    Returns {decId: {q, options:[...], correct: idx, why, principle}}."""
    qrows = _quiz_rows() if qrows is None else qrows
    by_pr = {}
    for r in qrows:
        pr = r.get("principle")
        if pr:
            by_pr.setdefault(pr, []).append(r)
    # Distractor pool, SAME-DOMAIN first: naives from the OTHER (canonical) principles this very
    # plan uses, then any naive in the bank as fallback. Keeps a proposals question's wrong answers
    # about proposals — not the ML scars that happen to sit at the top of quiz.jsonl.
    plan_prs = [p for p in (plan.canonical_principle(n.get("principle"), name) for n in pl)
                if p in by_pr]
    same_naive = [by_pr[p][0].get("naive", "").strip() for p in plan_prs
                  if by_pr[p][0].get("naive", "").strip()]
    all_naive = [r.get("naive", "").strip() for r in qrows if r.get("naive", "").strip()]
    out = {}
    for n in pl:
        did = n.get("id")
        pr = plan.canonical_principle(n.get("principle"), name)
        if not did or pr not in by_pr:
            continue
        row = by_pr[pr][0]
        correct = _quiz_lead(row.get("a", ""))
        if not correct:
            continue
        opts, seen = [correct], {correct.lower()}
        trap = (row.get("naive", "") or "").strip()
        if trap and trap.lower() not in seen:
            opts.append(trap)
            seen.add(trap.lower())
        for nv in same_naive + all_naive:  # same-domain distractors first, bank as fallback
            if len(opts) >= 4:
                break
            if nv.lower() not in seen:
                opts.append(nv)
                seen.add(nv.lower())
        order = sorted(range(len(opts)),
                       key=lambda i: hashlib.md5((did + opts[i]).encode("utf-8")).hexdigest())
        shuffled = [opts[i] for i in order]
        out[did] = {"q": row.get("q", ""), "options": shuffled,
                    "correct": shuffled.index(correct), "why": row.get("why", ""),
                    "principle": pr}
    return out


def _chat_blob(name, goal, pl, glossary, clarify):
    """The grounding the in-browser chatbot reads: project goal, the full glossary (for the
    system prompt), and per-decision context + already-absorbed FAQ/terms. Pure render of the
    substrate — the widget invents nothing, it only asks/captures around what's here."""
    clar_by, gloss_by = {}, {}
    for c in clarify:
        clar_by.setdefault(c.get("dec"), []).append(
            {"q": c.get("q", ""), "a": c.get("a", ""), "ts": c.get("ts", "")})
    for g in glossary:
        if g.get("dec"):
            gloss_by.setdefault(g["dec"], []).append(
                {"term": g.get("term", ""), "plain": g.get("plain", ""), "why": g.get("why", "")})
    quizmap = _decision_quiz(pl, name=name)
    decmap = {}
    for n in pl:
        did = n.get("id")
        if not did:
            continue
        decmap[did] = {"decision": n.get("decision", ""), "choice": n.get("choice", ""),
                       "principle": n.get("principle", ""), "why": n.get("why", ""),
                       "faq": clar_by.get(did, []), "terms": gloss_by.get(did, []),
                       "quiz": quizmap.get(did)}
        # the anchored code (resolved in _load_project) grounds the decision's Ask-AI chat too,
        # so "what does this code do?" is answerable from the card itself
        _anc_txt = "\n\n".join(f"{a.get('loc','')}\n{a.get('code','')}"
                               for a in (n.get("_anchors") or [])
                               if not a.get("paper") and a.get("code"))
        if _anc_txt:
            decmap[did]["code"] = _anc_txt[:3000]
    # per-BLOCK grounding: {title, text} for each report section, assembled from the SAME sources
    # viz renders from — so every block's chatbot answers from its own content (block-context-blob).
    blocks = {}
    ex_txt = []
    for n in pl:
        for e in (n.get("examples") or []):
            if isinstance(e, dict):
                ex_txt.append(f"{e.get('cap','')}: {e.get('code','')}")
    if ex_txt:
        blocks["data-section"] = {"title": "DATA — what the report's project reads & writes",
                                  "text": "  ".join(ex_txt)}
    try:
        foc = [json.loads(l) for l in paths.resolve(f"focus.{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        foc = []
    if foc:
        blocks["current-focus"] = {"title": "CURRENT FOCUS",
            "text": "  ".join(f"[{f.get('status')}] {f.get('title')}: {f.get('trying','')} NEXT: {f.get('next','')}" for f in foc)}
    try:
        stg = [json.loads(l) for l in paths.resolve(f"pipeline.{name}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        stg = []
    if stg:
        blocks["pipeline"] = {"title": "Pipeline — the data flow",
                              "text": " -> ".join(f"{s.get('label')} ({s.get('note')})" for s in stg)}
    if goal:
        blocks["what-we-want"] = {"title": "WHAT WE WANT", "text": " ".join(goal.split())[:600]}
    # GLOBAL context so any block's (or decision's) chatbot can cross-reference the whole plan
    context = {"goal": " ".join((goal or "").split())[:300],
               "decisions": [f"{n.get('id')}: {n.get('plain') or n.get('decision','')}" for n in pl if n.get("id")],
               "blocks": [b["title"] for b in blocks.values()]}
    data = {"project": name, "goal": goal, "model": "claude-opus-4-8",
            "glossary": [{"term": g.get("term", ""), "plain": g.get("plain", ""),
                          "why": g.get("why", "")} for g in glossary],
            "decisions": decmap, "blocks": blocks, "context": context}
    # escape '<' so the JSON can never break out of its <script> host
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def pipeline_html(name):
    """The REAL data flow, authored per-project in research/pipeline.<name>.jsonl as an ordered
    list of {label, note} stages, rendered left-to-right with arrows. No file -> nothing. (The old
    version laid out the plan's PHASES with arrows, which was misleading — phases are decision
    categories, not a processing flow.)"""
    fp = paths.resolve(f"pipeline.{name}.jsonl")
    if not fp.exists():
        return ""
    stages = []
    try:
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                stages.append(json.loads(line))
    except Exception:
        return ""
    if not stages:
        return ""
    cards = []
    for i, s in enumerate(stages):
        cards.append(f"<div class='pp-stage'><div class='pp-t'>{_ec(s.get('label',''))}</div>"
                     f"<div class='pp-note'>{_ec(s.get('note',''))}</div></div>")
        if i < len(stages) - 1:
            cards.append("<div class='pp-arr'>▶</div>")
    return ("<h2 class='sec'>Pipeline — the data flow</h2>"
            "<div class='pp'>" + "".join(cards) + "</div>"
            "<div class='tl-chat' data-block='pipeline'></div>")


def _gloss_map(glossary):
    m = {}
    for g in glossary or []:
        t = (g.get("term") or "").strip()
        if len(t) >= 2 and g.get("plain"):
            m.setdefault(t.lower(), g["plain"])
    return m


def _gloss(text_escaped, gmap):
    """Wrap the first occurrence of each glossary term with a hover-tooltip <abbr> (dotted)."""
    for term in sorted(gmap, key=len, reverse=True):
        pat = re.compile(r"(?<![\w-])(" + re.escape(_e(term)) + r")(?![\w-])", re.I)
        text_escaped = pat.sub(
            lambda mo: f"<abbr class='gl-term' title=\"{_e(gmap[term])}\">{mo.group(1)}</abbr>",
            text_escaped, count=1)
    return text_escaped


def glossary_html(glossary):
    seen, rows = set(), []
    for g in sorted(glossary or [], key=lambda x: (x.get("term") or "").lower()):
        t = (g.get("term") or "").strip()
        if not t or t.lower() in seen or not g.get("plain"):
            continue
        seen.add(t.lower())
        # id = the term (backend matches glossary.<name>.jsonl by term). plain + why are editable;
        # the why span always renders (empty when unset) so edit mode can ADD a why.
        rows.append(
            f"<div class='gl-row'><b>{_e(t)}</b> — "
            f"<span class='gl-plain'{_eattr('glossary', 'plain', g.get('plain',''), id=t)}>{_e(g.get('plain',''))}</span> "
            f"<span class='gl-why'{_eattr('glossary', 'why', g.get('why',''), id=t)}>{_e(g.get('why',''))}</span></div>")
    if not rows:
        return ""
    return ("<details class='gl-box'><summary>Glossary — every term in plain words</summary>"
            + "".join(rows) + "</details>")


def feedback_section_html(name):
    """🖍 Operator feedback — every note/question the reader left, digested into WHY it was
    left (confusion / correction / readability) plus the action each implies. The digest is
    written by feedback.py at --absorb time; renders only when feedback exists. This closes
    the loop visibly: the reader sees their margin notes were heard, the agent reads the same
    file (feedback.<name>.jsonl) to fix the glossary, re-examine disputed decisions, and
    improve the report itself."""
    try:
        fb = [e for e in tree._load_jsonl(paths.resolve(f"feedback.{name}.jsonl"))
              if isinstance(e, dict)]
        if not fb:
            return ""
        col = {"confusion": "#1d4ed8", "correction": "#b91c1c", "readability": "#92400e"}
        kinds = {}
        rows = []
        for e in fb:
            k = str(e.get("kind") or "unclassified")
            done = bool(e.get("resolved"))
            kinds[k] = kinds.get(k, 0) + 1
            act = str(e.get("action") or e.get("insight") or "")
            rows.append(
                f"<div class='fb-row{' done' if done else ''}'>"
                f"<span class='fb-kind' style='background:{col.get(k, '#64748b')}'>{_e(k)}</span>"
                f"<div><div class='fb-note'>“{_e(_trunc(str(e.get('quote') or ''), 90))}” — "
                f"{_ec(str(e.get('note') or ''))}</div>"
                + (f"<div class='fb-act'>→ {_ec(act)}</div>" if act else "")
                + ("<div class='fb-done'>✓ addressed</div>" if done else "")
                + "</div></div>")
        head = " · ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
        return (f"<details class='gl-box'><summary>🖍 Operator feedback — {head}</summary>"
                + "".join(rows) + "</details>")
    except Exception:
        return ""  # feedback is an annotation layer — it must never take the report down


def render_html(name, goal, bar, pl, nodes, knowledge, kinds, id2phase, phase_order,
                glossary=None, clarify=None, motivation="", tldr="", surprises=None, purpose="",
                narrative=""):
    glossary, clarify = glossary or [], clarify or []
    gmap = _gloss_map(glossary)
    summ = plan.summary(pl)
    counts = summ["counts"]
    mt = plan.main_thread(pl)
    pillars = plan.pillars(pl)
    avoided = plan.avoided(pl)
    rows = timeline_rows(tree.load_events(name, tree.load_facts(name)), knowledge)
    # PLANNING STAGE: a plan exists but no experiments have run (no dated log events, no search
    # tree). The mature arc/timeline/tree would render empty; instead tell a tight plan story.
    planning = bool(pl) and not rows and not nodes

    H = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{_e(name)} — research tree</title><style>{CSS}{CHAT_CSS}{QUIZ_CSS}{ANNOT_CSS}{EDIT_CSS}</style>"
         # JS-STRIPPED viewers (phone inline preview, some sandboxes) never run NAV_JS, and the
         # tabbed sections default to display:none — without this fallback the whole report body
         # renders BLANK there. No JS -> hide the dead tab bar, lay every section out in flow
         # (same shape the print stylesheet uses).
         "<noscript><style>.rnav{display:none}.rsec{display:block}</style></noscript>"
         "</head><body><div class='wrap'>"]

    # ---- header / TLDR ----
    H.append("<div class='hdr'>")
    _sub = ("Trainlint plan · a plan in progress — motivation · goal · decisions · next"
            if planning else
            "research tree · the project as one story: want · problem · bottleneck · did · next")
    H.append(f"<h1>{_e(name)}</h1><div class='sub'>{_sub}</div>")
    if narrative:  # LLM wrote the prose — it replaces the templated lead+tldr+surprises+newly-done
        H.append(f"<div class='llm'>{_md2html(narrative)}</div>")
    else:
        # one plain lead line: why we're doing this. Editable: the purpose lead writes back to
        # purpose.<name>.txt (prev = the RAW file text, whole-file overwrite); the goal fallback
        # writes back to goal.<name>.txt (prev = the RAW file incl. its DONE/Pillars clauses).
        _lead = purpose_funnel_html(purpose, mt, _eattr("purpose", "", purpose))
        if not _lead and goal:  # no purpose authored yet -> the GOAL is still the reader's anchor
            _goal_raw = goal
            try:
                _gp = paths.resolve(f"goal.{name}.txt")
                if _gp.exists():
                    _goal_raw = _gp.read_text(encoding="utf-8").strip()
            except Exception:
                pass
            _lead = f"<div class='lead'{_eattr('goal', '', _goal_raw)}>{_ec(goal)}</div>"
        H.append(_lead)
        if tldr:  # TL;DR — one bullet per line of tldr.<name>.txt
            _tl = [ln.strip().lstrip("-•* \t").strip() for ln in tldr.splitlines() if ln.strip()]
            _body = ("<ul class='tldr-list'>" + "".join(f"<li>{_ec(l)}</li>" for l in _tl) + "</ul>"
                     if len(_tl) > 1 else (_ec(_tl[0]) if _tl else ""))
            H.append(f"<div class='tldr'><span class='tldr-tag'>TL;DR</span>{_body}</div>")
    if bar:  # the DONE bar (the project's success criterion) always shows, whoever wrote the prose
        H.append(f"<div class='kv'><span class='k'>DONE</span><span>{_ec(bar)}</span></div>")
    if planning:  # planning stage has no log/tree yet -> the plan-only arc is fine; the mature
        H.append(planning_story_html(motivation, goal, bar, pl))  # 5-beat is dropped (redundant with
    # the lead line + surprises + newly-done + focus + timeline, and it lied "nothing done" when the
    # tree had no verdicts even though the log was full).
    H.append("<div class='score'><div class='dots'>" + _dots(counts) + "</div>"
             f"<div class='lbl'>{summ.get('decided_built',0)}/{counts.get('decided',0)} decided built · "
             f"{counts.get('verified',0)} verified · {counts.get('open',0)} open  "
             f"({summ['total']} decisions)</div></div>")
    if pillars:
        H.append("<div class='chips'>" + "".join(
            f"<span class='chip pillar'>◆ {_e(p['id'])}</span>" for p in pillars) + "</div>")
    # NOTE: the anti-prior "don't drift back" list (plan.avoided / not_this) is an AGENT guardrail —
    # it stops the model regressing to a rejected approach, and it lives in the compass + doorman for
    # that purpose. It is NOT for the human reader (terse regex-adjacent jargon), so it is deliberately
    # NOT rendered in this report. A human-facing "what we ruled out, and why" belongs in the story
    # (plain prose + the reason), authored — not this auto-dump.
    try:
        import goalcheck as _gc  # noqa: E402
        _gd = _gc.brief(name)
    except Exception:
        _gd = ""
    if _gd:  # _gd is self-labeled (means-first and/or scope-drift) — render as-is
        H.append("<div class='rej'>" + _e(_gd) + "</div>")
    H.append("</div>")  # hdr

    # ---- SECTIONED BODY: everything below the header is grouped into tabbed sections behind
    # the sticky .rnav bar (one view on screen at a time — the whole report was one endless
    # scroll). An empty section drops out, taking its tab with it; NAV_JS drives the switching.

    # ---- 🎢 SURPRISES: always render the structured band (each card carries its own per-item
    # chatbox) — even under the LLM narrative, so every surprise stays individually askable. ----
    now_sec = [surprises_html(surprises)]

    # ---- 🆕 NEWLY DONE: which DECISION(s) moved this run, in one short plain sentence each
    # (the decision's `plain` field — jargon-free by design). All dated detail is in the Timeline
    # below; this bar stays a one-glance summary, never a note dump. ----
    _new_ids, _new_date = newly_done(name)  # id-set also powers the per-decision 🆕 badge in the spine
    if _new_ids and not narrative:
        _id2plain = {n.get("id"): (n.get("plain") or n.get("decision", "")) for n in pl}
        _ids = sorted(_new_ids)
        _phr = " · ".join(_ec(_id2plain.get(i, i)) for i in _ids[:2])
        if len(_ids) > 2:
            _phr += f" (+{len(_ids) - 2} more — see Timeline)"
        now_sec.append(f"<div class='newbar'>🆕 <b>Newly done ({_e(_new_date)}):</b> {_phr}</div>")

    # ---- CURRENT FOCUS: the active trial-and-error work right now ----
    now_sec.append(focus_section_html(name))

    # ---- DATA section + pipeline: what the project reads & writes, and the REAL data flow ----
    flow_sec = [data_section_html(pl), pipeline_html(name)]

    # ---- anchors rollup: reviewability of the whole plan in one line (from n['_anchors'],
    # resolved in _load_project). Red count = built decisions a reviewer can't see code for.
    _acnt = {"pinned": 0, "drifted": 0, "broken": 0, "other": 0, "unanchored": 0}
    _abase = paths.project_home(name) or None  # cwd-independent (digest re-render path)
    for _n in pl:
        _codeanc = [a for a in (_n.get("_anchors") or []) if not a.get("paper")]
        for _a in _codeanc:
            k = _a.get("kind")
            _acnt["pinned" if k in ("pinned", "commit") else
                  "drifted" if k == "drifted" else
                  "broken" if k == "missing" else "other"] += 1
        if (not _codeanc and _n.get("status") in ("decided", "verified")
                and plan.artifact_exists(_n, _abase)):
            _acnt["unanchored"] += 1
    anch_line = ""
    if any(_acnt.values()):
        parts = [f"{_acnt['pinned']} pinned"]
        if _acnt["drifted"]:
            parts.append(f"⚠ {_acnt['drifted']} drifted")
        if _acnt["broken"]:
            parts.append(f"<b class='anch-red'>✗ {_acnt['broken']} broken</b>")
        if _acnt["other"]:
            parts.append(f"{_acnt['other']} unpinned")
        if _acnt["unanchored"]:
            parts.append(f"<b class='anch-red'>✗ {_acnt['unanchored']} built with NO code to review</b>")
        anch_line = "<span><b>anchors</b> ⛓ " + " · ".join(parts) + "</span>"
        # the LANDING tab must point at the code, or a reviewer never finds it (the code lives
        # 2 clicks deep in 🧭 Decisions; this strip is the front-door). NAV_JS's #href handler
        # switches tabs on click; with JS stripped it's a plain in-page anchor to the same section.
        now_sec.insert(0, (
            "<a class='ancbar' href='#sec-decisions'>⛓ <b>Code under review:</b> "
            + " · ".join(parts)
            + " <span class='ancbar-go'>see every decision's code →</span></a>"))

    # ---- legend (lives with the decisions it explains) ----
    if planning:
        legend = ("<div class='legend'>"
                  "<span><b>decisions</b> ✓ verified ◐ decided+built ✎ decided on paper (not built) ○ open</span>"
                  "<span>◆ pillar — a core dimension the project always rests on</span>"
                  "<span>★ main thread — the one decision to settle next</span>"
                  + anch_line +
                  "<span>click a decision to see its principle (or ask its chatbot)</span></div>")
    else:
        legend = ("<div class='legend'>"
                  "<span><b>spine</b> ✓ verified ◐ decided+built ✎ decided on paper (not built) ○ open</span>"
                  "<span><b>tree</b> ⚠ open problem · ✓ wall closed · ◆ tested · ↩ backtracked · ● decided · ○ idea</span>"
                  "<span><b>edges</b> ⚠ wall → 📖 paper it unlocks</span>"
                  + anch_line +
                  "<span>click a decision to see its principle</span></div>")

    # ---- timeline (suppressed at planning stage — nothing has happened yet) ----
    tl_sec = []
    if not planning:
        tl_sec.append("<h2 class='sec'>Timeline — how the search got here</h2>")
        if rows:
            tl_sec.append("<div class='card tl'>")
            for r in rows:
                g, col, lbl = KIND.get(r["kind"], ("•", "#64748b", r["kind"]))
                d = r["delta"]
                dhtml = ""
                if d is not None:
                    cls = "up" if d > 0 else "flat"
                    dhtml = f"<span class='delta {cls}'>{('+' if d>0 else '')}{d}</span>"
                read = ""
                if r["paper"]:
                    read = (f"<br><a class='read' href='#kn-{_e(r['paper']['id'])}'>"
                            f"📖 now readable: {_e(_trunc(r['paper']['title'],46))}</a>")
                _tf = _e(f"TIMELINE {r['ts']} [{lbl}] {r['direction']}: {r['note']}")
                tl_sec.append(f"<div class='row'><div class='date'>{_e(r['ts'])}</div>"
                              f"<div class='mk' style='color:{col}'>{g}</div>"
                              f"<div class='body'><span class='dir'>{_e(r['direction'])}</span>"
                              f"<span class='knd'>{_e(lbl)}</span>{dhtml}"
                              f"<div class='note'>{_e(r['note'])}{read}</div>"
                              f"<div class='tl-chat' data-focus=\"{_tf}\"></div></div></div>")
            tl_sec.append("</div>")
        else:
            tl_sec.append("<div class='card'><div class='empty'>No dated events harvested yet — the "
                          "timeline fills in from the session log (walls, verdicts, backtracks).</div></div>")

    # ---- decision spine (full-width at planning stage; beside the search tree once it exists) ----
    spine = ["<div><h2 class='sec'>"
             + ("Decisions — strategy first, then implementation" if planning else "Decision spine — strategy vs implementation")
             + "</h2><div class='card'>"]
    # Group by ALTITUDE, not phase: the high-level bets up front; the code-level contracts &
    # details folded away under Implementation, so strategy isn't drowned in detail.
    _lvl_groups = [
        ("Strategy — the high-level bets", [n for n in pl if n.get("level", "high") != "impl"], False),
        ("Implementation — code-level contracts & detail", [n for n in pl if n.get("level") == "impl"], True),
    ]
    for _gname, _decs, _collapse in _lvl_groups:
        if not _decs:
            continue
        if _collapse:
            spine.append(f"<details class='implgrp'><summary class='phase'>{_e(_gname)} · {len(_decs)}</summary>")
        else:
            spine.append(f"<div class='phase'>{_e(_gname)}</div>")
        for n in _decs:
            st = n.get("status", "open")
            you = "<span class='you'>← you are here</span>" if (mt and n.get("id") == mt.get("id")) else ""
            pl_tag = "<span class='pill-tag'>◆ pillar</span>" if n.get("pillar") else ""
            new_tag = "<span class='new-tag'>🆕 NEW</span>" if n.get("id") in _new_ids else ""
            _g, _c = _dec_glyph(n)
            # SUMMARY up front = one plain-language sentence (the `plain` field); fall back to the
            # choice only if a decision hasn't got one yet (the lint flags those).
            plain = _ec(n.get("plain", "")) or _gloss(_e(n.get("choice", "")), gmap)
            # EXAMPLES go in a COLLAPSED foldable block — clear, indented, out of the way until opened.
            ex = n.get("examples") or []
            ex_html = ""
            if ex:
                rows = []
                for x in ex:
                    if isinstance(x, dict):  # {cap, code}: a caption + a real code block
                        cap = _ec(x.get("cap", ""))
                        code = _e(x.get("code", ""))
                        rows.append(f"<div class='exitem'>"
                                    + (f"<div class='excap'>{cap}</div>" if cap else "")
                                    + f"<pre class='excode'>{code}</pre></div>")
                    else:  # legacy string -> still put it in a code block, never loose prose
                        rows.append(f"<div class='exitem'><pre class='excode'>{_e(str(x))}</pre></div>")
                # examples OPEN by default — the whole point is to SEE them, not dig two folds down
                ex_html = (f"<details class='draw' open><summary>examples ({len(ex)})</summary>"
                           f"<div class='dex'>{''.join(rows)}</div></details>")
            # ---- ANCHORS: the exact code this decision is reviewable against (baked at render
            # time — see _resolve_anchors). The card gets a summary badge (⛓/⚠/✗) and an open fold
            # with the pinned snippet(s); a BUILT decision with no anchor gets a loud red stub
            # instead, so "nothing to review" is visible debt, not silence.
            anc = [a for a in (n.get("_anchors") or []) if not a.get("paper")]
            # base=project_home: viz is also spawned by the digest re-render with a foreign cwd,
            # and a relative artifact path must not silently flip built -> unbuilt there.
            _built = plan.artifact_exists(n, paths.project_home(name) or None)
            _kinds = {a.get("kind") for a in anc}
            if _kinds & {"missing"}:  # a pin pointing at nothing is broken, not green
                anch_tag = "<span class='anch-tag miss'>✗ anchor broken</span>"
            elif _kinds & {"drifted", "unreachable"}:
                anch_tag = "<span class='anch-tag warn'>⚠ code drifted</span>"
            elif anc:
                anch_tag = "<span class='anch-tag ok'>⛓ code</span>"
            elif _built:  # "paper" never excuses a BUILT decision: artifact on disk = there IS code
                anch_tag = "<span class='anch-tag miss'>✗ no code to review</span>"
            else:
                anch_tag = ""
            anc_html = ""
            if anc:
                arows = []
                for a in anc:
                    dif_html = ""
                    if a.get("diff"):
                        dlines = []
                        for dl in a["diff"].splitlines():
                            c = ("dfa" if dl.startswith("+") and not dl.startswith("+++") else
                                 "dfr" if dl.startswith("-") and not dl.startswith("---") else "")
                            dlines.append(f"<span class='{c}'>{_e(dl)}</span>" if c else _e(dl))
                        dif_html = (f"<details class='draw'><summary>what changed since "
                                    f"(pinned → working tree)</summary>"
                                    f"<pre class='excode anccode'>{chr(10).join(dlines)}</pre></details>")
                    note = f" — {_e(a['note'])}" if a.get("note") else ""
                    cmd = (f"<div class='anccmd'>reproduce: <code>{_e(a['cmd'])}</code></div>"
                           if a.get("cmd") else "")
                    code_html = (f"<pre class='excode anccode'>{_e(a['code'])}</pre>"
                                 if a.get("code") else "")
                    # the click-through: open EXACTLY these lines at EXACTLY this commit on the
                    # repo's web UI — the "take me to the code" button this whole feature is for
                    link = (f"<a class='anclink' href='{_e(a['href'])}' target='_blank' "
                            f"rel='noopener'>open this code ↗</a>" if a.get("href") else "")
                    arows.append(f"<div class='exitem'>"
                                 f"<div class='anccap'><code>{_e(a['loc'])}</code>{link}"
                                 f"<br>{_e(a['cap'])}{note}</div>{code_html}{dif_html}{cmd}</div>")
                anc_html = (f"<details class='draw' open><summary>⛓ the code behind this decision "
                            f"({len(anc)})</summary><div class='dex'>{''.join(arows)}</div></details>")
            elif _built:
                anc_html = (f"<div class='anch-stub'>✗ built, but no anchor recorded — a reviewer "
                            f"can't see WHICH code this is. Backfill: <code>python3 "
                            f"$CLAUDE_PLUGIN_ROOT/research/anchor.py {_e(name)} {_e(n.get('id',''))} "
                            f"&lt;file&gt;:&lt;start&gt;-&lt;end&gt;</code></div>")
            # the dense original decision text also folds away — open only if you want the full rationale
            _did = n.get("id", "")
            choice_full = _gloss(_e(n.get("choice", "")), gmap)
            choice_fold = (f"<details class='draw'><summary>full decision text</summary>"
                           f"<div class='dchfull'{_eattr('decision', 'choice', n.get('choice',''), id=_did)}>"
                           f"{choice_full}</div></details>") if choice_full else ""
            # a decision that carries examples or anchored code opens by default so it's visible on load
            dec_open = " open" if (ex or anc) else ""
            spine.append(f"<details class='dec'{dec_open}><summary>"
                         f"<span class='gl' style='color:{_c}'"
                         f"{_eattr('decision', 'status', st, id=_did, type='select', opts='open,decided,verified', render='glyph')}>{_g}</span>"
                         f"<span class='dsum'><span class='dq'"
                         f"{_eattr('decision', 'decision', n.get('decision',''), id=_did)}>"
                         f"{_gloss(_e(n.get('decision','')), gmap)}</span>{new_tag}{you}{pl_tag}{anch_tag}"
                         f"<br><span class='dch'>{plain}</span></span></summary>"
                         f"<div class='dwhy'><span class='pr'>{_e(n.get('principle',''))}</span> "
                         f"<span class='dwhyt'{_eattr('decision', 'why', n.get('why',''), id=_did)}>"
                         f"{_ec(n.get('why',''))}</span></div>"
                         f"{ex_html}"
                         f"{anc_html}"
                         f"{choice_fold}"
                         f"<div class='tl-quiz' data-dec=\"{_e(n.get('id',''))}\"></div>"
                         f"<div class='tl-chat' data-dec=\"{_e(n.get('id',''))}\"></div></details>")
        if _collapse:
            spine.append("</details>")
    spine.append("</div></div>")
    dec_sec = [legend]
    if planning:
        dec_sec.append("".join(spine))
    else:
        dec_sec.append("<div class='cols'>")
        dec_sec.append("".join(spine))
        dec_sec.append("<div><h2 class='sec'>Search tree — the directions explored</h2>"
                       "<div class='card' style='padding:12px 8px;overflow-x:auto'>")
        dec_sec.append(tree_svg(nodes, knowledge, kinds, id2phase, phase_order))
        dec_sec.append("</div></div>")
        dec_sec.append("</div>")  # cols

    # ---- nav bar + sections: a tab only exists when its section has content; the glossary
    # (a one-line collapsed box) and the foot stay outside the tabs, always visible. ----
    secs = [(i, l, h) for i, l, h in (
        ("sec-now", "🎢 Now", "".join(p for p in now_sec if p)),
        ("sec-flow", "🔀 Data &amp; pipeline", "".join(p for p in flow_sec if p)),
        ("sec-timeline", "📅 Timeline", "".join(tl_sec)),
        ("sec-decisions", "🧭 Decisions", "".join(dec_sec)),
    ) if h.strip()]
    if len(secs) > 1:
        H.append("<div class='rnav'>" + "".join(
            f"<button type='button' data-sec='{i}'>{l}</button>" for i, l, _ in secs)
            + "<button type='button' data-sec='all'>⊞ All</button></div>")
    # the first section ships pre-opened server-side: if the viewer strips <script> (some inline
    # renderers do), the reader still gets the header + lead section instead of a blank body
    for k, (i, _l, h) in enumerate(secs):
        H.append(f"<section class='rsec{' on' if k == 0 else ''}' id='{i}'>{h}</section>")

    H.append(feedback_section_html(name))
    H.append(glossary_html(glossary))

    H.append(f"<div class='foot'>Trainlint · derived from research/plan.{_e(name)}.jsonl + "
             f"log.{_e(name)}.jsonl + knowledge.{_e(name)}.jsonl — never hand-maintained.<br>"
             "ask a decision's chatbot, then <b>Export memory</b> → "
             f"<code>viz.py {_e(name)} --absorb &lt;file&gt;</code> to fold it into the glossary + FAQ.</div>")
    H.append("</div>")  # wrap
    H.append('<script id="tl-data" type="application/json">'
             + _chat_blob(name, goal, pl, glossary, clarify) + "</script>")
    H.append("<script>" + CHAT_JS + "</script>")
    H.append("<script>" + QUIZ_JS + "</script>")
    H.append("<script>" + NAV_JS + "</script>")
    H.append("<script>" + ANNOT_JS + "</script>")  # indexes the DOM the widgets built
    H.append("<script>" + EDIT_JS + "</script>")   # last: arms the owner-only inline editor
    H.append("</body></html>")
    return "\n".join(H)


# --- ascii summary (stdout / hook) ----------------------------------------------------

# A self-contained slide layer — no reveal.js, no CDN, fully offline. Each .slide fills the
# viewport; only .active shows; ~30 lines of inline JS (DECK_JS) handle arrow-key paging. The
# .beat / .dec / .chip / .card / .phase styles all come from the report CSS above — reused
# verbatim so a slide looks identical to the same block in the HTML report. @media print stacks
# every slide one-per-page so the browser's Print → Save-as-PDF gives a real PDF deck.
SLIDES_CSS = """
html,body{margin:0;height:100%;background:#0b1220;color:#cbd5e1;overflow:hidden}
.deck{height:100vh;width:100vw;position:relative}
.slide{position:absolute;inset:0;display:none;flex-direction:column;
  padding:34px 48px;box-sizing:border-box;overflow:hidden;font-size:18px}
.slide.active{display:flex}
.slide.cover{justify-content:center;align-items:center;text-align:center}
/* LLM-authored slides: a title + a few big bullets, vertically centred, generous spacing. */
.slide .sbul{list-style:none;margin:26px 0 0;padding:0;max-width:23em;align-self:center}
.slide .sbul li{position:relative;padding:0 0 0 30px;margin:0 0 20px;font-size:27px;line-height:1.42;color:#e2e8f0}
.slide .sbul li:before{content:"▸";position:absolute;left:0;color:#38bdf8;font-size:24px}
.slide .snote{margin-top:auto;padding-top:14px;font-size:14px;color:#64748b;font-style:italic;border-top:1px solid #1e293b}
.slide h2.sec+.sbul{margin-top:34px}
/* deck-theme-scope: the report's base .card{#fff}/.tl/.gl-* are reused verbatim and would
   render as white boxes on the dark deck. Re-skin them DARK *only* under .slide, so the report
   (render_html) keeps its light theme byte-for-byte. */
.slide .card{background:#0f172a;border-color:#1e293b;color:#cbd5e1}
.slide .phase{color:#7dd3fc}
.slide .tl .row{border-bottom-color:#1e293b}
.slide .tl .date,.slide .tl .knd,.slide .pp-s,.slide .dch{color:#94a3b8}
.slide .tl .note,.slide .dq{color:#e2e8f0}
.slide .pp-stage{background:#0f172a;border-color:#1e293b}
.slide .pp-t{color:#e2e8f0}
.slide .dwhy,.slide .gl-row{color:#cbd5e1;border-top-color:#1e293b}
.slide .pr{background:#1e293b;color:#cbd5e1}
.slide .gl-box{background:#0f172a;border-color:#1e293b}.slide .gl-box summary{color:#e2e8f0}
.slide .dec-flat{border-bottom-color:#1e293b}
.slide h1{font-size:42px;color:#e2e8f0;margin:0 0 6px}
.slide h2.sec{font-size:24px;color:#e2e8f0;margin:0 0 16px}
.cover-goal{font-size:27px;color:#e2e8f0;margin:18px 0;max-width:84%;line-height:1.45}
.cover-done{color:#fbbf24;margin:6px 0 14px}
.cover .score{justify-content:center}
.slide .story{margin:0}
.slide .beat .bt{font-size:25px;line-height:1.45}.slide .beat .bl{font-size:15px}
.slide .beat .blist li{font-size:18px}.slide .beat .tail{font-size:18px}
.dec-flat{padding:11px 0;border-bottom:1px solid #1e293b;display:flex;gap:9px}
.dec-flat:last-child{border-bottom:none}
.deck-nav{position:fixed;bottom:13px;right:18px;font-size:12px;color:#64748b;z-index:20;
  font-family:system-ui,sans-serif}
@media print{html,body{overflow:visible;height:auto}.deck{height:auto}
  .slide{display:flex!important;position:relative;inset:auto;height:100vh;
    page-break-after:always}.deck-nav{display:none}}
"""

# Minimal offline slide navigator: ←/→ (or space / PgUp-PgDn) to page, Home/End to jump, click
# anywhere to advance, deep-link via #N. No dependency — inlined into every deck.
DECK_JS = """
(function(){var s=[].slice.call(document.querySelectorAll('.slide')),i=0,
c=document.getElementById('deck-cnt');
// slide-fit-strategy: build-time (Python) can't know rendered height, so GUARANTEE fit at
// runtime — shrink the slide's font until it no longer overflows its own box (or hits a floor).
// Measured per slide, memoized; this is what makes "no slide scrolls" a guarantee, not a guess.
function fit(el){if(el.dataset.fit)el.style.fontSize=el.dataset.fit;else el.style.fontSize='';
  var fs=parseFloat(getComputedStyle(el).fontSize),min=fs*0.55,g=0;
  while(el.scrollHeight>el.clientHeight+1&&fs>min&&g++<60){fs-=1;el.style.fontSize=fs+'px';}
  el.dataset.fit=fs+'px';}
function show(n){i=Math.max(0,Math.min(s.length-1,n));
  s.forEach(function(el,k){el.classList.toggle('active',k===i);});
  fit(s[i]);
  if(c)c.textContent=(i+1)+' / '+s.length;
  if(('#'+(i+1))!==location.hash)history.replaceState(null,'','#'+(i+1));}
function go(d){show(i+d);}
document.addEventListener('keydown',function(e){
  if(e.key==='ArrowRight'||e.key==='PageDown'||e.key===' ')go(1);
  else if(e.key==='ArrowLeft'||e.key==='PageUp')go(-1);
  else if(e.key==='Home')show(0);else if(e.key==='End')show(s.length-1);});
document.addEventListener('click',function(e){if(!e.target.closest('a'))go(1);});
var h=parseInt((location.hash||'').slice(1),10);show(isNaN(h)?0:h-1);})();
"""


def render_slides(name, goal, bar, pl, nodes, knowledge, kinds, id2phase, phase_order,
                  glossary=None, clarify=None, motivation="", tldr=""):
    """A slide deck for the SAME project as render_html — one self-contained, OFFLINE
    .slides.html (no CDN, no reveal.js; the paging engine is the inlined DECK_JS). Reuses every
    data builder the HTML report uses (story_beats / spine_groups / timeline_rows / tree_svg /
    glossary) so the deck never drifts from the report. Present in a browser (←/→ to page);
    Print → Save-as-PDF gives a real PDF deck (one slide per page)."""
    glossary = glossary or []
    gmap = _gloss_map(glossary)
    summ = plan.summary(pl)
    counts = summ["counts"]
    mt = plan.main_thread(pl)
    pillars = plan.pillars(pl)
    rows = timeline_rows(tree.load_events(name, tree.load_facts(name)), knowledge)
    planning = bool(pl) and not rows and not nodes

    def _sec(inner, extra=""):
        return f"<section class='slide{(' ' + extra) if extra else ''}'>{inner}</section>"

    secs = []
    # 1 - cover: name - goal headline - done-bar - pillar chips - score
    head, _bul, done = _want_parts(goal, bar, pl)
    chips = "".join(f"<span class='chip pillar'>◆ {_e(p['id'])}</span>" for p in pillars)
    cover = (f"<h1>{_e(name)}</h1><div class='cover-goal'>{_e(head)}</div>"
             + (f"<div class='cover-done'><b>done</b> = {_e(done)}</div>" if done else "")
             + (f"<div class='chips'>{chips}</div>" if chips else "")
             + "<div class='score'><div class='dots'>" + _dots(counts) + "</div>"
             f"<div class='lbl'>{counts.get('verified',0)} verified · {counts.get('decided',0)} "
             f"decided · {counts.get('open',0)} open  ({summ['total']} decisions)</div></div>")
    secs.append(_sec(cover, "cover"))

    # 2 - narrative middle. DEFAULT: an LLM AUTHORS a distilled, one-idea-per-slide deck from the
    # substrate — the template used to DUMP 10k-char timeline / decision slides (unreadable). The
    # template arc (beats + timeline + spine) is the fallback when no LLM is set or it fails.
    _prov = os.environ.get("TRAINLINT_SLIDES_LLM",
                           os.environ.get("TRAINLINT_REPORT_LLM", "codex")).strip().lower()
    deck = llm_slides(name, _prov) if _prov else []
    if deck:
        for sl in deck:
            secs.append(_sec(_llm_slide_html(sl)))
    else:
        beats = (planning_story_beats(motivation, goal, bar, pl) if planning
                 else story_beats(goal, bar, pl, nodes, rows))
        for b in beats:
            secs.append(_sec(_render_beats([b])))
        if not planning and rows:
            tl = ["<h2 class='sec'>Timeline — how the search got here</h2><div class='card tl'>"]
            for r in rows:
                g, col, lbl = KIND.get(r["kind"], ("•", "#64748b", r["kind"]))
                tl.append(f"<div class='row'><div class='date'>{_e(r['ts'])}</div>"
                          f"<div class='mk' style='color:{col}'>{g}</div>"
                          f"<div class='body'><span class='dir'>{_e(r['direction'])}</span> "
                          f"<span class='knd'>{_e(lbl)}</span>"
                          f"<div class='note'>{_e(r['note'])}</div></div></div>")
            tl.append("</div>")
            secs.append(_sec("".join(tl)))
        for ph, decs in spine_groups(pl):
            s = [f"<h2 class='sec'>Decisions — {_e(ph)}</h2><div class='card'>"]
            for n in decs:
                you = ("<span class='you'>← you are here</span>"
                       if (mt and n.get("id") == mt.get("id")) else "")
                pl_tag = "<span class='pill-tag'>◆ pillar</span>" if n.get("pillar") else ""
                _g, _c = _dec_glyph(n)
                s.append("<div class='dec-flat'>"
                         f"<span class='gl' style='color:{_c}'>{_g}</span>"
                         f"<span class='dsum'><span class='dq'>{_gloss(_e(n.get('decision','')), gmap)}"
                         f"</span>{you}{pl_tag}"
                         f"<br><span class='dch'>→ {_gloss(_e(n.get('choice','')), gmap)}</span>"
                         f" <span class='pr'>{_e(n.get('principle',''))}</span></span></div>")
            s.append("</div>")
            secs.append(_sec("".join(s)))

    # 3 - the data-flow visual (compact; useful in BOTH modes as an appendix)
    if not planning:
        secs.append(_sec("<h2 class='sec'>The data flow</h2>" + pipeline_html(name)))

    # 6 - search tree: SVG inlined, renders natively in the deck (no rasterization)
    if not planning and nodes:
        secs.append(_sec("<h2 class='sec'>Search tree — the directions explored</h2>"
                         "<div class='card' style='padding:12px 8px;overflow:auto'>"
                         + tree_svg(nodes, knowledge, kinds, id2phase, phase_order)
                         + "</div>", "tree-slide"))

    # 7 - glossary appendix
    if glossary:
        secs.append(_sec("<h2 class='sec'>Glossary</h2>" + glossary_html(glossary)))

    return "\n".join([
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        f"<title>{_e(name)} — slides</title>",
        f"<style>{CSS}{SLIDES_CSS}</style></head><body>",
        "<div class='deck'>",
        "\n".join(secs),
        "</div>",
        "<div class='deck-nav'><span id='deck-cnt'></span> · ←/→ page · Print→PDF</div>",
        f"<script>{DECK_JS}</script>",
        "</body></html>"])


def stdout_summary(name, goal, bar, pl, nodes, knowledge, htmlpath):
    summ = plan.summary(pl)
    c = summ["counts"]
    mt = plan.main_thread(pl)
    rows = timeline_rows(tree.load_events(name, tree.load_facts(name)), knowledge)
    out = [f"# research tree ({name})  ·  {summ['total']} decisions "
           f"[{summ.get('decided_built',0)}/{c.get('decided',0)} decided built · "
           f"{c.get('verified',0)} verified · {c.get('open',0)} open]"]
    if goal:
        out.append(f"  goal : {_trunc(goal, 92)}")
    try:
        import goalcheck as _gc  # noqa: E402
        _gd = _gc.brief(name)
    except Exception:
        _gd = ""
    if _gd:
        out.append("  " + _trunc(_gd, 160))
    if mt:
        out.append(f"  NOW  : {_trunc(mt.get('decision',''), 92)}  (main thread → {mt.get('id','')})")
    if rows:
        out.append("\n  timeline (latest):")
        for r in rows[-5:]:
            g = KIND.get(r["kind"], ("•",))[0]
            d = f"  {('+' if (r['delta'] or 0) > 0 else '')}{r['delta']}" if r["delta"] is not None else ""
            out.append(f"    {r['ts']}  {g} {r['direction']:<18}{d}  {_trunc(r['note'], 48)}")
    ready = []
    for n in nodes.values():
        for w in n.get("walls", []):
            p = wall_paper(w, knowledge)
            if p and p["title"] not in ready:
                ready.append(p["title"])
    if ready:
        out.append("\n  ready to read (wall → paper): " + " · ".join(_trunc(t, 38) for t in ready))
    out.append(f"\nHTML: {htmlpath}")
    return "\n".join(out)


# --- anchors: bake the REVIEWABLE code behind each decision into the report -----------------------
# A decision's `anchors` (recorded by research/anchor.py) pin file:lines@commit. The report is a
# static blob viewed OFF-machine (phone, R2) — nothing can be fetched at view time — so the anchored
# snippet is resolved HERE, at render time, where git and the repo exist, and baked into the HTML.
# Pinned truth first: `git show <commit>:<file>` is what the reviewer reviews; the working tree is
# only consulted to DETECT drift (and as fallback when the pin is unreachable), never silently
# substituted for the pin.

_ANCH_MAX_LINES = 120          # per-snippet line cap
_ANCH_MAX_BYTES = 8_000        # per-snippet byte cap
_ANCH_TOTAL_BYTES = 1_500_000  # whole-report code budget (worker upload caps the blob at ~5MB)


def _git_out(repo, *args, timeout=10):
    """(rc, stdout) of git -C <repo> <args>; never raises (anchors must fail-open to captions)."""
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout
    except Exception:
        return 1, ""


def _slice(text, lines):
    """The FULL [start,end] 1-based line slice of text (whole text when lines is falsy) — never
    truncated here: drift detection must compare complete ranges (a change past a display cap is
    still drift). Display truncation happens in _numbered only."""
    rows = text.splitlines()
    lo, hi = (lines[0], lines[1]) if lines else (1, len(rows))
    lo = max(1, lo)
    picked = rows[lo - 1:hi]
    clipped = len(picked) > _ANCH_MAX_LINES  # will the DISPLAY be cut? (caption hint)
    return picked, lo, clipped


def _numbered(rows, start):
    """Line-numbered display text, capped at _ANCH_MAX_LINES lines / _ANCH_MAX_BYTES bytes —
    the caps are display-only; callers compare the uncapped rows for drift."""
    out, size = [], 0
    for i, ln in enumerate(rows):
        if i >= _ANCH_MAX_LINES:
            out.append(f"    … │ ({len(rows) - i} more lines — line cap; use the command below)")
            break
        s = f"{start + i:>5} │ {ln}"
        size += len(s) + 1
        if size > _ANCH_MAX_BYTES:
            out.append("    … │ (snippet byte cap reached — use the command below)")
            break
        out.append(s)
    return "\n".join(out)


def _repo_web(root, cache):
    """The repo's browseable web URL (from remote.origin.url), '' if none. Turns
    git@host:owner/repo.git and https://host/owner/repo.git into https://host/owner/repo —
    enough for GitHub-style /blob/<sha>/<path>#Lx-Ly permalinks. Cached per root."""
    key = "web::" + root
    if key not in cache:
        rc, url = _git_out(root, "config", "--get", "remote.origin.url")
        url, web = url.strip(), ""
        if rc == 0 and url:
            m = re.match(r"^[\w.-]+@([\w.-]+):(.+?)(?:\.git)?/?$", url)
            if m:
                web = f"https://{m.group(1)}/{m.group(2)}"
            elif url.startswith(("http://", "https://")):
                web = re.sub(r"\.git/?$", "", url)
        cache[key] = web
    return cache[key]


def _resolve_one_anchor(s, home, roots):
    """One anchor spec -> {loc, kind, cap, code, diff, cmd, href}. kind: pinned | drifted |
    fileonly | unreachable | commit | missing. href = commit-pinned web permalink (GitHub-style,
    line-highlighted) when the repo has a remote — the CLICK-THROUGH into the code; '' otherwise.
    Everything degrades to a caption; nothing raises."""
    note = s.get("note", "")
    base = s.get("repo") or home or os.getcwd()
    if base not in roots:
        rc, top = _git_out(base, "rev-parse", "--show-toplevel")
        roots[base] = top.strip() if rc == 0 and top.strip() else ""
    root = roots[base] or base

    # a whole COMMIT as the evidence -> subject + --stat listing
    if s.get("commit") and not s.get("file"):
        sha = s["commit"]
        rc, head = _git_out(root, "show", "--no-patch", "--format=%h %s  (%an · %ad)",
                            "--date=short", sha)
        if rc != 0:
            return {"loc": f"commit {sha}", "kind": "missing", "cap": "commit not found in "
                    + root, "code": "", "diff": "", "cmd": "", "href": "", "note": note}
        _, stat = _git_out(root, "show", "--stat", "--format=", sha)
        web = _repo_web(root, roots)
        return {"loc": f"commit {sha}", "kind": "commit", "cap": head.strip(),
                "code": stat.strip()[:_ANCH_MAX_BYTES], "diff": "",
                "cmd": f"git -C {root} show {sha}",
                "href": f"{web}/commit/{sha}" if web else "", "note": note}

    f, lines, sha = s.get("file", ""), s.get("lines"), s.get("commit", "")
    loc = f + (f":{lines[0]}-{lines[1]}" if lines else "") + (f"@{sha}" if sha else "")
    fs_path = Path(f) if os.path.isabs(f) else Path(root) / f
    working = ""
    if fs_path.is_file():
        try:
            working = fs_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            working = ""

    if not sha:  # file-only anchor (non-git dir, or not committed when recorded)
        if not working:
            return {"loc": loc, "kind": "missing", "cap": f"file not found: {fs_path}",
                    "code": "", "diff": "", "cmd": "", "href": "", "note": note}
        rows, lo, clipped = _slice(working, lines)
        cap = "file-only (no commit pin) — showing the current working copy" \
              + (" · clipped" if clipped else "")
        return {"loc": loc, "kind": "fileonly", "cap": cap, "code": _numbered(rows, lo),
                "diff": "", "cmd": f"sed -n '{lines[0]},{lines[1]}p' {fs_path}" if lines else str(fs_path),
                "href": "", "note": note}

    rel = f if not os.path.isabs(f) else os.path.relpath(f, root)
    # the CLICK-THROUGH: a commit-pinned, line-highlighted permalink into the repo's web UI —
    # exactly the reviewed lines, immune to later pushes (sha-addressed, not branch-addressed).
    web = _repo_web(root, roots)
    frag = f"#L{lines[0]}-L{lines[1]}" if lines else ""
    href = f"{web}/blob/{sha}/{rel}{frag}" if web else ""
    rc, pinned = _git_out(root, "show", f"{sha}:{rel}")
    if rc != 0:  # pin unreachable (history rewritten, wrong repo…) -> working copy, loudly captioned
        if not working:
            return {"loc": loc, "kind": "missing",
                    "cap": f"anchor commit {sha} unreachable AND no working copy at {fs_path}",
                    "code": "", "diff": "", "cmd": "", "href": "", "note": note}
        rows, lo, clipped = _slice(working, lines)
        return {"loc": loc, "kind": "unreachable",
                "cap": f"⚠ anchor commit {sha} unreachable — showing CURRENT working copy instead"
                       + (" · clipped" if clipped else ""),
                "code": _numbered(rows, lo), "diff": "",
                "cmd": f"git -C {root} show {sha}:{rel}", "href": href, "note": note}

    rows, lo, clipped = _slice(pinned, lines)
    code = _numbered(rows, lo)
    cmd = f"git -C {root} show {sha}:{rel}" + (f" | sed -n '{lines[0]},{lines[1]}p'" if lines else "")
    wrows, _, _ = _slice(working, lines) if working else ([], lo, False)
    if working and wrows != rows:  # the SAME range moved on -> pinned stays canon, diff shows drift
        dif = "\n".join(difflib.unified_diff(rows, wrows, f"{rel}@{sha} (reviewed)",
                                             f"{rel} (working tree now)", lineterm=""))[:_ANCH_MAX_BYTES]
        return {"loc": loc, "kind": "drifted",
                "cap": f"pinned at {sha}" + (" · clipped" if clipped else "")
                       + " · ⚠ these lines have CHANGED since — pinned version shown, diff below",
                "code": code, "diff": dif, "cmd": cmd, "href": href, "note": note}
    cap = f"pinned at {sha} · unchanged in working tree" if working else \
          f"pinned at {sha} · (file gone from working tree)"
    return {"loc": loc, "kind": "pinned", "cap": cap + (" · clipped" if clipped else ""),
            "code": code, "diff": "", "cmd": cmd, "href": href, "note": note}


def _resolve_anchors(pl, name):
    """Resolve every decision's anchors ONCE (report + slides share the pass) onto n['_anchors'],
    respecting the global byte budget. Returns rollup counts for the header line."""
    home = paths.project_home(name) or os.getcwd()
    roots, spent = {}, 0
    stats = {"pinned": 0, "drifted": 0, "fileonly": 0, "unreachable": 0, "missing": 0,
             "commit": 0, "unanchored_built": 0}
    for n in pl:
        specs = plan._anchor_specs(n)
        resolved = []
        for s in specs:
            if s.get("paper"):
                resolved.append({"paper": True})
                continue
            r = _resolve_one_anchor(s, home, roots)
            if spent + len(r["code"]) > _ANCH_TOTAL_BYTES:
                r["code"], r["diff"] = "", ""
                r["cap"] += " · snippet omitted (report code budget reached) — use the command below"
            spent += len(r["code"]) + len(r["diff"])
            stats[r["kind"]] = stats.get(r["kind"], 0) + 1
            resolved.append(r)
        n["_anchors"] = resolved
        if (not plan.has_anchor(n)
                and n.get("status") in ("decided", "verified")
                and plan.artifact_exists(n, home)):  # home, not cwd: digest re-render safe
            stats["unanchored_built"] += 1
    return stats


def _load_project(name):
    """Everything the renderers need for one project — all derived, no new files."""
    facts = tree.load_facts(name)
    nodes = tree.build_tree(tree.load_events(name, facts), facts)
    pl = plan.load(name)
    _resolve_anchors(pl, name)  # bakes n['_anchors'] onto each decision (report + slides share it)
    know = tree._load_jsonl(paths.resolve(f"knowledge.{name}.jsonl"))
    glossary = tree._load_jsonl(paths.resolve(f"glossary.{name}.jsonl"))
    clarify = tree._load_jsonl(paths.resolve(f"clarify.{name}.jsonl"))
    gp = paths.resolve(f"goal.{name}.txt")
    goal, bar = split_goal(gp.read_text(encoding="utf-8") if gp.exists() else "")
    mp = paths.resolve(f"motivation.{name}.txt")
    motivation = " ".join(mp.read_text(encoding="utf-8").split()) if mp.exists() else ""
    tp = paths.resolve(f"tldr.{name}.txt")
    tldr = tp.read_text(encoding="utf-8").strip() if tp.exists() else ""  # keep line breaks -> bullets
    pp = paths.resolve(f"purpose.{name}.txt")
    purpose = pp.read_text(encoding="utf-8").strip() if pp.exists() else ""
    surprises = tree._load_jsonl(paths.resolve(f"surprises.{name}.jsonl"))
    kinds = {}
    for e in tree.load_events(name, facts):
        d = e.get("direction")
        if d and d != "?":
            kinds.setdefault(d, []).append(e.get("kind", "experiment"))
    id2phase = {n.get("id"): n.get("phase", "") for n in pl}
    phase_order = []
    for n in pl:
        if n.get("phase", "") not in phase_order:
            phase_order.append(n.get("phase", ""))
    return {"name": name, "facts": facts, "nodes": nodes, "pl": pl, "know": know,
            "goal": goal, "bar": bar, "kinds": kinds, "id2phase": id2phase,
            "phase_order": phase_order, "glossary": glossary, "clarify": clarify,
            "motivation": motivation, "tldr": tldr, "purpose": purpose, "surprises": surprises}


# --- optional LLM-written narrative (same HTML shell; just a swappable LLM entry) ----------------
# The report's prose is normally templated. Set TRAINLINT_REPORT_LLM=kimi|claude|gemini to have an LLM
# WRITE the opening narrative from the same substrate; it renders into the SAME report shell.
REPORT_SYS = (
    "Write a terse status report for the operator who owns this project. Open with ONE plain sentence "
    "(what this is + why, from `purpose`). Then LEAD with the SURPRISES — where reality broke from "
    "intuition (from `surprises`): looked-hard-was-trivial, looked-trivial-was-the-wall, assumed-vs-"
    "real bottleneck; make them the spine. Then what we DID/built (from `log`, newest first) and NEXT "
    "(from `focus`). <=3 sentences per point, plain language, no ceremony; mechanical passing is the "
    "floor, never celebrated. Ground every claim in the data; invent nothing. Output short Markdown.")


def _llm(provider, sysp, userp):
    """One swappable LLM entry: 'kimi' (CLI, subscription), 'gemini' (GEMINI_API_KEY), else 'claude'
    (Claude Code subscription OAuth via modeljudge). Returns the model's text."""
    if provider == "kimi":
        import subprocess
        r = subprocess.run(["kimi", "--print", "-y", "--output-format", "stream-json",
                            "-p", sysp + "\n\n" + userp], capture_output=True, text=True, timeout=240)
        texts = []
        for line in r.stdout.splitlines():
            try:
                d = json.loads(line)
            except Exception:
                continue

            def walk(o):
                if isinstance(o, dict):
                    if o.get("type") == "text" and isinstance(o.get("text"), str):
                        texts.append(o["text"])
                    for v in o.values():
                        walk(v)
                elif isinstance(o, list):
                    for v in o:
                        walk(v)
            walk(d)
        return max(texts, key=len) if texts else ""
    if provider == "codex":
        import subprocess
        import tempfile
        outf = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False).name
        subprocess.run(["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                        "-o", outf, sysp + "\n\n" + userp],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        try:
            return Path(outf).read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    if provider == "gemini":
        import os
        import urllib.request
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            return ""
        model = os.environ.get("TRAINLINT_GEMINI_MODEL", "gemini-2.5-flash")
        body = json.dumps({"system_instruction": {"parts": [{"text": sysp}]},
                           "contents": [{"role": "user", "parts": [{"text": userp}]}],
                           "generationConfig": {"maxOutputTokens": 2048}}).encode()
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            j = json.loads(resp.read())
        return "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(ROOT.parent / "hooks"))
    c = importlib.import_module("modeljudge")._client()
    if c is None:
        return ""
    r = c.messages.create(model="claude-sonnet-4-6", max_tokens=2048, system=sysp,
                          messages=[{"role": "user", "content": userp}])
    return "".join(getattr(b, "text", "") for b in r.content).strip()


def _md2html(md):
    """Minimal markdown -> HTML for the LLM narrative (## headers, - bullets, **bold**, paragraphs)."""
    md = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _e(md).replace("&lt;b&gt;", "").replace("&lt;/b&gt;", ""))
    out, ul = [], False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("- ") or s.startswith("* "):
            if not ul:
                out.append("<ul>"); ul = True
            out.append(f"<li>{s[2:]}</li>")
            continue
        if ul:
            out.append("</ul>"); ul = False
        if s.startswith("### ") or s.startswith("## ") or s.startswith("# "):
            out.append(f"<h4>{s.lstrip('# ')}</h4>")
        elif s:
            out.append(f"<p>{s}</p>")
    if ul:
        out.append("</ul>")
    return "".join(out)


def llm_narrative(name, provider):
    """Gather the substrate and let the chosen LLM write the report prose (or '' on failure)."""
    def read(fn):
        p = paths.resolve(fn)
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    def jl(fn):
        p = paths.resolve(fn)
        out = []
        if p.exists():
            for x in p.read_text(encoding="utf-8").splitlines():
                x = x.strip()
                if not x or x.startswith("#"):
                    continue
                try:
                    out.append(json.loads(x))
                except Exception:
                    pass
        return out
    pl = plan.load(name)
    data = {"purpose": read(f"purpose.{name}.txt"), "goal": read(f"goal.{name}.txt"),
            "decisions": [{"id": d.get("id"), "status": d.get("status"),
                           "plain": d.get("plain") or d.get("decision"), "built": bool(d.get("artifact"))}
                          for d in pl],
            "log": jl(f"log.{name}.jsonl"), "surprises": jl(f"surprises.{name}.jsonl"),
            "focus": jl(f"focus.{name}.jsonl")}
    try:
        return _llm(provider, REPORT_SYS, "Project data (JSON):\n" + json.dumps(data, ensure_ascii=False))
    except Exception:
        return ""


SLIDES_SYS = (
    "You are a staff-level presenter turning a research project's substrate into a CRISP, talk-ready "
    "slide deck. Output ONLY a JSON array of slides — no prose, no code fence. Each slide is "
    '{"title": str, "bullets": [str, ...], "note": str}. RULES: '
    "(1) ONE idea per slide. 2-5 bullets, each <= 14 words, punchy, plain language — never a wall of text. "
    "(2) GROUND every claim in the provided data; NEVER invent facts, numbers, names, decisions, or events "
    "not present. If something is unknown, leave it out. "
    "(3) DISTILL, don't dump: merge related decisions, keep only the 3-5 that matter, translate jargon, "
    "drop minor ones. This is a talk, not the document. "
    "(4) Follow this arc, ~7-10 slides total: the PROBLEM (why this project exists / what's hard), the "
    "APPROACH (how we attack it), the KEY DECISIONS distilled (what we chose and the one-line why), what "
    "SURPRISED us (where reality broke the plan), WHERE IT STANDS now, and the SINGLE next thing. "
    "(5) 'note' = one sentence of speaker context for that slide (optional, may be empty). "
    "Return the JSON array and nothing else.")


def _parse_deck(raw):
    """Extract the JSON slide array from an LLM reply (tolerates ```json fences / stray prose)."""
    if not raw:
        return []
    t = raw.strip()
    if "```" in t:  # strip the first fenced block's fence markers
        m = re.search(r"```(?:json)?\s*(.+?)```", t, re.S)
        if m:
            t = m.group(1).strip()
    i, j = t.find("["), t.rfind("]")
    if i < 0 or j <= i:
        return []
    try:
        deck = json.loads(t[i:j + 1])
    except Exception:
        return []
    out = []
    for s in deck if isinstance(deck, list) else []:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).strip()
        bullets = [str(b).strip() for b in (s.get("bullets") or []) if str(b).strip()]
        if title or bullets:
            out.append({"title": title, "bullets": bullets[:6], "note": str(s.get("note", "")).strip()})
    return out


def llm_slides(name, provider):
    """Let the chosen LLM AUTHOR the narrative deck (distilled, one-idea-per-slide) from the same
    substrate the report reads — replacing the old template dump. Returns [] on any failure so the
    caller falls back to the template deck (never breaks the render)."""
    def read(fn):
        p = paths.resolve(fn)
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""

    def jl(fn):
        p = paths.resolve(fn)
        out = []
        if p.exists():
            for x in p.read_text(encoding="utf-8").splitlines():
                x = x.strip()
                if x and not x.startswith("#"):
                    try:
                        out.append(json.loads(x))
                    except Exception:
                        pass
        return out
    try:
        pl = plan.load(name)
        mt = plan.main_thread(pl)
        data = {
            "project": name,
            "goal": read(f"goal.{name}.txt"), "purpose": read(f"purpose.{name}.txt"),
            "main_thread": (mt.get("plain") or mt.get("decision")) if mt else "",
            "pillars": [p.get("id") for p in plan.pillars(pl)],
            "decisions": [{"id": d.get("id"), "status": d.get("status"),
                           "plain": d.get("plain") or d.get("decision"), "choice": d.get("choice"),
                           "why": d.get("why"), "pillar": bool(d.get("pillar"))} for d in pl],
            "surprises": jl(f"surprises.{name}.jsonl"),
            "focus": jl(f"focus.{name}.jsonl"),
            "recent_log": jl(f"log.{name}.jsonl")[-12:],
        }
        return _parse_deck(_llm(provider, SLIDES_SYS,
                                "Project data (JSON):\n" + json.dumps(data, ensure_ascii=False)))
    except Exception:
        return []


def _llm_slide_html(sl):
    """One LLM-authored slide dict -> the inner HTML for a <section class='slide'>."""
    title = _e(sl.get("title", "")) or "&nbsp;"
    bullets = "".join(f"<li>{_e(b)}</li>" for b in sl.get("bullets", []))
    inner = f"<h2 class='sec'>{title}</h2>" + (f"<ul class='sbul'>{bullets}</ul>" if bullets else "")
    if sl.get("note"):  # speaker note: hidden on the face, visible in Print/PDF via SLIDES_CSS
        inner += f"<div class='snote'>{_e(sl['note'])}</div>"
    return inner


def generate(name):
    """Write the TWO views of one project from a single load — the interactive report
    research/viz/<name>.html and the offline slide deck <name>.slides.html — and return
    (htmlpath, slidespath, project-dict). One _load_project() pass, so neither can drift.
    The report HTML is itself the phone deliverable: the close SendUserFile's it with
    display:'render' and the Claude mobile app renders it inline (no PNG card needed)."""
    d = _load_project(name)
    # STABLE render target (stable-render-dir): all sessions/versions render into ONE dir under
    # data_root (survives plugin version bumps), not the versioned ROOT/viz — so a single durable
    # server serves every project and a bump can't strand it (the 8420->stale-dir bug). Same reason
    # per-project data already lives in data_root (paths.py).
    outdir = paths.data_root() / "viz"
    outdir.mkdir(parents=True, exist_ok=True)
    try:  # silently keep a loopback server up so the JS-heavy report (chatbots) is browsable
        import serve
        serve.ensure(outdir)
    except Exception:
        pass
    import os
    _prov = os.environ.get("TRAINLINT_REPORT_LLM", "codex").strip().lower()  # default: codex writes the prose
    if _prov in ("none", "off", "0", "false", "template"):  # any opt-out spelling -> templated prose
        _prov = ""
    narrative = llm_narrative(name, _prov) if _prov else ""
    htmlpath = outdir / f"{name}.html"
    htmlpath.write_text(render_html(d["name"], d["goal"], d["bar"], d["pl"], d["nodes"],
                                    d["know"], d["kinds"], d["id2phase"], d["phase_order"],
                                    glossary=d["glossary"], clarify=d["clarify"],
                                    motivation=d["motivation"], tldr=d["tldr"],
                                    surprises=d["surprises"], purpose=d["purpose"],
                                    narrative=narrative),
                        encoding="utf-8")
    slidespath = outdir / f"{name}.slides.html"
    slidespath.write_text(render_slides(d["name"], d["goal"], d["bar"], d["pl"], d["nodes"],
                                        d["know"], d["kinds"], d["id2phase"], d["phase_order"],
                                        glossary=d["glossary"], clarify=d["clarify"],
                                        motivation=d["motivation"], tldr=d["tldr"]),
                          encoding="utf-8")
    try:
        import push
        push.push_report(name, htmlpath, slidespath)
    except Exception:
        pass
    return htmlpath, slidespath, d


def absorb(name, blob_path):
    """Fold an exported `viz-memory.<name>.json` (what the in-browser chatbots captured) back
    into the substrate, digest + auto-apply the feedback, then regenerate. Dedupe throughout so
    re-absorbing the same export is a no-op."""
    import json as _json
    blob = _json.loads(Path(blob_path).read_text(encoding="utf-8"))
    added, cadded, madded, aadded = _fold_blob(name, blob)
    fsum = _digest_and_apply(name)
    htmlpath, _slides, _ = generate(name)
    print(f"absorbed {added} glossary term(s) + {cadded} FAQ entr(y/ies) + {madded} mastered "
          f"decision(s) + {aadded} highlight note(s)"
          + (f"\nfeedback digest: {fsum}" if fsum else "")
          + f"\nregenerated HTML: {htmlpath}")


def _digest_and_apply(name):
    """Classify the new feedback, then auto-apply the safe fixes (confusion -> glossary).
    Fail-open but never silent. Returns (summary, changed) — changed=True when this run digested
    a new item or applied a fix, so the caller can skip a pointless codex-narrative regen."""
    try:
        import feedback as _fb
        touched = _fb.digest(name)
        resolved, terms = _fb.apply(name)
        fsum = _fb.summary(name)
        if terms:
            fsum += f"; auto-applied: {terms} glossary term(s) for {resolved} confusion item(s)"
        return fsum, bool(touched or terms)
    except Exception as _fe:  # a broken digest must leave a trace
        print(f"[feedback] digest skipped: {_fe}", file=sys.stderr)
        return "", False


def _fold_blob(name, blob):
    """Fold one exported memory blob into the substrate files. Glossary terms append to
    glossary.<name>.jsonl — the SAME file /trainlint:plan drills — so a concept the operator
    kept asking about becomes drillable; the raw Q&A appends to clarify.<name>.jsonl, which viz
    renders as an FAQ under each decision. Returns (glossary, faq, mastered, notes) counts."""
    import json as _json

    gpath = paths.wfile(f"glossary.{name}.jsonl")
    have = {e.get("term", "").lower() for e in (tree._load_jsonl(gpath) if gpath.exists() else [])}
    added = 0
    with gpath.open("a", encoding="utf-8") as f:
        for t in blob.get("glossary", []):
            term = (t.get("term") or "").strip()
            if term and term.lower() not in have:
                rec = {"term": term, "plain": t.get("plain", ""), "why": t.get("why", "")}
                if t.get("dec"):
                    rec["dec"] = t["dec"]
                f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
                have.add(term.lower())
                added += 1

    cpath = paths.wfile(f"clarify.{name}.jsonl")
    seen = {(e.get("dec"), e.get("q")) for e in (tree._load_jsonl(cpath) if cpath.exists() else [])}
    cadded = 0
    with cpath.open("a", encoding="utf-8") as f:
        for dec, items in (blob.get("faq") or {}).items():
            for it in items:
                q = (it.get("q") or "").strip()
                if q and (dec, q) not in seen:
                    rec = {"dec": dec, "q": q, "a": it.get("a", ""), "ts": it.get("ts", "")}
                    if it.get("focus"):  # what the question was ABOUT (highlight/card text)
                        rec["focus"] = it["focus"]
                    f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
                    seen.add((dec, q))
                    cadded += 1

    # mastery the web quiz recorded -> the SAME store the terminal /trainlint:quiz writes, so a
    # decision passed in the browser clears the understanding-gate too. Skipped silently if the
    # progress module/decision isn't resolvable (fail-open, like the rest of absorb).
    madded = 0
    try:
        import progress as _progress
        pl = plan.load(name)
        for did in (blob.get("mastered") or {}):
            node = plan.by_id(pl, did)
            if node:
                _progress.mark(name, node, mastered=True)
                madded += 1
    except Exception:
        pass

    # operator highlight-comments (the 🖍 notes) -> comments.<name>.jsonl, so margin notes made
    # in the browser reach the substrate the agent reads next session. Dedupe by annotation id.
    # dedupe by (id, text) — RE-absorbing is a no-op, but an EDITED note (same id, new text)
    # appends a new version line, so edits reach the digest instead of vanishing behind the id
    apath = paths.wfile(f"comments.{name}.jsonl")
    ahave = {(e.get("id"), e.get("comment"))
             for e in (tree._load_jsonl(apath) if apath.exists() else []) if isinstance(e, dict)}
    aadded = 0
    with apath.open("a", encoding="utf-8") as f:
        for an in (blob.get("annotations") or []):
            if not isinstance(an, dict):
                continue
            aid = str(an.get("id") or "").strip()
            note = str(an.get("comment") or "").strip()
            if aid and note and (aid, note) not in ahave:
                f.write(_json.dumps({"id": aid, "quote": str(an.get("quote") or ""),
                                     "comment": note, "sec": str(an.get("sec") or ""),
                                     "ts": str(an.get("ts") or "")}, ensure_ascii=False) + "\n")
                ahave.add((aid, note))
                aadded += 1

    return added, cadded, madded, aadded


def _digest_status_write(obj):
    """Atomically write data_root/.digest_status.json — the tiny contract between a digest run
    (any entry point: CLI, chat_backend's POST /digest) and the report page's status poll.
    States: running {started,pid,project} -> done {finished,summary} | error {finished,error}.
    Fail-silent: a broken status write must never break the digest itself."""
    import json as _json
    import os as _os
    import tempfile as _tf
    try:
        p = paths.data_root() / ".digest_status.json"
        fd, tmp = _tf.mkstemp(dir=str(p.parent), prefix=".tl-dst-")
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_json.dumps(obj, ensure_ascii=False))
        _os.replace(tmp, p)
    except Exception:
        pass


def digest_alive(pid):
    """True iff `pid` is a LIVE digest process (a `viz.py … --digest`) — NOT merely any live pid.
    Both digest guards (the button spawn in chat_backend and this CLI wrapper) key off this so they
    (a) never wedge forever on a status whose pid was recycled to some unrelated process, and
    (b) never double-run alongside a genuinely slow-but-alive digest. Reads /proc/<pid>/cmdline
    (Linux); anything unreadable/non-Linux -> False (fail-open: the guard then allows a run rather
    than blocking, so a stuck status can never permanently jam the button)."""
    try:
        pid = int(pid)
        if pid <= 0:
            return False
        cl = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ")
        return b"--digest" in cl and b"viz" in cl
    except Exception:
        return False


def digest_feedback(default_name=None):
    """Status-tracked wrapper around the real digest (_digest_feedback_run) so the report page's
    “Deal with all requests” button can poll progress. Writes running/done/error to
    .digest_status.json, and refuses to start while another LIVE digest run holds it (two
    concurrent runs would race the glossary/clarify appends)."""
    import json as _json
    import os as _os
    import time as _time
    try:  # concurrency guard: a genuinely-alive foreign digest wins; a dead/recycled pid is overridden
        st = _json.loads((paths.data_root() / ".digest_status.json").read_text(encoding="utf-8"))
        if st.get("state") == "running" and int(st.get("pid") or 0) != _os.getpid() \
                and digest_alive(st.get("pid")):
            print(f"another digest is already running (pid {st['pid']}) — skipped")
            return
    except Exception:
        pass
    _digest_status_write({"state": "running", "started": _time.time(), "pid": _os.getpid(),
                          "project": default_name or ""})
    try:
        lines = _digest_feedback_run(default_name)
        _digest_status_write({"state": "done", "finished": _time.time(), "project": default_name or "",
                              "summary": " | ".join(lines)[:500]})
    except BaseException as e:  # incl. KeyboardInterrupt — never leave a stale "running"
        _digest_status_write({"state": "error", "finished": _time.time(), "project": default_name or "",
                              "error": str(e)[:300]})
        raise


def _digest_feedback_run(default_name=None):
    """THE one command behind “digest feedback”: pull the operator feedback the report pages
    filed on the server (zero clicks browser-side — the pages auto-sync), fold every blob into
    its project's substrate, classify it (confusion/correction/readability), auto-apply the safe
    fixes (confusion -> glossary), re-render + re-upload the touched reports, and only then
    delete the consumed blobs server-side. Corrections stay pending in the compass — a human
    'this is wrong' needs judgment, not automation. Returns the per-project summary lines."""
    import re as _re
    try:
        import push
        pulled = push.pull_feedback()
    except Exception:
        pulled = []
    # The blob body is UNTRUSTED (server-stored, filed by whoever viewed the report). Its project
    # field drives local file paths, so validate it EXACTLY like the worker validates the URL param
    # — a strict allowlist — before it ever reaches paths.wfile / generate. Anything else is junk.
    def _safe_proj(v):
        v = str(v or "").strip()
        return v if _re.fullmatch(r"[A-Za-z0-9._-]{1,128}", v) else ""

    # default_name is TRUSTED (CLI arg / active project); blob project fields are UNTRUSTED.
    by = {}
    for key, blob in pulled:
        if not isinstance(blob, dict):  # valid-JSON non-dict (list/str/num) must not crash the run
            print(f"[digest] non-dict blob skipped: {key}", file=sys.stderr)
            continue
        proj = _safe_proj(blob.get("project")) or (default_name or "")
        if not proj:
            print(f"[digest] blob with bad/no project skipped: {key}", file=sys.stderr)
            continue
        by.setdefault(proj, []).append((key, blob))
    if default_name:
        by.setdefault(default_name, [])  # digest whatever landed locally for the named project too
    if not by:
        print("no pending feedback (server queue empty; no project named)")
        return ["no pending feedback"]
    out = []
    for proj, blobs in sorted(by.items()):
        counts = [0, 0, 0, 0]
        consumed = []  # only blobs whose fold SUCCEEDED — a failed fold is retried next run
        for key, blob in blobs:
            try:
                c = _fold_blob(proj, blob)
                counts = [a + b for a, b in zip(counts, c)]
                consumed.append(key)
            except Exception as e:
                print(f"[digest] {proj}: bad blob kept for retry: {e}", file=sys.stderr)
        fsum, changed = _digest_and_apply(proj)
        if not consumed and not changed:
            out.append(f"{proj}: nothing new")
            continue  # nothing folded, nothing digested/applied -> skip the codex regen entirely
        try:
            htmlpath, _s, _d = generate(proj)  # re-render + re-upload; one project's failure
        except Exception as e:                 # must not abort the rest of the batch
            print(f"[digest] {proj}: regenerate failed, blobs kept: {e}", file=sys.stderr)
            out.append(f"{proj}: regenerate FAILED")
            continue
        for key in consumed:  # consume ONLY after fold + regen both succeeded
            try:
                import push
                push.delete_feedback(key)
            except Exception:
                pass
        line = (f"{proj}: {len(consumed)}/{len(blobs)} blob(s) -> +{counts[0]} glossary, "
                f"+{counts[1]} FAQ, +{counts[3]} note(s)" + (f"; {fsum}" if fsum else ""))
        print(line + f"\n  -> {htmlpath}")
        out.append(line)
    return out


def main():
    """One project, one self-contained HTML report. The argument is the project name (default
    = active project). `--absorb <blob.json>` folds an exported memory blob into the substrate
    and regenerates. `--digest` pulls the feedback the report pages auto-synced to the server,
    absorbs + classifies + auto-applies it, and re-renders every touched project."""
    args = sys.argv[1:]
    name_args = [a for a in args if not a.startswith("-")]
    blob = None
    if "--absorb" in args:
        i = args.index("--absorb")
        blob = args[i + 1] if i + 1 < len(args) else None
        if not blob:
            sys.exit("usage: viz.py [project] --absorb <viz-memory.json>")
        # the project name is any non-flag arg that isn't the blob path
        name_args = [a for a in name_args if a != blob]
    if "--digest" in args:
        try:
            nm = tree._active(name_args[0] if name_args else None)
        except Exception:
            nm = name_args[0] if name_args else None
        digest_feedback(nm)
        return
    name = tree._active(name_args[0] if name_args else None)
    if blob:
        absorb(name, blob)
        return
    htmlpath, slidespath, d = generate(name)
    print(stdout_summary(name, d["goal"], d["bar"], d["pl"], d["nodes"], d["know"], htmlpath))
    # The sign-off every plan/execute close ends on. BOTH the report HTML and the slides deck are
    # phone deliverables (which-html: ship both): the close SendUserFile's EACH with display:'render'
    # and the Claude mobile app renders them inline — the report is full detail + the per-decision
    # chatbots, the deck is the glanceable paged view. The report doorman checks BOTH were sent.
    print(f"SLIDES: {slidespath}  (open in a browser · ←/→ to page · Print → Save-as-PDF)")
    print(f"PHONE: SendUserFile BOTH with display:'render' — the app renders each inline:")
    print(f"PHONE:   {htmlpath}   (the interactive report — full detail + chatbots)")
    print(f"PHONE:   {slidespath}   (the slides deck — glanceable, paged)")
    try:  # silent loopback server (started in generate) — surface the browser URL where the
        import serve  # JS-heavy report (per-block chatbots, quizzes) actually works
        _u = serve.url()  # check-only, never spawns a 2nd server (generate() is the spawn point)
        if _u:
            print(f"SERVE: {_u}/{name}.html  (browse in a REAL browser — chatbots/JS run here, "
                  f"unlike a JS-stripped inline preview; reach it via  ssh -L {_u.rsplit(':',1)[-1]}:127.0.0.1:{_u.rsplit(':',1)[-1]} <host>)")
    except Exception:
        pass


if __name__ == "__main__":
    main()
