#!/usr/bin/env python3
"""Readiness lint (outward) — just-in-time knowledge, gated by your frontier.

It takes the WALLS in the tree (problems you've actually hit) and matches them against
a knowledge library (papers/refs indexed by the PROBLEM they solve + prerequisites).
It surfaces "this may be readable now" — because external knowledge becomes meaningful
only once your own search reaches the matching frontier. Trigger = a wall, NOT recency.
Coach-only: it hints; it never makes you read anything.

A wall is a DUAL signal: the governor's stop-shape AND this surfacer's unlock key.
"""


def report(nodes, knowledge):
    walls = [(n["direction"], w) for n in nodes.values() for w in n.get("walls", [])]
    hints = []
    for direction, w in walls:
        for k in knowledge:
            if k.get("read"):
                continue
            keys = k.get("match", [])
            if any(str(kk).lower() in w.lower() for kk in keys):
                hints.append(
                    f"[research-lint:readiness] wall \"{w}\" (@{direction}) <-> \"{k['title']}\" "
                    f"— it's about exactly {k.get('problem', '')}; prereqs {k.get('prereqs', [])} "
                    f"you likely already have, so it may be readable NOW (reading earlier = cargo-cult).")
    if not hints:
        hints.append("[research-lint:readiness] no current wall matches a knowledge-library entry "
                     "(revisit when you hit a new wall).")
    return hints
