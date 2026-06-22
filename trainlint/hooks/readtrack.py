#!/usr/bin/env python3
"""Read-before-edit tracker — catch the agent acting on code it never looked at.

The LLM's failure mode in an unfamiliar codebase is to assert (and then edit) how something
works from its prior + the filename, without reading the actual file. The doorman can't see the
agent's PROSE claims, but it CAN see this: an Edit to a file the agent hasn't READ this session.
That's the action-level shadow of "asserting without grounding" — and it's exactly where silent
bugs come from.

`record(data)` logs every file the agent reads (Read tool, or cat/head/sed/grep in Bash) into a
per-session set. `check(data)` coaches (agent-facing, never blocks) when an Edit/overwrite targets
an EXISTING file not in that set. Fail-open: any error → no tracking, no coaching.
"""
import re
from pathlib import Path

STATE = Path(__file__).resolve().parent.parent / "research" / ".state"
READ_TOOLS = {"Read", "NotebookRead"}
EDIT_TOOLS = {"Edit", "MultiEdit", "Write"}
# cat/head/… <file> in a Bash command counts as having read the file
_BASH_READ = re.compile(r"\b(?:cat|head|tail|less|more|bat|sed|awk|grep|rg)\b[^|;&]*?([/\w.\-]+\.\w+)")


def _resolve(x):
    try:
        return str(Path(x).resolve())
    except Exception:
        return str(x)


def _set_path(session):
    return STATE / f"reads.{session}.txt"


def record(data):
    """Log files the agent has read this session. No-op without a session id."""
    session = data.get("session_id", "")
    if not session:
        return
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    paths = []
    if tool in READ_TOOLS:
        for k in ("file_path", "path", "notebook_path"):
            if ti.get(k):
                paths.append(str(ti[k]))
    elif tool == "Bash":
        paths = _BASH_READ.findall(str(ti.get("command", "")))
    if not paths:
        return
    try:
        STATE.mkdir(exist_ok=True)
        p = _set_path(session)
        cur = p.read_text(encoding="utf-8") if p.exists() else ""
        with p.open("a", encoding="utf-8") as f:
            for x in paths:
                rp = _resolve(x)
                if rp not in cur:
                    f.write(rp + "\n")
                    cur += rp + "\n"
    except Exception:
        pass


def check(data):
    """Coach if editing an EXISTING file the agent hasn't read this session."""
    tool = data.get("tool_name", "")
    if tool not in EDIT_TOOLS:
        return []
    session = data.get("session_id", "")
    if not session:
        return []
    ti = data.get("tool_input", {}) or {}
    fp = ti.get("file_path") or ti.get("path")
    if not fp:
        return []
    try:
        if not Path(fp).exists():   # creating a NEW file is fine — nothing to have read
            return []
    except Exception:
        return []
    rp = _resolve(fp)
    try:
        p = _set_path(session)
        readset = p.read_text(encoding="utf-8") if p.exists() else ""
    except Exception:
        readset = ""
    if rp in readset:
        return []
    return [{"level": "coach", "name": "read-before-edit",
             "message": (f"⚠️ you're editing {Path(fp).name} but haven't READ it this session — don't "
                         "infer how it works from the filename or your prior. Read it first. Acting on "
                         "code you haven't looked at is the action-level form of asserting without "
                         "grounding (and exactly where silent bugs hide).")}]
