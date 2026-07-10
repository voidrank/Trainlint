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


# spec-prose lint — the GOAL is the ONE human-facing north star, but agents (pressured to be
# complete) inflate it into a bracket-annotated spec paragraph the operator can't read (the
# 2026-07-10 "goal 我一个都看不懂" scar: goal.rename-hansard.txt grew to a 560-char English lead
# with [decision-id] brackets). The contract (plan.md step 2): goal.txt = ONE plain sentence in the
# operator's language + a plain `DONE =` line; decision ids, scope enumerations and file/regex
# detail live in plan.<name>.jsonl (`plain`/`scope_drop`), never in the goal. Deterministic signals.
_SPEC_BRACKET = re.compile(r"\[[a-z0-9][a-z0-9-]{2,}(?:\s[^\]]*)?\]")   # [decision-id] / [id note…]
_SPEC_TECH = re.compile(r"\w+\.(?:py|md|json|jsonl|toml|yaml|sh)\b|\b[A-Z]{2,}[A-Z_]*_[A-Z_]+\b"
                        r"|_re\b|\bregex\b")                            # file names / ENV_VARS / regexes


def spec_prose(name=None):
    """Warn when goal.<name>.txt reads as agent spec-prose instead of one plain sentence + DONE.
    Returns a warning string or ''."""
    if planlib is None:
        return ""
    name = planlib._active(name)
    goal = _goal_text(name).strip()
    if not goal:
        return ""
    lead = re.split(r"(?i)\bDONE\b", goal, 1)[0].strip()
    signals = []
    if _SPEC_BRACKET.search(goal):
        signals.append("[decision-id] brackets")
    if len(lead) > 400:
        signals.append(f"lead runs ~{len(lead)} chars before DONE")
    if _SPEC_TECH.search(goal):
        signals.append("file/env/regex detail")
    if not signals:
        return ""
    return ("⚠️ GOAL is SPEC-PROSE (" + ", ".join(signals) + "): the north star is a human-facing "
            "surface — ONE plain sentence in the operator's language + a plain DONE line. Move "
            "decision ids, scope enumerations and technical detail into plan decisions "
            "(plain/scope_drop), then redraft goal.txt with kimi (human-facing prose is kimi's).")


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


# operator-correction lint — the reader left a margin note in the report saying something is
# WRONG (feedback.<name>.jsonl, kind=correction, written by feedback.py at --absorb time). A human
# disputing a claim outranks any green metric: it must reach the agent's context, not sit in a
# file. Cleared by re-examining the claim and adding "resolved": true to that line.
def unaddressed_corrections(name=None):
    """Warn while feedback.<name>.jsonl holds unresolved items. Corrections get the loud line
    (a human disputing a claim outranks any green metric); confusion/readability/unclassified
    get one soft line so glossary and report fixes also have a consumer, not just a display."""
    if planlib is None:
        return ""
    name = planlib._active(name)
    try:
        import json as _json
        p = paths.resolve(f"feedback.{name}.jsonl")
        rows = []
        if p.exists():
            for x in p.read_text(encoding="utf-8").splitlines():
                x = x.strip()
                if not x or x.startswith("#"):
                    continue
                try:
                    e = _json.loads(x)
                except Exception:
                    continue
                if isinstance(e, dict):
                    rows.append(e)
    except Exception:
        return ""
    parts = []
    todo = [r for r in rows if r.get("kind") == "correction" and not r.get("resolved")]
    if todo:
        heads = "; ".join(f"«{str(r.get('quote') or '')[:40]}»: “{str(r.get('note') or '')[:60]}”"
                          for r in todo[:2])
        more = f" (+{len(todo) - 2} more)" if len(todo) > 2 else ""
        parts.append(f"⚠️ {len(todo)} operator CORRECTION(s) unaddressed — the reader disputes "
                     f"the report: {heads}{more} — re-examine each claim against the data, then "
                     f"mark that line resolved:true in feedback.{name}.jsonl")
    soft = [r for r in rows if r.get("kind") in ("confusion", "readability", "unclassified")
            and not r.get("resolved")]
    if soft:
        kinds = {}
        for r in soft:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        parts.append("🖍 reader feedback pending ("
                     + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
                     + f"): read feedback.{name}.jsonl, apply each action (explain / fix the "
                     f"report), then mark resolved:true")
    return "  ·  ".join(parts)


def brief(name=None):
    """Combined one-line goal warnings for the compass / report — MEANS-first framing, a MECHANICAL
    main thread, SPEC-PROSE goal text, a MISSING purpose, unaddressed operator CORRECTIONS, AND
    scope drift, each self-labeled; '' when everything is clean."""
    parts = []
    for w in (means_first(name), mechanical_main_thread(name), spec_prose(name),
              missing_purpose(name), unaddressed_corrections(name)):
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
