#!/usr/bin/env python3
"""Research-lint entrypoint — rebuild the frontier tree from traces, run the two
read-only lints, print SHAPE hints. Coach-only: shows the search shape, never prescribes.

Run on a schedule (cron / a Claude Code routine) or on demand:
    python3 lint.py [project]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree        # noqa: E402
import governor    # noqa: E402
import surfacer    # noqa: E402


def run(name=None):
    name = tree._active(name)
    facts = tree.load_facts(name)
    nodes = tree.build_tree(tree.load_events(name, facts), facts)
    know = tree._load_jsonl(Path(__file__).resolve().parent / f"knowledge.{name}.jsonl")
    out = [f"# research-lint ({name}) — {len(nodes)} directions reconstructed", "",
           "## 方向性(对内·只照形状)"]
    out += ["- " + h for h in governor.report(nodes, facts)]
    out += ["", "## 就绪度(对外·只提示该读啥)"]
    out += ["- " + h for h in surfacer.report(nodes, know)]
    return "\n".join(out)


if __name__ == "__main__":
    # Safe for use as a SessionStart hook: any error → print nothing, exit 0.
    try:
        arg = sys.argv[1] if (len(sys.argv) > 1 and not sys.argv[1].startswith("-")) else None
        print(run(arg))
    except Exception:
        pass
    sys.exit(0)
