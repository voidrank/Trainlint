#!/usr/bin/env python3
"""hansard-in-repo ▸ <home>/.hansard/ routing (research/paths.py).

A project's memory files (<kind>.<name>.<ext>) live INSIDE the project at <home>/.hansard/
so git versions them with the code. project.<name>.json (the registry holding `home`) stays
in the global data dir. Throwaway TRAINLINT_DATA_DIR + throwaway homes — never touches real data.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["TRAINLINT_DATA_DIR"] = str(_TMP)
for k in ("HARNESS_PROJECT", "CLAUDE_CODE_SESSION_ID"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))
import paths  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        fails += 1


home = _TMP / "repos" / "projX"
home.mkdir(parents=True)
paths.wfile("project.projX.json").write_text('{"_c":"x"}', encoding="utf-8")
paths.set_project_home("projX", str(home))

# 1. wfile routes a registered project's memory file into <home>/.hansard/
w = paths.wfile("plan.projX.jsonl")
check(w == home / ".hansard" / "plan.projX.jsonl", f"wfile routes to <home>/.hansard ({w})")
w.write_text('{"id":"d1"}\n', encoding="utf-8")

# 2. resolve prefers the .hansard copy
check(paths.resolve("plan.projX.jsonl") == w, "resolve prefers <home>/.hansard")

# 3. the registry itself is NOT routed — project.<name>.json stays in the data dir
check(paths.wfile("project.projX.json") == _TMP / "project.projX.json",
      "project.<name>.json stays in the global data dir")

# 4. a not-yet-created routable file: resolve and wfile agree on the .hansard target
check(paths.resolve("knowledge.projX.jsonl") == home / ".hansard" / "knowledge.projX.jsonl",
      "resolve of a not-yet-created file returns the .hansard write target")

# 5. move-on-first-write: a pre-existing data-dir copy is MOVED (content preserved, no fork)
old = _TMP / "log.projX.jsonl"
old.write_text("# old history\n", encoding="utf-8")
check(paths.resolve("log.projX.jsonl") == old, "pre-migration read falls back to the data dir")
w = paths.wfile("log.projX.jsonl")
check(w == home / ".hansard" / "log.projX.jsonl" and not old.exists()
      and w.read_text(encoding="utf-8") == "# old history\n",
      "first wfile MOVES the data-dir copy into .hansard (history continues, no fork)")

# 6. an UNREGISTERED project name does not route
check(paths.wfile("plan.nosuch.jsonl") == _TMP / "plan.nosuch.jsonl",
      "unregistered project stays in the data dir")

# 6b. board substrate never routes — agent_board reads data_root()/tasks.<board>.jsonl directly
check(paths.wfile("tasks.projX.jsonl") == _TMP / "tasks.projX.jsonl",
      "tasks.<board>.jsonl stays in the data dir (agent_board bypasses resolve)")

# 7. 'example' never routes (excluded from the homes table)
paths.wfile("project.example.json").write_text('{"_c":"e"}', encoding="utf-8")
paths.set_project_home("example", str(home))
check(paths.wfile("log.example.jsonl") == _TMP / "log.example.jsonl",
      "'example' fixtures never route into a real home")

# 8. a registered project whose home DIR is gone falls back to the data dir
ghost = _TMP / "repos" / "ghost"
ghost.mkdir()
paths.wfile("project.ghost.json").write_text("{}", encoding="utf-8")
paths.set_project_home("ghost", str(ghost))
ghost.rmdir()
check(paths.wfile("plan.ghost.jsonl") == _TMP / "plan.ghost.jsonl",
      "missing home dir -> fail-open to the data dir")

# 9. bulk migrate moves the rest, skips existing destinations, ignores the registry
(_TMP / "goal.projX.txt").write_text("the goal\n", encoding="utf-8")
(_TMP / "glossary.projX.jsonl").write_text('{"term":"t"}\n', encoding="utf-8")
(home / ".hansard" / "glossary.projX.jsonl").write_text("# already here\n", encoding="utf-8")
n = paths.migrate(["projX"])
check(n == 1 and (home / ".hansard" / "goal.projX.txt").read_text(encoding="utf-8") == "the goal\n",
      "migrate moves data-dir files into .hansard")
check((_TMP / "glossary.projX.jsonl").exists()
      and (home / ".hansard" / "glossary.projX.jsonl").read_text(encoding="utf-8") == "# already here\n",
      "migrate never overwrites an existing .hansard copy (skip, source kept)")
check((_TMP / "project.projX.json").exists(), "migrate leaves the registry in the data dir")

# 10. cwd-inference still works with in-repo memory (regression: routing must not break it)
os.environ["CLAUDE_CODE_SESSION_ID"] = "sessHD"
_prev = os.getcwd()
try:
    os.chdir(home)
    check(paths.active_project() == "projX", "cwd inference unaffected by .hansard routing")
finally:
    os.chdir(_prev)

print()
sys.exit(1 if fails else 0)
