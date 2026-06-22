#!/usr/bin/env python3
"""Regression tests for the full pipeline (prefilter -> checks -> decide).

Run after editing triggers.jsonl / checks.jsonl / any hook module:
    python3 tests/run.py
Exits non-zero if any case fails, so it can gate a commit.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "hooks"))
import router  # noqa: E402

CASES = Path(__file__).resolve().parent / "cases.jsonl"


def channel_of(out):
    if out is None:
        return "none"
    hso = out.get("hookSpecificOutput", {})
    if hso.get("permissionDecision") == "deny":
        return "deny"
    if out.get("systemMessage"):
        return "escalate"
    if hso.get("additionalContext"):
        return "coach"
    return "none"


def main():
    fails = 0
    total = 0
    for line in CASES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        c = json.loads(line)
        total += 1
        out = router.decide(c["event"])
        ch = channel_of(out)
        blob = json.dumps(out, ensure_ascii=False) if out else ""
        missing = [s for s in c.get("expect_contains", []) if s not in blob]
        ok = (ch == c["expect_channel"]) and not missing
        if ok:
            print(f"ok    [{ch:8s}] {c['desc']}")
        else:
            fails += 1
            print(f"FAIL  [{ch:8s}] {c['desc']}")
            if ch != c["expect_channel"]:
                print(f"        channel: expected {c['expect_channel']}, got {ch}")
            if missing:
                print(f"        missing substrings: {missing}")
    # --- model backend: union-merges semantic catches on top of the regex floor ---
    import classifier
    classifier.set_backend(lambda d: [{"class": "coach", "name": "MOCK", "message": "MOCK-EXTRA"}])
    total += 1
    msgs = {x["message"] for x in classifier.classify(
        {"hook_event_name": "UserPromptSubmit", "prompt": "重新train吧"})}
    if "MOCK-EXTRA" in msgs and any("distribution" in m for m in msgs):
        print("ok    [model   ] backend merges with regex floor")
    else:
        fails += 1
        print("FAIL  [model   ] backend should union with regex floor")
    classifier.set_backend(None)

    print(f"\n{total - fails}/{total} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
