#!/usr/bin/env python3
"""Autopilot gate — keep driving /hansard:execute-and-report until a HUMAN is needed.

Fires on the Stop event, AFTER the report doorman (reportcheck) has passed, so it
only ever evaluates a CLEANLY-FINISHED plan / execute-and-report turn (and never
races the report-rewrite bounce). It asks a small fast model (Haiku) one question:

    given where the project stands, must we PAUSE for the human, or may the loop
    CONTINUE on its own?

PAUSE if ANY of: (1) human review/judgment/approval is genuinely needed; (2) the
next step needs GPU / compute / a training run / a resource a human must launch;
(3) the project is blocked on an important strategic/scope decision only the human
should make. Otherwise CONTINUE — and we return a Stop `decision:block` whose reason
tells the agent to invoke /hansard:execute-and-report again. That is the loop.

SAFETY (this never runs away):
  * OPT-IN. Off unless TRAINLINT_AUTOPILOT is truthy. Default = no autopilot.
  * CAPPED. At most TRAINLINT_AUTOPILOT_MAX (default 8) CONSECUTIVE auto-continues
    per session; then it lets the turn stop. A real human prompt / a long idle gap
    resets the counter.
  * GATED by Haiku, biased to PAUSE — "when in doubt, pause" is in the prompt.
  * FAIL-CLOSED on the loop / FAIL-OPEN on the session: any error, no credential,
    SDK missing, ambiguous verdict -> return None (let the turn STOP). A bug here
    can only fail to continue; it can never block the session or force a loop.

It only CONTINUES; it can never deny a tool or alter anything else.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent
_RESEARCH = _HOOKS.parent / "research"
_STATE = _RESEARCH / ".autopilot.json"

sys.path.insert(0, str(_HOOKS))
try:
    import modeljudge  # reuse its credential + anthropic client (subscription OAuth, no API key)
except Exception:  # pragma: no cover
    modeljudge = None

_DEFAULT_MAX = 8
# A gap longer than this between two Stop events means the human stepped away / took
# over — treat the next continue as a fresh run and reset the consecutive counter.
_RESET_GAP_S = 1800
_MAX_TOKENS = 400


def _enabled():
    return os.environ.get("HANSARD_AUTOPILOT") or os.environ.get("TRAINLINT_AUTOPILOT", "").strip().lower() in ("1", "on", "true", "yes")


def _cap():
    try:
        return max(1, int(os.environ.get("HANSARD_AUTOPILOT_MAX") or os.environ.get("TRAINLINT_AUTOPILOT_MAX", "").strip() or _DEFAULT_MAX))
    except Exception:
        return _DEFAULT_MAX


def _load_state():
    try:
        return json.loads(_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st):
    try:
        _STATE.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass


def _last_assistant_text(transcript_path):
    """The text of the final assistant message in the transcript, or '' on any failure."""
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
        if obj.get("type") != "assistant" and obj.get("role") != "assistant":
            continue
        msg = obj.get("message", obj)
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return ""


def _is_clean_plan_or_execute_report(text):
    """A finished plan / execute-and-report turn signs off on `HTML: <path>.html` (commands/
    plan.md step 6, shared by both commands). Require that sign-off so we only ever autopilot a
    cleanly-completed planning turn — never a mid-task message or an arbitrary answer."""
    if not text or len(text) < 300:
        return False
    return bool(re.search(r"\bHTML:\s*\S+\.html\b", text, re.I)
                or re.search(r"\bviz[\w./-]*\.html\b", text, re.I))


def _project_state():
    """Compact human-readable snapshot (goal + phase map) for the gate to read."""
    goal = ""
    try:
        for p in _RESEARCH.glob("goal.*.txt"):
            goal = p.read_text(encoding="utf-8").strip()
            break
    except Exception:
        pass
    plan_map = ""
    try:
        plan_map = subprocess.run(
            [sys.executable, str(_RESEARCH / "plan.py")],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        pass
    return (goal, plan_map)


_SYS = (
    "You are the AUTOPILOT GATE for an ML-research planning loop (hansard). After each "
    "/hansard:execute-and-report (or /hansard:plan) turn you decide whether the loop may "
    "CONTINUE autonomously or must PAUSE for the human.\n"
    "PAUSE if ANY of these is true:\n"
    "  (1) human review / judgment / approval is genuinely needed before the next step;\n"
    "  (2) the next step needs GPU / compute / a training or fine-tuning run, or any resource a "
    "human must provision or launch;\n"
    "  (3) the project is blocked on an important strategic or scope decision that only the human "
    "should make (e.g. an OPEN load-bearing decision involving a value/scope judgment, or two "
    "options with no clear winner).\n"
    "CONTINUE only if the immediate next step is concrete, low-stakes work the agent can finish "
    "entirely on its own with data already on disk (write a script, run a probe/measurement, build "
    "a small artifact, score existing samples).\n"
    "When in doubt, PAUSE. Reason in ONE or TWO sentences, then end with a line EXACTLY:\n"
    "VERDICT: PAUSE   or   VERDICT: CONTINUE"
)


def _gate(goal, plan_map, report):
    """(decision, reason). decision in {'CONTINUE','PAUSE'}; '' reason on failure -> caller pauses."""
    if modeljudge is None:
        return ("", "")
    client = modeljudge._client()
    if client is None:
        return ("", "")
    user = (f"PROJECT GOAL:\n{goal}\n\nPLAN STATE:\n{plan_map}\n\n"
            f"THE TURN THAT JUST FINISHED (its closing report):\n{report[:3500]}\n\n"
            "Decide: PAUSE for the human, or CONTINUE the loop?")
    try:
        r = client.messages.create(
            model="claude-haiku-4-5", max_tokens=_MAX_TOKENS,
            system=_SYS, messages=[{"role": "user", "content": user[:8000]}])
        text = "".join(getattr(b, "text", "") for b in r.content).strip()
    except Exception:
        return ("", "")
    m = re.search(r"VERDICT\s*[:\-]?\s*\**\s*(CONTINUE|PAUSE)", text, re.I)
    decision = m.group(1).upper() if m else ("PAUSE" if "PAUSE" in text.upper() else "")
    reason = re.split(r"VERDICT\s*[:\-]", text, flags=re.I)[0].strip().replace("\n", " ")
    return (decision, reason[:300])


def check(data):
    """Return a continuation REASON string (the loop continues) or None (the turn stops).

    Called from router's Stop branch ONLY when the report gate is clean. Never raises."""
    try:
        if not _enabled():
            return None

        report = _last_assistant_text(data.get("transcript_path", ""))
        st = _load_state()
        sid = str(data.get("session_id") or "default")
        now = time.time()
        prev = st.get(sid, {})
        # Long idle gap or a turn that is NOT a clean plan/execute report => the human is driving;
        # reset the streak and (for a non-report turn) do nothing.
        if now - float(prev.get("ts", 0)) > _RESET_GAP_S:
            prev = {}
        if not _is_clean_plan_or_execute_report(report):
            if prev:
                st[sid] = {"count": 0, "ts": now}
                _save_state(st)
            return None

        cap = _cap()
        count = int(prev.get("count", 0))
        if count >= cap:
            # Reached the consecutive cap — stop and reset, so the human gets the wheel back.
            st[sid] = {"count": 0, "ts": now}
            _save_state(st)
            return None

        goal, plan_map = _project_state()
        decision, reason = _gate(goal, plan_map, report)
        if decision != "CONTINUE":
            st[sid] = {"count": 0, "ts": now}  # PAUSE / ambiguous -> reset streak, let it stop
            _save_state(st)
            return None

        nxt = count + 1
        st[sid] = {"count": nxt, "ts": now}
        _save_state(st)
        why = (" " + reason) if reason else ""
        return (
            f"[hansard autopilot ▸ {nxt}/{cap}] No human review, GPU/compute, or blocking "
            f"decision is needed right now.{why}\n\n"
            "Continue autonomously: invoke the /hansard:execute-and-report skill to drive the "
            "current main thread to its next on-disk artifact, then report as usual. "
            "If at any point the next step would need human judgment, a GPU/training run, or a "
            "strategic/scope decision, STOP and say so plainly instead of continuing the loop. "
            f"(Autopilot cap: {cap} consecutive auto-continues; set TRAINLINT_AUTOPILOT=0 to stop.)"
        )
    except Exception:
        return None
