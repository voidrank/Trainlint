#!/usr/bin/env python3
"""Codex compatibility shim — one job: make Codex's edit tool look like Claude's.

Codex deliberately cloned Claude Code's hook protocol (same stdin JSON, same
`hookSpecificOutput` field names, same `${CLAUDE_PLUGIN_ROOT}` env alias), so the
whole router pipeline runs unchanged — EXCEPT for tool names. Codex's PreToolUse
fires for `Bash`, `apply_patch`, and MCP tools; there is NO Edit/Write/MultiEdit/
Read. File edits arrive as a single `apply_patch` envelope (or, on older Codex,
a Bash heredoc that pipes the same envelope into `apply_patch`).

`normalize(data)` rewrites that envelope into the Claude-style Edit `tool_input`
the rest of the pipeline already reads (`file_path` / `files` / `new_string` /
`content`) and sets `tool_name = "Edit"`. After this, prefilter's edit branch,
checks._field, readtrack, planaware and classifier all work with zero changes.

It mutates `data` in place and is a NO-OP on Claude input or on anything without
a patch envelope — so it is safe to call on every event. Fail-open: any parse
trouble leaves `data` untouched.
"""
import re

_MARK = "*** Begin Patch"
# header lines that name a target file inside an apply_patch envelope
_FILE_HDR = re.compile(r'^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$')
_MOVE_HDR = re.compile(r'^\*\*\* Move to:\s*(.+?)\s*$')


def _parse_patch(text):
    """(paths, added_text) from an apply_patch envelope. added_text = every
    inserted line (the new content the checks need to scan)."""
    paths, added = [], []
    for line in text.splitlines():
        m = _FILE_HDR.match(line) or _MOVE_HDR.match(line)
        if m:
            paths.append(m.group(1))
            continue
        # apply_patch marks inserted lines with a single leading '+'
        # (no '+++' unified-diff header to confuse us)
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return paths, "\n".join(added)


def _find_patch_text(ti):
    """Locate the envelope string regardless of which key Codex used to carry it."""
    for v in ti.values():
        if isinstance(v, str) and _MARK in v:
            return v
        if isinstance(v, list):
            for x in v:
                if isinstance(x, str) and _MARK in x:
                    return x
    return None


def normalize(data):
    """Map Codex apply_patch -> Claude-style Edit tool_input, in place."""
    try:
        tool = data.get("tool_name", "")
        ti = data.get("tool_input") or {}
        text = None
        if tool == "apply_patch":
            text = _find_patch_text(ti)
        elif tool == "Bash":
            cmd = ti.get("command", "")
            if _MARK in cmd:                 # Bash-wrapped heredoc form
                text = cmd
        if not text:
            return data

        paths, added = _parse_patch(text)
        if not paths:                        # mentions the marker but isn't a real patch
            return data

        new_ti = dict(ti)
        new_ti["file_path"] = paths[0]
        new_ti["files"] = paths
        new_ti["new_string"] = added
        new_ti["content"] = added
        data["tool_name"] = "Edit"
        data["tool_input"] = new_ti
    except Exception:
        pass                                 # fail-open: never break the router
    return data
