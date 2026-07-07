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
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402  — per-project data lives outside the versioned plugin dir
try:
    import plan as planlib  # noqa: E402
except Exception:  # pragma: no cover
    planlib = None


def _goal_text(name):
    try:
        return paths.resolve(f"goal.{name}.txt").read_text(encoding="utf-8")
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


# means/end lint — a GOAL that leads with a MEANS (an activity whose object is a model/tool) instead
# of the DELIVERABLE. The scar: "Train a small Qwen3 LoRA…" put the disposable tool up front and
# buried the real end (the DATA) at the tail, so the report read means-first and no one could tell the
# goal. Deterministic: fires only when the first clause is <means-verb> … <model/tool-noun>.
_MEANS_NOUN = (r"(models?|lora|adapters?|networks?|classifiers?|checkpoints?|transformers?"
               r"|pipelines?|systems?|tools?)")
_MEANS_LEAD = re.compile(
    r"^\s*(train|build|fine[-\s]?tune|implement|develop|construct|code)\b[^.;—]*?\b"
    + _MEANS_NOUN + r"\b", re.I)


def means_first(name=None):
    """Warn when the GOAL is means-first (see comment above). Returns a warning string or ''."""
    if planlib is None:
        return ""
    name = planlib._active(name)
    goal = _goal_text(name)
    if not goal:
        return ""
    first = re.split(r"(?i)\bDONE\b", goal, 1)[0].strip()
    first = re.split(r"(?<=[.;—])\s", first, 1)[0]
    if _MEANS_LEAD.search(first):
        return ("⚠️ GOAL is MEANS-first: it leads with building a tool, not the deliverable — state "
                "the END (what we actually want) first and demote the model/tool to the means.")
    return ""


# mechanical-main-thread lint — the MAIN THREAD (the load-bearing decision everything supposedly waits
# on) is really a MECHANICAL floor-check a script settles with zero judgment. The scar:
# `base-model-and-tokenizer` ("assert byte-exact tokenizer parity") was driven as the main thread and
# even popped a "mark it verified?" question — for a model the DATA pipeline didn't even use yet.
# A load-bearing decision must BOTH gate the deliverable AND need a human call; a checkable parity is
# the FLOOR (guarantee it silently), never the thing everything waits on.
_MECH_RE = re.compile(
    r"byte[-\s]?exact|round[-\s]?trip|tokenizer\s+parit|(?:stack|train[-\s/]*serve)\s+parit"
    r"|format[-\s]?valid|checksum|lossless|schema[-\s]?clean", re.I)


def mechanical_main_thread(name=None):
    """Warn when the current main thread is a mechanical parity/format check, not a judgment call."""
    if planlib is None:
        return ""
    name = planlib._active(name)
    try:
        mt = planlib.main_thread(planlib.load(name))
    except Exception:
        return ""
    if not mt:
        return ""
    text = " ".join(str(mt.get(k, "")) for k in ("decision", "plain", "choice"))
    if _MECH_RE.search(text):
        return ("⚠️ MAIN THREAD is a MECHANICAL check («" + mt.get("id", "") + "»): a script settles it "
                "byte-for-byte with zero judgment — that is the FLOOR (guarantee it silently), not what "
                "everything waits on. Re-pick a main thread that gates the DELIVERABLE and needs a human call.")
    return ""


# big-picture / ownership lint — the project records WHAT it builds (goal) but not what it ULTIMATELY
# serves (the end product, the downstream consumer, the owner). Heads-down producing a deliverable with
# no articulated purpose is how work drifts from what actually matters, and how you optimize the wrong
# axis (e.g. polishing one language when the real need is coverage). The general idea: form the big
# picture, ASK the operator what they'll do with this, and record it in purpose.<name>.txt.
def missing_purpose(name=None):
    """Warn when purpose.<name>.txt (the why-chain: end product / downstream consumer / owner) is
    absent or empty. Deterministic; fail-open."""
    if planlib is None:
        return ""
    name = planlib._active(name)
    try:
        p = paths.resolve(f"purpose.{name}.txt")
        txt = p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except Exception:
        return ""
    if not txt:
        return ("⚠️ NO PURPOSE recorded: the plan says WHAT we build but not what it ULTIMATELY serves "
                "(end product / downstream consumer / owner). Form the big picture and ASK the operator "
                "what they will do with this — then write purpose.<name>.txt.")
    return ""


def brief(name=None):
    """Combined one-line goal warnings for the compass / report — MEANS-first framing, a MECHANICAL
    main thread, a MISSING purpose, AND scope drift, each self-labeled; '' when everything is clean."""
    parts = []
    for w in (means_first(name), mechanical_main_thread(name), missing_purpose(name)):
        if w:
            parts.append(w)
    ds = drift(name)
    if ds:
        parts.append("⚠️ GOAL↔scope drift: " + "; ".join(
            f"«{d['id']}» dropped “{d['phrase']}” from scope but the GOAL still claims it"
            for d in ds) + " — re-derive goal.txt's DONE line so the north star matches the decisions")
    return "  ·  ".join(parts)


if __name__ == "__main__":
    nm = sys.argv[1] if len(sys.argv) > 1 else None
    b = brief(nm)
    print(b or "goal ↔ decisions: consistent (no scope_drop phrase still appears in goal.txt)")
