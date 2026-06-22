---
description: Build the project PLAN via a deterministic workflow (gathers full context, decomposes into decisions, GUARANTEES the plan is written), then reviews it with you and quizzes you
argument-hint: "[review | status | <free-text context>]"
---
The PLAN is the project's floor plan: an ordered list of DECISIONS (one JSONL line each in
`${CLAUDE_PLUGIN_ROOT}/research/plan.<active-project>.jsonl`), every one tagged with the
transferable PRINCIPLE that governs it. Active project = `${CLAUDE_PLUGIN_ROOT}/.active-project`.

**Why a workflow, not a to-do list here:** drafting a real plan spans many turns of code-reading,
and a long markdown to-do list gets dropped the moment the conversation diverges — that's how a
plan got "started" but never written, so the quiz never came. A workflow is a little program that
runs each step itself, so **writing the plan is guaranteed**, not left to the model to remember.

## `review` / `status`
Just read the existing plan and show it grouped by phase with status icons (✓ verified · decided
○ open), calling out `open` and `decided`-but-unverified ones. Change nothing. (`python3
research/plan.py` prints this.) Do NOT run the workflow for these.

## Otherwise — draft/update the plan

1. Get the active project name: read `${CLAUDE_PLUGIN_ROOT}/.active-project`.
2. **Launch the deterministic workflow** (this is the whole point — one reliable tool call):
   call the **Workflow** tool with
   `scriptPath: "${CLAUDE_PLUGIN_ROOT}/workflows/plan.workflow.js"` and
   `args: { "project": "<active-name>", "pluginRoot": "${CLAUDE_PLUGIN_ROOT}" }`.
   It runs in the background (Gather → Decompose → **Write**) and, when done, returns
   `{ exposition, decisions, planFile, decisionList }` — and `plan.<name>.jsonl` is now on disk.
   - If `$ARGUMENTS` is free text (e.g. "focus on the turn-based audio discussion"), pass it as
     `args.hint` so the workflow steers its gathering.
   - **Fallback only if the Workflow tool is unavailable:** do it inline — read goal/facts/code,
     write the COMPLETE start-to-finish context (plain language, every term defined, file:line
     grounded, UNKNOWNs marked), decompose into decisions, and WRITE `plan.<name>.jsonl` yourself
     BEFORE doing anything else. Don't let it sprawl unwritten.
3. **Review with me (interactive — this part can't be in the workflow):** present the returned
   `exposition` and the decision list. Let me correct anything; apply edits to the plan file.
4. **Quiz me (interactive):** immediately walk the decisions as `/trainlint:quiz` does — pose each
   decision's governing principle as a question, grade against the principle, **answer SHARP**
   (concrete fact first, principle second, zero hedging), drill misses with fresh scars, and
   `progress.mark` the ones I get. Soft — "skip" exits.

The workflow guarantees step 2 (the plan is written); steps 3–4 are the interactive half. Even if
the conversation diverges after, the SessionStart understanding-gate will resurface the
un-mastered decisions — so the plan→quiz chain no longer depends on the model's memory.
