#!/usr/bin/env python3
"""Project PLAN — the ordered list of DECISIONS that defines a project, and the
"floor plan" both machines consume.

Until now Trainlint only ever saw individual tool calls and matched keywords in
whatever file was being touched. The mistakes that actually cost weeks are not
keystrokes — they are DECISIONS (which ckpt to bootstrap, how to eval, what the
loss weights are). The plan is the missing representation of those decisions.

It feeds two distinct machines off ONE artifact:
  - the plan-quiz walks these decisions to teach the operator (deliberate, offline);
  - the plan-aware doorman LOCATES a live action on this plan and routes/escalates
    by the decision's state + governing principle (online) — instead of keyword spray.

Authored by the agent (drafted from goal+facts) and confirmed by the user; durable.
One JSONL line per decision:
  id | phase | decision | choice | principle | why | status | match?
    status   : open (undecided) | decided (chosen + rationale) | verified (checked it holds)
    principle: the governing law id — links to quiz.jsonl principles + the rule layer
    match    : regex recognising an action that touches this decision (used by the doorman)

Pure & read-only. Degrades to [] when a project has no plan yet.
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

STATUSES = ("open", "decided", "verified")


def _active(name=None):
    if name:
        return name
    n = os.environ.get("HARNESS_PROJECT", "").strip()
    if n:
        return n
    try:
        t = (ROOT.parent / ".active-project").read_text(encoding="utf-8").strip()
        if t:
            return t
    except Exception:
        pass
    return ""


def load(name=None):
    """Ordered list of plan decision nodes. [] if there is no plan file."""
    name = _active(name)
    p = ROOT / f"plan.{name}.jsonl"
    rows = []
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


def by_id(plan, pid):
    for n in plan:
        if n.get("id") == pid:
            return n
    return None


def locate(haystack, plan=None, name=None):
    """Which plan decisions does this action text touch? (its `match` regex hit).
    This is the bridge the plan-aware doorman uses: action text -> decision(s)."""
    if plan is None:
        plan = load(name)
    if not haystack:
        return []
    out = []
    for n in plan:
        m = n.get("match")
        if not m:
            continue
        try:
            if re.search(m, haystack, re.IGNORECASE):
                out.append(n)
        except re.error:
            continue
    return out


def summary(plan=None, name=None):
    """Counts by status + the still-risky decisions, for briefings / viz."""
    if plan is None:
        plan = load(name)
    counts = {s: 0 for s in STATUSES}
    for n in plan:
        counts[n.get("status", "open")] = counts.get(n.get("status", "open"), 0) + 1
    return {
        "total": len(plan),
        "counts": counts,
        "open": [n for n in plan if n.get("status", "open") == "open"],
        "unverified": [n for n in plan if n.get("status") == "decided"],
    }


def main_thread(plan=None, name=None):
    """The single driving decision — the 'main thread' the work should focus on right now:
    the load-bearing OPEN decision if one is marked (the one that most gates the plan / the
    cheapest test that could invalidate it), else the first open, else the first decided-but-
    unverified. None when everything is verified. This is what keeps work focused instead of a
    flat menu of every decision."""
    if plan is None:
        plan = load(name)
    opens = [n for n in plan if n.get("status", "open") == "open"]
    lb = [n for n in opens if n.get("load_bearing")]
    if lb:
        return lb[0]
    if opens:
        return opens[0]
    unver = [n for n in plan if n.get("status") == "decided"]
    return unver[0] if unver else None


def pillars(plan=None, name=None):
    """The project's CORE dimensions — decisions marked `pillar: true`. The compass shows these
    EVERY turn, EVEN WHEN they're decided, so a core dimension (e.g. the text/audio interleave)
    can't vanish from view just because its decision is settled. Unlike main_thread (the one open
    thing to drive NEXT), pillars are about what the project fundamentally IS — they keep the whole
    shape in view instead of collapsing it to a single thread or a one-line goal."""
    if plan is None:
        plan = load(name)
    return [n for n in plan if n.get("pillar")]


def avoided(plan=None, name=None):
    """The explicitly REJECTED options (anti-prior decisions) the agent must not drift back into.
    A decision pins one with two fields:
      not_this: human-readable rejected option (e.g. "use the reference codebase's pipeline as the impl")
      not_re:   regex that recognizes an action DRIFTING toward it (specific to the rejected
                *usage*, not the legitimate reference — so 'borrow the reference recipe' doesn't trip it)
    Used by the compass (ambient reminder) + the plan-aware doorman (action-level catch)."""
    if plan is None:
        plan = load(name)
    return [{"id": n.get("id", ""), "not_this": n.get("not_this", ""),
             "choice": n.get("choice", ""), "not_re": n.get("not_re", ""), "why": n.get("why", "")}
            for n in plan if n.get("not_re") and n.get("not_this")]


def brief(name=None):
    """One-line plan status, or '' if no plan exists for this project."""
    plan = load(name)
    if not plan:
        return ""
    c = summary(plan)["counts"]
    return (f"plan: {len(plan)} decisions "
            f"({c.get('verified', 0)} verified / {c.get('decided', 0)} decided / "
            f"{c.get('open', 0)} open)")


if __name__ == "__main__":
    import sys
    nm = sys.argv[1] if len(sys.argv) > 1 else None
    pl = load(nm)
    if not pl:
        print(f"(no plan for project '{_active(nm)}' — draft one with /trainlint:plan)")
        sys.exit(0)
    # Compact, phase-grouped MAP — the "overview" layer of the plan report (Shneiderman: overview
    # first). Scannable by id + status icon; details stay on demand (read plan.<name>.jsonl / quiz).
    mt = main_thread(pl)
    head = brief(nm)
    if mt:
        head += f"  ·  main thread → {mt.get('id','')}"
    print(head)
    ICON = {"verified": "✓", "decided": "●", "open": "○"}
    width = max((len(n.get("id", "")) for n in pl), default=0)
    phases = []  # phases in first-appearance order — no hard-coded list, generic across projects
    for n in pl:
        if n.get("phase", "") not in phases:
            phases.append(n.get("phase", ""))
    for ph in phases:
        print(f"\n  {ph}")
        for n in pl:
            if n.get("phase", "") != ph:
                continue
            mark = ICON.get(n.get("status", "open"), "?")
            tags = ("★" if n.get("load_bearing") else " ") + ("◆" if n.get("pillar") else "")
            print(f"    {mark} {n.get('id',''):<{width}}  {tags}")
    print("\n  ●=decided ○=open ✓=verified · ★=main thread ◆=pillar"
          "  ·  detail: read research/plan.<name>.jsonl or /trainlint:plan")
