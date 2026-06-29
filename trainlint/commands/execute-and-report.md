---
description: Drive the project forward — pick the one decision everything waits on, propose & run the cheapest move to settle it, then report what happened (search-tree shape + HTML report) and record the outcome back into the plan
argument-hint: "[project | decision-id]"
---
`execute-and-report` is the **doing** half of the loop — `/trainlint:plan` decides, this one
ACTS and then SHOWS. It reads the plan, drives the single decision that gates the rest, and
folds the result back in. The always-on doorman (hooks) watches the work as you run it; this
command is what aims the work and what closes the loop afterward.

Active project = `${CLAUDE_PLUGIN_ROOT}/.active-project`. Plan lives at
`${CLAUDE_PLUGIN_ROOT}/research/plan.<active-project>.jsonl`. If there is **no plan yet**, stop and
tell me to run `/trainlint:plan` first — there is nothing to drive without decisions. If every
decision is already `verified`, skip the driving and go straight to the report.

## 1. Find the one thing everything waits on (the main thread)
Run `python3 "${CLAUDE_PLUGIN_ROOT}/research/plan.py" $ARGUMENTS` to print the phase-grouped map and
the **main thread** — the `load_bearing` open decision, the cheapest test that could invalidate the
whole plan. That decision is the target. (If `$ARGUMENTS` names a specific decision id, drive THAT
one instead — the operator gets to override the auto-pick.)

Read the target decision's `decision` (the open question), `why`, and `principle`. Read the project's
move-set and trunk-checks from `research/facts.<name>.json` (`candidate_moves`, `trunk_checks`) and
the danger patterns from `project.<name>.json` — those are the doorman's facts, so an action that
trips one (`bad_storage_re`, `locked_configs_re`, `preproc_trap_re`, a frozen-component encode that
breaks the `codec_contract`) will get caught as you run it. Knowing them up front means you propose a
move that won't bounce.

## 2. Propose the cheapest move that could SETTLE it — and drive it
Don't survey options and hand priority back to me. Pick the single cheapest experiment/action that
could decide the target — the one most likely to *invalidate* it if it's wrong (a trunk-check before a
branch: diff-vs-base, verify-data-distribution, power=1.0, trained-enough-epochs — match
`trunk_checks`). Say in plain words: **what we're testing, the concrete move, and what result settles
it which way.** Lead the *why* with the mechanism or number, not the principle id.

Then **GO** — start the move in the same turn. Write the script, kick the run, make the probe. End in
motion, not a menu. The doorman is live the whole time: if the action drifts toward a rejected prior
(the `not_re` patterns) or breaks a frozen contract, it will steer or bounce — that is the system
working, not a failure.

## 3. Record the outcome back into the plan
When the move produces a result, close the loop — don't leave the plan stale:
- Update the target decision in `research/plan.<name>.jsonl`: `open → decided` once the move picks a
  `choice` (write the choice + a one-line `why` grounded in the result), or `decided → verified` once
  a run confirms it. Never mark `verified` on a guess — only on an observed result.
- Harvest the run into the search tree so the lint can see it:
  `python3 "${CLAUDE_PLUGIN_ROOT}/research/harvest.py"` (it reads `runs_glob`/`direction_regex` from
  the facts). A move that produced no logged run just doesn't show as a tree node — that's fine.

## 4. Report — what happened, in explain-like-a-person voice
This half is the old `viz`/`lint` surface, unchanged in capability. Run both:
- `python3 "${CLAUDE_PLUGIN_ROOT}/research/lint.py" <project>` — the search-tree SHAPE (stalled /
  deepening / abandoned branches, walls that now match a paper). This is a LINT: describe the shape,
  never prescribe abandoning a branch — that judgment is mine.
- `python3 "${CLAUDE_PLUGIN_ROOT}/research/viz.py" <project>` — the self-contained HTML report (5-beat
  story · dated timeline · phase-ordered decision spine beside the search tree · knowledge-readiness
  edges). Show me the compact ASCII summary it prints to stdout, and send me the single HTML file it
  points at (the `HTML: <path>` line). It opens in any browser; each decision in the spine carries an
  expandable "💬 Ask about this" chatbot (browser-side Anthropic API, key stored only in the browser).

Wrap the output in the **explain-like-a-person voice** (same five rules `/trainlint:plan` closes
with — and the `Stop` report-doorman enforces them here too):
1. Write from the reader's chair — assume I didn't build this; define each term the first time.
2. Point at the real thing, not its codename — say "keeping the words and audio from drifting apart,"
   not `time-grid-coherence`; keep the id as a trailing tag.
3. Known before new, joined by because/therefore — a report is a causal chain, not a list.
4. Concrete numbers beat abstract principles — lead the *why* with the mechanism/number.
5. Cut the ceremony — no BLUF, no legends; just explain.

Lay it out: **what we just did + what it settled** → **where the plan stands now** (`<N>/<total>
decided · the new main thread`) → **the map** (the phase-grouped skeleton from `plan.py`, wrapped in
prose) → **the next thing everything now waits on**, and drive it. End in motion.

To absorb browser-side learnings back into the substrate (glossary terms + Q&A a viewer captured):
`python3 "${CLAUDE_PLUGIN_ROOT}/research/viz.py" <project> --absorb <viz-memory.json>` — glossary terms
append to `research/glossary.<project>.jsonl` (the SAME file `/trainlint:plan` drills) and the raw Q&A
to `research/clarify.<project>.jsonl`, then the HTML regenerates with both rendered under each
decision. Re-absorbing the same export is a no-op (deduped). Only run this when I ask — it WRITES.
