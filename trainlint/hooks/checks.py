#!/usr/bin/env python3
"""Stage 3 — deterministic verifiers for the FATAL items.

These never use a model. They inspect facts (a path, the actual diff text, the
command) and return an exact verdict. They are triggered by action structure, so
they fire even if the stage-2 model mis-routes — the catastrophic checks do not
depend on the model being right.

Rules live in checks.jsonl. Each rule:
  name     id
  tool     regex on tool name (optional)
  inspect  which field(s) to scan: "path" | "content" | "command" | "any"
  match    regex that must be PRESENT to fire
  unless   regex that, if present, SUPPRESSES the verdict (optional)
  level    "reject" (machine-certain violation → bounce) | "escalate" (human verify)
  message  what to say
"""
import json
import re
from pathlib import Path

import facts  # project-facts expansion of {{placeholders}}

try:
    import modeljudge  # opt-in Haiku FP-suppressor (on hooks/ sys.path via the router)
except Exception:  # pragma: no cover
    modeljudge = None

CHECKS = Path(__file__).resolve().parent / "checks.jsonl"


def load_checks(path=CHECKS):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _field(ti, tool, inspect):
    parts = []
    if inspect in ("path", "any"):
        for k in ("file_path", "path"):
            if ti.get(k):
                parts.append(str(ti[k]))
        if isinstance(ti.get("files"), list):
            parts.extend(str(x) for x in ti["files"])
    if inspect in ("content", "any"):
        for k in ("new_string", "content"):
            if ti.get(k):
                parts.append(str(ti[k]))
        if isinstance(ti.get("edits"), list):       # MultiEdit
            for e in ti["edits"]:
                if isinstance(e, dict) and e.get("new_string"):
                    parts.append(str(e["new_string"]))
    if inspect in ("command", "any"):
        if ti.get("command"):
            parts.append(str(ti["command"]))
    if inspect == "any":
        parts.append(tool)
    return "\n".join(parts)


def _run_verifier(spec, text):
    """spec = 'module.func'; func(text) -> (fire: bool, message: str|None).
    Any import/run error fails OPEN (no fire)."""
    try:
        mod_name, fn_name = spec.rsplit(".", 1)
        import importlib
        mod = importlib.import_module("verifiers." + mod_name)
        return getattr(mod, fn_name)(text)
    except Exception:
        return (False, None)


def run(data, checks=None):
    """Return [{name, level, message}, ...] for every fatal rule that fires."""
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    out = []
    for c in (load_checks() if checks is None else checks):
        tp = c.get("tool")
        if tp and not re.search(tp, tool, re.I):
            continue
        hay = _field(ti, tool, c.get("inspect", "any"))
        if not hay:
            continue
        msg_override = None
        if c.get("verifier"):
            # REAL verifier: a python function does an actual parse/check, not a regex.
            fire, vmsg = _run_verifier(c["verifier"], hay)
            if not fire:
                continue
            msg_override = vmsg
        else:
            try:
                if not re.search(facts.expand(c["match"]), hay, re.I):
                    continue
                unless = c.get("unless")
                if unless and re.search(facts.expand(unless), hay, re.I):
                    continue
            except (re.error, KeyError):
                continue
        # opt-in Haiku FP-suppression: ONLY for regex keyword checks — never a parsed verifier, a
        # machine-certain catastrophic guard, or a sticky scar — kills the "keyword in a comment /
        # read-only command / fixture" alarm. Fail-open: off / unsure -> the regex verdict stands.
        if not c.get("verifier") and not c.get("machine_certain") and not c.get("sticky") \
                and modeljudge is not None and modeljudge.is_false_positive(
                    hay, (c.get("name", "") + ": " + facts.expand(c.get("message", "")))[:240]):
            continue
        out.append({
            "name": c.get("name", ""),
            "level": c.get("level", "escalate"),
            # "certain" = machine-certain it's WRONG (the downgrade must never touch it). A verifier
            # that parses the actual wrong value (mel-power, frozen-encode) is certain; one that only
            # confirms "this IS model code" but leaves correctness to a human (check_model_code) is
            # NOT — it sets "machine_certain": false so a settled-decision downgrade can still apply.
            "certain": c.get("machine_certain", bool(c.get("verifier"))),
            # a deliberate, scar-backed guard that must reach the user even near a settled
            # decision (it is not a stray keyword hit) — exempt it from the plan downgrade
            "sticky": bool(c.get("sticky")),
            "message": facts.expand(msg_override or c.get("message", "")),
        })
    return out
