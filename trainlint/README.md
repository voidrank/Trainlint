# trainlint

A **soft guardrail harness** for AI/ML training, packaged as a Claude Code plugin.

A "doorman" between you and the coding agent. On every message / agent tool-use it
does one of four things — **let it pass / quietly remind the agent / stop it and alert you / bounce the agent outright** —
to stop the *silent* failures that wasted weeks in a real training project
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

## The report doorman (the `Stop` event)

A finished message is *prose*, not a tool action — so the action pipeline above has nothing to
bind to, and the explain-like-a-person voice rules used to be persuasion the model drops at large
context. `reportcheck.py` binds to `Stop`/`SubagentStop`: when the final message is a plan REPORT
(cites ≥2 active-plan decision ids, long-form) that skips the stance line or the map, leads with
bare decision-ids, or leans on undefined jargon, it bounces **once** (`decision: block`) for a
rewrite — loop-safe via `stop_hook_active`, fail-open on any error. Deterministic and conservative:
it gates only objective, spec-mandated misses, never a judgement about "good prose".

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
├── project.example.json      this project's action-rule facts (swap to port)
├── quiz.jsonl             principle bank: each Q = a transferable principle via a scar
├── hooks/
│   ├── router.py          orchestrator (fail-open, exit 0, permissionDecision)
│   ├── prefilter.py  checks.py + checks.jsonl  classifier.py  planaware.py  facts.py
│   ├── reportcheck.py     the REPORT doorman (Stop event): plan-report readability gate
│   ├── codex_compat.py    Codex shim: apply_patch envelope -> Claude-style Edit tool_input
│   ├── hooks.json
│   └── verifiers/         REAL checks (mel-power, frozen-encode, manifest-leak, effective-lr, model-code, shape-flow)
├── research/              the PROJECT layer (plan / compass / search-tree), per-project facts
│   ├── flow.py            lifecycle hook: context briefing · always-on compass · hints · viz
│   ├── plan.py            the decision floor-plan + main_thread() selector
│   ├── progress.py        plan-quiz mastery/coverage state
│   ├── plan.example.jsonl  facts.example.json  goal.example.txt  knowledge.example.jsonl  log.example.jsonl
│   ├── plan.workflow.js  internal engine for /trainlint:plan (big-codebase offload; not its own command)
│   ├── tree.py  governor.py  surfacer.py  lint.py  harvest.py  new_project.py
│   ├── viz.py               research-tree HTML (5-beat story · timeline · spine+tree); planning-stage mode before any run
│   ├── principles.jsonl     distilled project-AGNOSTIC laws (the refined layer)
├── commands/{plan,execute-and-report}.md
├── codex/hooks.json  install-codex.sh    Codex CLI port (apply_patch matcher, PreCompact harvest)
└── tests/{run.py, cases.jsonl, test_planaware.py, test_codex_compat.py}   +  research/test_research.py
```

## Commands (two — decide, then do)

The surface is deliberately **two slash commands**: one for the *thinking* half of the loop, one
for the *doing* half. (Scaffolding, drilling, lint, and viz all fold into these — they were never
separate stages of the work, just separate buttons.)

| command | what it does |
|---|---|
| `/trainlint:plan [review\|status\|from-log\|<id\|topic\|free-text>]` | the **decide** half — registers the project if new (thin scaffold), establishes its FULL context (plain language, file:line grounded), decomposes into decisions (written as you go), fills the facts, then **quizzes you** on each (pass an id/topic/concept to drill just one) — closing on the **compass** (goal · main thread · next action), never a menu |
| `/trainlint:execute-and-report [project\|decision-id]` | the **do** half — picks the one decision everything waits on (the `load_bearing` main thread), proposes & **drives** the cheapest move to settle it (doorman live the whole time), records the outcome back into the plan, then **reports**: the search-tree shape (the old `lint`) + the self-contained HTML report (the old `viz`) |

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
- **report** — when the tree gains a *real* search (branching or a wall), nudge to run
  `/trainlint:execute-and-report` to render + send the picture.
- harvest runs on `PreCompact`/`SessionEnd`.

Quizzing has two paths: **deliberate** (the quiz built into `/trainlint:plan`, walking the plan's
decisions) and
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

`/trainlint:plan <name>` — it registers the project (thin scaffold), reads the code, fills the facts,
writes the decisions + goal + glossary, then quizzes you, all in one flow. Then drive it with
`/trainlint:execute-and-report`. See `project.example.json` / `research/*.example.*` as worked examples.

## Install

See **[../INSTALL.md](../INSTALL.md)** — the plugin install, verification, opt-in knobs
(`HARNESS_MODEL`), and how to port to another project.

**OpenAI Codex CLI** is supported too: run `./install-codex.sh`. Codex cloned Claude Code's
hook protocol, so the whole pipeline runs unchanged — the only deltas (`apply_patch` instead of
Edit/Write, no `SessionEnd`) are absorbed by `hooks/codex_compat.py` and `codex/hooks.json`. See
INSTALL.md → *Form B*.

**Kimi CLI (Kimi Code)** is supported via `./install-kimi.sh`. Kimi's hooks are **block-only**
(no context injection), so `hooks/kimi_compat.py` maps its tools (`Shell`/`WriteFile`/
`StrReplaceFile`) to Claude shapes and adapts the router's output to Kimi's model: reject +
report-doorman + harvest port cleanly, escalate becomes a block ("escalate-by-block"), and the
soft coach/compass layer is dropped. See INSTALL.md → *Form C*.

## Test

```bash
python3 tests/run.py              # 34/34 — the action doorman (incl. shape-flow)
python3 tests/test_reportcheck.py # 10/10 — the report doorman (Stop event)
python3 tests/test_planaware.py   # 15/15 — plan-aware routing
python3 research/test_research.py # 20/20 — tree / governor / surfacer / plan / main-thread
```

## ⚠️ Footgun (we got locked out once)

Never move/delete a script a hook points at without first making the new path valid. A missing
script → `python3` exits 2 → Claude Code treats it as a **block** → every Bash/Edit/Write (incl.
subagents) is denied, unrecoverable from inside the session. That is why the router blocks only via
`permissionDecision`, never via exit code.
