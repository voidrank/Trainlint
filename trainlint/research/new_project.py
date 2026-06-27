#!/usr/bin/env python3
"""Register a NEW project — a THIN scaffolder. It only creates the empty per-project
substrate and sets the project active. It does NOT make you fill a pile of TODO fields.

  python3 new_project.py <name>

The facts that used to be a TODO ceremony here (the doorman's danger patterns in
project.<name>.json, the research layer's runs_glob/direction_regex in facts.<name>.json)
are now filled by `/trainlint:plan` while it establishes the project's full context —
because that step reads the actual code anyway, which is the only honest way to know them.
Until then the files are empty stubs and the doorman simply stays silent on this project
(empty facts -> placeholders no-match, never a crash).
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

# Empty stub — /trainlint:plan fills the danger-pattern keys from the real code.
ACTION_FACTS = {
    "_comment": "Action-rule facts for <name>. EMPTY — /trainlint:plan fills these while "
                "establishing context (it reads the project to learn the danger patterns). The "
                "doorman stays silent on this project until then. See project.example.json for the "
                "full key shape (bad_storage_re, locked_configs_re, preproc_trap_re, ...)."
}

# Minimal research facts: sensible thresholds + empty trace-reading keys. Empty runs_glob/
# direction_regex make the research-lint degrade cleanly to an empty tree (no crash).
RESEARCH_FACTS = {
    "_comment": "Research facts for <name> — /trainlint:plan fills runs_glob/direction_regex/"
                "candidate_moves from the real run layout. See research/facts.example.json.",
    "thresholds": {"patience_P": 3, "window_K": 3, "flat_eps": 0.01},
    "runs_glob": "",
    "direction_regex": "",
    "trunk_checks": [],
    "candidate_moves": []
}


def main():
    if len(sys.argv) < 2:
        print("usage: new_project.py <name>")
        sys.exit(2)
    name = sys.argv[1]

    def w(p, s):
        if p.exists():
            print("exists, skip:", p.name)
            return
        p.write_text(s.replace("<name>", name), encoding="utf-8")
        print("wrote:", p.relative_to(ROOT))

    w(ROOT / f"project.{name}.json", json.dumps(ACTION_FACTS, ensure_ascii=False, indent=2))
    w(HERE / f"facts.{name}.json", json.dumps(RESEARCH_FACTS, ensure_ascii=False, indent=2))
    w(HERE / f"knowledge.{name}.jsonl",
      "# papers/refs indexed by the PROBLEM they solve. one JSON object per line.\n"
      "# fields: id | title | problem | concepts[] | prereqs[] | match[] (wall keywords) | read(bool)\n")
    w(HERE / f"log.{name}.jsonl",
      "# durable append-only annotation log (harvested from sessions). starts empty.\n")
    w(HERE / f"plan.{name}.jsonl",
      "# Project PLAN: the ordered DECISIONS that define this run, each tagged with the\n"
      "# transferable PRINCIPLE that governs it. Draft it with /trainlint:plan. See plan.example.jsonl.\n"
      "# fields: id | phase | decision | choice | principle | why | status(open|decided|verified) | match(regex)\n")
    w(HERE / f"goal.{name}.txt", "")
    (ROOT / ".active-project").write_text(name + "\n", encoding="utf-8")
    print(f"\nregistered '{name}' and set it active. Empty substrate created — nothing to "
          f"hand-fill.\nNext: run `/trainlint:plan` — it establishes the full project context, "
          f"fills the facts files from the real code, drafts the decisions, then quizzes you.")


if __name__ == "__main__":
    main()
