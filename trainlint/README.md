# trainlint

A **soft guardrail harness** for AI/ML training, packaged as a Claude Code plugin.

A "doorman" between you and the coding agent. On every message / agent tool-use it
does one of four things — **let it pass / quietly remind the agent / stop it and alert you / bounce the agent outright** —
to stop the *silent* failures that wasted weeks in a real Duplex-MiMo saga
(power=2.0 mel / OOD silence codes / dropped AR-shift / DeepSpeed scheduler override
/ fake demos / serial guessing …). Plus two more layers: it keeps you on the **main thread**
of the work, and it maps the **search tree** of what you've already tried.

> **Read [`DESIGN.md`](DESIGN.md) before adding rules.** It is the meta-knowledge:
> the principles that must not drift as the rules grow.

---

## The four channels

| channel | mechanism | who sees it |
|---|---|---|
| **silent** | nothing | nobody |
| **coach** | `additionalContext` | agent only (quiet steer) |
| **escalate** | `systemMessage` | you + agent ("please check this code") |
| **reject** | `permissionDecision: deny` | agent only (bounce, redo) — you undisturbed |

The router is **fail-open** and **always exits 0**; blocking is done via
`permissionDecision`, never a non-zero exit. A bug in the harness can never lock the
session.

## Pipeline (the action doorman)

```
prefilter (structural, default-open: drop reads/docs/self-edits)
  └─> checks      (deterministic reject/escalate; some via real verifiers)
      classifier  (regex floor + opt-in small-model recall booster)
      planaware   (locate the action on the project PLAN → route by the decision it touches)
  └─> drop any message with an unfilled {{fact}} → merge by severity → render to a channel
```

`planaware` is what makes routing decision-aware instead of keyword-only: it maps a live action
onto the plan decision it touches and routes by that decision's status — an **open** (undecided)
fork escalates, a **settled** one stays a quiet coach (and downgrades a keyword-only escalation, so
probe scripts don't trip the "needs your eyes" alarm). A still-unmastered decision gets a gentle
"you haven't walked this in quiz yet" flag.

## Three layers (mechanism / principle / facts)

- **mechanism** — `hooks/*.py` (router pipeline). Fixed.
- **general-principle rules** — `triggers.jsonl` (coach) + `hooks/checks.jsonl`
  (reject/escalate). Principles only; project strings are `{{placeholders}}`.
- **project facts** — `project.<name>.json` expanded by `hooks/facts.py`.
  **To port to another project: write a new `project.<name>.json`, rules unchanged.**

## Structure

```
trainlint/
├── DESIGN.md  README.md
├── triggers.jsonl         coach rules (§1 portable core / §2 templated principles)
├── project.mimo.json      this project's action-rule facts (swap to port)
├── quiz.jsonl             principle bank: each Q = a transferable principle via a scar
├── hooks/
│   ├── router.py          orchestrator (fail-open, exit 0, permissionDecision)
│   ├── prefilter.py  checks.py + checks.jsonl  classifier.py  planaware.py  facts.py
│   ├── hooks.json
│   └── verifiers/         REAL checks (mel-power arg parse, manifest-leak, effective-lr)
├── research/              the PROJECT layer (plan / compass / search-tree), per-project facts
│   ├── flow.py            lifecycle hook: context briefing · always-on compass · hints · viz
│   ├── plan.py            the decision floor-plan + main_thread() selector
│   ├── progress.py        plan-quiz mastery/coverage state
│   ├── plan.mimo.jsonl  facts.mimo.json  goal.mimo.txt  knowledge.mimo.jsonl  log.mimo.jsonl
│   ├── tree.py  governor.py  surfacer.py  viz.py  lint.py  harvest.py  new_project.py
├── workflows/plan.workflow.js   optional background planner (foreground is the default)
├── commands/{init,plan,quiz,viz,lint}.md
└── tests/{run.py, cases.jsonl, test_planaware.py}   +  research/test_research.py
```

## Commands (slash commands, when installed as a plugin)

| command | what it does |
|---|---|
| `/trainlint:init <name>` | register a NEW project — a THIN scaffolder (empty stubs + set active; no TODO ceremony) |
| `/trainlint:plan` | establish the project's FULL context (plain language, file:line grounded), decompose into decisions (written as you go), fill the facts, then quiz — closing on the **compass** (goal · main thread · next action), never a menu |
| `/trainlint:quiz [id\|topic\|concept]` | drill the plan's decisions and the sticky **concepts** you keep forgetting (mastery-tracked); miss it → drilled with fresh scars |
| `/trainlint:viz [project]` | visualize the research search tree + knowledge-readiness edges (ASCII + a phone-friendly PNG) |
| `/trainlint:lint [project]` | run the research-lint: reconstruct the tree, surface directionality + readiness hints (read-only) |

`/trainlint:plan` runs in the **foreground** by default (the agent owns the journey — it stays engaged
and you can interject); a background workflow is available only for a very large codebase.

## Project flow (`research/flow.py`, automatic, per project — never tied to a session)

Anchored to the **project** (the `.active-project` name), to work events, and to each turn — *not*
to session boundaries (a session may never end):

- **context** — `SessionStart` injects a briefing led by the **goal + main thread**; it flags an
  un-written plan (registered but no decisions) and an un-walked plan (decisions not yet mastered).
- **compass** — every `UserPromptSubmit` injects an always-on, agent-facing compass (🎯 goal · main
  thread) so the agent stays locked on the one thing that matters instead of drifting into busywork.
- **hint** — the one-line research-lint hint, deduped (only when it changes).
- **viz** — when the tree gains a *real* search (branching or a wall), nudge to render + send it.
- harvest runs on `PreCompact`/`SessionEnd`.

Quizzing has two paths: **deliberate** (`/trainlint:quiz` over the plan's decisions) and
**concept-gap** (the `concept-gap-quiz` trigger fires the moment you ask what a term means / say you
don't follow one → it **escalates a popup**: quiz you first, then define it plainly and log it to
`research/glossary.<name>.jsonl`). The popup is `sticky` — it survives even when the touched decision
is already settled, since a concept gap is never a false alarm. The old soft SessionStart quiz nudge
and the opt-in mid-action quiz-gate were both removed.

## Adding a rule / quiz question

1. Ask: **would this survive a project change? what PRINCIPLE survives?** (DESIGN.md §8 q0)
2. Principle → a rule line (use `{{fact}}` for project specifics) or a quiz `principle`.
3. Fact → `project.<name>.json`. Add a `tests/cases.jsonl` case.
4. `python3 tests/run.py` (must pass).

## Onboard a new project

`/trainlint:init <name>` (thin registrar) → `/trainlint:plan` (it reads the code, fills the facts,
writes the decisions + goal + glossary, then quizzes you). See `project.mimo.json` /
`research/*.mimo.*` as worked examples.

## Install

See **[../INSTALL.md](../INSTALL.md)** — the plugin install, verification, opt-in knobs
(`HARNESS_MODEL`), and how to port to another project.

## Test

```bash
python3 tests/run.py              # 21/21 — the action doorman
python3 tests/test_planaware.py   # 8/8  — plan-aware routing
python3 research/test_research.py # 18/18 — tree / governor / surfacer / plan / main-thread
```

## ⚠️ Footgun (we got locked out once)

Never move/delete a script a hook points at without first making the new path valid. A missing
script → `python3` exits 2 → Claude Code treats it as a **block** → every Bash/Edit/Write (incl.
subagents) is denied, unrecoverable from inside the session. That is why the router blocks only via
`permissionDecision`, never via exit code.
