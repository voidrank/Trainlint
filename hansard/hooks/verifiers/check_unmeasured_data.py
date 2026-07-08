#!/usr/bin/env python3
"""Verifier: BLOCK a training/packing LAUNCH while the data-contract is unmeasured.

Why a verifier and not a regex: data correctness lives in the DISTRIBUTION, not in
any line of the launch command. A code-lint cannot see "30% is music" or "timestamps
were dropped". So this does not grade the command text — it reads the project's
MEASURED data-contract and refuses the launch if facts are still UNKNOWN (or the
non-substitutable fresh-eyes inspection hasn't been done).

Clearing it: run the profiler (project.json:data_lint_cmd) and fill the MEASURED
block so UNKNOWN empties, OR move a fact you accept the risk on into an
"ACKNOWLEDGED" block — turning a silent assumption into a recorded, owned risk.

Fail-OPEN: no data_contract / no launch / any error -> no fire. A project that
never defined a data-contract is simply not gated (this is opt-in per project).
"""
import re

import facts  # hooks/ is on sys.path (router inserts it)

# A hard reject must recognize an actual LAUNCH being executed, not a launcher word
# merely MENTIONED (in a heredoc, a string, a grep pattern, a comment) — otherwise it
# cry-wolfs on `cat > f` / `grep sbatch`. So we split the command into top-level
# segments and require a segment to START with a launcher token.
_SEG = re.compile(r"&&|\|\||[;|\n]")
_LAUNCH_HEAD = re.compile(
    r"^(sbatch|srun|torchrun|deepspeed|accelerate|mega-fish|mega-fish-dit|"
    r"(python3?\s+\S*(pretrain_fish|pretrain_dit))|"
    r"(bash\s+\S*submit_)|(\S*submit_\w+\.sh))\b",
    re.I,
)


def _is_launch(text):
    return any(_LAUNCH_HEAD.match(seg.strip()) for seg in _SEG.split(text or ""))


def _open_unknowns(contract):
    """Facts that block: still-UNKNOWN entries + an un-done fresh-eyes inspection."""
    out = []
    unk = contract.get("UNKNOWN")
    if isinstance(unk, dict):
        out += [k for k in unk if not k.startswith("_")]
    ncbd = contract.get("NOT_CHECKED_BY_DESIGN")
    if isinstance(ncbd, dict):
        if "NOT DONE" in str(ncbd.get("fresh_eyes_inspection", "")).upper():
            out.append("fresh_eyes_inspection")
    return out


def fire(text):
    try:
        f = facts.load_facts()
        contract = f.get("data_contract")
        if not isinstance(contract, dict):
            return (False, None)  # project defines no data-contract -> not gated
        if not _is_launch(text):
            return (False, None)  # not a training/packing launch (mention != execution)
        unknowns = _open_unknowns(contract)
        if not unknowns:
            return (False, None)  # measured / acknowledged -> let it through
        listed = ", ".join(unknowns[:8]) + (" …" if len(unknowns) > 8 else "")
        msg = (
            "🚦 BLOCKED — launching training/packing on UNMEASURED data. The data-contract "
            f"still has UNKNOWN facts: {listed}. A code-lint can't see data bugs (music in the "
            "speech, dropped timestamps, OOD silence) — they live in the distribution. "
            "TO PROCEED: run the profiler (project.json:data_lint_cmd) and fill the MEASURED "
            "block so these empty, OR move ones you accept the risk on into an \"ACKNOWLEDGED\" "
            "block in data_contract. 'Green' must mean measured-or-owned, never assumed."
        )
        return (True, msg)
    except Exception:
        return (False, None)  # FAIL OPEN
