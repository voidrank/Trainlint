# research/ — the project layer (sibling of Hansard's action doorman)

Hansard's hooks are an **action-time doorman** (don't make one wrong move). This directory
is the **project layer**: it turns a project's *decisions* and *search* into one derived,
demo-ready picture, and feeds two machines off it — a teaching **quiz** and a **plan-aware
doorman** — plus periodic, just-in-time coaching. Everything here is a **derived view,
rebuilt every run, never hand-maintained**. Read-only, fail-soft.

## What it produces (overview first)

| view | what it is | how to get it |
|---|---|---|
| **Plan** — the spine | the ordered DECISIONS that define the run, each tagged with a transferable principle + a status (open → decided → verified) | `python3 plan.py` · `/hansard:plan` (decide + quiz) |
| **Report** — the demo | ONE self-contained HTML, 5 tabs: 📅 Timeline (opens with newly-done · focus) · 🎛 Agents (working sessions) · 🧠 Skills (the plugin's slash commands) · 🎯 Goals (DONE bar + what's left) · 🖍 Requests (operator notes, adopt/dismiss). Spine + search tree + pipeline render in the slides deck | `python3 viz.py [proj]` → `viz/<proj>.html` · `/hansard:execute-and-report` |
| **Coaching hints** | the two read-only lints (below), one line, just-in-time | `python3 lint.py [proj]` (`--brief` = the SessionStart one-liner) · `/hansard:execute-and-report` |

## The two lints (pure hints — they never restrict exploration)

- **governor (inward)** — reconstructs the frontier tree and shows each branch's shape:
  status / spend / recent marginal gains / walls hit / unexplored candidate moves / missing
  trunk-checks. It **never says "abandon"** (research is non-monotonic — a plateau can precede
  a breakthrough); it corrects unsupervised-search bias (sunk-cost over-DFS, novelty over-BFS)
  with *information*, not control.
- **surfacer (outward)** — takes the **walls** you actually hit and surfaces the knowledge
  entry that addresses each: "readable now." Trigger = a wall, **not recency** (reading
  earlier = cargo-cult).

A **wall is a dual signal**: the governor's stop-shape AND the surfacer's unlock key.

## Durability — why it survives compaction/deletion

The tree is rebuilt every run from two durable sources, never a maintained file:

- **skeleton** ← repo traces (run-dir names + metrics): `derive_structured()` globs
  `runs_glob`, parses directions via `direction_regex`.
- **annotations** ← `log.<name>.jsonl`, an append-only log of the JUDGMENTS traces can't prove
  (wall / verdict / hypothesis / abandon / trunk-check). Lives in git.

Sessions are ephemeral, so `harvest.py` pulls those judgments into the log before they're lost
(wire to `PreCompact`/`SessionEnd`). The crude keyword pass tags `direction="?"`; the **LLM
pass** assigns `direction` and **MUST use a plan decision id** — `normalize_direction()` snaps
a near-miss onto the canonical id — so the search tree and the decision spine speak one
vocabulary. Append-only → never rots. No log → degrade to **skeleton only** (lossy, not broken).

## Run

```bash
python3 viz.py [project]      # the research-tree HTML report (+ a compact ASCII summary)
python3 lint.py [project]     # the two lints' hints   (--brief = SessionStart one-liner)
python3 harvest.py <transcript.jsonl> [project]   # PreCompact / SessionEnd / periodic
python3 test_research.py      # 20/20
```

## Files (mechanism · general principles · per-project facts)

```
mechanism (general, fixed)
  tree.py            build the derived tree from the merged event stream
  governor.py surfacer.py lint.py    the two read-only lints + their entrypoint
  viz.py             the research-tree HTML report (one project, one self-contained file)
  harvest.py         session transcript → durable log (LLM pass assigns plan-id directions)
  plan.py progress.py    the decision floor-plan + quiz mastery/coverage state
  flow.py            lifecycle hooks: context · compass · hint · viz-nudge · harvest
  load.py            /hansard:load's deterministic half: discover inhale-able sources
                     (CLAUDE.md · .claude/skills · auto-memory), load-once manifest,
                     SessionStart digest, prompt→skill hint
  new_project.py     thin scaffolder for a new project

general, project-free
  principles.jsonl   the distilled, transferable LAWS (the refined layer) — no project nouns

per-project facts — live IN the project at <home>/.hansard/ so git versions them with the code
  plan.<name>.jsonl       the ordered decisions (the spine)
  facts.<name>.json       thresholds / runs_glob / direction_regex / trunk_checks / candidate_moves
  knowledge.<name>.jsonl  papers/refs indexed by the PROBLEM they solve (+ wall match keywords)
  skills.<name>.jsonl     reusable PROCEDURES (inhaled by /hansard:load, appended as work teaches)
  load.<name>.json        the load-once manifest: which sources were inhaled, content-hashed
  log.<name>.jsonl        durable append-only annotations (git-committed)
  goal.<name>.txt         one-line goal + the "done" bar
```

A project's memory routes to `<home>/.hansard/` once its `home` is stamped (registration does
this); `project.<name>.json` (the registry holding `home`) plus session locks and server files
stay in the global data dir. Reads fall back to the old flat data dir, and the first write
moves the old copy in — `python3 paths.py migrate [project]` does it in bulk (deploy the new
plugin code FIRST: old code doesn't know `.hansard/` and would stop seeing moved files).

## Inhaling an existing project (`/hansard:load`)

A project that predates Hansard already carries context — `.claude/skills/`, `CLAUDE.md`/
`AGENTS.md`, Claude Code auto-memory. `/hansard:load` reads it ONCE, and the agent sorts every
item into the store that can act on it: procedure → `skills.<name>.jsonl`, fact/finding →
`knowledge.<name>.jsonl`, guardable mistake → `project.<name>.json` facts (the doorman),
term → glossary. `load.py mark` stamps a content-hashed manifest, so a re-run ingests only
new/changed sources. From then on `flow.py` injects the digest at every SessionStart (plus the
keep-producing contract) and points a matching prompt at its loaded skill — so every new
session/agent starts FROM the inhaled memory instead of rediscovering it.

## Port to another project

`/hansard:plan <name>` (it registers the project, then plans it) — or hand-write
`facts/knowledge/log/plan/goal.<name>.*`. The mechanism and `principles.jsonl` are unchanged.
Active project = `HARNESS_PROJECT` env / this session's lock / the project whose home contains your cwd.
