---
description: Build the project PLAN in the foreground — establish the full project context, decompose into decisions (written as you go), then quiz you on each
argument-hint: "[review | status | <free-text context>]"
---
The PLAN is the project's floor plan: an ordered list of DECISIONS (one JSONL line each in
`${CLAUDE_PLUGIN_ROOT}/research/plan.<active-project>.jsonl`), every one tagged with the
transferable PRINCIPLE that governs it. Active project = `${CLAUDE_PLUGIN_ROOT}/.active-project`.

**Do this in the FOREGROUND — you are the protagonist, not a relayer.** Planning is interactive and
creative: you read the code, you reason it out, the user corrects you, you quiz them. Own that
journey in this conversation. (There's an optional background workflow for a huge codebase —
see the bottom — but the default is to do it here, live, so you stay engaged and the user can
interject. A background workflow hands you a finished blob and leaves you flat; don't default to it.)

## SYSTEM PROMPT — establish COMPLETE context BEFORE any decision (non-negotiable)

A plan is worthless if the context underneath it is fuzzy. Before you write a single decision,
lay out the project **from end to end, completely clearly** — this is the part that, when skipped,
left the operator lost ("what's DAC? what's s2?" mid-project). Rules for this exposition:

- **Trace the whole pipeline, start to finish, in order**: raw data → preprocessing → tokenizer/
  codec → packing/format → model (architecture; what is FROZEN vs trained) → forward → loss →
  training (parallelism, batch, LR, schedule, ckpt init) → eval protocol → deployment. No gaps.
- **Define every term the operator would need, in plain language** — no unexplained jargon. The
  first time a name appears (a codec like DAC, a stage like "s2", an abbreviation), say what it is.
- **Ground every claim in the actual code** — cite `file:line` for each structural fact. No
  "it probably works like…". If you state it, you've read it.
- **Name every frozen component and its exact contract** (sample rate, power, n_codebooks, …).
- **State where the project IS now vs the target.**
- **Mark unknowns as UNKNOWN, loudly.** A clearly-marked hole is context; a smooth guess is a landmine.

Present this full picture to me FIRST and let me correct it.

## Draft / update the plan (foreground)

1. Read `${CLAUDE_PLUGIN_ROOT}/.active-project`, then `research/goal.<name>.txt`,
   `project.<name>.json`, `research/facts.<name>.json`, and the ACTUAL code/configs they point to.
   If `$ARGUMENTS` is free text (e.g. "focus on the turn-based audio discussion"), let it steer you.
2. Do the COMPLETE-CONTEXT exposition above. Show me, let me correct it.
3. Decompose into the ordered DECISIONS — every place a silent choice determines correctness (data,
   preprocessing, ckpt init, forward/mask/loss, loss weights, parallelism/batch, LR/schedule, eval,
   deploy). For each: id (kebab) | phase | decision (the question) | choice ("" + status `open` if
   genuinely undecided — never invent one) | principle (reuse a `quiz.jsonl` id if one fits, else
   coin a kebab id) | why | status(open|decided|verified) | match (regex recognizing an action that
   touches this decision). **WRITE each decision to `research/plan.<name>.jsonl` AS YOU CONFIRM IT —
   incrementally, not all at the end** (keep the header comment). So if the conversation diverges,
   the progress already on disk survives — the plan is never "started but unwritten".
4. **While establishing context, also FILL the facts files** init left empty (you're the one reading
   the code): `project.<name>.json` (the doorman's danger patterns — bad_storage_re,
   locked_configs_re, preproc_trap_re/preproc_ok_re, frozen_component, the *_example fields; see
   `project.mimo.json`) and `research/facts.<name>.json` (runs_glob, direction_regex,
   candidate_moves, trunk_checks; see `research/facts.mimo.json`). Leave a key empty rather than fake it.
5. **Quiz me** — once the decisions are written, walk them as `/trainlint:quiz` does: pose each
   decision's governing principle as a question, grade against the principle, **answer SHARP**
   (concrete fact first, principle second, zero hedging), drill misses with fresh scars, and
   `progress.mark` the ones I get. Soft — "skip" exits.

If the plan ends up only partly written (we ran out of room, got pulled away), that's fine — the
SessionStart briefing flags a registered-but-unwritten plan and the understanding-gate flags the
un-mastered decisions, so nothing is silently dropped.

## `review` / `status`
Just read the existing plan and show it grouped by phase with status icons (✓ verified · decided
○ open), calling out `open` and `decided`-but-unverified ones. Change nothing. (`python3
research/plan.py` prints this.)

## Optional — offload the reading to a background workflow (only for a huge codebase)
If gathering context would mean reading a very large codebase, you MAY offload the parallel reading
to `${CLAUDE_PLUGIN_ROOT}/workflows/plan.workflow.js` via the Workflow tool
(`args: { project, pluginRoot }` — pass them or the script plans the wrong project). It runs in the
background and writes the plan itself. Trade-off: you lose the live, interactive feel and come back
to a finished result. Default to the foreground flow above unless the codebase is genuinely too big.
