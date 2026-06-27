# research/ — the project layer (sibling of Trainlint's action doorman)

Trainlint's hooks are an **action-time doorman** (don't make one wrong move). This directory
is the **project layer**: it turns a project's *decisions* and *search* into one derived,
demo-ready picture, and feeds two machines off it — a teaching **quiz** and a **plan-aware
doorman** — plus periodic, just-in-time coaching. Everything here is a **derived view,
rebuilt every run, never hand-maintained**. Read-only, fail-soft.

## What it produces (overview first)

| view | what it is | how to get it |
|---|---|---|
| **Plan** — the spine | the ordered DECISIONS that define the run, each tagged with a transferable principle + a status (open → decided → verified) | `python3 plan.py` · `/trainlint:plan` · `/trainlint:quiz` |
| **Research tree** — the demo | ONE self-contained HTML: top-down TLDR · dated timeline · decision spine beside the search tree · knowledge-readiness edges | `python3 viz.py [proj]` → `viz/<proj>.html`; `viz.py index` → cross-project overview |
| **Distilled principles** — the refined layer | the project-AGNOSTIC laws those decisions refine into, tagged by how far they've been tempered (recurrence + verification) | `python3 viz.py principles` → `viz/principles.html` (laws live in `principles.jsonl`) |
| **Coaching hints** | the two read-only lints (below), one line, just-in-time | `python3 lint.py [proj]` (`--brief` = the SessionStart one-liner) |

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
python3 viz.py index          # regenerate every project + a linked overview + the principles ledger
python3 viz.py principles     # just the project-agnostic principles ledger
python3 lint.py [project]     # the two lints' hints   (--brief = SessionStart one-liner)
python3 harvest.py <transcript.jsonl> [project]   # PreCompact / SessionEnd / periodic
python3 test_research.py      # 20/20
```

## Files (mechanism · general principles · per-project facts)

```
mechanism (general, fixed)
  tree.py            build the derived tree from the merged event stream
  governor.py surfacer.py lint.py    the two read-only lints + their entrypoint
  viz.py             the research-tree HTML + cross-project index + principles ledger
  harvest.py         session transcript → durable log (LLM pass assigns plan-id directions)
  plan.py progress.py    the decision floor-plan + quiz mastery/coverage state
  flow.py            lifecycle hooks: context · compass · hint · viz-nudge · harvest
  new_project.py     thin scaffolder for a new project

general, project-free
  principles.jsonl   the distilled, transferable LAWS (the refined layer) — no project nouns

per-project facts (swap these to port — mechanism untouched)
  plan.<name>.jsonl       the ordered decisions (the spine)
  facts.<name>.json       thresholds / runs_glob / direction_regex / trunk_checks / candidate_moves
  knowledge.<name>.jsonl  papers/refs indexed by the PROBLEM they solve (+ wall match keywords)
  log.<name>.jsonl        durable append-only annotations (git-committed)
  goal.<name>.txt         one-line goal + the "done" bar
```

## Port to another project

`/trainlint:init <name>` (thin registrar) then `/trainlint:plan` — or hand-write
`facts/knowledge/log/plan/goal.<name>.*`. The mechanism and `principles.jsonl` are unchanged.
Active project = `HARNESS_PROJECT` env / `.active-project` file / default `example`.
