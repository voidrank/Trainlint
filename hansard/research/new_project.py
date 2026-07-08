#!/usr/bin/env python3
"""Register a NEW project — a THIN scaffolder. It only creates the empty per-project
substrate and sets the project active. It does NOT make you fill a pile of TODO fields.

  python3 new_project.py <name>

The facts that used to be a TODO ceremony here (the doorman's danger patterns in
project.<name>.json, the research layer's runs_glob/direction_regex in facts.<name>.json)
are now filled by `/hansard:plan` while it establishes the project's full context —
because that step reads the actual code anyway, which is the only honest way to know them.
Until then the files are empty stubs and the doorman simply stays silent on this project
(empty facts -> placeholders no-match, never a crash).
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402  — per-project data lives outside the versioned plugin dir

# Empty stub — /hansard:plan fills the danger-pattern keys from the real code.
ACTION_FACTS = {
    "_comment": "Action-rule facts for <name>. EMPTY — /hansard:plan fills these while "
                "establishing context (it reads the project to learn the danger patterns). The "
                "doorman stays silent on this project until then. See project.example.json for the "
                "full key shape (bad_storage_re, locked_configs_re, preproc_trap_re, ...)."
}

# Minimal research facts: sensible thresholds + empty trace-reading keys. Empty runs_glob/
# direction_regex make the research-lint degrade cleanly to an empty tree (no crash).
RESEARCH_FACTS = {
    "_comment": "Research facts for <name> — /hansard:plan fills runs_glob/direction_regex/"
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
        try:
            rel = p.relative_to(ROOT)
        except ValueError:
            rel = p
        print("wrote:", rel)

    w(paths.wfile(f"project.{name}.json"), json.dumps(ACTION_FACTS, ensure_ascii=False, indent=2))
    # stamp the project's HOME = where it's being registered from (the context->project link the
    # per-session resolver maps cwd/touched-paths against). Idempotent + preserves other keys, so
    # re-registering just re-stamps home. os.getcwd() is overridable via $TRAINLINT_PROJECT_HOME.
    paths.set_project_home(name, os.environ.get("HANSARD_PROJECT_HOME") or os.environ.get("TRAINLINT_PROJECT_HOME", "").strip() or os.getcwd())
    w(paths.wfile(f"facts.{name}.json"), json.dumps(RESEARCH_FACTS, ensure_ascii=False, indent=2))
    w(paths.wfile(f"knowledge.{name}.jsonl"),
      "# papers/refs indexed by the PROBLEM they solve. one JSON object per line.\n"
      "# fields: id | title | problem | concepts[] | prereqs[] | match[] (wall keywords) | read(bool)\n")
    w(paths.wfile(f"log.{name}.jsonl"),
      "# durable append-only annotation log (harvested from sessions). starts empty.\n")
    w(paths.wfile(f"plan.{name}.jsonl"),
      "# Project PLAN: the ordered DECISIONS that define this run, each tagged with the\n"
      "# transferable PRINCIPLE that governs it. Draft it with /hansard:plan. See plan.example.jsonl.\n"
      "# fields: id | phase | decision | choice | principle | why | status(open|decided|verified) | match(regex)\n"
      "#         + artifact (path/glob the choice produced -> BUILT) + anchors (the REVIEWABLE code:\n"
      "#         \"file:start-end@commit\" — record with research/anchor.py; \"paper\" = prose-only)\n")
    w(paths.wfile(f"goal.{name}.txt"), "")
    # motivation.<name>.txt — the optional "why this matters" beat the viz report leads with at
    # the PLANNING stage (before any experiment). Empty stub: viz omits the beat until /plan
    # fills it. Must stay empty (viz renders the whole file as prose — a comment would show up).
    w(paths.wfile(f"motivation.{name}.txt"), "")
    # bind THIS session to the new project (session-project-lock) — NOT a global .active-project write.
    # home was stamped above; the lock is keyed by $CLAUDE_CODE_SESSION_ID so concurrent registrations
    # in other sessions don't clobber. If there's no session id (a bare CLI run), the project is still
    # reachable by cwd-inference (its home is stamped) or an explicit /hansard:use.
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    home = os.environ.get("HANSARD_PROJECT_HOME") or os.environ.get("TRAINLINT_PROJECT_HOME", "").strip() or os.getcwd()
    if sid:
        paths.write_session_lock(sid, name, home=home, bound_by="plan")
    print(f"\nregistered '{name}' and bound it to this session. Empty substrate created — nothing to "
          f"hand-fill.\nNext: run `/hansard:plan` — it establishes the full project context, "
          f"fills the facts files from the real code, drafts the decisions, then quizzes you.")


if __name__ == "__main__":
    main()
