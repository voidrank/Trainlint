#!/usr/bin/env python3
"""Opt-in Haiku judge — fail-open behaviour (off by default, never blocks/adds fires)."""
import os
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(HOOKS))
import modeljudge          # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails += 1


# default (no HARNESS_MODEL) -> disabled, fail-open
os.environ.pop("HARNESS_MODEL", None)
check(modeljudge.enabled() is False, "model judge is OFF by default")
check(modeljudge.is_not_model_code("def forward(self,x): ...") is False,
      "is_not_model_code fail-open (off) -> False, so the regex floor stands")

# enabled flag set but no API key -> still disabled (fail-open)
os.environ["HARNESS_MODEL"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
check(modeljudge.enabled() is False, "HARNESS_MODEL=1 but no API key -> still disabled (fail-open)")
check(modeljudge.is_not_model_code("anything") is False, "no key -> is_not_model_code False")
os.environ.pop("HARNESS_MODEL", None)

print(f"\n{4 - fails}/4 passed")
sys.exit(1 if fails else 0)
