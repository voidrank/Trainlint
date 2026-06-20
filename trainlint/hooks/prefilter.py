#!/usr/bin/env python3
"""Stage 1 — structural pre-filter. Deterministic, DEFAULT-OPEN.

It does NOT guess topic/intent (that is the model's job in stage 2). It only
answers a cheap, robust structural question: "could this action change anything
or go outward?" Read-only / docs / self-edits are dropped; everything that
mutates code/config/data or sends a file is passed up. When unsure → pass up.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# commands that only READ — safe to drop without ever asking the model
READONLY_BASH = re.compile(
    r'^\s*(ls|cat|head|tail|less|more|bat|grep|rg|ag|find|fd|wc|stat|file|echo|'
    r'pwd|whoami|which|type|tree|du|df|env|printenv|date|nvidia-smi|squeue|sinfo|'
    r'git\s+(status|log|diff|show|branch|remote|rev-parse)|'
    r'python3?\s+-c\s+["\']?\s*print)\b', re.I)

CODE_EXT = re.compile(r'\.(py|json|jsonl|ya?ml|sh|sbatch|toml|cfg|ini|txt|sql)$', re.I)
DOC_EXT = re.compile(r'\.(md|rst)$', re.I)


def _paths(ti):
    out = []
    for k in ("file_path", "path"):
        if ti.get(k):
            out.append(str(ti[k]))
    if isinstance(ti.get("files"), list):
        out.extend(str(x) for x in ti["files"])
    return out


def is_self_edit(ti):
    """Editing the harness's own repo → drop (also avoids the dir name 'train'
    spuriously matching). Resolves symlinks so both real and linked paths match."""
    for t in _paths(ti):
        try:
            rp = Path(t).resolve()
        except Exception:
            continue
        if rp == PLUGIN_ROOT or PLUGIN_ROOT in rp.parents:
            return True
    return False


def classify_action(data):
    """Return 'inspect' (send to checks + classifier) or 'drop' (silent no-op)."""
    event = data.get("hook_event_name", "")

    if event == "UserPromptSubmit":
        return "inspect" if data.get("prompt", "").strip() else "drop"

    if event not in ("PreToolUse", "PostToolUse"):
        return "drop"

    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}

    if is_self_edit(ti):
        return "drop"
    if tool in ("Read", "Grep", "Glob", "NotebookRead", "TodoWrite"):
        return "drop"
    if tool == "Bash":
        return "drop" if READONLY_BASH.match(ti.get("command", "")) else "inspect"
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        paths = _paths(ti)
        # drop only if EVERY target is provably a doc; otherwise default-open
        if paths and all(DOC_EXT.search(p) for p in paths):
            return "drop"
        return "inspect"
    if tool == "SendUserFile":
        return "inspect"           # outward-facing → always inspect

    return "inspect"               # unknown tool → default-open
