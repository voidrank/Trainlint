#!/usr/bin/env python3
"""REPORT doorman tests (Stop event) — the surface the linter used to be blind to.

A finished message is prose, not an action, so it reached no hook; the voice rules
(commands/plan.md step 6) were pure persuasion. This binds to Stop and bounces a
report-shaped message that skips the spec's anchors. Run against plan.example.jsonl.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
os.environ["HARNESS_PROJECT"] = "example"   # deterministic: gate against the worked-example plan
import router  # noqa: E402

fails = 0
_tmp = Path(tempfile.mkdtemp())


def check(cond, msg):
    global fails
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        fails += 1


def _stop_event(text, *, active=False, name="transcript"):
    """Write `text` as the last assistant turn and return a Stop event pointing at it."""
    p = _tmp / f"{name}.jsonl"
    with p.open("w", encoding="utf-8") as f:
        f.write(json.dumps({"message": {"role": "user", "content": "go"}}) + "\n")
        f.write(json.dumps({"message": {"role": "assistant",
                                        "content": [{"type": "text", "text": text}]}}) + "\n")
    ev = {"hook_event_name": "Stop", "transcript_path": str(p), "session_id": "s"}
    if active:
        ev["stop_hook_active"] = True
    return ev


def _decision(out):
    return (out or {}).get("decision")


def _reason(out):
    return (out or {}).get("reason", "")


# A real patois report from a session: cites plan ids, NO stance line, NO map.
BAD = (
    "**Pinned: bootstrap from `checkpoints/stage3-ckpt`** at TP=4/EP=8.\n\n"
    "Going 30B MoE re-opens two decisions — `eff-batch` (now micro-batch + TP/EP) and "
    "`lr-regime` (MoE LR != dense numbers). Noted both in the plan.\n\n"
    "### Where this leaves you\n\n"
    "- **Codec:** the project codec — causal, raw waveform.\n"
    "- **Layout:** per-frame interleave, assistant-only loss.\n\n"
    "- **Base:** the stage3 checkpoint, 30B MoE, fresh start.\n"
    "- **Streaming risk:** closed by construction.\n\n"
    "### The concrete next move\n\n"
    "The first real build step is the data side: produce interleaved duplex packs ordered "
    "[user_t, asst_t] per frame, with an assistant-only loss_mask riding on the existing "
    "vq_mask/cu_seqlens machinery via the offline packed builder in rows.py and collator.py. "
    "Everything else — the duplex stage config off stage3, the MoE batch/LR re-grounding — sits "
    "on top of that layout being right.\n\n"
    "Want me to start there? You can also /trainlint:plan to drill the re-grounded decisions."
)

# A compliant report: plain language, the stance line, the phase-grouped map glyphs, ids as tags.
GOOD = (
    "We're building a Chinese voice model that listens while it talks.\n\n"
    "9/17 decided · 3 pillars · main thread → the input contract for the frozen codec.\n\n"
    "phase data\n  ✓ how workers read data (`data-storage`)\n  ● the silent-gap fill (`silence-padding`)\n"
    "phase loss\n  ○ the weight on empty frames (`empty-loss-weight`)\n\n"
    "What's locked: the codec is the clock everything times against, so it goes first. "
    "The effective batch (`eff-batch`) and the learning-rate plan (`lr-regime`) are re-grounded for MoE.\n\n"
    "The cheapest next move is to trace the offline packer — want me to?"
)

# 1. The bad report bounces with a concrete, spec-anchored reason.
out = router.decide(_stop_event(BAD, name="bad"))
check(_decision(out) == "block", "bad patois report -> decision:block")
check("stance" in _reason(out) and "map" in _reason(out),
      "the bounce names the two missing anchors (stance line + map)")
check("step 6" in _reason(out),
      "the bounce points at the standard it violates (commands/plan.md step 6)")

# 2. Loop guard: stop_hook_active means we already bounced once -> never bounce again.
out = router.decide(_stop_event(BAD, active=True, name="bad2"))
check(out is None, "stop_hook_active=true -> silent (one forced rewrite, no loop)")

# 3. A compliant report passes silently (no false positive on good prose).
out = router.decide(_stop_event(GOOD, name="good"))
check(out is None, "compliant report (stance + map + glossed ids) -> silent")

# 4. A short answer is not a report, even if it names a decision.
out = router.decide(_stop_event("Done — fixed the bug in `mel-power`, tests green.", name="short"))
check(out is None, "short answer (< report length) -> silent")

# 5. A long answer citing < 2 plan ids is ordinary prose, not a plan walk.
chatter = ("Here is a long explanation about `mel-power` and general training hygiene. " * 12)
out = router.decide(_stop_event(chatter, name="chatter"))
check(out is None, "long answer citing only one id -> not report-shaped -> silent")

# 6. Bare-codename branch: ids leading bullets, but stance + map present -> still bounces on rule 2.
bare = (
    "8/17 decided · 3 pillars · main thread → the codec contract.\n\n"
    "phase loss ○ ● ✓ main thread →\n\n"
    "- `eff-batch` is set to 512 via micro-batch and gradient accumulation across the workers.\n"
    "- `lr-regime` is 1e-4 with a linear warmup over the first few hundred steps, then cosine decay.\n"
    "- `mel-power` stays at 1.0 so the frozen codec sees the magnitude spectrogram it was trained on.\n"
    "These are the locked training knobs going into the next run. They are written into the stage "
    "config rather than left to a launcher default, which is a long enough paragraph to clear the "
    "minimum-report-length floor so the gate actually evaluates the body for the codename problem."
)
out = router.decide(_stop_event(bare, name="bare"))
check(_decision(out) == "block" and "codenames" in _reason(out),
      "ids leading their lines -> bounce on voice rule 2 (codenames, not meanings)")

# 7. Undefined-jargon density: stance + map present and ids glossed, but the body leans on raw
#    identifiers the plan-id check can't see (cu_seqlens, acme_codec_v2, rows.py, TP=4) -> bounce.
jargon = (
    "9/17 decided · 3 pillars · main thread → the codec contract.\n\n"
    "phase data ○ ● ✓ main thread →\n\n"
    "The effective batch (`eff-batch`) and learning rate (`lr-regime`) are re-grounded for the new run. "
    "We produce interleaved packs ordered user_t then assistant_t, with an assistant-only loss_mask "
    "riding on the existing vq_mask and cu_seqlens machinery in rows.py and collator.py, feeding the "
    "acme_codec_v2 codec at TP=4 and EP=8.\n\n"
    "What's locked: the codec is the clock everything times against, so it goes first; the streams are "
    "interleaved per frame so the model hears the user while it speaks; and the loss only counts the "
    "assistant frames so it learns to stay quiet. The cheapest next move is to trace the offline packer "
    "and confirm the mask lines up — want me to start there? This paragraph pads the body comfortably "
    "past the minimum report-length floor so the gate evaluates the jargon density in the lines above."
)
out = router.decide(_stop_event(jargon, name="jargon"))
check(_decision(out) == "block" and "jargon" in _reason(out),
      "raw identifiers (cu_seqlens, acme_codec_v2, rows.py, TP=4) -> bounce on voice rule 1")

# 8. Non-Stop events are untouched by the report gate (no regression to the action paths).
out = router.decide({"hook_event_name": "Stop", "transcript_path": str(_tmp / "missing.jsonl")})
check(out is None, "missing transcript -> fail-open silent (never raises)")

print(f"\n{10 - fails}/10 passed")
sys.exit(1 if fails else 0)
