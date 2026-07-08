#!/usr/bin/env python3
"""session-project-lock ▸ the STORE layer (paths.session_lock).

The store half of replacing the global .active-project with a per-session lock: each session
binds its own project, keyed by session_id, so concurrent sessions never clobber. This tests the
storage primitives in isolation (read/write/round-trip/isolation/sanitize) — ADDITIVE code that
active_project() does not consume yet. Uses a throwaway TRAINLINT_DATA_DIR so it never touches
real data.
"""
import os
import sys
import tempfile
from pathlib import Path

# point data_root() at a throwaway dir BEFORE importing paths
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


# 1. unbound session -> None (silent, not a crash)
check(paths.read_session_lock("sess-A") is None, "unbound session reads as None")
check(paths.read_session_lock("") is None, "empty session_id reads as None")

# 2. write then round-trip
p = paths.write_session_lock("sess-A", "asr_rewrite", home="/home/x/asr", bound_by="plan")
check(p is not None and Path(p).exists(), "write_session_lock persists a file")
rec = paths.read_session_lock("sess-A")
check(rec and rec["project"] == "asr_rewrite" and rec["home"] == "/home/x/asr"
      and rec["bound_by"] == "plan" and rec.get("ts"),
      "round-trip returns {project, home, bound_by, ts}")

# 3. the store lives under data_root()/sessions/ (survives version bumps — not in the plugin dir)
check(paths.sessions_dir() == _TMP / "sessions" and Path(p).parent == _TMP / "sessions",
      "lock lives at data_root()/sessions/, outside the versioned plugin dir")

# 4. session ISOLATION — two sessions bind different projects, neither clobbers the other
paths.write_session_lock("sess-B", "session-project-lock", home="/home/x/hansard", bound_by="use")
check(paths.read_session_lock("sess-A")["project"] == "asr_rewrite"
      and paths.read_session_lock("sess-B")["project"] == "session-project-lock",
      "two concurrent sessions hold DIFFERENT projects (no global clobber)")

# 5. explicit rebind overwrites the SAME session's lock (sticky-but-switchable)
paths.write_session_lock("sess-A", "session-project-lock", bound_by="use")
check(paths.read_session_lock("sess-A")["project"] == "session-project-lock",
      "an explicit rebind overwrites the session's own lock")

# 6. a messy session_id can't escape the sessions dir (path-injection safety)
p2 = paths.write_session_lock("../../etc/passwd", "x")
check(p2 is not None and Path(p2).parent == _TMP / "sessions",
      "a path-y session_id is sanitized inside sessions/ (no traversal)")

# 7. missing project -> no write (nothing to bind)
check(paths.write_session_lock("sess-C", "") is None, "writing with empty project is a no-op")

print(f"\n{9 - fails}/9 passed")
sys.exit(1 if fails else 0)
