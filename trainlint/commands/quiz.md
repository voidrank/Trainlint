---
description: Walk the project PLAN as a quiz — drilled on each decision's principle, only the new/changed/unmastered ones, until you've got them
argument-hint: "[decision-id | topic | all]"
---
The quiz is **plan-driven**: it walks the project's actual DECISIONS
(`${CLAUDE_PLUGIN_ROOT}/research/plan.<active-project>.jsonl`) and drills you on the transferable
PRINCIPLE governing each — using a freshly-generated scar each time, so you prove understanding,
not memory. Mastery is tracked, so it only ever drills what you haven't shown you understand.

Active project = `${CLAUDE_PLUGIN_ROOT}/.active-project`. This is **SOFT** — "skip" exits any
time, never blocks your work.

## Pick the target set
- Read the plan and the progress state. The helper `research/progress.py` does the bookkeeping:
  `progress.targets(plan)` returns the decisions that are **new, changed, or not-yet-mastered**
  (a decision whose content `fp` no longer matches what you were last quizzed on, or that was
  never passed). Never re-drill a mastered+unchanged decision.
- If `$ARGUMENTS` is a decision id or topic → restrict to that decision. If `all` → every decision.
- If the target set is empty → say "every plan decision is mastered — nothing to drill" and stop
  (offer `/trainlint:quiz all` to review anyway).

## Walk each target decision, in plan order
For each decision `d`:
1. Show me its `phase` + `decision` (the question at stake). **Withhold** `choice` and `why`. Ask
   me the governing question — frame it from `d.principle` (look the principle up in
   `${CLAUDE_PLUGIN_ROOT}/quiz.jsonl` for the canonical `context→q→naive→why→a` shape, but pose it
   about THIS project's decision). Wait for my answer.
2. When I answer, reveal `d.choice` + `d.why`, and judge whether I grasped the underlying
   **principle** (the transferable law) — not just the surface detail.
3. **If I got the principle →** mark it mastered: `progress.mark(name, d, mastered=True)` (stamps
   the decision's current fingerprint), and move to the next decision.
4. **If I missed it or couldn't answer →** don't let it go. GENERATE 2-3 fresh questions that drill
   the SAME `principle` with a DIFFERENT concrete scar (reuse other `quiz.jsonl` items sharing that
   principle, and/or invent new ones in the same shape). Present them as a numbered menu, let me
   choose, grade against the principle, and keep going on that principle until I clearly have it —
   then mark it mastered and continue. Leave it unmastered (don't mark) if I "skip".

## Close
Report what got mastered and what's still open. The unmastered/skipped decisions stay in the
target set, so they resurface the next time the plan changes. Keep it soft throughout.
