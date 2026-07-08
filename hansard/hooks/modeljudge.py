#!/usr/bin/env python3
"""Opt-in Haiku judge — a CLASSIFICATION (routing) refinement, NEVER a correctness judge.

Some FP filters are fuzzy for regex: it can't tell a data-pipeline script that `import torch` from
real model-forward code, nor a comment that merely *mentions* a rejected approach from an action that
actually performs it. A small fast model reads the action, reasons briefly, and classifies which kind
it is. Under the design rule the model only ROUTES/classifies — it NEVER judges whether the code is
correct (that stays human, via the escalation it gates).

Opt-in: HARNESS_MODEL=1 + a usable credential. The credential is either ANTHROPIC_API_KEY, or the
Claude Code subscription OAuth token in ~/.claude/.credentials.json (scope user:inference) used as a
bearer auth_token. Fail-OPEN: off / no credential / SDK missing / error / ambiguous verdict -> the
safe default, so the deterministic regex floor stands and recall is never lost. It can only SUPPRESS
a regex false-positive, never add a fire.
"""
import json
import os
import re
import time
from pathlib import Path

_CREDS = Path.home() / ".claude" / ".credentials.json"
# Headroom for the judge to reason a sentence or two before its VERDICT line. Haiku is cheap; a
# one-token answer mis-judged the borderline cases, so we let it think, then parse the verdict.
_MAX_TOKENS = 512


def _credential():
    """(kind, value) for the anthropic client: ('api_key', k) from env if present, else
    ('oauth', token) from the Claude Code subscription creds. None if neither is usable."""
    k = os.environ.get("ANTHROPIC_API_KEY")
    if k:
        return ("api_key", k)
    try:
        oa = json.loads(_CREDS.read_text(encoding="utf-8")).get("claudeAiOauth") or {}
        tok = oa.get("accessToken")
        exp = oa.get("expiresAt")
        if tok and (not exp or time.time() * 1000 < exp):  # skip an expired token -> fail-open
            return ("oauth", tok)
    except Exception:
        pass
    return None


def enabled():
    return (os.environ.get("HARNESS_MODEL", "").strip().lower() in ("1", "on", "true")
            and _credential() is not None)


def _client():
    """An anthropic client from whatever credential is available, or None (fail-open)."""
    cred = _credential()
    if not cred:
        return None
    try:
        import anthropic
    except Exception:
        return None
    kind, val = cred
    try:
        if kind == "api_key":
            return anthropic.Anthropic(api_key=val, timeout=12)
        return anthropic.Anthropic(auth_token=val, timeout=12)  # subscription OAuth bearer
    except Exception:
        return None


def _ask(system, user):
    """Full response text (upper-cased); '' on any failure (fail-open)."""
    if not enabled():
        return ""
    c = _client()
    if c is None:
        return ""
    try:
        r = c.messages.create(
            model="claude-haiku-4-5", max_tokens=_MAX_TOKENS,
            system=system, messages=[{"role": "user", "content": str(user)[:4000]}])
        return "".join(getattr(b, "text", "") for b in r.content).strip().upper()
    except Exception:
        return ""


def _classify(system, user, suppress_word, keep_word):
    """Run the judge and return True ONLY if it CLEARLY concludes `suppress_word`. The judge reasons,
    then ends with `VERDICT: <WORD>`; we parse that. keep_word / ambiguous (both words / neither) /
    error / disabled -> False, so the deterministic regex fire stands. The judge can only ever
    SUPPRESS a false positive (return True), never add one."""
    text = _ask(system, user)
    if not text:
        return False
    m = re.search(r"VERDICT\s*[:\-]?\s*\**\s*(" + suppress_word + "|" + keep_word + ")", text)
    if m:
        return m.group(1) == suppress_word
    # no explicit verdict line — only trust an UNAMBIGUOUS single-word presence
    has_s, has_k = (suppress_word in text), (keep_word in text)
    return has_s and not has_k


def is_not_model_code(text):
    """True ONLY if the model CONFIDENTLY classifies this edit as a data-pipeline / config / eval /
    probe script (not model forward/mask/sampling code). Off / error / 'model code' / ambiguous ->
    False (keep the deterministic fire)."""
    if not text:
        return False
    return _classify(
        ("Classify a code edit. MODEL = it edits a neural network's forward / attention-mask / "
         "sampling / generate logic. OTHER = it is a data-pipeline, config/yaml, eval, or probe "
         "script that merely mentions those words. Do NOT judge whether the code is correct — only "
         "classify which kind it is. Reason in one or two sentences, then end with a line exactly: "
         "VERDICT: MODEL  or  VERDICT: OTHER."),
        text, "OTHER", "MODEL")


def is_false_positive(action_text, concern):
    """General FP-suppressor for the keyword/regex gates (anti-prior drift, the high-stakes quiz
    gate, the danger-pattern checks, the trigger rubric). A regex flagged `action_text` for
    `concern`; the judge decides whether that flag is a REAL hit or an INCIDENTAL match — the
    trigger words sit in a comment / docstring / string literal / log line, OR the command is
    read-only, OR it's a clearly-labeled test fixture / negative example, so the action does NOT
    actually perform the concern.

    Returns True ONLY when enabled AND the judge concludes INCIDENTAL. Off / unavailable / error /
    ambiguous -> False, so the deterministic regex fire stands. It can only SUPPRESS a false
    positive, never add a fire, and it ROUTES (real vs incidental), never judges correctness. When
    unsure it is instructed to answer REAL -> recall kept."""
    if not action_text or not concern:
        return False
    return _classify(
        ("A regex flagged a coding agent's pending action for this concern:\n"
         f"  {str(concern)[:400]}\n"
         "Decide whether the flag is a REAL hit or an INCIDENTAL match. INCIDENTAL = the trigger "
         "words appear only in a comment / docstring / string literal / log message, OR the command "
         "is READ-ONLY (grep/cat/ls/find/head/tail/python -c print), OR it is a clearly-labeled test "
         "fixture / negative example — the action does NOT actually perform the concern. REAL = the "
         "action genuinely does the flagged thing. Do NOT judge whether the code is correct — only "
         "classify real vs incidental. When genuinely unsure, choose REAL. Reason in one or two "
         "sentences, then end with a line exactly: VERDICT: REAL  or  VERDICT: INCIDENTAL."),
        action_text, "INCIDENTAL", "REAL")
