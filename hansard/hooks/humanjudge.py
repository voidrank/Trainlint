#!/usr/bin/env python3
"""Human-judgment router — on Stop (the model wants to EOS), ask a small model ONE routing question:
does THIS step's conclusion need a HUMAN to judge before it's accepted?

This stays on the RIGHT side of hansard's design line: the model does NOT judge correctness
(that's the human's call) — it only ROUTES, deciding when human judgment is WARRANTED. When it is,
the hook bounces the stop with a reason that tells the agent to ASK the operator (AskUserQuestion)
instead of quietly ending as if the step were done ("应问尽问").

Fires when a step's conclusion rests on something a mechanical check can't settle:
  * a decision flipped to decided/verified on THIN or edge-of-threshold evidence,
  * a conclusion that rests on SUBJECTIVE quality (reads-naturally, tags-used-well) the scorers miss,
  * a STRATEGIC / scope call, an irreversible / outward action,
  * a claim EXTRAPOLATED beyond what the run actually tested (small-sample -> general claim).

SAFE: opt-in (TRAINLINT_HUMANJUDGE truthy) + a usable credential. Bounces at most ONCE per turn
(stop_hook_active guard) so it can't loop. Fail-OPEN: any error / no credential / ambiguous verdict
-> None (let the turn end). It can only ever ASK; it never blocks a tool or judges correctness.
"""
import json
import os
import re
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOKS))
try:
    import modeljudge  # reuse its subscription-OAuth client
except Exception:  # pragma: no cover
    modeljudge = None

_MAX_TOKENS = 350
_MIN_CHARS = 400  # a short reply isn't a step conclusion worth routing


def _enabled():
    return os.environ.get("HANSARD_HUMANJUDGE") or os.environ.get("TRAINLINT_HUMANJUDGE", "").strip().lower() in ("1", "on", "true", "yes")


def _last_assistant_text(transcript_path):
    try:
        lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
    except Exception:
        return ""
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except Exception:
            continue
        msg = obj.get("message", obj)
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        c = msg.get("content", "")
        if isinstance(c, str):
            return c
        parts = [b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


_SYS = (
    "You are a HUMAN-JUDGMENT ROUTER for an ML-research step. You do NOT judge whether the work is "
    "correct — that is the human's call. You decide only ONE thing: is there a PENDING conclusion in "
    "this message that needs a HUMAN to judge BEFORE it is acted on / accepted?\n"
    "The distinction is NOT tense (done vs pending). It is: is there a CLAIM or CHOICE here that "
    "only a human should stand behind?\n"
    "Answer AUTONOMOUS-OK (do NOT flag) when the message is:\n"
    "  - a MECHANICAL / reversible action (pushed, committed, wrote a file, rendered, ran a script) — "
    "even stated as done; the machine can do these;\n"
    "  - an action the operator EXPLICITLY AUTHORIZED this turn (the message says the user asked for "
    "it / said push / said do it);\n"
    "  - pure ANALYSIS, diagnosis, explanation, or discussion with no decision being committed;\n"
    "  - a result fully settled by the numbers shown, or a message that already ASKS the operator.\n"
    "Answer NEEDS-HUMAN when a decision is (or is about to be) marked decided/VERIFIED and that "
    "verdict rests on something a mechanical check can't settle — EVEN if the message states it as "
    "already done, because that 'verified' should not stand without a human:\n"
    "  - SUBJECTIVE quality a scorer can't see (reads naturally? tags used tastefully? faithful in "
    "spirit?);\n"
    "  - THIN or edge-of-threshold evidence (small sample; a number sitting near its gate; 1 known "
    "failure in the sample);\n"
    "  - a claim EXTRAPOLATED beyond what the run tested; or a STRATEGIC/scope commitment.\n"
    "Default to AUTONOMOUS-OK. Reason in ONE sentence, then end with a line EXACTLY:\n"
    "VERDICT: NEEDS-HUMAN   or   VERDICT: AUTONOMOUS-OK"
)


def check(data):
    """Return a reason string (bounce the stop -> tell the agent to ASK) or None (let it end)."""
    try:
        if not _enabled():
            return None
        if data.get("stop_hook_active"):
            return None  # already bounced once this turn -> never loop
        if modeljudge is None:
            return None
        client = modeljudge._client()
        if client is None:
            return None
        text = _last_assistant_text(data.get("transcript_path", ""))
        if len(text) < _MIN_CHARS:
            return None
        try:
            r = client.messages.create(
                model="claude-haiku-4-5", max_tokens=_MAX_TOKENS, system=_SYS,
                messages=[{"role": "user", "content": "The step just finished. Its report:\n\n" + text[:6000]}])
            out = "".join(getattr(b, "text", "") for b in r.content).strip()
        except Exception:
            return None
        m = re.search(r"VERDICT\s*[:\-]?\s*\**\s*(NEEDS-HUMAN|AUTONOMOUS-OK)", out, re.I)
        verdict = m.group(1).upper() if m else ("NEEDS-HUMAN" if "NEEDS-HUMAN" in out.upper() else "")
        if verdict != "NEEDS-HUMAN":
            return None
        why = re.split(r"VERDICT\s*[:\-]", out, flags=re.I)[0].strip().replace("\n", " ")[:300]
        return ("⚠️ [human-judgment router] This step's conclusion looks like it needs YOUR judgment, "
                "not just the mechanical checks: " + (why or "a subjective/thin-evidence/strategic result.")
                + "\nBefore finishing, ASK the operator with the AskUserQuestion tool — surface the "
                "conclusion + the plausible alternatives so they can judge it (应问尽问). If you truly "
                "already confirmed it with them this turn, say so and stop.")
    except Exception:
        return None
