---
description: Draft or review the project PLAN — the ordered decisions that define this run, each tagged with the principle that governs it
argument-hint: "[draft|review|status]"
---
The PLAN is the project's floor plan: an ordered list of DECISIONS (one JSONL line each in
`${CLAUDE_PLUGIN_ROOT}/research/plan.<active-project>.jsonl`), every one tagged with the
transferable PRINCIPLE that governs it. It is the representation Trainlint was missing — it
feeds two machines: the plan-quiz (walks you through each decision to teach it) and the
plan-aware doorman (locates a live action on the plan to escalate the right thing, instead of
keyword-matching). See `research/plan.mimo.jsonl` as a worked example. Fields per line:

  id | phase | decision | choice | principle | why | status(open|decided|verified) | match(regex)

Active project = the name in `${CLAUDE_PLUGIN_ROOT}/.active-project`.

## `draft` (default when the project has no plan yet)

1. Read `research/goal.<name>.txt`, `project.<name>.json`, and `research/facts.<name>.json`.
2. Decompose the WHOLE project into its decisions — every place a silent choice determines
   correctness: data (storage, train/val split), preprocessing (anything fed to a frozen
   component), checkpoint init, model/forward/mask, loss weights, parallelism/batch,
   LR/schedule, eval protocol, deployment/streaming. One decision per real fork.
3. For EACH decision fill: the `decision` question; the `choice` (leave `""` and status
   `open` if it is genuinely undecided — never invent one); the governing `principle` (reuse a
   `principle` id from `quiz.jsonl` if one fits, else coin a new kebab-case id); a one-line
   `why`; a `status`; and a `match` regex that recognises an action touching this decision.
4. Show me the draft as a numbered list grouped by `phase`. Let me correct it. THEN write
   `research/plan.<name>.jsonl` (preserve the header comment lines).

## `review` / `status`

Read the plan and show it grouped by phase, each decision with its status icon
(✓ verified / · decided / ○ open). Call out the `open` and the `decided`-but-not-`verified`
ones explicitly — those are where a silent mistake still hides. Change nothing unless I ask.
(`python3 research/plan.py` prints the same view.)

This is collaborative: propose, let me edit, then write. An unconfirmed decision stays `open` —
do not promote it to `decided`/`verified` without my say-so. Keep it SOFT; never block my work.

## After ANY plan change → enter quiz mode (on the changed decisions only)

Once you've written/updated the plan, IMMEDIATELY continue into the plan-quiz — but only over the
decisions that are **new, changed, or not-yet-mastered** (`progress.targets(plan)`; never re-drill
a mastered+unchanged decision). Walk them exactly as `/trainlint:quiz` does: pose each decision's
governing principle as a question, grade against the principle, drill the misses with fresh scars,
and `progress.mark` the ones I get. If nothing is new/changed/unmastered, say so and stop. Soft
throughout — "skip" exits.

