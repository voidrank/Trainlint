#!/usr/bin/env python3
"""Stage 1 — structural pre-filter. Deterministic, DEFAULT-OPEN.

It does NOT guess topic/intent (that is the model's job in stage 2). It only
answers a cheap, robust structural question: "could this action change anything
or go outward?" Read-only / docs / self-edits are dropped; everything that
mutates code/config/data or sends a file is passed up. When unsure → pass up.
"""
import json
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def _plugin_name(root):
    try:
        return json.loads(
            (root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        ).get("name")
    except Exception:
        return None


# this plugin's name, read once — the fingerprint that identifies our OWN source tree
_SELF_NAME = _plugin_name(PLUGIN_ROOT)

# commands that only READ — safe to drop without ever asking the model
READONLY_BASH = re.compile(
    r'^\s*(ls|cat|head|tail|less|more|bat|grep|rg|ag|find|fd|wc|stat|file|echo|'
    r'pwd|whoami|which|type|tree|du|df|env|printenv|date|nvidia-smi|squeue|sinfo|'
    r'git\s+(status|log|diff|show|branch|remote|rev-parse)|'
    r'python3?\s+-c\s+["\']?\s*print)\b', re.I)

CODE_EXT = re.compile(r'\.(py|json|jsonl|ya?ml|sh|sbatch|toml|cfg|ini|txt|sql)$', re.I)
DOC_EXT = re.compile(r'\.(md|rst)$', re.I)

# read-only even when CHAINED: `cd x; grep ...`, `grep x | head`, `cd a && ls`.
# Split on shell separators and require EVERY segment to be a read-only verb
# (a bare `cd` and the data_lint profiler are benign exploration too). A quoted
# separator may mis-split, but that only makes a segment fail -> "inspect" (safe:
# errs toward inspecting, never toward wrongly dropping a write).
_BASH_SEP = re.compile(r'&&|\|\||[;|\n]')
_CD_SEG = re.compile(r'^\s*cd\s+\S+\s*$', re.I)
_PROFILER_SEG = re.compile(r'^\s*python3?\s+\S*data_lint\.py\b', re.I)


def _readonly_bash(command):
    if not command or not command.strip():
        return False
    segs = [s for s in _BASH_SEP.split(command) if s.strip()]
    if not segs:
        return False
    return all(_CD_SEG.match(s) or _PROFILER_SEG.match(s) or READONLY_BASH.match(s)
               for s in segs)


def _paths(ti):
    out = []
    for k in ("file_path", "path"):
        if ti.get(k):
            out.append(str(ti[k]))
    if isinstance(ti.get("files"), list):
        out.extend(str(x) for x in ti["files"])
    return out


def _in_own_source_tree(rp):
    """True if rp lives inside ANY checkout of THIS plugin — the installed cache, a
    dev repo, or a git worktree — identified by an ancestor .claude-plugin/plugin.json
    whose name matches ours. The installed PLUGIN_ROOT check below only covers the cache
    copy; this also exempts the dev repo. Without it, editing the harness's own rule
    sources (which NECESSARILY embed the very keyword patterns the checks scan for, as
    regex literals) makes the running harness flag its own source — so the dev repo
    becomes uneditable through it."""
    if not _SELF_NAME:
        return False
    for anc in (rp, *rp.parents):
        marker = anc / ".claude-plugin" / "plugin.json"
        try:
            if marker.is_file() and _plugin_name(anc) == _SELF_NAME:
                return True
        except Exception:
            continue
    return False


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
        if _in_own_source_tree(rp):
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
        return "drop" if _readonly_bash(ti.get("command", "")) else "inspect"
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        paths = _paths(ti)
        # drop only if EVERY target is provably a doc; otherwise default-open
        if paths and all(DOC_EXT.search(p) for p in paths):
            return "drop"
        return "inspect"
    if tool == "SendUserFile":
        return "inspect"           # outward-facing → always inspect

    return "inspect"               # unknown tool → default-open
