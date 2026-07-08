#!/usr/bin/env python3
"""Kimi shim tests — tool normalization (Shell/WriteFile/StrReplaceFile -> Claude
shapes) and the block-only output adaptation (escalate-by-block, coach dropped).
"""
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import kimi_compat  # noqa: E402
import prefilter    # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


# --- 1. tool normalization ---
d = kimi_compat.normalize({"hook_event_name": "PreToolUse", "tool_name": "StrReplaceFile",
                           "tool_input": {"path": "/home/u/mimo/train.py",
                                          "edit": {"old": "power = 1.0", "new": "power = 2.0"}}})
check(d["tool_name"] == "Edit", "StrReplaceFile -> Edit")
check(d["tool_input"]["file_path"] == "/home/u/mimo/train.py", "path -> file_path")
check("power = 2.0" in d["tool_input"]["new_string"], "edit.new -> new_string")

dl = kimi_compat.normalize({"hook_event_name": "PreToolUse", "tool_name": "StrReplaceFile",
                            "tool_input": {"path": "/x/y.py",
                                           "edit": [{"old": "a", "new": "n1"}, {"old": "b", "new": "n2"}]}})
check("n1" in dl["tool_input"]["new_string"] and "n2" in dl["tool_input"]["new_string"], "list[Edit] -> all news in new_string")

w = kimi_compat.normalize({"hook_event_name": "PreToolUse", "tool_name": "WriteFile",
                           "tool_input": {"path": "/x/z.py", "content": "import torch"}})
check(w["tool_name"] == "Write" and w["tool_input"]["file_path"] == "/x/z.py", "WriteFile -> Write, path -> file_path")

s = kimi_compat.normalize({"hook_event_name": "PreToolUse", "tool_name": "Shell",
                           "tool_input": {"command": "python train.py"}})
check(s["tool_name"] == "Bash" and s["tool_input"]["command"] == "python train.py", "Shell -> Bash, command kept")

check(prefilter.classify_action(d) == "inspect", "normalized edit reaches checks (inspect)")

# no-op on a Claude/Codex edit
nat = kimi_compat.normalize({"tool_name": "Edit", "tool_input": {"file_path": "/a", "new_string": "x"}})
check(nat["tool_name"] == "Edit", "native Edit untouched")

# --- 2. block-only output adaptation ---
# reject (deny) -> kept
out = kimi_compat.adapt_for_kimi({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                  "permissionDecision": "deny", "permissionDecisionReason": "no jfs"}})
check(out["hookSpecificOutput"]["permissionDecision"] == "deny", "reject -> deny kept")
check(out["hookSpecificOutput"]["permissionDecisionReason"] == "no jfs", "reject reason kept")

# escalate (systemMessage) -> escalate-by-block
esc = kimi_compat.adapt_for_kimi({"systemMessage": "⚠️ check this",
                                  "hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                         "additionalContext": "ctx"}})
check(esc["hookSpecificOutput"]["permissionDecision"] == "deny", "escalate -> deny (escalate-by-block)")
check("check this" in esc["hookSpecificOutput"]["permissionDecisionReason"], "escalate message -> block reason")

# report-doorman block (Stop) -> deny
rep = kimi_compat.adapt_for_kimi({"decision": "block", "reason": "rewrite the report"})
check(rep["hookSpecificOutput"]["permissionDecision"] == "deny", "Stop decision:block -> deny")
check(rep["hookSpecificOutput"]["permissionDecisionReason"] == "rewrite the report", "Stop reason carried")

# coach only -> dropped
coach = kimi_compat.adapt_for_kimi({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                                           "additionalContext": "compass…"}})
check(coach is None, "coach (additionalContext only) -> dropped on Kimi")

check(kimi_compat.adapt_for_kimi(None) is None, "None -> None")

print(("\nFAILED %d" % fails) if fails else "\nall kimi-compat tests passed")
sys.exit(1 if fails else 0)
