#!/usr/bin/env python3
"""Frontier tree — a DERIVED VIEW, rebuilt from traces every run. Never maintained.

Two event sources, MERGED:
  - structured: re-derived each run from durable repo traces (run dir names + metrics).
    Best-effort; degrades to [] if the runs aren't reachable. Always current, deterministic.
  - annotations: a durable, append-only research log (the JUDGMENTS traces can't prove —
    why-abandoned / hypothesis / verdict / wall / dead-end). Harvested from ephemeral
    sessions into git BEFORE they are compacted/deleted (see harvest.py).

The tree is a fold over the merged event stream, grouped by `direction`. Pure & read-only.
This is a LINT substrate: it shows the search shape; it never prunes or decides.
"""
import glob
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(ROOT))
import paths  # noqa: E402  — per-project data lives outside the versioned plugin dir


def _active(name=None):
    return name or paths.active_project()


def _load_jsonl(path):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


def load_facts(name):
    try:
        return json.loads(paths.resolve(f"facts.{name}.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_annotations(name):
    return _load_jsonl(paths.resolve(f"log.{name}.jsonl"))


def derive_structured(facts):
    """Enumerate run dirs → experiment events. Degrades to [] off-cluster."""
    out = []
    g, rgx = facts.get("runs_glob"), facts.get("direction_regex")
    if not g or not rgx:
        return out
    try:
        for d in sorted(glob.glob(g)):
            m = re.search(rgx, Path(d).name)
            if not m:
                continue
            direction = m.group(2) if (m.lastindex and m.lastindex >= 2 and m.group(2)) else "base"
            parent = m.group(1) if m.lastindex else None
            out.append({"kind": "experiment", "direction": direction,
                        "parent": parent, "run": Path(d).name})
    except Exception:
        return []
    return out


def load_events(name=None, facts=None):
    name = _active(name)
    facts = facts if facts is not None else load_facts(name)
    return derive_structured(facts) + load_annotations(name)


def build_tree(events, facts=None):
    facts = facts or {}
    th = facts.get("thresholds", {})
    P, K, eps = th.get("patience_P", 3), th.get("window_K", 3), th.get("flat_eps", 0.01)
    nodes = {}
    for e in events:
        d = e.get("direction")
        if not d or d == "?":
            continue
        n = nodes.setdefault(d, {"direction": d, "parent": e.get("parent"),
                                 "experiments": [], "deltas": [], "walls": [],
                                 "abandoned": False, "trunk": {}, "notes": []})
        if e.get("parent") and not n["parent"]:
            n["parent"] = e["parent"]
        kind = e.get("kind", "experiment")
        if kind == "experiment":
            n["experiments"].append(e.get("run") or e.get("note", ""))
            if e.get("delta") is not None:
                n["deltas"].append(e["delta"])
        elif kind == "abandon":
            n["abandoned"] = True
            n["notes"].append(("abandon", e.get("note", "")))
        elif kind == "wall":
            n["walls"].append(e.get("note", ""))
        elif kind == "trunk-check":
            n["trunk"][e.get("note", "check")] = e.get("trunk_ok")
        else:  # verdict / hypothesis / deadend
            n["notes"].append((kind, e.get("note", "")))
    for n in nodes.values():
        n["spend"] = len(n["experiments"])
        dz = n["deltas"]
        won = any(t == "verdict" and re.search(r"win|基线|定为|拿下", s) for t, s in n["notes"])
        if n["abandoned"]:
            n["status"] = "abandoned"
        elif won:
            n["status"] = "won"
        elif n["spend"] >= P and len(dz) >= K and all(abs(x) < eps for x in dz[-K:]):
            n["status"] = "stalled"
        elif n["spend"] > 0:
            n["status"] = "deepening"
        else:
            n["status"] = "open"
    by_parent = {}
    for d, n in nodes.items():
        by_parent.setdefault(n.get("parent"), []).append(d)
    for n in nodes.values():
        n["siblings"] = [s for s in by_parent.get(n.get("parent"), []) if s != n["direction"]]
    return nodes
