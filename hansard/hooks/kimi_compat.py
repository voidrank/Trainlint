#!/usr/bin/env python3
"""Kimi compatibility shim — two jobs, both Kimi-specific.

Kimi CLI (Kimi Code, a Python agent; hooks in ~/.kimi/config.toml) has a hook
system whose INPUT matches Claude's (hook_event_name/tool_name/tool_input/prompt
on stdin) but differs in two ways hansard must absorb:

1. TOOL NAMES.  Kimi's PreToolUse matches `tool_name` against Shell / WriteFile /
   StrReplaceFile (kimi_cli/tools/{shell,file/write,file/replace}.py), with input
   fields `command` / `path,content` / `path,edit:{old,new}`. `normalize()` rewrites
   those into the Claude-style Bash/Write/Edit `tool_input` (`file_path`/`new_string`/
   `content`) the rest of the pipeline already reads. No-op on Claude/Codex input.

2. OUTPUT MODEL.  Kimi is BLOCK-ONLY: its callers read ONLY HookResult.action
   (block/allow) + reason (runner.py:77-82, kimisoul.py:618/665, toolset.py:397);
   anything a hook prints to stdout that ISN'T `permissionDecision:deny` is dropped.
   So Claude's two soft channels have no native home. Per the project decision
   (kimi-output-model = "gate + escalate-by-block"), `adapt_for_kimi()` rewrites the
   router's Claude-style output into what Kimi honors:
     - reject (permissionDecision:deny)         -> kept as-is              (works)
     - report-doorman block (decision:block)    -> permissionDecision:deny  (Stop re-turn)
     - escalate (systemMessage)                 -> permissionDecision:deny  (escalate-by-block)
     - coach only (additionalContext)           -> dropped                  (no native path)
   Gated on TRAINLINT_HOST=kimi (set by install-kimi.sh); off-host it never runs.

Both functions fail-open: any trouble leaves the data/output untouched.
"""
from __future__ import annotations

from typing import Any


# ----- 1. tool-name + tool_input normalization (Kimi -> Claude shapes) -----
def normalize(data: dict[str, Any]) -> dict[str, Any]:
    try:
        tool = data.get("tool_name", "")
        ti = data.get("tool_input") or {}

        if tool == "Shell":
            # Kimi `command` field already matches Claude Bash's `command`
            data["tool_name"] = "Bash"

        elif tool == "WriteFile":
            new = dict(ti)
            if "path" in new:
                new["file_path"] = new["path"]
            data["tool_name"] = "Write"
            data["tool_input"] = new

        elif tool == "StrReplaceFile":
            new = dict(ti)
            if "path" in new:
                new["file_path"] = new["path"]
            edit = ti.get("edit")
            edits = edit if isinstance(edit, list) else ([edit] if isinstance(edit, dict) else [])
            edits = [e for e in edits if isinstance(e, dict)]
            new["edits"] = [{"old_string": e.get("old", ""), "new_string": e.get("new", "")} for e in edits]
            new["new_string"] = "\n".join(e.get("new", "") for e in edits)
            new["old_string"] = "\n".join(e.get("old", "") for e in edits)
            new["content"] = new["new_string"]
            data["tool_name"] = "Edit"
            data["tool_input"] = new
    except Exception:
        pass
    return data


# ----- 2. output adaptation (Claude-style channels -> Kimi block-only) -----
def _deny(event: str, reason: str) -> dict[str, Any]:
    return {"hookSpecificOutput": {"hookEventName": event or "PreToolUse",
                                   "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def adapt_for_kimi(out: dict[str, Any] | None) -> dict[str, Any] | None:
    """Rewrite a Claude-style router output dict into Kimi's block-only form."""
    if not out:
        return None
    try:
        hso = out.get("hookSpecificOutput", {}) or {}
        event = hso.get("hookEventName", "")

        # report-doorman block (Stop): Claude uses {decision:block,reason};
        # Kimi only recognizes permissionDecision:deny -> action=block -> re-turn(reason).
        if out.get("decision") == "block":
            return _deny(event or "Stop", out.get("reason", ""))

        # native tool reject — already the shape Kimi parses; strip coach extras.
        if hso.get("permissionDecision") == "deny":
            return _deny(event, hso.get("permissionDecisionReason", ""))

        # escalate -> escalate-by-block (the alert becomes the block reason).
        if out.get("systemMessage"):
            return _deny(event, out["systemMessage"])

        # coach only (additionalContext, no block) — Kimi can't inject it. Drop.
        return None
    except Exception:
        return out
