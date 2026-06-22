#!/usr/bin/env python3
"""Plan-aware doorman tests — the three behaviours the audit demanded.

Run against the worked-example plan.mimo.jsonl (active project = mimo). No
session_id in the events -> dedupe is off -> deterministic.
"""
import sys
from pathlib import Path

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
           "tool_input": {"file_path": "/home/shiyil/mimo/deploy/stream.py",
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
         "tool_input": {"file_path": "/home/shiyil/mimo/tf_top1.py",
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
          "tool_input": {"file_path": "/home/shiyil/mimo/encode.py",
                         "new_string": "mel = MelSpectrogram(sample_rate=24000, power=2.0)"}}
out = router.decide(ev_mel)
check("user" in _surfaced(out).lower() or "confirm" in _surfaced(out).lower()
      or "mel" in _surfaced(out).lower(),
      "machine-certain mel-power verifier still escalates to the user (not downgraded)")

# 4. ANTI-PRIOR WATCH — drifting toward an explicitly rejected option is caught on ANY action,
#    coach-level (agent-facing), and names the decision that rejected it.
ev_drift = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "torchrun train.py --resume dup_d3v21s_nofreeze/latest"}}
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

print(f"\n{11 - fails}/11 passed")
sys.exit(1 if fails else 0)
