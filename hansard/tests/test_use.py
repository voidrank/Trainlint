#!/usr/bin/env python3
"""session-project-lock ▸ /hansard:use — the explicit session<->project bind (research/use.py).

Sticky + explicit switch: bind THIS session to a project, stamp its home, write the session lock,
and (transitionally) the global. Throwaway TRAINLINT_DATA_DIR so it never touches real data.
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["TRAINLINT_DATA_DIR"] = str(_TMP)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))
import paths  # noqa: E402
import use    # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        fails += 1


# a registered project to switch to
paths.wfile("project.proj-x.json").write_text('{"_comment":"x"}', encoding="utf-8")

# 1. binding an UNregistered project is refused (switch, not create)
ok, msg = use.bind("ghost", session_id="s1")
check(not ok and "no project" in msg, "binding an unregistered project is refused")

# 2. bind with an explicit session id -> session lock + home set (NO global write)
ok, msg = use.bind("proj-x", home="/home/x/proj-x", session_id="s1")
check(ok, "bind succeeds for a registered project")
rec = paths.read_session_lock("s1")
check(rec and rec["project"] == "proj-x" and rec["bound_by"] == "use", "session lock written (bound_by=use)")
check(paths.project_home("proj-x") == "/home/x/proj-x", "home stamped")
check(not (_TMP / ".active-project").exists(), "NO global .active-project is written (remove-global)")

# 3. session ISOLATION — a second session binds elsewhere without touching the first
paths.wfile("project.proj-y.json").write_text('{"_comment":"y"}', encoding="utf-8")
use.bind("proj-y", home="/home/x/proj-y", session_id="s2")
check(paths.read_session_lock("s1")["project"] == "proj-x"
      and paths.read_session_lock("s2")["project"] == "proj-y",
      "two sessions bound to different projects, no clobber")

# 4. STICKY + explicit switch — rebinding the SAME session moves only it
use.bind("proj-y", session_id="s1")
check(paths.read_session_lock("s1")["project"] == "proj-y", "explicit re-bind switches this session")

# 5. default home = cwd when none given / none stamped
paths.wfile("project.proj-z.json").write_text('{"_comment":"z"}', encoding="utf-8")
use.bind("proj-z", session_id="s3")
check(paths.project_home("proj-z") == os.getcwd(), "home defaults to cwd when unset and no --home")

# 6. env fallback — no explicit session id uses $CLAUDE_CODE_SESSION_ID
os.environ["CLAUDE_CODE_SESSION_ID"] = "envsess"
use.bind("proj-x")
check(paths.read_session_lock("envsess") and paths.read_session_lock("envsess")["project"] == "proj-x",
      "session id read from $CLAUDE_CODE_SESSION_ID when not passed")

print(f"\n{9 - fails}/9 passed")
sys.exit(1 if fails else 0)
