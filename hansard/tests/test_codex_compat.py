#!/usr/bin/env python3
"""Codex apply_patch shim tests — the envelope must become Claude-style Edit
tool_input so the unchanged pipeline (prefilter/checks/readtrack) can read it.
"""
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import codex_compat  # noqa: E402
import prefilter     # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


PATCH = (
    "*** Begin Patch\n"
    "*** Update File: /home/user/proj/train.py\n"
    "@@\n"
    "-old = 1\n"
    "+power = 2.0  # mel power\n"
    "*** Add File: /home/user/proj/new.py\n"
    "+import torch\n"
    "*** End Patch\n"
)

# 1. first-class apply_patch tool -> normalized to Edit with paths + added content
d = codex_compat.normalize(
    {"hook_event_name": "PreToolUse", "tool_name": "apply_patch",
     "tool_input": {"input": PATCH}})
check(d["tool_name"] == "Edit", "apply_patch -> tool_name Edit")
check(d["tool_input"]["files"] == ["/home/user/proj/train.py", "/home/user/proj/new.py"],
      "both target paths extracted")
check("power = 2.0" in d["tool_input"]["new_string"] and "import torch" in d["tool_input"]["new_string"],
      "added lines captured as new_string")
check("-old = 1" not in d["tool_input"]["new_string"], "removed lines are NOT captured")

# 2. Bash-wrapped heredoc form (older Codex) -> also normalized
d2 = codex_compat.normalize(
    {"hook_event_name": "PreToolUse", "tool_name": "Bash",
     "tool_input": {"command": "apply_patch <<'EOF'\n" + PATCH + "EOF\n"}})
check(d2["tool_name"] == "Edit", "Bash heredoc apply_patch -> Edit")
check(d2["tool_input"]["file_path"] == "/home/user/proj/train.py", "heredoc path extracted")

# 3. downstream pipeline now treats it as a code edit -> 'inspect', not dropped
check(prefilter.classify_action(d) == "inspect", "normalized edit reaches checks (inspect)")

# 4. NO-OP on Claude input and on a plain shell command
plain = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": "ls -la"}}
check(codex_compat.normalize(dict(plain))["tool_name"] == "Bash", "plain Bash untouched")
edit = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
        "tool_input": {"file_path": "/x/y.py", "new_string": "z"}}
check(codex_compat.normalize(dict(edit))["tool_input"]["file_path"] == "/x/y.py",
      "native Claude Edit untouched")

print(("\nFAILED %d" % fails) if fails else "\nall codex-compat tests passed")
sys.exit(1 if fails else 0)
