# Trainlint

A local [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).

## Plugin: `trainlint`

A **soft guardrail harness** for AR-LLM multimodal training — a "doorman" between
you and the coding agent that, at high-stakes moments, stays silent / quietly
reminds the agent / asks you to check / bounces a bad action. Built from a real
Duplex-MiMo debugging saga, but the rules are general; project specifics live in a
swappable `project.<name>.json`.

See [`trainlint/README.md`](trainlint/README.md) for usage and
[`trainlint/DESIGN.md`](trainlint/DESIGN.md) for the design philosophy
(read DESIGN.md before adding rules).

## Install

```
/plugin marketplace add /path/to/this/dir
/plugin install trainlint@trainlint
/reload-plugins
```

Or for a single machine without the plugin system, point `~/.claude/settings.json`
hooks at `trainlint/hooks/router.py` (see the plugin README).

## Test

```
cd trainlint && python3 tests/run.py
```
