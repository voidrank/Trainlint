# DESIGN — design philosophy / meta knowledge

> **Before you add any trigger / check, read this page first.**
> This file is the plugin's "memory." The code (`triggers.jsonl` / `checks.jsonl`) will keep growing—
> almost all future work is just "adding logic." But **why it is designed this way** is the unchanging root. Any logic added by whoever comes later (including a future
> Claude), if it violates the principles below, is better left unadded. Rules are leaves; this page is the trunk.

---

## 0. In one sentence: it is a "doorman"

It stands between the user and the agent (Claude). Each time **the user sends a message** or **the agent is about to act** (run a command / edit a file /
send a file), the doorman takes a look and does exactly one of four things:

| Doorman's action | Channel (in code) | Who perceives it |
|---|---|---|
| **Let through** | no output | nobody |
| **Quietly remind the agent** | `additionalContext` (coach) | only the agent |
| **Stop it and alert the user** | `systemMessage` (escalate) | user + agent |
| **Bounce it straight back to the agent** | `permissionDecision: deny` (reject) | only the agent (the user is not disturbed) |

Everything the whole plugin does is this doorman. The rest is detail.

---

## 1. What it is guarding against — silent failure (this is its reason to exist)

The pits we stepped into during training (power=2.0 mel, speech_mask, loss weights, scheduler getting overwritten, fake demos…)
**were all silent**: no error raised, loss kept dropping, train/infer wrong in the same consistent way, neither human nor agent noticed, and by the time it was caught
several days of GPU had already been burned.

> **The doorman's deepest duty: at the very moment the agent is about to commit one of these old mistakes, turn the "silent" into "loud"—
> either stop it itself, or alert the user, or quietly correct the agent. Never let a pit slip past silently again.**

Before adding any rule, ask first: "Is mine turning a **silent** failure mode into a loud one?" If what it guards against is a problem that will crash with its own
error, then no doorman is needed—the compiler/exception will shout on its own.

---

## 1b. Why a linter — not a prompt, a skill, or a workflow

The *form* was forced by the failure mode, not chosen. These bugs are **silent, continuous, and
you don't know you're making them.** To catch a mistake with those three properties, the tool
must be all of: **(1) ambient** (fires on every action, without being asked), **(2) at the moment**
the action happens, **(3) action-aware** (it sees the actual diff/command), **(4) able to verify
deterministically**, and **(5) able to actually stop the bad one — while never being able to lock
you out.** Only a hook-based linter is all five. Each alternative fails on the properties of *this*
failure mode:

- **Guidance / a static prompt (CLAUDE.md).** It is *persuasion you can ignore* — the model sees
  the rules and, especially at large context, drops them. It is always-on (noise + token cost: 20+
  rules become a wall the model skims, instead of the one relevant rule at the right moment). It is
  **blind** to the specific diff. And it can **enforce nothing.** A hook delivers the *same
  knowledge* but just-in-time, action-aware, and able to deny — so Trainlint keeps the rules as
  data and ships them through hooks, *just-in-time, not always-on*.
- **A skill / command.** It must be **invoked.** But the whole problem is that *you don't know
  you're about to make the mistake* — you'd never invoke a "check my training code" step at the
  exact moment you drop an off-by-one. A silent mistake has no trigger to invoke anything; a linter
  must watch *ambiently*, unbidden. (Where on-demand DOES fit — visualize, quiz, init — Trainlint
  uses commands. Hooks for the watching, commands for the deliberate.)
- **A workflow (multi-agent orchestration).** It is **heavyweight, deliberate, batch**: you open it
  explicitly for a big task, it spends many agents/tokens, runs to the end, and returns a snapshot.
  You can't run that on *every* edit — too slow, too expensive, it shreds the flow. And a workflow
  is itself an agent *reasoning*, so its work is heavy and lands in the main trajectory; the
  linter's check is off-thread, deterministic-first, and only the one-line hint surfaces. A workflow
  is a fine **complement** (an occasional deep audit), but the wrong tool for a per-action guard.

**One line:** a prompt can't enforce and gets ignored; a skill or workflow must be invoked, but you
don't know to invoke them — and both are too heavy and blind to the actual action. The only form
that catches *a mistake you didn't know you were making, at the instant you make it, cheaply, on
every action, and can actually stop it* is a linter. The form is dictated by the failure mode, not
selected from a menu.

---

## 2. Core principle: route every decision to the party that can actually judge it

This is the soul of the entire design. It is not "remind the agent at the right moment," but **sort by verifiability**:

| Who can judge this | How to handle it | Example |
|---|---|---|
| **A machine can judge** (facts/structure, machine-checkable) | auto **reject**, bounce silently, **don't bother the user** | data put on JFS, edited a locked base config |
| **Only a human can judge** (a design judgment, where the code is the ground truth and the agent is unreliable) | **escalate**, stop, explicitly ask the user to look at that piece of code | attention mask design, mutually-exclusive mask layout |
| **Neither can judge well but low risk** | **coach**, silently nudge the agent | general best-practice reminders |
| **Irrelevant** | let through | reading files, `ls` |

The criterion: **only affects "how to do it" → silent; changes/violates the user's instruction, or makes the user carry risk without knowing → speak up.**

---

## 3. The (future) small model only sorts, it is never the judge

Rule matching is regex (the fallback in `classifier.py`). In the future a small, fast model will be wired in to do intent sorting. **But remember:**

> **Never put a "right/wrong, irreversible" decision behind a probabilistic judge.**
> The small model only answers the **easy** question ("which class of action is this, who should judge it"), it does **not** answer the hard one ("is this actually right or wrong").
> Judging right from wrong is always done by: a deterministic script (`checks.py`) or a human.

Three guardrails keep the small model's mistakes cheap:
1. **Fatal machine-checkable items don't go through the model**—they are triggered directly by the action structure (`checks.py`); even if the model misroutes, it can't get past them.
2. **Asymmetric tuning**: for high-risk classes, prefer false positives (the user glances once more) over false negatives.
3. In the "human-verified" class the model is only responsible for **bringing the user's eyes to the right place**; it doesn't need to judge correctly.

---

## 4. Three inviolable safety invariants

1. **Prefilter is default-open** (`prefilter.py`): only drop things that can be **proven harmless** (pure reads, docs, self-edits);
   when unsure, always pass it through to be checked. **Never filter by topic/keyword at the door**—that is default-closed, and would
   **silently miss** dangerous actions that didn't match a keyword, making all the downstream judgment pointless.
   > **Self-edit spans every checkout.** `is_self_edit` drops not just the installed cache copy (`PLUGIN_ROOT`) but *any* checkout of
   > this plugin — dev repo, git worktree — found by walking the target's ancestors for a `.claude-plugin/plugin.json` whose name matches
   > ours (`_in_own_source_tree`). Without this, editing the harness's own rule sources gets flagged *by the harness itself*: a verifier file
   > NECESSARILY embeds the very keyword patterns it scans for, so the running harness reads its own source as "model code" and blocks the
   > edit — the dev repo becomes uneditable through the live plugin. (Found by dogfooding: adding `check_shapeflow.py` bounced off the quiz-gate.)
2. **The router fails open as a whole** (`router.py`): any internal error → do nothing, output nothing. If the doorman itself has a bug,
   it must never jam up the workflow because of it.
3. **Interception uses only `permissionDecision: deny`, never a non-zero exit code.** The router always does `exit 0`.
   > ⚠️ **hard-won footgun**: the moment the script the hook points to goes missing, `python` exit code 2 = Claude Code treats it as interception,
   > so **every** Bash/Edit/Write (even a sub-agent's) gets blocked, the session can't rescue itself, and recovery is only possible from a terminal
   > outside the session. So (a) interception goes through JSON, not the exit code; (b) when changing the script path the hook points to, **get the
   > new path in place first → then change settings → finally delete the old path**.

---

## 5. Soft by default, hard with a reason — intensity is deliberate, not decoration

The vast majority of interventions are **whispers** (coach). **Speaking up** (escalate) and **bouncing** (deny) are reserved for the deserving
few. "Whether the user should be told" is itself a design dimension:

- Just "how to do the job well" → silent (the user didn't ask to see your checklist).
- Changed/violated the user's instruction, or makes the user carry risk without knowing → speak up.
- Irreversible / trust-critical / a machine-certain violation → bounce. (Plus the ONE principle-based block: high-stakes work on an un-quizzed decision — see §5b.)

When adding a rule, **default to coach**; only raise to escalate once you've thought through "why the user must know"; only raise to reject when the
machine is "100% certain this is a violation and should be bounced."

---

## 5b. The ONE principle-based block: the high-stakes quiz gate (a deliberate, narrow exception)

§5 says block only on machine-certain *facts*; §7 records that a blanket "hard gate + receipt" was tried and **rejected**. One narrow exception was
later adopted **on purpose**, because the soft version measurably failed: in real sessions the plan-quiz GATE fired and was ignored ~100% of the time
(0 quizzes), so high-stakes work kept happening on decisions the operator had never drilled. A nag nobody acts on is worse than nothing — it trains you to ignore popups.

So `planaware` now **BLOCKS** (`reject` → `permissionDecision: deny`) when **all** of these hold:
- the action is a **tool** action (PreToolUse) — never a prompt;
- it touches a plan decision whose `phase` is **high-stakes** (`model` / `loss` / `train`);
- that decision is **not yet `mastered`** in the quiz.

The deny instructs the agent to QUIZ the user on the decision's principle, then run `python3 research/progress.py mark <id>` to record mastery; the same
action then goes through. (A single action can touch several decisions — you must clear **each** un-mastered high-stakes one. Keep `match` regexes tight so an edit doesn't trip unrelated gates.)

**Why this is NOT the rejected receipt system (§7):**
- **Narrow** — only high-stakes phases + tool events. Everything else stays soft (coach/escalate).
- **Clearable in-session** — a real quiz + `mark` lifts it; an explicit "skip" + `mark` also lifts it. No external process, no hash-bound receipt.
- **Catch-22-guarded** — the `progress.py mark` command is exempt from the gate, so the clear-path can never itself be blocked.
- **Fail-open** — any error in planaware → no items; it blocks ONLY via `permissionDecision`, never an exit code, so it still can never lock the session (§4).
- **Not deduped** — it fires on *every* attempt (only `mark` clears it), unlike the once-per-session soft reminders.

This is the single place plan/principle knowledge is allowed to block. **Keep it narrow.** Widening the high-stakes set, or gating prompts, drifts back
toward the rejected receipt system — don't.

---

## 6. Three layers: mechanism / general rules / project facts — separate "principle" from "fact"

Early versions welded the principle and the project facts into the same rule (`/jfs/`, `power=1.0`, `code 351`); switch projects and the whole thing dies.
Now it splits into three layers, the lower the more volatile:

1. **Mechanism** (`router.py` + `prefilter/checks/classifier/facts`)—untouched.
2. **General rules**—two kinds:
   - `triggers.jsonl` SECTION 1 (**portable kernel**): process/diagnostic discipline, **zero facts**, usable as-is across projects.
   - `triggers.jsonl` SECTION 2 + `checks.jsonl`: **general principles**, with all project-specific strings written as `{{placeholders}}`.
3. **Project facts** (`project.<name>.json`)—the fill-in-the-blank layer. `{{bad_storage_re}}`=`/jfs/|/nas/`,
   `{{preproc_trap_re}}`=`MelSpectrogram\(...`, `{{frozen_component}}`=`the frozen audio tokenizer` …

`facts.py` expands `{{...}}` into the current project's facts before they are used in match/inject/message.

> **The criterion: when writing a rule, any string that holds only for this project (paths, code numbers, library names, file names) gets abstracted into a `{{fact}}`.
> The rule body keeps only the "principle."** That way "switch project = swap a facts file, leave the rules untouched" (see §10).

One more rule of thumb: principle → add a rule line (referencing facts via `{{}}`); fact → into `project.<name>.json`;
then add one `tests/cases.jsonl` case + run `python3 tests/run.py`.

**Daily work is "adding rule lines / filling in facts," so this "why" page must be written down—the data will grow, the principles must not drift.**

---

## 7. Approaches we tried and rejected (don't backtrack)

- **Hard gate + receipt system** (every action must present a hash-bound PASS receipt to be allowed through): too heavy, too brittle,
  a missing receipt jams the whole flow and forces the user through a process. **Rejected**, replaced by the current soft injection.
  (A *narrow* hard gate was later adopted for high-stakes un-quizzed decisions ONLY — see §5b. It avoids every failure mode above by being
  scoped to 3 phases, clearable in-session via quiz+`mark`, catch-22-guarded, and fail-open — it is the deliberate exception, not a reopening of this door.)
- **Keyword regex as the sole filter at the door**: default-closed, rephrase it and it leaks. **Rejected**, the door changed to a structural filter that only looks at
  "read/write · internal vs external."
- **Letting a (small) model judge right/wrong directly**: a probabilistic judge will err across "this many checks." **Rejected**, the model only sorts.

---

## 8. Before adding a rule/quiz, ask yourself these six questions

0. **Does this still live on another project? Which principle is the living part?** (the most important filter)
   - Write the **principle** into the rule body / quiz headline; abstract the **project instance** (path, code number, library name, parameter value) into
     a `{{fact}}` in `project.<name>.json`, or demote it in the quiz to "…just an instance."
   - Criterion: if switching projects makes this **literally invalid**, then it tests trivia, not knowledge—extract the principle behind it.
   - Example: `power=2.0` is not domain knowledge, it's one instance of the "frozen component contract" principle (switch to CLIP and it's image norm).
1. Am I guarding a **silent** failure? (no → maybe no doorman needed)
2. **Who can judge** this? machine → check (reject); only a human → check (escalate); neither for sure → trigger (coach).
3. Is the intensity I'm giving **deserved**? (default coach, escalation needs a reason)
4. Will my `match` **collateral-damage** unrelated paths/wording? (give it `unless` or tighten it; add a counterexample to tests)
5. Is it safe **when it fails**? (a rule should only produce "let through / remind," never crash the router → that would fail-open into silence)

---

## 9. File map

```
hooks/prefilter.py     stage1 structural prefilter (read/write · default-open)
hooks/checks.py        stage3 deterministic fatal-item engine (reads checks.jsonl)
hooks/checks.jsonl     reject/escalate policy table (general principles + {{facts}}) ← most worth reviewing
hooks/classifier.py    stage2 intent sorting (currently regex fallback, awaiting small model)
hooks/facts.py         project facts loading + {{placeholder}} expansion
hooks/router.py        orchestrator: merge three stages → land on a channel; fail-open; always exit 0
triggers.jsonl         coach rules: SECTION1 portable kernel / SECTION2 general principles + {{facts}}
project.example.json   the example project's facts (fills {{placeholders}}); to switch projects, copy to project.<name>.json
.active-project        (optional) write the project name; otherwise env HARNESS_PROJECT, otherwise default example
tests/                 must run when adding rules; cases.jsonl is the behavior snapshot
```

## 10. Porting to another project

The rules don't change, only the facts do:

1. `cp project.example.json project.<new project>.json`, fill each key with the new project's facts
   (frozen component, unreliable-storage regex, preprocessing-trap regex, reference implementation, locked-config regex …).
2. `echo <new project> > .active-project` (or set `HARNESS_PROJECT`).
3. Keep `triggers.jsonl` SECTION 1 (process/diagnostics) as-is; if SECTION 2 + `checks.jsonl` have
   a failure mode unique to the new project that the existing principles don't cover, **add a new general-principle rule** (keep referencing facts via `{{}}`).
4. `python3 tests/run.py` (add a few cases for the new project).

Example: building a ViT classification project → in `project.vit.json` set `frozen_component`=CLIP,
`preproc_trap_re`=image normalize calls, `preproc_ok_re`=the correct mean/std……
the general rule `preproc-matches-frozen-config` automatically takes effect for CLIP, without one rule line changing.

## 11. Quiz: teach principles, not trivia

`quiz.jsonl` is the knowledge layer: the harness **reminds**, the quiz **tests** whether the operator truly understands. Each item is a mini-lesson:
`principle` (the transferable law, headline) → `context` (why you'd run into it) → `q` → `naive` (the common wrong answer = the cognitive gap)
→ `why` (the causal mechanism) → `a` (closing on the principle, with the domain demoted to "an instance").

Iron rule (same as §8 item 0): **the question tests the `principle`, the domain is just the scar that makes it clear.** If two questions are backed by the same
principle (e.g. `np.zeros`→OOD and `power=2.0`→OOD are both `frozen-component-contract`), they should
**share the same `principle`**—this is exactly the expression of "principles are transferable, instances are not."

Quizzing has two paths (the old opt-in mid-action quiz-gate was removed — it was dead, gated behind a
flag and unwired from the router): the deliberate `/trainlint:quiz` command over the plan's decisions,
and the `concept-gap-quiz` trigger. The trigger fires the moment a concept gap shows in the prompt
("what is X" / "I don't follow X") and **escalates a user-facing popup** (level `escalate`) rather than
a silent coach steer. It carries `sticky: true`, which exempts it from the plan-aware "settled
decision → downgrade to coach" rule: a concept gap is never a false alarm, even on a closed decision.
The popup surfaces and asks you to prove understanding; it never blocks.

---

## 13. Shape-flow: make the AGENT derive the data→model→loss flow itself

When you wire a new dataset into a model, or change the model's forward/loss, the silent killers are
**shape-COMPATIBLE but semantically wrong** wirings: an in-place vs next-token-shifted loss (does `logits[t]`
line up with `labels[t]` or `t+1`?); a weight/mask that broadcasts onto the WRONG axis instead of the same
`[B,T]`; an attention mask whose shape works but whose causal/bidirectional semantics are wrong; a multi-stream
layout on the wrong axis. None of these crash — a pure "do the shapes match numerically" check misses them all.

The `shape-flow` rule fires the moment such wiring happens and nudges the agent — **silently, coach-level** — to
DERIVE the end-to-end flow itself: walk one batch `dataloader → every layer → loss scalar`, and for each step record
not just the shape but (a) symbolic shape (`B,T,V,…`), (b) the *meaning* of each axis, (c) the *alignment invariant* a
silent bug would break. It is the **"agent derives first"** layer — distinct from the escalate-to-human rules
(`check_model_code.forward/.loss`), which ask a *person* to review correctness. Escalation here is not a separate gate:
it is a clause in the guidance — escalate ONLY when the derivation does not close.

Two firing surfaces, because the channels see different things (`classifier._haystack` gives a tool event only the
path, never the diff):
- **prompt surface** — `shape-flow-on-talk` in `triggers.jsonl` (portable kernel): fires while you're still *describing*
  the change, the earliest/cheapest moment to derive.
- **edit surface** — `shape-flow-on-edit` in `checks.jsonl` + `check_shapeflow.wiring`: inspects the diff content, so it
  fires on the actual forward/loss/dataloader/collate edit and can point at specific code.

The product is a persisted baseline (`research/shapeflow.<active-project>.md`); on the next change the agent diffs its
fresh derivation against it and flags which steps moved — the same "record it so the gap is tracked" pattern as
`concept-gap-quiz`'s glossary.

`derive-the-shape-flow` is the *on-paper* half. Its runnable companion is **`prove-wiring-on-cpu`** (`triggers.jsonl`,
portable kernel): it fires when you're about to *run* new forward/loss/mask code — a smoke/selftest, "verify the wiring",
or promoting to a GPU/full-scale launch — and nudges the agent to turn the derived invariants into **runnable asserts**
plus at least one **behavioral probe** that checks *meaning, not shape* (e.g. perturb a future position and assert an
earlier output is bit-identical iff causality holds; render the attention mask / loss positions as a grid). Run it with
a tiny random model — seconds, no checkpoint, no GPU — and promote only when green; a red or absent smoke means the GPU
run is measuring a bug, not the model. Derive (shape-flow) → prove (smoke + probe) → promote is the engineering pass.

### Portable data lints (modality-agnostic, zero facts)

Alongside the diagnostic/process rules, SECTION 1 carries the data-quality sins that recur in EVERY modality
(text, vision, audio, multimodal) — so they live in the portable kernel, not behind project `{{facts}}`:

- **`no-leakage-across-splits`** — the #1 self-deception. Dedup on the FULL identity key BEFORE splitting; split by
  GROUP (speaker/document/patient/scene) not by row; check NEAR-duplicates across splits (MinHash/SimHash for text,
  perceptual-hash/embedding for image/audio). Inflated eval from cross-split leakage is the default failure.
- **`train-infer-preprocessing-parity`** — training-serving skew: resize/normalize/channel-order/resample/tokenizer/
  padding must be byte-identical train vs infer. Share ONE code path, pin params + library versions, probe one sample
  through both paths and assert identical tensors.
- **`eval-set-contamination`** — a benchmark only measures generalization if the model never saw it. Check the eval set
  isn't (near-)duplicated in the training corpus; prefer post-cutoff / canary'd sets; else label the number "possibly
  contaminated."
- **`filler-is-not-neutral`** — any placeholder (pad token / np.zeros / silence / black frame / [MASK]) is a REAL input:
  it can encode OOD and it can leak into the loss as a learned target. Check what it encodes to, mask it from the loss
  on the same [B,T] grid, report the filler %. (The portable generalization of the audio-specific `padding-encodes-OOD`
  edit-surface check.)

These are prompt-surface coach nudges; the audio-specific *edit-surface* enforcement (`padding-encodes-OOD`,
`preproc-matches-frozen-config`, `codec-encode-params-match`, `train-read-on-fast-storage`) stays in `checks.jsonl`
with project `{{facts}}` — same principle, project-specific catch.
