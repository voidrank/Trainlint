#!/usr/bin/env python3
"""Project-facts layer. The portable rules contain {{placeholders}}; this loads
the active project's facts and expands them.

Active project resolution (first match wins):
  1. env HARNESS_PROJECT
  2. this session's lock / the project whose home contains your cwd (research/paths.py active_project())
  3. "example"

Facts file: <plugin_root>/project.<name>.json

To port the harness to another project: write project.<name>.json with the same
keys, and bind it to your session with /hansard:use <name>. The rules never change; only facts do.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))
import paths  # noqa: E402  — per-project data lives outside the versioned plugin dir
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_CACHE = None


def _active_name():
    return paths.active_project()


def load_facts():
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        _CACHE = json.loads(paths.resolve(f"project.{_active_name()}.json").read_text(encoding="utf-8"))
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
