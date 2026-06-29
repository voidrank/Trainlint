---
description: Build the project PLAN in the foreground — establish the full project context, decompose into decisions (written as you go), then quiz you on each. Or reconstruct it from a past session log.
argument-hint: "[review | status | from-log [session-id|path] | <free-text context>]"
---
The PLAN is the project's floor plan: an ordered list of DECISIONS (one JSONL line each in
`${CLAUDE_PLUGIN_ROOT}/research/plan.<active-project>.jsonl`), every one tagged with the
transferable PRINCIPLE that governs it. Active project = `${CLAUDE_PLUGIN_ROOT}/.active-project`.

## Scaffold first if the project isn't registered yet
`/trainlint:plan` is the single entry point — it registers a new project AND plans it. Before
anything else, check `${CLAUDE_PLUGIN_ROOT}/.active-project` and whether
`project.<name>.json` / `research/facts.<name>.json` exist for it. If the project the operator
named isn't registered yet (or `$ARGUMENTS` is a fresh project name with no substrate), run the
thin registrar first — `python3 "${CLAUDE_PLUGIN_ROOT}/research/new_project.py" <name>` — which
creates the empty per-project files and sets it active, then continue straight into the planning
flow below. It deliberately does NOT make you fill a pile of TODO fields; the facts get filled here,
while you read the actual code. (There is no separate `init` command — scaffolding is just plan's
first step when needed.)

## `from-log` — reconstruct the plan from a past session transcript (recovery)

The plan is **cheap and reconstructable** — a session that already worked it out contains every
decision. If the plan file is gone (e.g. a plugin update started a fresh cache) or you want to
rebuild it from a session, run `from-log`:

1. **Find the transcript.** Session logs are JSONL at `~/.claude/projects/<project-dir>/<id>.jsonl`.
   `$ARGUMENTS` after `from-log` may be a session id, a full path, or a hint ("a prior session")
   — resolve it (newest matching log if a hint). The file can be huge: **grep/stream it, never read
   it whole** (or hand it to a subagent).
2. **Extract what the session ESTABLISHED**, not every passing remark: the GOAL (name the pillars in
   it), each DECISION with its `choice`/`status`, the `principle` governing it, the `load_bearing`
   one, the 2-4 `pillar`s, any anti-prior rejections (`not_this`/`not_re`), and defined terms (for
   the glossary). The log is the source of truth for what was decided — you don't need to re-read all
   the code, only fill gaps the log left `open`/UNKNOWN.
3. **Write** `research/plan.<name>.jsonl` (+ `goal.<name>.txt`, `glossary.<name>.jsonl`, and fill the
   facts files) in the normal schema. Show me the reconstruction to correct, then quiz me on it.

(Mastery/progress is the one thing a log can't restore — it's accumulated, not stated. That's fine;
re-walking the quiz rebuilds it.)

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
- **Name every frozen component and CAPTURE its exact contract** (sample rate, power, n_codebooks,
  config_name, …). This is the methodology that turns the lint from *reciting discipline* into a
  *precise catch*: DERIVE each value by READING the component's real source — the code that
  trained/froze it, its config file, or the checkpoint metadata — never type a number from memory;
  mark UNKNOWN if you can't read it. Record what you read into `project.<name>.json` as
  `"codec_contract": {param: "value", ...}`. The `check_frozen_encode` verifier then flags any encode
  param that DIFFERS from this contract, value-for-value (the power=2.0 / wrong-sample-rate scar),
  the moment it's typed. The verifier is the general mechanism; the contract is per-project facts you
  read from that project — so the precise catch generalises to any codebase without hard-coding.
- **State where the project IS now vs the target.**
- **Mark unknowns as UNKNOWN, loudly.** A clearly-marked hole is context; a smooth guess is a landmine.

Present this full picture to me FIRST and let me correct it.

## Draft / update the plan (foreground)

1. Read `${CLAUDE_PLUGIN_ROOT}/.active-project`, then `research/goal.<name>.txt`,
   `project.<name>.json`, `research/facts.<name>.json`, and the ACTUAL code/configs they point to.
   If `$ARGUMENTS` is free text (e.g. "focus on the turn-based audio discussion"), let it steer you.
2. Do the COMPLETE-CONTEXT exposition above. Show me, let me correct it. Then distill the project's
   overall GOAL into ONE clear, concrete sentence and write it to `research/goal.<name>.txt` — this
   is the north star the compass shows every turn; make it concrete (what we're building + the bar
   for "done"), not a vague aspiration.
   As you define each term in the exposition, also record it to `research/glossary.<name>.jsonl` as
   `{term, plain, why}` — the project's living glossary the concept-gap quiz draws on (so a term the
   operator keeps asking about, e.g. DAC/s2/codec, can be drilled until it sticks).
3. Decompose into the ordered DECISIONS — every place a silent choice determines correctness (data,
   preprocessing, ckpt init, forward/mask/loss, loss weights, parallelism/batch, LR/schedule, eval,
   deploy). For each: id (kebab) | phase | decision (the question) | choice ("" + status `open` if
   genuinely undecided — never invent one) | principle (reuse a `quiz.jsonl` id if one fits, else
   coin a kebab id) | why | status(open|decided|verified) | match (regex recognizing an action that
   touches this decision). **WRITE each decision to `research/plan.<name>.jsonl` AS YOU CONFIRM IT —
   incrementally, not all at the end** (keep the header comment). So if the conversation diverges,
   the progress already on disk survives — the plan is never "started but unwritten".
   **`decided` ≠ built.** `decided` means a choice is TYPED with a rationale — it does NOT mean
   anything was made. A decision counts as **built** only when it names an `"artifact"` (a path/glob
   the choice produces) AND that artifact exists on disk; the surfaces show built-of-decided (`0/8
   built`) and paint a decided-but-artifact-less decision `✎` (paper), not `●` (built). So when a
   decision's choice is the kind that should PRODUCE something (a script, a dataset, a config), give
   it an `"artifact"` now (even before it exists) so "is it built yet?" is a fact on disk, not a vibe.
   If a choice NARROWS the project's scope, also add `"scope_drop": ["<phrase removed>", ...]` — the
   goal↔scope checker flags any dropped phrase still present in `goal.<name>.txt`, so the north star
   can't keep advertising a target a decision already abandoned (the multi-track→text-only scar).
   Mark exactly ONE decision `"load_bearing": true` — the open decision that most gates the rest
   (the cheapest test that could invalidate the whole plan). That one becomes the **main thread**.
   Also mark the **2-4 PILLARS** `"pillar": true` — the project's CORE dimensions, the things it
   fundamentally IS (e.g. the codec contract, the text/audio layout/interleave, the loss/abstain
   behavior). Pillars stay in the compass every turn EVEN WHEN decided, so a core dimension can't
   silently drop out of view. (`load_bearing` = the one thing to drive NEXT; `pillar` = a thing the
   project always rests on.) And when you write the GOAL (step 2), NAME the pillars in it — don't
   collapse a multi-component project into the single flashiest component.
   **Anti-prior:** if a decision REJECTS an option you (the agent) would otherwise keep drifting
   toward — because your context is saturated with it (e.g. "build on repo A, NOT in repo B
   codebase / NOT repo B's codec"; "fresh-from-base, NOT resume a prior duplex ckpt") — pin it with
   `"not_this": "<the rejected usage in plain words>"` and `"not_re": "<regex for an action drifting
   toward it>"`. Make `not_re` match the rejected *usage*, NOT the legitimate reference (so "borrow
   repo B's recipe/data" must NOT trip it). The doorman then catches that drift on every action and the
   compass keeps it in front of you every turn — so a strong prior can't quietly win back the decision.
4. **While establishing context, also FILL the facts files** the registrar left empty (you're the one reading
   the code): `project.<name>.json` (the doorman's danger patterns — bad_storage_re,
   locked_configs_re, preproc_trap_re/preproc_ok_re, frozen_component, the *_example fields; see
   `project.example.json`) and `research/facts.<name>.json` (runs_glob, direction_regex,
   candidate_moves, trunk_checks; see `research/facts.example.json`). Leave a key empty rather than fake it.
5. **Quiz me** — once the decisions are written, walk them right here (this is the quiz; there is no
   separate `/trainlint:quiz` command). Use `research/progress.py` for the mastery bookkeeping:
   `progress.targets(plan)` returns the decisions that are new, changed, or not-yet-mastered — drill
   only those (never re-drill a mastered+unchanged one). If `$ARGUMENTS` is a decision id / topic /
   concept, restrict the walk to that. Pose **every**
   question through the **`AskUserQuestion` tool** (NOT plain text + end-of-turn — that produces no
   pop-up and the operator misses it): one correct option paraphrasing the answer, the `naive` wrong
   answer plus 1-2 plausible distractors, "Other" left for a free-text explanation. The answer comes
   back as a tool result, so grade against the principle, **answer SHARP** (concrete fact first,
   principle second, zero hedging), `progress.mark` the ones I get, and roll straight into the next
   decision IN THE SAME TURN — don't yield between questions. Drill misses with fresh scars (also via
   `AskUserQuestion`). Soft — "skip" exits.
6. **The closing REPORT — explain it like a person, end in motion.** When the decisions are written,
   give me a report a teammate who *just walked in* could follow — not a status dump in project patois.
   The layout below is layered (short first, expand only the one thing that matters); the **VOICE** is
   what makes it explainable, and the voice is the point of this step. Five rules for the voice:

   1. **Write from the reader's chair.** Assume I did NOT build this. The job is to make an outsider
      understand. Define each term the first time it appears, in one plain phrase. (The curse of
      knowledge: the writer forgets the reader doesn't share the jargon.)
   2. **Point at the real thing, not its codename.** Don't report `duplex-interleave-layout` /
      `time-grid-coherence`; say what it IS — "keeping the words and the audio from drifting apart."
      The id is a filename; lead with the plain meaning and keep the id as a trailing tag.
   3. **Known before new, joined by because/therefore.** Anchor each new idea to a familiar one, and
      show the causal chain so I see WHY one decision gates the next ("the codec is locked first
      because it's the clock everything else times against"). A report is a chain, not a list.
   4. **Concrete numbers beat abstract principles.** "5 words ≈ 230 ms of audio but barely any content,
      so a long sentence drifts" explains; "principle: anchor-to-codec-clock" does not. Lead the *why*
      with the mechanism/number; the principle name is secondary.
   5. **Cut the ceremony.** No "BLUF", no icon legends, no "details-on-demand" labels in the prose —
      that is talking *about* the report instead of giving it. Just explain.

   Then lay it out in this order (layered, but don't announce the layers):
   - **What we're building + where it stands** — one plain sentence, then `<built>/<decided> built ·
     <V> verified · <k> pillars · main thread → <plain name of the load_bearing decision>`. Lead with
     built-of-decided, not a bare decided count — "0/8 built" is the honest state; "8 decided" reads
     as almost-done when nothing's been made. (The report doorman bounces a plan walk that hides this.)
   - **The map** — the phase-grouped skeleton from `python3 research/plan.py` (don't hand-format it).
     A picture of the whole shape, not a list of options to pick from. (`✎` = decided on paper, `●` = built.)
   - **What's locked** — each `decided` one in a sentence: its plain meaning, what we chose, and (if it
     carries an anti-prior) what we ruled out and why. Say which are actually BUILT (artifact on disk)
     vs decided on paper — don't let a paper choice read as foundation. The foundation I can trust.
   - **The one thing everything waits on** — expand ONLY the `load_bearing` decision: the real problem
     in plain words, the concrete reason it gates the rest, and the cheapest test that could kill it.
   - **Next, and drive it** — one concrete action to settle that, then GO DO IT (propose the move and
     start). Don't stop to ask which decision I'd like to revisit — that hands priority back to me and
     kills momentum.
   - **Everything else** — one line: "<M> still open across <phase>→<phase> — ask about any, or
     re-run `/trainlint:plan <id>` to drill one." A pointer, not a dump.

   **End by returning the HTML report path.** After the prose report, render the visual report and
   hand me its path as the final line — `/trainlint:plan` and `/trainlint:execute-and-report` BOTH
   close the same way, on `HTML: <path>`. Run `python3 "${CLAUDE_PLUGIN_ROOT}/research/viz.py"
   <project>` and surface the `HTML: <path>` line it prints. Before any experiment this is fine, not
   an empty tool: `viz.py` detects the planning stage and renders the plan story (motivation · goal ·
   main thread · next) over a full-width decision spine, suppressing the empty timeline/tree. The
   prose report is still the substance and the main thread is still the destination — the HTML link
   is just how every plan/execute turn signs off, so I always have the one-glance picture to open.

   **This is enforced, not just asked.** A finished report is prose, not a tool action, so it used to
   reach no hook — the voice rules were persuasion the model drops at large context. The `Stop` hook
   (`hooks/reportcheck.py`) now reads the emitted report: if it walks the plan but skips the stance
   line or the map, leads with bare decision-ids, leans on undefined jargon (`cu_seqlens`, `TP=4`),
   or **omits the `HTML: <path>` sign-off** (the report wasn't rendered), it bounces ONCE for a
   rewrite. So the layout above — including running `viz.py` — is a contract the doorman checks, not a suggestion.

If the plan ends up only partly written (we ran out of room, got pulled away), that's fine — the
SessionStart briefing flags a registered-but-unwritten plan, the understanding-gate flags the
un-mastered decisions, and the compass keeps the goal + main thread visible every turn — so nothing
is silently dropped and the work stays pointed at the one thing that matters.

## `review` / `status`
Read the existing plan and report it back in the SAME explain-like-a-person voice as step 6 (the five
voice rules) — what we're building, where it stands, what's locked, and the one thing everything waits
on. `python3 research/plan.py` prints the phase-grouped map; wrap that map in plain prose, don't just
paste it. Change nothing on disk. Unlike the fresh-plan close, review is READ-ONLY: end on a
recommendation ("the cheapest next move is X — want me to?"), don't auto-start the action.

## Optional — offload the reading to the background planning engine (only for a huge codebase)
`/trainlint:plan` is the ONLY plan command. For a normal project, do the foreground flow above. If
gathering context would mean reading a very large codebase, this same command MAY offload the parallel
reading to its internal engine at `${CLAUDE_PLUGIN_ROOT}/research/plan.workflow.js` via the Workflow
tool (`scriptPath` it; `args: { project, pluginRoot }` — pass them or the script plans the wrong
project). It runs in the background and writes the plan itself. The engine is not a separate command —
it's an implementation detail of this one. Trade-off: you lose the live, interactive feel and come
back to a finished result. Default to the foreground flow above unless the codebase is genuinely too big.
