#!/usr/bin/env python3
"""Project-facts layer. The portable rules contain {{placeholders}}; this loads
the active project's facts and expands them.

Active project resolution (first match wins):
  1. env HARNESS_PROJECT
  2. <plugin_root>/.active-project  (a file containing the project name)
  3. "mimo"

Facts file: <plugin_root>/project.<name>.json

To port the harness to another project: write project.<name>.json with the same
keys, set .active-project to <name>. The rules never change; only facts do.
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_CACHE = None


def _active_name():
    n = os.environ.get("HARNESS_PROJECT")
    if n:
        return n.strip()
    f = ROOT / ".active-project"
    try:
        t = f.read_text(encoding="utf-8").strip()
        if t:
            return t
    except Exception:
        pass
    return "mimo"


def load_facts():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        _CACHE = json.loads((ROOT / f"project.{_active_name()}.json").read_text(encoding="utf-8"))
    except Exception:
        _CACHE = {}
    return _CACHE


def expand(s):
    """Replace {{key}} with the project fact. Unknown keys are left literal
    (so a missing fact degrades to a harmless no-match, never a crash)."""
    if not isinstance(s, str) or "{{" not in s:
        return s
    facts = load_facts()
    return _PLACEHOLDER.sub(lambda m: str(facts.get(m.group(1), m.group(0))), s)
