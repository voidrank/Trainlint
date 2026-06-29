#!/usr/bin/env python3
"""Goal ↔ decisions consistency — catch the GOAL still claiming a scope a DECISION already dropped.

The scar this exists for: `downstream-multitrack-synthesis` was decided TEXT-ONLY (operator: '只要纯
文本的'), but goal.<name>.txt's DONE line still advertised "multi-track synthetic data". Nothing in the
workflow flagged the contradiction, so the north star kept pointing at a target the in-scope work can
no longer hit — and the compass repeated that stale goal every turn.

Mechanism (deterministic, operator-authored, generic — no semantic guessing): when a decision NARROWS
scope, it declares the phrases it removed:

    "scope_drop": ["multi-track synthetic data", "synthetic audio"]

This module flags any `scope_drop` phrase that STILL appears in goal.<name>.txt. That's the exact
contradiction above, caught the moment the goal and the decision disagree. Pure & read-only; fail-open
(any error -> no warnings, never raises).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
try:
    import plan as planlib  # noqa: E402
except Exception:  # pragma: no cover
    planlib = None


def _goal_text(name):
    try:
        return (ROOT / f"goal.{name}.txt").read_text(encoding="utf-8")
    except Exception:
        return ""


def drift(name=None):
    """[{id, phrase, choice}] for every (decision, phrase) where the decision dropped `phrase` from
    scope (its `scope_drop`) but goal.<name>.txt still asserts it. [] when consistent / nothing to
    check. Case-insensitive substring match — the phrase is the operator's own words, so an exact
    contains is both precise and predictable."""
    if planlib is None:
        return []
    name = planlib._active(name)
    goal = _goal_text(name)
    if not goal:
        return []
    gl = goal.lower()
    out = []
    for d in planlib.load(name):
        drops = d.get("scope_drop") or []
        if isinstance(drops, str):
            drops = [drops]
        for ph in drops:
            if isinstance(ph, str) and ph.strip() and ph.strip().lower() in gl:
                out.append({"id": d.get("id", ""), "phrase": ph.strip(),
                            "choice": d.get("choice", "")})
    return out


def brief(name=None):
    """One-line drift warning for the compass / briefing, or '' when goal and decisions agree."""
    ds = drift(name)
    if not ds:
        return ""
    parts = [f"«{d['id']}» dropped “{d['phrase']}” from scope but the GOAL still claims it"
             for d in ds]
    return ("⚠️ GOAL↔scope drift: " + "; ".join(parts)
            + " — re-derive goal.txt's DONE line so the north star matches the decisions")


if __name__ == "__main__":
    nm = sys.argv[1] if len(sys.argv) > 1 else None
    b = brief(nm)
    print(b or "goal ↔ decisions: consistent (no scope_drop phrase still appears in goal.txt)")
