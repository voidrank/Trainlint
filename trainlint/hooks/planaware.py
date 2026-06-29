#!/usr/bin/env python3
"""Stage 2.4 — PLAN-AWARE routing.

Locate a live action on the project PLAN (research/plan.<name>.jsonl) and route by
the touched DECISION's status + governing principle — instead of matching keywords
in whatever file happens to be open.

This is the fix for why the plugin underperformed in real use (audit of session
8cc76f15): it keyword-escalated throwaway probe scripts and delivered the right
principle ~285 lines too late. With the plan, an action is mapped to the decision
it actually touches:

  open      -> ESCALATE: you're acting on an UNDECIDED fork; here's its principle — decide it
  decided   -> COACH:    decided but unverified; make the code match the choice, then verify
  verified  -> COACH:    settled; deliver the known-right answer + principle, just-in-time

Each (session, decision) is surfaced AT MOST ONCE — no more identical repeated
escalations (the audit's "3 byte-identical, ignored" failure). A verified decision
also lets the router DOWNGRADE a keyword-only escalation (the region is settled) —
see router.decide. Fail-OPEN: any error -> no items, never raises.
"""
import re
import sys
from pathlib import Path

RESEARCH = Path(__file__).resolve().parent.parent / "research"
STATE = RESEARCH / ".state"
# plan phases where work is expensive + irreversible — the gate only fires here (high-stakes only)
HIGH_STAKES = {"model", "loss", "train"}
sys.path.insert(0, str(RESEARCH))
try:
    import plan as planlib  # noqa: E402
except Exception:  # pragma: no cover
    planlib = None
try:
    import progress as progresslib  # noqa: E402  (plan-quiz mastery state)
except Exception:  # pragma: no cover
    progresslib = None
try:
    import modeljudge  # opt-in Haiku FP-suppressor; on sys.path (hooks/) via the router
except Exception:  # pragma: no cover
    modeljudge = None


def _haystack(data):
    """Fuller than classifier's: include the DIFF content too, so content-level
    decisions (e.g. a MelSpectrogram edit) are located, not just path/command."""
    if data.get("hook_event_name", "") == "UserPromptSubmit":
        return data.get("prompt", "") or ""
    ti = data.get("tool_input", {}) or {}
    parts = [str(data.get("tool_name", ""))]
    for k in ("command", "file_path", "path"):
        if ti.get(k):
            parts.append(str(ti[k]))
    if isinstance(ti.get("files"), list):
        parts.extend(str(x) for x in ti["files"])
    for k in ("new_string", "content"):
        if ti.get(k):
            parts.append(str(ti[k]))
    if isinstance(ti.get("edits"), list):
        for e in ti["edits"]:
            if isinstance(e, dict) and e.get("new_string"):
                parts.append(str(e["new_string"]))
    return " ".join(parts)


def _seen_then_mark(session, did):
    """True if this (session, decision) was already surfaced. Marks it on first
    sight. No session id (e.g. in tests) -> never deduped. Best-effort, fail-open."""
    if not session:
        return False
    m = STATE / f"plan-seen.{session}.{did}"
    try:
        if m.exists():
            return True
        STATE.mkdir(exist_ok=True)
        m.write_text("1", encoding="utf-8")
    except Exception:
        pass
    return False


def _action_is_foreign(data):
    """True if this action edits a file inside a tree marked with a `.trainlint-foreign`
    file — a repo that is NOT a trainlint-managed training project (e.g. trainlint-builder),
    whose files are necessarily full of the very training vocabulary the gate scans for.
    Mirrors prefilter._in_own_source_tree (which exempts the harness's OWN checkouts); this
    exempts marked third-party trees. Path-based (edits only); fail-open."""
    ti = data.get("tool_input", {}) or {}
    paths = []
    for k in ("file_path", "path"):
        if ti.get(k):
            paths.append(str(ti[k]))
    if isinstance(ti.get("files"), list):
        paths.extend(str(x) for x in ti["files"])
    for t in paths:
        try:
            rp = Path(t).resolve()
        except Exception:
            continue
        for anc in (rp, *rp.parents):
            try:
                if (anc / ".trainlint-foreign").is_file():
                    return True
            except Exception:
                continue
    return False


def assess(data):
    """Return (items, located). items are severity-tagged + carry plan_decision;
    located is every plan decision this action touches (used for the downgrade)."""
    if planlib is None:
        return [], []
    # FOREIGN EXEMPTION: an edit inside a tree marked .trainlint-foreign is not a managed
    # training project — skip the whole plan gate for it (mirrors prefilter's own-source drop).
    if _action_is_foreign(data):
        return [], []
    try:
        hay = _haystack(data)
        full = planlib.load()
        located = planlib.locate(hay, full)
    except Exception:
        return [], []
    # CATCH-22 GUARD: the gate-clearing command (`progress.py mark <id>`) must NEVER itself be
    # blocked by the hard gate — otherwise the gate could never be cleared. Let it straight through.
    if re.search(r"progress\.py['\"]?\s+mark\b", hay or ""):
        return [], located
    items = []
    # ANTI-PRIOR WATCH — catch the agent drifting toward an explicitly REJECTED option, on ANY
    # action (not just ones that touch the decision's topic). NOT deduped: it fires every time the
    # agent drifts, because the whole job is to keep correcting a strong prior the user already
    # rejected ("build on repo A, not repo B's codec" / "fresh-from-base, not resume a prior ckpt").
    # Coach-level — agent-facing, never blocks (the user already said it; the AGENT needs reminding).
    for d in full:
        nr = d.get("not_re")
        if not nr or not hay:
            continue
        try:
            if re.search(nr, hay, re.IGNORECASE):
                # opt-in Haiku precision: skip the drift nudge when the rejected words are only an
                # incidental mention (comment / read-only / negative fixture), not a real drift.
                if modeljudge is not None and modeljudge.is_false_positive(
                        hay, "drifting toward a REJECTED approach: " + (d.get("not_this") or nr)):
                    continue
                items.append({"level": "coach", "plan_decision": d.get("id", "?"),
                              "message": (f"⛔ drift toward a REJECTED option — {d.get('not_this','')}. "
                                          f"Decision «{d.get('id','?')}» chose: {d.get('choice','')} over it"
                                          + ((" (" + d['why'] + ")") if d.get("why") else "")
                                          + ". Don't drift back; revisit only if the user EXPLICITLY says to.")})
        except re.error:
            continue
    if not located and not items:
        return [], []
    # mastery state — the soft understanding-gate: acting on a decision you haven't walked in
    # quiz gets flagged (never blocked). Fail-open to "treat as mastered" so a missing state
    # file never nags.
    try:
        prog = progresslib.load(planlib._active()) if progresslib else {}
    except Exception:
        prog = {}
    session = data.get("session_id", "")
    is_tool = data.get("hook_event_name", "") in ("PreToolUse", "PostToolUse")
    progress_cli = RESEARCH / "progress.py"
    for d in located:
        did = d.get("id", "?")
        status = d.get("status", "open")
        princ = d.get("principle", "")
        decision = d.get("decision", "")
        why = (" " + d["why"]) if d.get("why") else ""
        choice = d.get("choice", "")
        mastered = bool(prog.get(did, {}).get("mastered"))
        # HARD GATE: model/loss/training-stage work on a decision you haven't DRILLED in quiz. On a
        # TOOL action this BOUNCES the action (reject -> permissionDecision deny) until the decision is
        # quizzed + mastered. Deliberately NOT deduped — it must fire on EVERY attempt, or the agent
        # could clear it just by retrying; only progress.mark (after a real quiz) clears it. This is the
        # ONE place plan-knowledge blocks: scoped to high-stakes phases + tool events, fail-open elsewhere.
        hard = (not mastered) and d.get("phase", "") in HIGH_STAKES
        # opt-in Haiku precision: don't BLOCK when the action only mentions this high-stakes decision
        # incidentally (the words sit in a comment, or it's a read-only probe / a labeled fixture) —
        # fall through to the soft status reminders instead. Fail-open: unsure -> still blocks.
        if hard and is_tool and modeljudge is not None and modeljudge.is_false_positive(
                hay, f"{d.get('phase')}-stage work (editing model / loss / training code) on «{decision}»"):
            hard = False
        if hard:
            if is_tool:
                items.append({"level": "reject", "sticky": True, "plan_decision": did,
                              "message": (
                                  f"🚦 BLOCKED — high-stakes {d.get('phase')}-stage action on «{decision}», a "
                                  f"decision you have NOT been quizzed on (principle: {princ}).{why} Bounced until "
                                  f"you prove you hold it. TO PROCEED: (1) QUIZ the user NOW — ask them to explain "
                                  f"«{princ}» as it governs this decision; grade sharp; correct misses. (2) When "
                                  f"they pass (or they explicitly say 'skip'), run `python3 {progress_cli} mark "
                                  f"{did}` to record mastery. (3) Retry this action — it will go through. The same "
                                  f"action keeps bouncing until mastered; do NOT skip the quiz.")})
                continue
            # not a tool event (a prompt): can't block a prompt — surface once as a sticky escalate
            if not _seen_then_mark(session, did):
                items.append({"level": "escalate", "sticky": True, "plan_decision": did,
                              "message": (f"🚦 GATE — you're about to do {d.get('phase')}-stage work on "
                                          f"«{decision}» but haven't DRILLED it in quiz (principle: {princ}).{why} "
                                          f"Quiz it first: `/trainlint:plan {did}`.")})
            continue
        # soft status reminders — deduped once per (session, decision)
        if _seen_then_mark(session, did):
            continue
        gate = ("" if mastered
                else f" (you haven't walked this decision in quiz yet — `/trainlint:plan {did}`)")
        if status == "open":
            items.append({"level": "escalate", "plan_decision": did,
                          "message": (f"⟦plan:{did}⟧ this acts on an UNDECIDED decision — "
                                      f"«{decision}» (principle: {princ}).{why} "
                                      f"Decide/confirm it before proceeding.{gate}")})
        elif status == "decided":
            # decided≠built: a choice typed (decided) is not the same as code/data on disk (built).
            # Fork the coaching so the agent drives the RIGHT next step instead of treating a paper
            # choice as if the work were done.
            try:
                is_built = planlib.artifact_exists(d)
            except Exception:
                is_built = False
            if is_built:
                items.append({"level": "coach", "plan_decision": did,
                              "message": (f"⟦plan:{did}⟧ built but UNVERIFIED — «{decision}» → "
                                          f"{choice} (principle: {princ}).{why} The artifact exists; "
                                          f"now VERIFY it holds (run it / check the result), then mark "
                                          f"verified.{gate}")})
            else:
                items.append({"level": "coach", "plan_decision": did,
                              "message": (f"⟦plan:{did}⟧ decided ON PAPER, NOT BUILT — «{decision}» → "
                                          f"{choice} (principle: {princ}).{why} A choice is typed but no "
                                          f"artifact exists on disk. BUILD it, then name the artifact on "
                                          f"the decision (`\"artifact\": \"...\"`) so it counts as built — "
                                          f"then verify.{gate}")})
        else:  # verified
            items.append({"level": "coach", "plan_decision": did,
                          "message": (f"⟦plan:{did}⟧ settled decision — «{decision}» → {choice} "
                                      f"(principle: {princ}).{why} Don't drift from it.{gate}")})
    return items, located
