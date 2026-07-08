#!/usr/bin/env python3
"""Research-lint entrypoint — rebuild the frontier tree from traces, run the two
read-only lints, print SHAPE hints. Coach-only: shows the search shape, never prescribes.

  python3 lint.py [project]            # full report (on demand)
  python3 lint.py --brief [project]    # SessionStart hook: only actionable hints, else silent

It runs as an OS subprocess (a subagent off the main trajectory); only this distilled
text is appended to the conversation, never the check machinery. For the full picture,
use viz.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree        # noqa: E402
import governor    # noqa: E402
import surfacer    # noqa: E402
import paths       # noqa: E402  — per-project data lives outside the versioned plugin dir


def _load(name):
    name = tree._active(name)
    facts = tree.load_facts(name)
    nodes = tree.build_tree(tree.load_events(name, facts), facts)
    know = tree._load_jsonl(paths.resolve(f"knowledge.{name}.jsonl"))
    return name, facts, nodes, know


def run(name=None):
    name, facts, nodes, know = _load(name)
    out = [f"# research-lint ({name}) — {len(nodes)} directions reconstructed", "",
           "## directionality (inward · shape only)"]
    out += ["- " + h for h in governor.report(nodes, facts)]
    out += ["", "## readiness (outward · just hints what to read)"]
    out += ["- " + h for h in surfacer.report(nodes, know)]
    return "\n".join(out)


def brief(name=None):
    """Only the ACTIONABLE bits — stalled branches + ready-to-read papers. Empty string
    when there's nothing to say (so SessionStart stays silent unless there's a real hint)."""
    name, facts, nodes, know = _load(name)
    lines = []
    for n in sorted(nodes.values(), key=lambda x: -x["spend"]):
        if n["status"] == "stalled":
            lines.append(f"'{n['direction']}' stalled ({n['spend']} runs, flat) — check the trunk first")
    seen = set()
    for n in nodes.values():
        for w in n.get("walls", []):
            for k in know:
                if k.get("read") or k["id"] in seen:
                    continue
                if any(str(m).lower() in w.lower() for m in k.get("match", [])):
                    seen.add(k["id"])
                    lines.append(f"now readable: {k['title']} (wall: {w[:34]})")
    if not lines:
        return ""
    return (f"[research-lint:{name}] " + " | ".join(lines)
            + "  (hints only — full picture: research/viz.py)")


if __name__ == "__main__":
    # Safe as a SessionStart hook: any error → print nothing, exit 0.
    try:
        args = sys.argv[1:]
        is_brief = "--brief" in args
        proj = next((a for a in args if not a.startswith("-")), None)
        text = brief(proj) if is_brief else run(proj)
        if text:
            print(text)
    except Exception:
        pass
    sys.exit(0)
