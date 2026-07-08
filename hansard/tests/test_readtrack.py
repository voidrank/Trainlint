#!/usr/bin/env python3
"""Read-before-edit tracker tests — editing code you haven't read gets a coach."""
import os
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import router      # noqa: E402
import readtrack   # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def _ctx(out):
    return ((out or {}).get("hookSpecificOutput", {}) or {}).get("additionalContext", "")


SESS = "_test_readtrack"
F = "/tmp/hansard_rbe_test.py"          # outside the plugin root (not a self-edit)
Path(F).write_text("x = 1\n", encoding="utf-8")
ED = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "session_id": SESS,
      "tool_input": {"file_path": F, "new_string": "x = 2"}}

# 1. editing an existing file not yet read -> coach
check("read it this session" in _ctx(router.decide(ED)),
      "editing an existing file you haven't read -> read-before-edit coach")

# 2. after reading it, the same edit is silent on that file
readtrack.record({"hook_event_name": "PreToolUse", "tool_name": "Read", "session_id": SESS,
                  "tool_input": {"file_path": F}})
check("read it this session" not in _ctx(router.decide(ED)),
      "after reading the file, no read-before-edit coach")

# 3. creating a NEW (nonexistent) file is fine — nothing to have read
NEW = {"hook_event_name": "PreToolUse", "tool_name": "Write", "session_id": SESS,
       "tool_input": {"file_path": "/tmp/hansard_brand_new_xyz.py", "content": "x"}}
check("read it this session" not in _ctx(router.decide(NEW)),
      "writing a brand-new file -> no read-before-edit coach")

# cleanup
for p in (F, HOOKS.parent / "research" / ".state" / f"reads.{SESS}.txt"):
    try:
        os.remove(p)
    except OSError:
        pass

print(f"\n{3 - fails}/3 passed")
sys.exit(1 if fails else 0)
