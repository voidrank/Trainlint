#!/usr/bin/env python3
"""Plan-quiz coverage/mastery state — so "quiz after any plan change" only ever
drills the decisions that are NEW or CHANGED or not-yet-mastered, never the ones
you've already shown you understand.

State lives in research/.state/<name>.plan-progress.json (gitignored):
  { "<decision-id>": {"fp": "<fingerprint>", "mastered": true}, ... }

`fp` is a fingerprint of the decision's content; if the decision is later edited
(its question/choice/principle/status changes), the fingerprint changes and it
re-enters the target set. Pure helpers; all writes are best-effort, fail-open.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".state"

_KEYS = ("id", "decision", "choice", "principle", "status")


def fingerprint(node):
    """Content hash of a decision — changes when the decision is edited."""
    key = "|".join(str(node.get(k, "")) for k in _KEYS)
    return hashlib.md5(key.encode("utf-8")).hexdigest()[:12]


def _path(name):
    return STATE / f"{name}.plan-progress.json"


def load(name):
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(name, prog):
    try:
        STATE.mkdir(exist_ok=True)
        _path(name).write_text(json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def targets(plan, prog=None, name=None):
    """Decisions that still need (re)quizzing: a new id, a changed fingerprint, or
    not yet mastered. Ordered as the plan is. This is what "quiz after any plan
    change" walks — mastered+unchanged decisions are skipped."""
    if prog is None:
        prog = load(name)
    out = []
    for n in plan:
        did = n.get("id")
        if not did:
            continue
        rec = prog.get(did)
        if rec is None or rec.get("fp") != fingerprint(n) or not rec.get("mastered"):
            out.append(n)
    return out


def mark(name, node, mastered=True):
    """Record that a decision has been walked (mastered or not). Stamps its current
    fingerprint, so editing the decision later re-opens it for quizzing."""
    prog = load(name)
    did = node.get("id")
    if did:
        prog[did] = {"fp": fingerprint(node), "mastered": bool(mastered)}
        save(name, prog)
    return prog
