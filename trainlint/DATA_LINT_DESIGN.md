# Data-lint design for trainlint

> Status: draft, lives next to DESIGN.md.

## The problem this fixes

trainlint is, at its core, a **code/decision lint**. It is strong where a bug is a
*line you can match* (a wrong preprocessing flag, a dropped autoregressive shift) or
a *fork you can quiz* (two architecture choices with a right answer). It is weak on
**data**, because the hard data bugs do not live in any line — they live in the
**distribution**: a chunk of the corpus is the wrong modality, labels are
systematically offset, a region you think is "empty" encodes to one fixed garbage
token, a packing step quietly dropped a field. A regex doorman cannot see those.

Observed failure modes from a real session (these are the design requirements):

1. The agent **assumed the data** (its modality, its layout) instead of measuring it;
   nothing forced an up-front "enumerate / measure your data" step.
2. The agent worked **serially** (one probe at a time) and the doorman *reinforced*
   that by bouncing each exploratory step; there was no "list all facts, gather in
   parallel" discipline.
3. The doorman **false-fired on data tooling** — matching a decision keyword inside a
   read-only exploration command or a profiler. A word-matching code lint is wrong
   for data-exploration actions.
4. The only "contract" was read-from-code; there was **no contract for the actual
   bytes**.
5. There was **no primitive that forces a human to open N samples and look**.

## Core principle

> The unit of a model-lint is a **line**. The unit of a data-lint is a
> **distribution + a sample a human actually looked at**.

So a data-lint cannot be "match a regex and warn at write-time." It must **refuse to
let you trust data you have not measured and not looked at**, and turn that invisible
empirical property into an explicit, recorded, gated artifact — the same move the
frozen-component contract makes for a frozen component's hidden params, but verified
by *measuring* instead of *reading*.

## The biggest assumption, and the rule that defuses it

Every data rule assumes **you already know what to look for** — they catch only known
failure modes. The dangerous inversion: a "green" contract reads as "the data is
fine" when it only means "the checks I thought of passed." False confidence is worse
than no tool.

**Design rule #0: the contract has no "all green" state.** It is a ledger with three
kinds of entries, always all visible:

- `MEASURED` — value + **provenance** + **sample_size** + **measured_at** (can go stale).
- `UNKNOWN` — not yet measured; loud; may be passed only by an explicit human
  acknowledgement (which converts a silent assumption into a recorded, owned risk).
- `NOT_CHECKED_BY_DESIGN` — what we deliberately do not check — the *visible*
  incompleteness, so nobody mistakes silence for safety.

## Provenance trust ladder

A fact is only as good as how you know it:

1. `measured_from_sample` — a profiler measured it on real records (best; carries
   sample size + error).
2. `read_from_code` — read out of a config/source (good for frozen params).
3. `human_asserted` — someone said so (lowest; mark `VERIFY` until measured).

## The four pieces

| Piece | General / per-project | What it is |
|-------|----------------------|------------|
| **Data principle bank** | general (`quiz.jsonl`) | the transferable data lessons: look-at-it · measure-don't-assume · behavior-must-be-in-data · no-op-is-OOD · verify-data-before-algorithm · provenance-trust-ladder · split-on-the-true-identity-key · measure-the-contract-before-you-train. Every project inherits + is quizzed on them. |
| **Measured data-contract (ledger)** | per-project (`project.<name>.json`) | the known/unknown/not-checked table above, filled by a profiler, with provenance + sample_size + measured_at. |
| **Data doorman rule** | general (hooks) | gate the **launch** (train/pack), not the edit. Block when a contract fact is UNKNOWN-and-unacknowledged. **Exempt read-only exploration and profilers** — never bounce a grep/read/profile. |
| **Profiler mechanism** | general skeleton + per-format reader | the skeleton defines *what facts + which anomaly rules*; each project writes a reader for its format. |

## Non-substitutable steps (because measurement only catches the known)

- **Fresh-eyes inspection**: a recorded human action — "opened N random samples at
  each stage and looked." No metric may stand in for it.
- **The bank grows from scars**: every new data scar → a new principle and/or contract
  field. The lint's worth is that it *admits it is never complete* and keeps growing.
- **Re-measure on change**: data is not static; a `measured_at` older than the latest
  data revision is UNKNOWN again. Gate-at-launch-once is not enough.
- **Don't cry wolf**: a data gate that false-fires trains people to disable it; semantic
  precision + exploration-exemption is a correctness requirement, not polish.

## Open assumptions, kept visible on purpose

- The contract **schema is incomplete** by construction (unknown-unknowns). Mitigated,
  not solved, by fresh-eyes + an open bank.
- The **profiler can be wrong** ("measured" ≠ "correct"); it needs its own self-tests.
- Measurement is **sampled** at scale; point estimates need error bars, not false precision.

## Implemented so far

- The five data principles above are in `quiz.jsonl` (so every project is quizzed on them).
- The doorman: compound/piped read-only commands are now recognized and dropped
  (`hooks/prefilter.py`), and a launch on an unmeasured data-contract is blocked
  (`hooks/checks.jsonl` + `hooks/verifiers/check_unmeasured_data.py`), keyed off a
  project's optional `data_contract` block — opt-in per project, fail-open.
