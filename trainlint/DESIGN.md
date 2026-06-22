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
- Irreversible / trust-critical / a machine-certain violation → bounce.

When adding a rule, **default to coach**; only raise to escalate once you've thought through "why the user must know"; only raise to reject when the
machine is "100% certain this is a violation and should be bounced."

---

## 6. Three layers: mechanism / general rules / project facts — separate "principle" from "fact"

Early versions welded the principle and the project facts into the same rule (`/jfs/`, `power=1.0`, `code 351`); switch projects and the whole thing dies.
Now it splits into three layers, the lower the more volatile:

1. **Mechanism** (`router.py` + `prefilter/checks/classifier/facts`)—untouched.
2. **General rules**—two kinds:
   - `triggers.jsonl` SECTION 1 (**portable kernel**): process/diagnostic discipline, **zero facts**, usable as-is across projects.
   - `triggers.jsonl` SECTION 2 + `checks.jsonl`: **general principles**, with all project-specific strings written as `{{placeholders}}`.
3. **Project facts** (`project.<name>.json`)—the fill-in-the-blank layer. `{{bad_storage_re}}`=`/jfs/|/nas/`,
   `{{preproc_trap_re}}`=`MelSpectrogram\(...`, `{{frozen_component}}`=`MiMo audio tokenizer` …

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
project.mimo.json      MiMo's facts (fills {{placeholders}}); to switch projects, copy to project.<name>.json
.active-project        (optional) write the project name; otherwise env HARNESS_PROJECT, otherwise default mimo
tests/                 must run when adding rules; cases.jsonl is the behavior snapshot
```

## 10. Porting to another project

The rules don't change, only the facts do:

1. `cp project.mimo.json project.<new project>.json`, fill each key with the new project's facts
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
