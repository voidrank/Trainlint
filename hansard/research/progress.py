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
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(ROOT))
import paths  # noqa: E402  — per-project data lives outside the versioned plugin dir
STATE = paths.state_dir()

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


if __name__ == "__main__":
    # CLI used by the hard gate's clear-loop: after the agent quizzes the user on a high-stakes
    # decision (and they pass, or explicitly skip), it runs `python3 progress.py mark <id>` to
    # record mastery — which is the ONLY thing that clears the gate. Usage:
    #   python3 progress.py mark <decision-id>            (active project)
    #   python3 progress.py mark <project> <decision-id>
    import sys
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "mark":
        sys.path.insert(0, str(ROOT))
        import plan as planlib
        name, did = (planlib._active(), a[1]) if len(a) == 2 else (a[1], a[2])
        node = planlib.by_id(planlib.load(name), did)
        if not node:
            print(f"no decision '{did}' in the plan for project '{name}'")
            sys.exit(1)
        mark(name, node, mastered=True)
        print(f"✓ marked '{did}' mastered for '{name}' — the hard gate will now let it through")
    else:
        print("usage: python3 progress.py mark [<project>] <decision-id>")
