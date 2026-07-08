#!/usr/bin/env python3
"""session-project-lock ▸ the HOME field (paths.project_home / set_project_home).

The context->project link: each project records the directory it belongs to, so the per-session
resolver can map a cwd or a touched path back to a project. Tests the read/write API in isolation
and that new_project stamps home at registration. Uses a throwaway TRAINLINT_DATA_DIR.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["TRAINLINT_DATA_DIR"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))
import paths  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        fails += 1


# 1. unstamped project -> '' (no crash)
check(paths.project_home("nope") == "", "unstamped project reads home as ''")

# 2. set then read
p = paths.set_project_home("proj-a", "/home/x/proj-a")
check(p is not None and Path(p).exists(), "set_project_home writes project.<name>.json")
check(paths.project_home("proj-a") == "/home/x/proj-a", "project_home round-trips the stamped dir")

# 3. stamping home PRESERVES other keys (the doorman danger patterns /plan fills)
pj = _TMP / "project.proj-b.json"
pj.write_text(json.dumps({"bad_storage_re": "np.save", "_comment": "keep me"}), encoding="utf-8")
paths.set_project_home("proj-b", "/home/x/proj-b")
d = json.loads(pj.read_text(encoding="utf-8"))
check(d.get("home") == "/home/x/proj-b" and d.get("bad_storage_re") == "np.save"
      and d.get("_comment") == "keep me",
      "stamping home preserves every other key")

# 4. re-stamp overwrites just home (idempotent)
paths.set_project_home("proj-a", "/home/x/moved")
check(paths.project_home("proj-a") == "/home/x/moved", "re-stamp replaces home in place")

# 5. empty home is a no-op (don't wipe a real link with a blank)
check(paths.set_project_home("proj-a", "") is None
      and paths.project_home("proj-a") == "/home/x/moved",
      "empty home is a no-op, existing home untouched")

# 6. new_project stamps home = TRAINLINT_PROJECT_HOME (or cwd) at registration
os.environ["TRAINLINT_PROJECT_HOME"] = "/home/x/registered-here"
import subprocess
np = Path(__file__).resolve().parent.parent / "research" / "new_project.py"
subprocess.run([sys.executable, str(np), "proj-reg"], capture_output=True,
               env={**os.environ}, check=False)
check(paths.project_home("proj-reg") == "/home/x/registered-here",
      "new_project.py stamps home at registration")

print(f"\n{7 - fails}/7 passed")
sys.exit(1 if fails else 0)
