# Trainlint

### It trained fine. That's the bug.

**A linter for AI/ML training — and for the AI agent doing it.** It catches the *silent*
mistakes (the ones that don't crash, where loss keeps dropping and the model is quietly
wrong) at the moment they happen — before they cost you a week of GPU.

> You lint your code. Your coding agent is now writing your *training* code. Lint that too.

---

## The week you'll never get back

You change one thing, launch, wait. Loss drops, nothing crashes — the output is just *off*.
Days of retraining and tuning later, you diff against the reference line by line and find it:
**one preprocessing default, silently wrong.** The run was dead on arrival. Nothing told you,
because nothing crashed.

Now an AI agent writes that code for you, just as confidently. Reading every line against a
reference by hand is an inhuman job — that's a linter's job. **Trainlint checks the line the
moment it's written.**

![the frustrating loop vs. catching it at write time](docs/the-loop.png)

## How it works — it's a bouncer at the door

Every time the agent goes to do something — change a config, launch a run, touch the model
code — it has to walk past one bouncer. The bouncer has seen runs blow up before. They size up
the move and do one of four things:

🚫 **"Nope, not this one."** — They recognize a move that's *already* sunk a run before: a
known-bad setting, the kind of silent misconfig that doesn't crash but quietly poisons the
output. They don't lecture, they don't ping you — they just turn it around at the door and say
*redo it*. A known disaster never makes it inside, and **your phone stays quiet**, because why
would you need to rule on something they're already sure about?

🙋 **"Hey — you'll want to see this."** — This is the only move that taps *you* on the
shoulder. Someone changed something no machine can check — a tweak to how the model trains or
sees its input, where there's no test that says right or wrong, but if it *is* wrong it eats a
week. So they hand you the diff: *you* look. Because here, your eyes are the only instrument that
works.

👈 **"Psst — double-check that."** — Not sure, not fatal, just worth a word. They lean over and
mutter it to the agent, who can fold it in or shrug it off. You never even hear it happen.

😶 **(says nothing)** — 99% of the time. Nothing's off, the door's open, the agent walks
through.

The trick is what *doesn't* reach you. Not "everything suspicious" — only that one sliver where
no machine can check it and no quiet word can fix it. **That's** why you don't end up muting
them: they only ever bug you when you're genuinely the last line of defense.

And two house rules keep them safe to leave on:

- **They bounce a move, never your direction.** Even a "nope" stops *one action* — your overall
  approach is still yours. (A plateau is often right before the breakthrough; they won't prune
  your search.)
- **They can't ever lock you out.** They turn a bad move away with a polite "denied," never by
  knocking the door off its hinges — a bug in the bouncer is always safer than the bug they're
  there to stop.

## What it catches — silent-wrong has only a few shapes

A bug that crashes is the easy kind — you fix it and move on. The expensive ones are *silent*:
loss still drops, nothing errors, the model is just quietly wrong. They feel infinitely varied,
but they aren't — they keep coming back in a handful of shapes. Trainlint knows the shapes.

**1. Training and inference quietly disagree.** The model learns under one setup and runs under
another: input preprocessing that no longer matches the frozen encoder it was built for; a mask
or an off-by-one shift that's there at training time but not at generation (or the reverse);
padding the tokenizer was never trained on. Each one feeds the model something it never saw —
nothing crashes, the output is just subtly, persistently off.

**2. The model takes the shortcut you left open.** Hand a weak component an easy crutch — say, a
peek at the answer during training — and it learns to lean on the crutch instead of the hard
signal you actually care about. Scores *with* the crutch look great; take it away at generation
and the whole thing collapses.

**3. You're not actually measuring the model.** An eval or a demo that would look the same
whether the model is brilliant or broken — a proxy that flatters you while telling you nothing.

**4. The value you wrote isn't the value that ran.** Config stacks up from flags, files, env,
and framework defaults, and the last writer silently wins. You burn a day tuning a number that
was overridden before the run even started.

**5. The ground rots under you.** Training reads from storage that corrupts under concurrent
load — fine in a ten-minute smoke test, fatal six hours into the real run.

Each shape is a **principle, not a project fact** — it survives a move to a new model or
codebase. Your project's specifics (which encoder, which path, which magic number) live in one
swappable facts file; the shapes don't change. That's ~20 rules today, every one an instance of
the families above.

## Why it's designed this way

**Why a linter at all — not a prompt, a skill, or a workflow?** The failure mode dictates the
form. These bugs are *silent, continuous, and you don't know you're making them.* A static prompt
(CLAUDE.md) is persuasion you can ignore — always-on noise, blind to the actual diff, and it can't
stop anything. A skill or a workflow has to be *invoked* — but you'd never invoke a "check my
training code" step at the exact moment you drop an off-by-one, and standing up a multi-agent
workflow for every edit is both too heavy and backwards: it makes *you* run the orchestration out
front, when what you want is the opposite — a copilot working quietly behind you, taking the grunt
work off your hands, unasked. Only something **ambient** (fires on every action, unbidden), **at the
moment**, **action-aware** (sees the diff), and **able to actually stop the bad one** catches a
mistake you didn't know you were making — that's a linter. (Trainlint still uses commands for the
deliberate parts — `plan`/`quiz`/`viz`/`lint`/`init` — and ships its rules just-in-time, not always-on.)

And given it's a linter:

- **A linter, not a gate.** Research is non-monotonic — a plateau often comes right before a
  breakthrough — so Trainlint *hints*; it never prunes your search or restricts the agent's
  exploration. It corrects the biases of unsupervised work (sunk cost, cargo-cult, blaming
  the data) with **information, not control**. The judgment stays yours.
- **It can never lock you out.** When it does block a machine-certain mistake, it does so with
  a *permission decision*, never by crashing — a bug in the guard must always be safer than
  the bug it guards against. Fail-open by construction.
- **Route each call to whoever can actually judge it.** Machine-checkable → bounced silently
  (you're undisturbed); only-a-human-can-verify (a forward/mask change) → escalated to you;
  everything else → a quiet nudge to the agent. A model may *route*, but it never judges
  correctness — that's for deterministic checks, or for you.
- **The knowledge is the principle; the project is just the scar.** Every rule is a law that
  survives a project change; the specifics (paths, calls, numbers) live in one swappable facts
  file. A wrong default isn't a rule — it's an *instance* of "match the frozen component's config."

The full rationale (the scars each rule came from) is in
[DESIGN.md](trainlint/DESIGN.md) — read it before adding rules.

## Install

```
/plugin marketplace add voidrank/Trainlint
/plugin install trainlint@trainlint
```

Pure Python standard library — **zero dependencies**. Then it just runs. See
[INSTALL.md](INSTALL.md) for a single-machine (no-plugin) setup.

## Use it on your project

```
/trainlint:init <name>      # register a project (thin — no TODO ceremony)
/trainlint:plan             # understand it end-to-end, decompose into decisions, get quizzed
/trainlint:quiz             # drill the decisions + the sticky concepts you keep forgetting
/trainlint:viz              # see your search tree
/trainlint:lint             # directionality + "read this now" hints
```

## Understand it first, then stay on the thread

A silent bug isn't the only way to lose a week. You can also start building before you understand
what you're building, or drift off the one thing that actually matters. So Trainlint front-loads
understanding and then keeps you pointed.

`/trainlint:plan` walks the whole project end-to-end **in plain language** — every term defined (no
"wait, what's a DAC?" three weeks in), every claim grounded in the actual code — and decomposes it
into the **decisions** that silently determine correctness. Then it **quizzes you** on each until
you actually hold it; a concept you keep forgetting gets drilled until it sticks.

From then on a **compass** stays lit every turn: your **goal**, the **main thread** (the one
load-bearing open question that gates everything right now), and the **next action**. It keeps the
agent — and you — on the thing that matters instead of polishing side-quests. Lose the goal and you
wander; lose the thread and you scatter. The compass is how the work stays *motivated and focused*.
The bouncer uses the same plan: it knows which decision an edit touches, so it escalates the genuinely
unresolved one and stays quiet on the settled ones — routing by the decision, not by keywords.

## There's a second layer: it maps where you've been

The bouncer stops single wrong *moves*. But there's a slower way to lose a week: going in
**circles** — over-tuning a dead branch, re-running what you already ruled out, hitting a wall a
paper would've explained.

So Trainlint also keeps a map. It reconstructs the **search tree** of directions you've tried —
rebuilt every run from the traces you already leave (your run names + a durable, compaction-proof
log), never something you hand-maintain. Then it *hints*, never prunes:

- when you've over-tuned one branch past diminishing returns
- when a stalled branch might be the *trunk's* fault, not the branch's
- which paper explains the wall you **just** hit — surfaced just-in-time, not by recency
  (reading it earlier is cargo-cult)

![an example search tree](docs/search-tree.png)

**Nothing to maintain, nothing to lose.** The tree is rebuilt from traces every run; the
irreplaceable "why we abandoned X" is harvested into git before a session compacts — anchored to
your work, never to a session (which may never end). See it any time with `/trainlint:viz`.

## Why it stays general

The **mechanism is fixed**, the **principles are portable**, and the **project facts are a
swappable file**. Porting to another project = write one `project.<name>.json`, the rules
unchanged. Read [DESIGN.md](trainlint/DESIGN.md) before adding rules — it's the meta-knowledge
that keeps the principles from drifting as the rule list grows.
