# Trainlint

### It trained fine. That's the bug.

**A linter for AR-LLM and multimodal training — and for the AI agent doing it.** It catches
the *silent* mistakes (the ones that don't crash, where loss keeps dropping and the model is
quietly wrong) at the moment they happen — before they cost you a week of GPU.

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

## How it works — a doorman, four moves

On every message / agent tool-use it does exactly one of four things, and only the last two
ever reach you:

| move | when | who sees it |
|---|---|---|
| **stay silent** | nothing to flag | nobody |
| **coach the agent** | a quiet reminder it should fold in | the agent only |
| **escalate to you** | "only a human can verify this — look at this diff" | you |
| **bounce it** | "this is a known mistake — redo it" | the agent only (you're undisturbed) |

**Soft by default** (it hints, it never restricts the agent's exploration) and
**fail-open** (a bug in the linter can never lock you out — it blocks a wrong action with a
permission decision, never by crashing).

## What it catches — general principles, not one project's trivia

Each rule is a **principle that survives a project change**; the project-specific values
(paths, library calls, magic numbers) live in one swappable file.

| principle | what it looks like |
|---|---|
| preprocessing must match the frozen component's training config | an input transform that silently differs from how the frozen encoder was trained → out-of-distribution inputs |
| inference must reproduce training's masks/shifts bit-for-bit | a dropped autoregressive off-by-one → the model learns to echo its input |
| no-op / padding regions are OOD under a frozen tokenizer | padding the tokenizer never saw → it maps to out-of-distribution codes that quietly become a chunk of your targets |
| training reads must be on fast, reliable storage | a networked filesystem that corrupts under concurrent load → a silent crash mid-training |
| an eval/demo must run end-to-end through the model | a proxy that looks right no matter how broken the model is |
| config from many sources silently overrides | print the *effective* value, don't trust the flag you wrote |
| a weak modality learns a shortcut and ignores the strong condition | high teacher-forced accuracy, garbage free-running generation |

…20+ rules. Each is a transferable principle for AR-LLM / multimodal training; your project's
specifics (paths, calls, numbers) live in one swappable facts file — write it once, the rules
don't change.

## Two layers

1. **Action doorman** — don't make a single wrong move (the rules above).
2. **Research-lint** — don't burn weeks going in circles. It reconstructs the *search tree*
   of directions you've tried (from your run names + a durable, compaction-proof log), shows
   when you've over-tuned one branch past diminishing returns, and surfaces the paper that
   explains the wall you just hit — *just-in-time, not by recency* (reading it earlier is
   cargo-cult). It only ever hints; it never prunes your search for you.

![an example search tree](docs/search-tree.png)

## Why it's designed this way

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
- **Catching single mistakes isn't enough — you also go in circles.** A second layer
  reconstructs your search tree from the traces you already leave, flags when you've over-tuned
  one branch, and surfaces the paper that explains the wall *you just hit*. It only ever hints.
- **Nothing to maintain, nothing to lose.** The tree is rebuilt from traces every run; the
  irreplaceable "why we abandoned X" is harvested into git before a session compacts. Anchored
  to your work — never to a session, which may never end.

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
/trainlint:init <name>      # scaffold the one facts file you fill in — the rules don't change
/trainlint:viz              # see your search tree
/trainlint:lint             # directionality + "read this now" hints
/trainlint:quiz             # get drilled on a transferable principle until you've got it
```

## Why it stays general

The **mechanism is fixed**, the **principles are portable**, and the **project facts are a
swappable file**. Porting to another project = write one `project.<name>.json`, the rules
unchanged. Read [DESIGN.md](trainlint/DESIGN.md) before adding rules — it's the meta-knowledge
that keeps the principles from drifting as the rule list grows.
