#!/usr/bin/env python3
"""Plan-aware doorman tests — the three behaviours the audit demanded.

Run against the worked-example plan.example.jsonl (active project = example). No
session_id in the events -> dedupe is off -> deterministic.
"""
import sys
from pathlib import Path

import os
os.environ.setdefault("HARNESS_PROJECT", "example")  # tests need an explicit project

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import planaware  # noqa: E402
import router     # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


def _surfaced(out):
    """What the user actually sees (the escalate channel)."""
    return (out or {}).get("systemMessage", "")


def _ctx(out):
    return ((out or {}).get("hookSpecificOutput", {}) or {}).get("additionalContext", "")


# 1. Acting on an OPEN decision -> ESCALATE, with the decision + its principle.
# absolute paths OUTSIDE the plugin root, else prefilter treats them as self-edits and drops them
ev_open = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
           "tool_input": {"file_path": "/home/user/proj/deploy/stream.py",
                          "new_string": "use the bidirectional encoder for streaming"}}
items, located = planaware.assess(ev_open)
check(any(i.get("plan_decision") == "streaming-encoder" and i["level"] == "escalate"
          for i in items),
      "OPEN decision (streaming-encoder) -> escalate item with its id")
out = router.decide(ev_open)
check("streaming-encoder" in _surfaced(out) or "UNDECIDED" in _surfaced(out),
      "router surfaces the undecided-decision escalation to the user")

# 2. Acting on a VERIFIED decision via a throwaway probe -> the keyword 'forward-change'
#    escalation is DOWNGRADED (no false user interruption), and the right principle is
#    delivered as a coach note. This is the audit's central false-alarm case.
ev_tf = {"hook_event_name": "PreToolUse", "tool_name": "Write",
         "tool_input": {"file_path": "/home/user/proj/tf_top1.py",
                        "content": "teacher forcing top-1; uses a sampler and top_p over logits"}}
items, located = planaware.assess(ev_tf)
check(any(d.get("id") == "eval-protocol" and d.get("status") == "verified" for d in located),
      "probe script locates onto the VERIFIED eval-protocol decision")
out = router.decide(ev_tf)
check("forward / mask / sampling" not in _surfaced(out),
      "the keyword forward-change escalation is NOT pushed to the user (downgraded)")
check("free-running" in _ctx(out) or "eval-protocol" in _ctx(out),
      "the agent still gets the right principle (free-running) as a coach note")

# 3. Machine-certain (verifier-backed) items are NEVER downgraded, even on a verified
#    decision. A mel edit hits the real mel-power verifier AND the verified mel-power plan
#    decision; the verifier escalation must still reach the user.
ev_mel = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
          "tool_input": {"file_path": "/home/user/proj/encode.py",
                         "new_string": "mel = MelSpectrogram(sample_rate=24000, power=2.0)"}}
out = router.decide(ev_mel)
check("user" in _surfaced(out).lower() or "confirm" in _surfaced(out).lower()
      or "mel" in _surfaced(out).lower(),
      "machine-certain mel-power verifier still escalates to the user (not downgraded)")

# 4. ANTI-PRIOR WATCH — drifting toward an explicitly rejected option is caught on ANY action,
#    coach-level (agent-facing), and names the decision that rejected it.
ev_drift = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "torchrun train.py --resume dup_run21s/latest"}}
items, _ = planaware.assess(ev_drift)
check(any("REJECTED" in i["message"] and i.get("plan_decision") == "ckpt-init" for i in items),
      "anti-prior: resuming from a previous duplex ckpt is flagged (cites ckpt-init)")
out = router.decide(ev_drift)
check("REJECTED" in _ctx(out) and "Needs your check" not in _surfaced(out),
      "anti-prior reaches the agent as a coach (not a user-facing escalation)")
# a LEGITIMATE mention (borrowing the recipe / fresh-from-base) must NOT trip the watch
ev_ok = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
         "tool_input": {"command": "torchrun train.py --init_from base --fresh"}}
items_ok, _ = planaware.assess(ev_ok)
check(not any("REJECTED" in i["message"] for i in items_ok),
      "anti-prior does NOT fire on the legitimate fresh-from-base path (no false positive)")

# 5. HARD GATE — model/loss/training-stage work on an UN-DRILLED decision now BLOCKS the tool
#    action (reject -> permissionDecision deny) until the decision is quizzed + mastered. The
#    gate-clearing `progress.py mark` command is exempt (catch-22 guard); non-high-stakes never gate.
_sp = HOOKS.parent / "research" / ".state" / "example.plan-progress.json"    # ensure nothing mastered
_bak = _sp.read_text() if _sp.exists() else None
try:
    _sp.unlink()
except OSError:
    pass


def _pd(out):
    return ((out or {}).get("hookSpecificOutput", {}) or {}).get("permissionDecision")


def _reason(out):
    return ((out or {}).get("hookSpecificOutput", {}) or {}).get("permissionDecisionReason", "")


ev_hs = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
         "tool_input": {"file_path": "/home/user/proj/loss.py", "new_string": "empty_loss_weight = 0.5"}}
out_hs = router.decide(ev_hs)
check(_pd(out_hs) == "deny",
      "hard gate: high-stakes un-drilled tool action is BLOCKED (permissionDecision deny)")
check("BLOCKED" in _reason(out_hs) and "mark" in _reason(out_hs),
      "the deny reason instructs the agent to quiz, then run the mark command to clear it")
ev_clear = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": f"python3 {HOOKS.parent}/research/progress.py mark empty-loss-weight"}}
check(_pd(router.decide(ev_clear)) != "deny",
      "catch-22 guard: the `progress.py mark` command itself is never blocked by the gate")
ev_lo = {"hook_event_name": "PreToolUse", "tool_name": "Write",
         "tool_input": {"file_path": "/home/user/proj/eval_metric.py", "content": "aggregate accuracy top-1"}}
check(_pd(router.decide(ev_lo)) != "deny",
      "non-high-stakes (eval-stage) work does NOT block")

# 6. FOREIGN EXEMPTION — an edit inside a tree marked .hansard-foreign skips the plan gate
#    entirely (a repo that is NOT a managed training project, e.g. hansard-builder). Runs
#    while the gate is armed (state deleted), so it proves the exemption bypasses a live gate.
import tempfile  # noqa: E402
_foreign = Path(tempfile.mkdtemp())
(_foreign / ".hansard-foreign").write_text("not a managed project\n")
ev_foreign = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
              "tool_input": {"file_path": str(_foreign / "loss.py"), "new_string": "empty_loss_weight = 0.5"}}
items_f, located_f = planaware.assess(ev_foreign)
check(items_f == [] and located_f == [],
      "foreign-marked tree: plan gate skipped (no items, no located)")
check(_pd(router.decide(ev_foreign)) != "deny",
      "foreign edit with the SAME high-stakes content is NOT blocked (vs bare path, which IS)")

if _bak is not None:
    _sp.write_text(_bak)

print(f"\n{17 - fails}/17 passed")
sys.exit(1 if fails else 0)
