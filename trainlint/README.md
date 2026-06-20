# trainlint

A **soft guardrail harness** for AR-LLM multimodal training, packaged as a Claude Code plugin.

A "doorman" between you and the coding agent. On every message / agent tool-use it
does one of four things — **放过 / 悄悄提醒 agent / 拦下并提醒你 / 直接打回 agent** —
to stop the *silent* failures that wasted weeks in a real Duplex-MiMo saga
(power=2.0 mel / OOD silence codes / dropped AR-shift / DeepSpeed scheduler override
/ fake demos / serial guessing …).

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

## Pipeline

```
prefilter (structural, default-open: drop reads/docs/self-edits)
  └─> checks  (deterministic reject/escalate; some via real verifiers)
      classifier (regex floor + opt-in small-model recall booster)
      quiz-gate (opt-in: surface a knowledge question)
  └─> merge by severity → render to a channel
```

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
├── project.mimo.json      this project's facts (swap to port)
├── quiz.jsonl             teaching quiz: each Q = a transferable principle via a scar
├── hooks/
│   ├── router.py          orchestrator (fail-open, exit 0, permissionDecision)
│   ├── prefilter.py  checks.py + checks.jsonl  classifier.py  facts.py  quiz.py
│   ├── hooks.json
│   └── verifiers/         REAL checks (mel-power arg parse, manifest-leak, effective-lr)
└── tests/{run.py, cases.jsonl}
```

## Adding a rule / quiz question

1. Ask: **would this survive a project change? what PRINCIPLE survives?** (DESIGN.md §8 q0)
2. Principle → a rule line (use `{{fact}}` for project specifics) or a quiz `principle`.
3. Fact → `project.<name>.json`. Add a `tests/cases.jsonl` case.
4. `python3 tests/run.py` (must pass).

## Install

**A — settings.json (single machine):** point `UserPromptSubmit` and
`PreToolUse` (matcher `Bash|Edit|Write|SendUserFile`) hooks at
`/ABS/PATH/trainlint/hooks/router.py`.

**B — plugin:** this dir sits in a marketplace (see `../.claude-plugin/marketplace.json`):
```
/plugin marketplace add /path/to/marketplace-root
/plugin install trainlint@trainlint
/reload-plugins
```
> Using A and B together double-injects — remove the settings.json hooks after installing the plugin.

## Opt-in knobs (default off)

- `HARNESS_MODEL=1` (+ `ANTHROPIC_API_KEY`) — small-model semantic recall booster (Haiku selector over the vetted catalog; never invents advice).
- `HARNESS_QUIZ=1` or a `.quiz-gate` file — surface a relevant knowledge question at high-stakes moments (never blocks).

## Test

```bash
python3 tests/run.py     # 21/21; run after editing any rule
```

## ⚠️ Footgun (we got locked out once)

Never move/delete the script a settings.json hook points at without first making the
new path valid. A missing script → `python3` exits 2 → Claude Code treats it as a
**block** → every Bash/Edit/Write (incl. subagents) is denied, unrecoverable from
inside the session. Order: **new path exists → change settings → remove old.**
That is why the router now blocks only via `permissionDecision`, never via exit code.
