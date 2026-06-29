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
import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree   # noqa: E402
import plan   # noqa: E402

ROOT = Path(__file__).resolve().parent

# status palettes (reused by ascii + html + svg) ---------------------------------------
TREE_ICON = {"open": "·", "deepening": "▸", "stalled": "⚠", "abandoned": "✗", "won": "★"}
TREE_FILL = {"open": "#e2e8f0", "deepening": "#bfdbfe", "stalled": "#fde68a",
             "abandoned": "#fecaca", "won": "#bbf7d0"}
TREE_EDGE = {"open": "#94a3b8", "deepening": "#3b82f6", "stalled": "#d97706",
             "abandoned": "#dc2626", "won": "#16a34a"}
DEC_ICON = {"verified": "✓", "decided": "◐", "open": "○"}
DEC_COLOR = {"verified": "#16a34a", "decided": "#d97706", "open": "#64748b"}
KIND = {  # (glyph, color, label)
    "experiment": ("●", "#2563eb", "experiment"),
    "wall":       ("⚠", "#d97706", "wall"),
    "abandon":    ("↩", "#dc2626", "backtrack"),
    "verdict":    ("★", "#16a34a", "verdict"),
    "hypothesis": ("◆", "#7c3aed", "hypothesis"),
    "deadend":    ("✗", "#64748b", "dead end"),
    "trunk-check":("✓", "#0d9488", "trunk-check"),
}


def _e(s):
    return html.escape(str(s), quote=True)


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


def timeline_rows(events, knowledge):
    """The dated story — annotation events that carry a ts, oldest first."""
    rows = []
    for e in events:
        ts = e.get("ts")
        if not ts:
            continue  # structured run-events have no date; they live in the tree, not the story
        kind = e.get("kind", "experiment")
        paper = wall_paper(e.get("note", ""), knowledge) if kind == "wall" else None
        rows.append({"ts": ts, "kind": kind, "direction": e.get("direction", "?"),
                     "note": e.get("note", ""), "delta": e.get("delta"), "paper": paper})
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
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,sans-serif;line-height:1.45}
.wrap{max-width:1120px;margin:0 auto;padding:22px}
.hdr{background:linear-gradient(135deg,#0f172a,#1e293b);color:#e2e8f0;border-radius:16px;padding:22px 26px}
.hdr h1{margin:0 0 2px;font-size:22px}
.hdr .sub{color:#94a3b8;font-size:13px;margin-bottom:14px}
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
.treecap{font-size:11.5px;color:var(--mut);padding:2px 10px 10px}
.treecap b{color:#334155}
.empty{color:var(--mut);font-size:13px;padding:22px 16px;text-align:center}
.foot{color:#94a3b8;font-size:11.5px;text-align:center;margin:22px 0 6px}
.pp{display:flex;flex-wrap:wrap;align-items:stretch;gap:6px;margin:4px 0 6px}
.pp-stage{flex:1 1 112px;min-width:112px;background:#fff;border:1px solid var(--line,#e2e8f0);border-top:3px solid;border-radius:9px;padding:8px 10px}
.pp-t{font-weight:700;font-size:12.5px}
.pp-now{color:#2563eb;font-size:10px;font-weight:700;margin-left:5px}
.pp-s{color:var(--mut,#64748b);font-size:11px;margin-top:2px}
.pp-arr{align-self:center;color:#cbd5e1;font-size:12px}
@media(max-width:560px){.pp-arr{display:none}.pp-stage{flex-basis:100%}}
abbr.gl-term{text-decoration:underline dotted #94a3b8;text-underline-offset:2px;cursor:help}
.gl-box{margin:14px 0 4px;background:#fff;border:1px solid var(--line,#e2e8f0);border-radius:10px;padding:2px 14px}
.gl-box summary{cursor:pointer;font-weight:700;font-size:12.5px;padding:9px 0}
.gl-row{font-size:12px;color:#334155;padding:4px 0;border-top:1px solid var(--line,#eef2f7)}
"""


# --- per-action-item chatbot: CSS + JS (the only client-side, JS-bearing parts) ----
# A self-contained widget under every decision. It calls the Anthropic API straight from
# the browser (key in localStorage, never baked into the file), grounds each answer in that
# decision + the project glossary, and captures what the user didn't grok into localStorage
# — which `viz --absorb <blob.json>` later folds back into glossary.* + clarify.* on disk.
CHAT_CSS = """
.tl-chat{margin:6px 0 10px 24px;font-size:13px}
.tl-ask{cursor:pointer;border:1px solid #c7d2fe;background:#eef2ff;color:#3730a3;border-radius:8px;padding:3px 10px;font-size:12px;font-weight:600}
.tl-ask:hover{background:#e0e7ff}
.tl-panel{display:none;margin-top:8px;border:1px solid var(--line);border-radius:10px;background:#fafbff;overflow:hidden}
.tl-panel.open{display:block}
.tl-saved{padding:8px 10px;border-bottom:1px solid #eef2f7}
.tl-saved h5{margin:0 0 4px;font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;color:#94a3b8}
.tl-term{font-size:12.5px;margin:3px 0}.tl-term b{color:#3730a3}
.tl-unsv{font-size:9.5px;color:#b45309;background:#fef3c7;border-radius:5px;padding:0 5px;margin-left:5px}
.tl-faq{font-size:12.5px;margin:5px 0;color:#334155}
.tl-faq .q{font-weight:700;color:#0f172a}
.tl-log{padding:8px 10px;min-height:54px;max-height:340px;overflow-y:auto;resize:vertical}
.tl-msg{margin:6px 0;font-size:13px;line-height:1.45;white-space:pre-wrap}
.tl-msg.u b{color:#3730a3}.tl-msg.a b{color:#16a34a}.tl-msg.err{color:#b91c1c}
.tl-in{display:flex;gap:6px;padding:8px 10px;border-top:1px solid #eef2f7}
.tl-in textarea{flex:1;border:1px solid #cbd5e1;border-radius:8px;padding:6px 8px;font:inherit;font-size:13px;resize:vertical;min-height:36px}
.tl-in button{border:0;background:#4f46e5;color:#fff;border-radius:8px;padding:0 14px;font-weight:600;cursor:pointer}
.tl-in button:disabled{background:#a5b4fc}
.tl-bar{position:fixed;right:14px;bottom:14px;display:flex;gap:8px;z-index:50}
.tl-bar button{border:1px solid #cbd5e1;background:#fff;border-radius:9px;padding:6px 12px;font-size:12px;font-weight:600;cursor:pointer;box-shadow:0 2px 8px rgba(15,23,42,.12)}
.tl-bar button:hover{background:#f8fafc}
"""

# Plain string (NOT an f-string) — the JS keeps its own braces/backticks. It reads the
# embedded <script id="tl-data"> blob for grounding; nothing about the project is hard-coded.
CHAT_JS = r"""
(function(){
  var el=document.getElementById('tl-data'); if(!el) return;
  var DATA=JSON.parse(el.textContent);
  var LS='trainlint_mem_'+DATA.project, KK='trainlint_anthropic_key', MK='trainlint_model';
  function mem(){try{return JSON.parse(localStorage.getItem(LS))||{faq:{},glossary:[]}}catch(e){return{faq:{},glossary:[]}}}
  function setMem(m){localStorage.setItem(LS,JSON.stringify(m))}
  function esc(s){return (s==null?'':String(s)).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
  function glossaryText(){return (DATA.glossary||[]).map(function(g){return '- '+g.term+': '+g.plain+(g.why?(' ('+g.why+')'):'')}).join('\n')}
  function sysPrompt(dec){
    return 'You are a tutor embedded in a research-planning report for the project "'+DATA.project+'".\n'+
      'PROJECT GOAL: '+DATA.goal+'\n\n'+
      'You answer questions about ONE decision in the plan:\n'+
      '  Decision: '+dec.decision+'\n'+
      '  Chosen: '+(dec.choice||'(still open)')+'\n'+
      '  Principle: '+(dec.principle||'')+'\n'+
      '  Why: '+(dec.why||'')+'\n\n'+
      'PROJECT GLOSSARY:\n'+glossaryText()+'\n\n'+
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
  async function ask(dec,convo){
    var key=localStorage.getItem(KK);
    if(!key) throw new Error('No API key set — click "Set API key" (bottom-right).');
    var res=await fetch('https://api.anthropic.com/v1/messages',{method:'POST',
      headers:{'content-type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01',
        'anthropic-dangerous-direct-browser-access':'true'},
      body:JSON.stringify({model:localStorage.getItem(MK)||DATA.model,max_tokens:1024,system:sysPrompt(dec),messages:convo})});
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
    var decId=node.getAttribute('data-dec'), dec=DATA.decisions[decId]; if(!dec) return;
    var convo=[];
    var btn=document.createElement('button'); btn.className='tl-ask'; btn.textContent='💬 Ask about this';
    var panel=document.createElement('div'); panel.className='tl-panel';
    var saved=document.createElement('div'), log=document.createElement('div'); log.className='tl-log';
    var inRow=document.createElement('div'); inRow.className='tl-in';
    var ta=document.createElement('textarea'); ta.placeholder='Ask anything about this decision… (Cmd/Ctrl+Enter to send)';
    var send=document.createElement('button'); send.textContent='Send';
    inRow.appendChild(ta); inRow.appendChild(send);
    panel.appendChild(saved); panel.appendChild(log); panel.appendChild(inRow);
    node.appendChild(btn); node.appendChild(panel);
    renderSaved(saved,decId);
    btn.addEventListener('click',function(e){e.preventDefault();panel.classList.toggle('open');});
    function addMsg(cls,who,txt){var d=document.createElement('div');d.className='tl-msg '+cls;d.innerHTML='<b>'+who+'</b> '+esc(txt);log.appendChild(d);log.scrollTop=log.scrollHeight;return d;}
    async function go(){
      var q=ta.value.trim(); if(!q) return; ta.value='';
      addMsg('u','You:',q); convo.push({role:'user',content:q});
      send.disabled=true; var wait=addMsg('a','Claude:','…thinking');
      try{
        var raw=await ask(dec,convo), pm=parseMemory(raw);
        wait.innerHTML="<b>Claude:</b> "+esc(pm.clean); convo.push({role:'assistant',content:raw});
        var m=mem(); m.faq=m.faq||{}; m.faq[decId]=m.faq[decId]||[];
        m.faq[decId].push({q:q,a:pm.clean,ts:new Date().toISOString()});
        m.glossary=m.glossary||[];
        pm.terms.forEach(function(t){if(t&&t.term)m.glossary.push({term:t.term,plain:t.plain||'',why:t.why||'',dec:decId})});
        setMem(m); renderSaved(saved,decId);
      }catch(err){wait.className='tl-msg err';wait.innerHTML='⚠ '+esc(err.message);}
      send.disabled=false; ta.focus();
    }
    send.addEventListener('click',go);
    ta.addEventListener('keydown',function(e){if(e.key==='Enter'&&(e.metaKey||e.ctrlKey))go();});
  }
  function toolbar(){
    var bar=document.createElement('div'); bar.className='tl-bar';
    var k=document.createElement('button'); k.textContent='🔑 Set API key';
    k.onclick=function(){var v=prompt('Anthropic API key (stored only in this browser):',localStorage.getItem(KK)||'');if(v!=null)localStorage.setItem(KK,v.trim());};
    var ex=document.createElement('button'); ex.textContent='⬇ Export memory';
    ex.onclick=function(){var m=mem(),blob={project:DATA.project,faq:m.faq||{},glossary:m.glossary||[]};
      var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(blob,null,2)],{type:'application/json'}));
      a.download='viz-memory.'+DATA.project+'.json';a.click();};
    bar.appendChild(k); bar.appendChild(ex); document.body.appendChild(bar);
  }
  document.querySelectorAll('.tl-chat').forEach(initWidget); toolbar();
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
    bullets = [(p.get("id", ""), _trunc(p.get("choice") or p.get("decision", ""), 96))
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
    beats.append({"cls": "want", "label": "🎯 想做什么", "head": head,
                  "bullets": bullets, "tail": (f"<b>done</b> = {_e(done)}" if done else "")})
    # 2 · 遇到问题
    if probs:
        beats.append({"cls": "prob", "label": "⛰ 遇到问题",
                      "head": _join(probs, lambda p: f"[{p[0]}] {p[1]}", 2, "walls"),
                      "sub": f"{len(probs)} wall(s) still standing"})
    else:
        beats.append({"cls": "prob", "label": "⛰ 遇到问题",
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
        beats.append({"cls": "did", "label": "🔧 干了什么",
                      "head": _join(did, lambda x: f"{x[0]} [{x[1]}] {x[2]}", 3, "moves"),
                      "sub": f"{len(did)} direction(s) resolved or backtracked"})
    else:
        beats.append({"cls": "did", "label": "🔧 干了什么",
                      "head": "no verdicts or backtracks logged yet"})
    # 5 · 要做什么
    if mt:
        beats.append({"cls": "next", "label": "➡️ 要做什么",
                      "head": mt.get("choice", "") or "drive the main thread to a verdict",
                      "sub": f"{n_open} decision(s) still open" if n_open else ""})
    else:
        beats.append({"cls": "next", "label": "➡️ 要做什么",
                      "head": "harden, verify the unverified, and ship"})
    return beats


def _render_beats(beats):
    """Render a list of story beats to HTML. A beat is {cls,label,head} plus optional
    sub / bullets (总分总 pillar list) / tail (pre-escaped HTML). Shared by the mature
    five-beat arc and the planning-stage arc so they stay visually identical."""
    H = ["<div class='story'>"]
    for b in beats:
        body = [f"<div class='bt'>{_e(b['head'])}"]
        if b.get("sub"):
            body.append(f"<span class='sm'>{_e(b['sub'])}</span>")
        if b.get("bullets"):
            body.append("<ul class='blist'>")
            for bid, btext in b["bullets"]:
                body.append(f"<li><b>{_e(bid)}</b> — {_e(btext)}</li>")
            body.append("</ul>")
        if b.get("tail"):
            body.append(f"<div class='tail'>{b['tail']}</div>")  # tail is pre-escaped HTML
        body.append("</div>")
        H.append(f"<div class='beat {b['cls']}'><div class='bl'>{_e(b['label'])}</div>"
                 f"{''.join(body)}</div>")
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
                      "head": mt.get("choice", "") or "settle this decision next",
                      "sub": f"{n_open} decision(s) still open" if n_open else ""})
    return beats


def planning_story_html(motivation, goal, bar, pl):
    return _render_beats(planning_story_beats(motivation, goal, bar, pl))


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
    decmap = {}
    for n in pl:
        did = n.get("id")
        if not did:
            continue
        decmap[did] = {"decision": n.get("decision", ""), "choice": n.get("choice", ""),
                       "principle": n.get("principle", ""), "why": n.get("why", ""),
                       "faq": clar_by.get(did, []), "terms": gloss_by.get(did, [])}
    data = {"project": name, "goal": goal, "model": "claude-opus-4-8",
            "glossary": [{"term": g.get("term", ""), "plain": g.get("plain", ""),
                          "why": g.get("why", "")} for g in glossary],
            "decisions": decmap}
    # escape '<' so the JSON can never break out of its <script> host
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def pipeline_html(pl):
    """The project as a left-to-right PIPELINE: one stage per phase (in first-appearance order),
    coloured by aggregate status — green=all settled, blue=holds the main thread, amber=has an
    open decision. Derived from the plan; no per-project authoring."""
    SC = {"done": "#16a34a", "open": "#d97706", "current": "#2563eb"}
    mt = plan.main_thread(pl)
    mt_id = mt.get("id") if mt else None
    groups = spine_groups(pl)
    cards = []
    for i, (ph, decs) in enumerate(groups):
        sts = [d.get("status", "open") for d in decs]
        cur = any(d.get("id") == mt_id for d in decs)
        st = "current" if cur else ("open" if any(s == "open" for s in sts) else "done")
        ndone = sum(1 for s in sts if s in ("decided", "verified"))
        now = "<span class='pp-now'>● now</span>" if cur else ""
        cards.append(f"<div class='pp-stage' style='border-top-color:{SC[st]}'>"
                     f"<div class='pp-t'>{_e(ph or '—')}{now}</div>"
                     f"<div class='pp-s'>{ndone}/{len(decs)} settled</div></div>")
        if i < len(groups) - 1:
            cards.append("<div class='pp-arr'>▶</div>")
    return ("<h2 class='sec'>Pipeline — the system, phase by phase</h2>"
            "<div class='pp'>" + "".join(cards) + "</div>")


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
        rows.append(f"<div class='gl-row'><b>{_e(t)}</b> — {_e(g['plain'])}</div>")
    if not rows:
        return ""
    return ("<details class='gl-box'><summary>Glossary — every term in plain words</summary>"
            + "".join(rows) + "</details>")


def render_html(name, goal, bar, pl, nodes, knowledge, kinds, id2phase, phase_order,
                glossary=None, clarify=None, motivation=""):
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
         f"<title>{_e(name)} — research tree</title><style>{CSS}{CHAT_CSS}</style></head><body><div class='wrap'>"]

    # ---- header / TLDR ----
    H.append("<div class='hdr'>")
    if planning:
        H.append(f"<h1>{_e(name)}</h1><div class='sub'>Trainlint plan · a plan in progress — "
                 "motivation · goal · decisions · next (no experiments run yet)</div>")
        H.append(planning_story_html(motivation, goal, bar, pl))
    else:
        H.append(f"<h1>{_e(name)}</h1><div class='sub'>research tree · a Trainlint derived view — "
                 "the project as one story: want · problem · bottleneck · did · next</div>")
        H.append(story_html(goal, bar, pl, nodes, rows))
    H.append("<div class='score'><div class='dots'>" + _dots(counts) + "</div>"
             f"<div class='lbl'>{counts.get('verified',0)} verified · {counts.get('decided',0)} decided · "
             f"{counts.get('open',0)} open  ({summ['total']} decisions)</div></div>")
    if pillars:
        H.append("<div class='chips'>" + "".join(
            f"<span class='chip pillar'>◆ {_e(p['id'])}</span>" for p in pillars) + "</div>")
    if avoided:
        H.append("<div class='rej'><b>don't drift back:</b> " +
                 " · ".join(_e(a["not_this"]) for a in avoided if a.get("not_this")) + "</div>")
    H.append("</div>")  # hdr

    # ---- pipeline (the system, phase by phase) ----
    # Only meaningful when phases form an actual processing flow (data→preproc→model→train→eval).
    # At planning stage the phases are decision CATEGORIES, not a pipeline, and the abstract
    # "N/M settled" score just duplicates the spine below — so skip it.
    if not planning:
        H.append(pipeline_html(pl))

    # ---- legend ----
    if planning:
        H.append("<div class='legend'>"
                 "<span><b>decisions</b> ✓ verified ◐ decided ○ open</span>"
                 "<span>◆ pillar — a core dimension the project always rests on</span>"
                 "<span>★ main thread — the one decision to settle next</span>"
                 "<span>click a decision to see its principle (or ask its chatbot)</span></div>")
    else:
        H.append("<div class='legend'>"
                 "<span><b>spine</b> ✓ verified ◐ decided ○ open</span>"
                 "<span><b>tree</b> ⚠ open problem · ✓ wall closed · ◆ tested · ↩ backtracked · ● decided · ○ idea</span>"
                 "<span><b>edges</b> ⚠ wall → 📖 paper it unlocks</span>"
                 "<span>click a decision to see its principle</span></div>")

    # ---- timeline (suppressed at planning stage — nothing has happened yet) ----
    if not planning:
        H.append("<h2 class='sec'>Timeline — how the search got here</h2>")
    if not planning:
        if rows:
            H.append("<div class='card tl'>")
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
                H.append(f"<div class='row'><div class='date'>{_e(r['ts'])}</div>"
                         f"<div class='mk' style='color:{col}'>{g}</div>"
                         f"<div class='body'><span class='dir'>{_e(r['direction'])}</span>"
                         f"<span class='knd'>{_e(lbl)}</span>{dhtml}"
                         f"<div class='note'>{_e(r['note'])}{read}</div></div></div>")
            H.append("</div>")
        else:
            H.append("<div class='card'><div class='empty'>No dated events harvested yet — the "
                     "timeline fills in from the session log (walls, verdicts, backtracks).</div></div>")

    # ---- decision spine (full-width at planning stage; beside the search tree once it exists) ----
    spine = ["<div><h2 class='sec'>"
             + ("Decisions — the plan, phase by phase" if planning else "Decision spine — what we know")
             + "</h2><div class='card'>"]
    for ph, decs in spine_groups(pl):
        spine.append(f"<div class='phase'>{_e(ph)}</div>")
        for n in decs:
            st = n.get("status", "open")
            you = "<span class='you'>← you are here</span>" if (mt and n.get("id") == mt.get("id")) else ""
            pl_tag = "<span class='pill-tag'>◆ pillar</span>" if n.get("pillar") else ""
            spine.append("<details class='dec'><summary>"
                         f"<span class='gl' style='color:{DEC_COLOR.get(st,'#64748b')}'>{DEC_ICON.get(st,'?')}</span>"
                         f"<span class='dsum'><span class='dq'>{_gloss(_e(n.get('decision','')), gmap)}</span>{you}{pl_tag}"
                         f"<br><span class='dch'>→ {_gloss(_e(n.get('choice','')), gmap)}</span></span></summary>"
                         f"<div class='dwhy'><span class='pr'>{_e(n.get('principle',''))}</span> "
                         f"{_e(n.get('why',''))}</div>"
                         f"<div class='tl-chat' data-dec=\"{_e(n.get('id',''))}\"></div></details>")
    spine.append("</div></div>")
    if planning:
        H.append("".join(spine))
    else:
        H.append("<div class='cols'>")
        H.append("".join(spine))
        H.append("<div><h2 class='sec'>Search tree — the directions explored</h2>"
                 "<div class='card' style='padding:12px 8px;overflow-x:auto'>")
        H.append(tree_svg(nodes, knowledge, kinds, id2phase, phase_order))
        H.append("</div></div>")
        H.append("</div>")  # cols

    H.append(glossary_html(glossary))

    H.append(f"<div class='foot'>Trainlint · derived from research/plan.{_e(name)}.jsonl + "
             f"log.{_e(name)}.jsonl + knowledge.{_e(name)}.jsonl — never hand-maintained.<br>"
             "ask a decision's chatbot, then <b>Export memory</b> → "
             f"<code>viz.py {_e(name)} --absorb &lt;file&gt;</code> to fold it into the glossary + FAQ.</div>")
    H.append("</div>")  # wrap
    H.append('<script id="tl-data" type="application/json">'
             + _chat_blob(name, goal, pl, glossary, clarify) + "</script>")
    H.append("<script>" + CHAT_JS + "</script>")
    H.append("</body></html>")
    return "\n".join(H)


# --- ascii summary (stdout / hook) ----------------------------------------------------

def stdout_summary(name, goal, bar, pl, nodes, knowledge, htmlpath):
    summ = plan.summary(pl)
    c = summ["counts"]
    mt = plan.main_thread(pl)
    rows = timeline_rows(tree.load_events(name, tree.load_facts(name)), knowledge)
    out = [f"# research tree ({name})  ·  {summ['total']} decisions "
           f"[{c.get('verified',0)} verified / {c.get('decided',0)} decided / {c.get('open',0)} open]"]
    if goal:
        out.append(f"  goal : {_trunc(goal, 92)}")
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


def _load_project(name):
    """Everything the renderers need for one project — all derived, no new files."""
    facts = tree.load_facts(name)
    nodes = tree.build_tree(tree.load_events(name, facts), facts)
    pl = plan.load(name)
    know = tree._load_jsonl(ROOT / f"knowledge.{name}.jsonl")
    glossary = tree._load_jsonl(ROOT / f"glossary.{name}.jsonl")
    clarify = tree._load_jsonl(ROOT / f"clarify.{name}.jsonl")
    gp = ROOT / f"goal.{name}.txt"
    goal, bar = split_goal(gp.read_text(encoding="utf-8") if gp.exists() else "")
    mp = ROOT / f"motivation.{name}.txt"
    motivation = " ".join(mp.read_text(encoding="utf-8").split()) if mp.exists() else ""
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
            "motivation": motivation}


def generate(name):
    """Write research/viz/<name>.html and return (path, project-dict)."""
    d = _load_project(name)
    outdir = ROOT / "viz"
    outdir.mkdir(exist_ok=True)
    htmlpath = outdir / f"{name}.html"
    htmlpath.write_text(render_html(d["name"], d["goal"], d["bar"], d["pl"], d["nodes"],
                                    d["know"], d["kinds"], d["id2phase"], d["phase_order"],
                                    glossary=d["glossary"], clarify=d["clarify"],
                                    motivation=d["motivation"]),
                        encoding="utf-8")
    return htmlpath, d


def absorb(name, blob_path):
    """Fold an exported `viz-memory.<name>.json` (what the in-browser chatbots captured) back
    into the substrate, then regenerate. Glossary terms append to glossary.<name>.jsonl — the
    SAME file /trainlint:plan drills — so a concept the operator kept asking about becomes
    drillable; the raw Q&A appends to clarify.<name>.jsonl, which viz renders as an FAQ under
    each decision. Dedupe so re-absorbing the same export is a no-op."""
    import json as _json
    blob = _json.loads(Path(blob_path).read_text(encoding="utf-8"))

    gpath = ROOT / f"glossary.{name}.jsonl"
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

    cpath = ROOT / f"clarify.{name}.jsonl"
    seen = {(e.get("dec"), e.get("q")) for e in (tree._load_jsonl(cpath) if cpath.exists() else [])}
    cadded = 0
    with cpath.open("a", encoding="utf-8") as f:
        for dec, items in (blob.get("faq") or {}).items():
            for it in items:
                q = (it.get("q") or "").strip()
                if q and (dec, q) not in seen:
                    f.write(_json.dumps({"dec": dec, "q": q, "a": it.get("a", ""),
                                         "ts": it.get("ts", "")}, ensure_ascii=False) + "\n")
                    seen.add((dec, q))
                    cadded += 1

    htmlpath, _ = generate(name)
    print(f"absorbed {added} glossary term(s) + {cadded} FAQ entr(y/ies) into "
          f"{gpath.name} + {cpath.name}\nregenerated HTML: {htmlpath}")


def main():
    """One project, one self-contained HTML report. The argument is the project name (default
    = active project). `--absorb <blob.json>` instead folds an exported memory blob into the
    substrate and regenerates."""
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
    name = tree._active(name_args[0] if name_args else None)
    if blob:
        absorb(name, blob)
        return
    htmlpath, d = generate(name)
    print(stdout_summary(name, d["goal"], d["bar"], d["pl"], d["nodes"], d["know"], htmlpath))


if __name__ == "__main__":
    main()
