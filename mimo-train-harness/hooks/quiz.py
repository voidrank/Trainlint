#!/usr/bin/env python3
"""Quiz-gate (stage 2.5) — OPT-IN, NON-BLOCKING.

At a high-stakes moment it surfaces a relevant quiz question so the operator has
to prove (to themselves, in chat) they understand before doing the action. It is
Socratic, not mechanical: it only SURFACES (escalate channel), never blocks — so
it can never lock the session, and answering is on your honour.

Enabled only when env HARNESS_QUIZ in {1,on,true} OR a .quiz-gate file exists at
the plugin root. Off by default → zero behaviour change.
"""
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUIZ = ROOT / "quiz.jsonl"


def enabled():
    if os.environ.get("HARNESS_QUIZ", "").strip().lower() in ("1", "on", "true"):
        return True
    return (ROOT / ".quiz-gate").exists()


def load():
    rows = []
    try:
        for line in QUIZ.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except Exception:
        pass
    return rows


def ask(data, haystack):
    """Return the gate-able quiz questions whose `when` matches this moment.
    Empty unless enabled. Caller surfaces (at most) the first."""
    if not enabled() or not haystack:
        return []
    out = []
    for q in load():
        w = q.get("when")
        if not w:
            continue
        try:
            if re.search(w, haystack, re.IGNORECASE):
                out.append(q)
        except re.error:
            continue
    return out
