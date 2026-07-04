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
import hashlib
import html
import json
import re
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
      '  Why: '+(dec.why||'')+'\n\n'+
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
    var key=localStorage.getItem(KK);
    if(!key) throw new Error('No API key set — click "Set API key" (bottom-right).');
    var res=await fetch('https://api.anthropic.com/v1/messages',{method:'POST',
      headers:{'content-type':'application/json','x-api-key':key,'anthropic-version':'2023-06-01',
        'anthropic-dangerous-direct-browser-access':'true'},
      body:JSON.stringify({model:localStorage.getItem(MK)||DATA.model,max_tokens:1024,system:sys,messages:convo})});
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
    var id, sys, ph;
    if(decId){var dec=DATA.decisions[decId]; if(!dec) return; id=decId; sys=sysPrompt(dec); ph='Ask anything about this decision…';}
    else if(blockId){var b=(DATA.blocks||{})[blockId]; if(!b) return; id=blockId; sys=sysPromptBlock(b); ph='Ask anything about this section…';}
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
    async function go(){
      var q=ta.value.trim(); if(!q) return; ta.value='';
      addMsg('u','You:',q); convo.push({role:'user',content:q});
      send.disabled=true; var wait=addMsg('a','Claude:','…thinking');
      try{
        var raw=await ask(sys,convo), pm=parseMemory(raw);
        wait.innerHTML="<b>Claude:</b> "+esc(pm.clean); convo.push({role:'assistant',content:raw});
        var m=mem(); m.faq=m.faq||{}; m.faq[id]=m.faq[id]||[];
        m.faq[id].push({q:q,a:pm.clean,ts:new Date().toISOString()});
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
    k.onclick=function(){var v=prompt('Anthropic API key (stored only in this browser):',localStorage.getItem(KK)||'');if(v!=null)localStorage.setItem(KK,v.trim());};
    var ex=document.createElement('button'); ex.textContent='⬇ Export memory';
    ex.onclick=function(){var m=mem(),blob={project:DATA.project,faq:m.faq||{},glossary:m.glossary||[],mastered:m.mastered||{}};
      var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(blob,null,2)],{type:'application/json'}));
      a.download='viz-memory.'+DATA.project+'.json';a.click();};
    bar.appendChild(k); bar.appendChild(ex); document.body.appendChild(bar);
  }
  document.querySelectorAll('.tl-chat').forEach(initWidget); toolbar();
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
QUIZ_JS = r"""
(function(){
  var el=document.getElementById('tl-data'); if(!el) return;
  var DATA=JSON.parse(el.textContent);
  var LS='trainlint_mem_'+DATA.project;
  function mem(){try{return JSON.parse(localStorage.getItem(LS))||{}}catch(e){return{}}}
  function setMem(m){localStorage.setItem(LS,JSON.stringify(m))}
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
        dec = f"<span class='fdec'>{_e(it.get('decision',''))}</span>" if it.get("decision") else ""
        nxt = f"<div class='fnext'><b>next:</b> {_ec(it.get('next',''))}</div>" if it.get("next") else ""
        cards.append(
            f"<div class='fcard'>"
            f"<div class='fhead'><span class='fst' style='background:{color.get(st,'#64748b')}'>{_e(st)}</span>"
            f"<span class='ftitle'>{_ec(it.get('title',''))}</span>{dec}</div>"
            f"<div class='ftry'>{_ec(it.get('trying',''))}</div>{nxt}</div>")
    return ("<div class='focussec'><div class='fshdr'>🎯 CURRENT FOCUS — what we're actively trying now</div>"
            + "".join(cards) + "<div class='tl-chat' data-block='current-focus'></div></div>")


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
         f"<title>{_e(name)} — research tree</title><style>{CSS}{CHAT_CSS}{QUIZ_CSS}</style></head><body><div class='wrap'>"]

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
             f"<div class='lbl'>{summ.get('decided_built',0)}/{counts.get('decided',0)} decided built · "
             f"{counts.get('verified',0)} verified · {counts.get('open',0)} open  "
             f"({summ['total']} decisions)</div></div>")
    if pillars:
        H.append("<div class='chips'>" + "".join(
            f"<span class='chip pillar'>◆ {_e(p['id'])}</span>" for p in pillars) + "</div>")
    if avoided:
        H.append("<div class='rej'><b>don't drift back:</b> " +
                 " · ".join(_e(a["not_this"]) for a in avoided if a.get("not_this")) + "</div>")
    try:
        import goalcheck as _gc  # noqa: E402
        _gd = _gc.brief(name)
    except Exception:
        _gd = ""
    if _gd:
        H.append("<div class='rej'><b>⚠️ goal↔scope drift:</b> " + _e(_gd) + "</div>")
    H.append("</div>")  # hdr

    # ---- 🆕 NEWLY DONE: what changed on the latest run (from the dated log) ----
    _new_ids, _new_date = newly_done(name)
    if _new_ids:
        _names = " · ".join(f"<code>{_e(i)}</code>" for i in sorted(_new_ids))
        H.append(f"<div class='newbar'><b>🆕 Newly done ({_e(_new_date)}):</b> {_names}</div>")

    # ---- CURRENT FOCUS: the active trial-and-error work right now ----
    H.append(focus_section_html(name))

    # ---- DATA section: the one place that DEMONSTRATES the data types (both modes) ----
    H.append(data_section_html(pl))

    # ---- pipeline: the REAL data flow (authored in pipeline.<name>.jsonl; empty -> nothing) ----
    H.append(pipeline_html(name))

    # ---- legend ----
    if planning:
        H.append("<div class='legend'>"
                 "<span><b>decisions</b> ✓ verified ◐ decided+built ✎ decided on paper (not built) ○ open</span>"
                 "<span>◆ pillar — a core dimension the project always rests on</span>"
                 "<span>★ main thread — the one decision to settle next</span>"
                 "<span>click a decision to see its principle (or ask its chatbot)</span></div>")
    else:
        H.append("<div class='legend'>"
                 "<span><b>spine</b> ✓ verified ◐ decided+built ✎ decided on paper (not built) ○ open</span>"
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
            # the dense original decision text also folds away — open only if you want the full rationale
            choice_full = _gloss(_e(n.get("choice", "")), gmap)
            choice_fold = (f"<details class='draw'><summary>full decision text</summary>"
                           f"<div class='dchfull'>{choice_full}</div></details>") if choice_full else ""
            # a decision that carries examples opens by default so its code blocks are visible on load
            dec_open = " open" if ex else ""
            spine.append(f"<details class='dec'{dec_open}><summary>"
                         f"<span class='gl' style='color:{_c}'>{_g}</span>"
                         f"<span class='dsum'><span class='dq'>{_gloss(_e(n.get('decision','')), gmap)}</span>{new_tag}{you}{pl_tag}"
                         f"<br><span class='dch'>{plain}</span></span></summary>"
                         f"<div class='dwhy'><span class='pr'>{_e(n.get('principle',''))}</span> "
                         f"{_ec(n.get('why',''))}</div>"
                         f"{ex_html}"
                         f"{choice_fold}"
                         f"<div class='tl-quiz' data-dec=\"{_e(n.get('id',''))}\"></div>"
                         f"<div class='tl-chat' data-dec=\"{_e(n.get('id',''))}\"></div></details>")
        if _collapse:
            spine.append("</details>")
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
    H.append("<script>" + QUIZ_JS + "</script>")
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
                  glossary=None, clarify=None, motivation=""):
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

    # 2 - story: one slide per beat (planning arc before any run, mature arc after)
    beats = (planning_story_beats(motivation, goal, bar, pl) if planning
             else story_beats(goal, bar, pl, nodes, rows))
    for b in beats:
        secs.append(_sec(_render_beats([b])))

    # 3 - pipeline (only once phases form a real processing flow)
    if not planning:
        secs.append(_sec("<h2 class='sec'>The data flow</h2>" + pipeline_html(name)))

    # 4 - timeline
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

    # 5 - decision spine: one slide per phase, decisions rendered open (not collapsible)
    for ph, decs in spine_groups(pl):
        s = [f"<h2 class='sec'>Decisions — {_e(ph)}</h2><div class='card'>"]
        for n in decs:
            st = n.get("status", "open")
            you = ("<span class='you'>← you are here</span>"
                   if (mt and n.get("id") == mt.get("id")) else "")
            pl_tag = "<span class='pill-tag'>◆ pillar</span>" if n.get("pillar") else ""
            _g, _c = _dec_glyph(n)
            s.append("<div class='dec-flat'>"
                     f"<span class='gl' style='color:{_c}'>{_g}</span>"
                     f"<span class='dsum'><span class='dq'>{_gloss(_e(n.get('decision','')), gmap)}"
                     f"</span>{you}{pl_tag}"
                     f"<br><span class='dch'>→ {_gloss(_e(n.get('choice','')), gmap)}</span>"
                     # slide-content-altitude: the slide face shows decision + choice + a principle
                     # tag only — the full why-paragraph (in the report) is dropped here so a dense
                     # phase doesn't overflow; the why is destined for speaker notes (notes-source).
                     f" <span class='pr'>{_e(n.get('principle',''))}</span></span></div>")
        s.append("</div>")
        secs.append(_sec("".join(s)))

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


def _load_project(name):
    """Everything the renderers need for one project — all derived, no new files."""
    facts = tree.load_facts(name)
    nodes = tree.build_tree(tree.load_events(name, facts), facts)
    pl = plan.load(name)
    know = tree._load_jsonl(paths.resolve(f"knowledge.{name}.jsonl"))
    glossary = tree._load_jsonl(paths.resolve(f"glossary.{name}.jsonl"))
    clarify = tree._load_jsonl(paths.resolve(f"clarify.{name}.jsonl"))
    gp = paths.resolve(f"goal.{name}.txt")
    goal, bar = split_goal(gp.read_text(encoding="utf-8") if gp.exists() else "")
    mp = paths.resolve(f"motivation.{name}.txt")
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
    htmlpath = outdir / f"{name}.html"
    htmlpath.write_text(render_html(d["name"], d["goal"], d["bar"], d["pl"], d["nodes"],
                                    d["know"], d["kinds"], d["id2phase"], d["phase_order"],
                                    glossary=d["glossary"], clarify=d["clarify"],
                                    motivation=d["motivation"]),
                        encoding="utf-8")
    slidespath = outdir / f"{name}.slides.html"
    slidespath.write_text(render_slides(d["name"], d["goal"], d["bar"], d["pl"], d["nodes"],
                                        d["know"], d["kinds"], d["id2phase"], d["phase_order"],
                                        glossary=d["glossary"], clarify=d["clarify"],
                                        motivation=d["motivation"]),
                          encoding="utf-8")
    try:
        import push
        push.push_report(name, htmlpath, slidespath)
    except Exception:
        pass
    return htmlpath, slidespath, d


def absorb(name, blob_path):
    """Fold an exported `viz-memory.<name>.json` (what the in-browser chatbots captured) back
    into the substrate, then regenerate. Glossary terms append to glossary.<name>.jsonl — the
    SAME file /trainlint:plan drills — so a concept the operator kept asking about becomes
    drillable; the raw Q&A appends to clarify.<name>.jsonl, which viz renders as an FAQ under
    each decision. Dedupe so re-absorbing the same export is a no-op."""
    import json as _json
    blob = _json.loads(Path(blob_path).read_text(encoding="utf-8"))

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
                    f.write(_json.dumps({"dec": dec, "q": q, "a": it.get("a", ""),
                                         "ts": it.get("ts", "")}, ensure_ascii=False) + "\n")
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

    htmlpath, _slides, _ = generate(name)
    print(f"absorbed {added} glossary term(s) + {cadded} FAQ entr(y/ies) + {madded} mastered "
          f"decision(s) into {gpath.name} + {cpath.name} + plan-progress\nregenerated HTML: {htmlpath}")


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
