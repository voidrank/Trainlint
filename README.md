# claude-marketplace

A local [Claude Code plugin marketplace](https://code.claude.com/docs/en/plugin-marketplaces).

## Plugin: `mimo-train-harness`

A **soft guardrail harness** for AR-LLM multimodal training — a "doorman" between
you and the coding agent that, at high-stakes moments, stays silent / quietly
reminds the agent / asks you to check / bounces a bad action. Built from a real
Duplex-MiMo debugging saga.

See [`mimo-train-harness/README.md`](mimo-train-harness/README.md) for usage and
[`mimo-train-harness/DESIGN.md`](mimo-train-harness/DESIGN.md) for the design
philosophy (read DESIGN.md before adding rules).

## Install

```
/plugin marketplace add /path/to/this/dir
/plugin install mimo-train-harness@mimo-local
/reload-plugins
```

Or for a single machine without the plugin system, point `~/.claude/settings.json`
hooks at `mimo-train-harness/hooks/router.py` (see the plugin README).

## Test

```
cd mimo-train-harness && python3 tests/run.py
```
