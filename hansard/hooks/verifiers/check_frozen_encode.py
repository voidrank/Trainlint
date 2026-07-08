#!/usr/bin/env python3
"""Verifier: a frozen codec's encode params must MATCH its training config, value-for-value.

This is the PRECISE-CATCH version of the founding scar (power=2.0 / wrong sample-rate). It does NOT
keyword-guess — it PARSES the params actually set in the edit and compares each against the project's
declared contract (project.<name>.json -> "codec_contract": {"param": "expected", ...}). It fires
ONLY when a contract param is set to a value that DIFFERS from what the frozen codec was frozen with
-> "you set sample_rate=24000 but the frozen DAC was frozen at 44100 -> OOD codes". That is the
silent-bug class hansard exists for, caught precisely instead of recited as discipline.

Empty/absent contract -> never fires (no false alarm on a project that hasn't declared one).
func(text) -> (fire: bool, message: str|None). Importable as verifiers.check_frozen_encode.check.
"""
import re

try:
    import facts  # hooks/facts.py — on sys.path when run through the router
except Exception:  # pragma: no cover
    facts = None

# cheap gate: only inspect edits that are actually about encoding / the codec
_CONTEXT = re.compile(r"encode|codec|vqgan|\bdac\b|load_model|MelSpectrogram|tokeniz|quantiz|n_codebook",
                      re.IGNORECASE)


def check(text):
    if not text or facts is None:
        return (False, None)
    try:
        contract = facts.load_facts().get("codec_contract", {})
    except Exception:
        contract = {}
    if not isinstance(contract, dict) or not contract or not _CONTEXT.search(text):
        return (False, None)
    wrong = []
    for param, expected in contract.items():
        exp = str(expected)
        m = re.search(r"\b" + re.escape(str(param)) + r"\s*[=:]\s*['\"]?([\w./+-]+)", text)
        if m and m.group(1) != exp:
            wrong.append(f"{param}={m.group(1)} (frozen codec expects {exp})")
    if not wrong:
        return (False, None)
    return (True, "FROZEN-CODEC param mismatch — " + "; ".join(wrong) + ". The codec was frozen with "
            "these exact values; a different one feeds it out-of-distribution input -> silently wrong "
            "codes (the power=2.0 / wrong-sample-rate class of silent bug). Match it, or confirm the "
            "contract genuinely changed.")
