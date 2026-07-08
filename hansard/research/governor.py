#!/usr/bin/env python3
"""Directionality lint (inward) — shows the search SHAPE, never prescribes.

It surfaces, per notable branch: status, spend, recent marginal gains, walls hit,
explored siblings, missing trunk-checks, and the unexplored candidate moves. It NEVER
says "abandon" — research is non-monotonic; a plateau can precede a breakthrough.
Coach-only: correct the biases of unsupervised search (sunk-cost over-DFS, novelty
over-BFS) with INFORMATION, not control. The judgment stays with the agent.
"""


def report(nodes, facts):
    facts = facts or {}
    K = facts.get("thresholds", {}).get("window_K", 3)
    trunk_required = facts.get("trunk_checks", [])
    candidates = set(facts.get("candidate_moves", []))
    explored = set(nodes.keys())
    hints = []

    for n in sorted(nodes.values(), key=lambda x: -x["spend"]):
        if n["status"] == "open":          # only a wall, no experiments yet — nothing to shape
            continue
        deltas = n["deltas"][-K:]
        line = (f"[research-lint:directionality] [{n['direction']}] status={n['status']} · {n['spend']} run(s)"
                + (f" · last {len(deltas)} gains {deltas}" if deltas else "")
                + (f" · walls {n['walls']}" if n["walls"] else ""))
        if n["status"] == "stalled":
            missing = [c for c in trunk_required if c not in n["trunk"]]
            if missing:
                line += (f" · WARN it flattened, but trunk-checks not recorded {missing} — "
                         f"don't sentence this branch to death on a possibly-contaminated trunk "
                         f"(the root cause may not be on this branch)")
        if n["siblings"]:
            line += f" · siblings explored {n['siblings']}"
        hints.append(line)

    unexplored = sorted(candidates - explored)
    if unexplored:
        hints.append(f"[research-lint:directionality] unexplored candidate moves: {unexplored}")
    hints.append("(This is the current search shape; the judgment is yours — the lint only "
                 "shows the shape, it never prunes for you.)")
    return hints
