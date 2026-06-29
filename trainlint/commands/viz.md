---
description: Generate the research tree — a demo-ready HTML report (5-beat story · timeline · decision spine · search tree)
argument-hint: "[project]"
---
Run the Trainlint research visualizer and show me the result. This is READ-ONLY — do not edit anything.

1. Run: `python3 "${CLAUDE_PLUGIN_ROOT}/research/viz.py" $ARGUMENTS`
   (the optional argument is the project name; default is the active project.)
2. Show me the compact ASCII summary it prints — goal · main thread · the verified/decided/open scoreboard · the latest timeline beats · any wall→paper "ready to read" hints.
3. Send me the single self-contained HTML file it points to (a line `HTML: <path>`). It opens in any browser: it leads with the project as one 5-beat story — 想做什么 (总分总) · 遇到问题 · BOTTLENECK · 干了什么 · 要做什么 — then a dated timeline and the phase-ordered decision spine beside the search tree, with knowledge-readiness edges.
4. Tell me each decision in the spine now carries an expandable "💬 Ask about this" chatbot. It answers from that decision + the project glossary by calling the Anthropic API straight from the browser — the user clicks **🔑 Set API key** once (stored only in their browser, never written into the file). What they didn't understand is captured to the browser's localStorage and exported via **⬇ Export memory** as `viz-memory.<project>.json`.

This step WRITES — keep it separate from the read-only `viz` above, and only run it when I ask:

- `python3 "${CLAUDE_PLUGIN_ROOT}/research/viz.py" <project> --absorb <viz-memory.json>` folds that export back into the substrate: glossary terms append to `research/glossary.<project>.jsonl` (the SAME file `/trainlint:quiz` drills, so a concept I kept asking about becomes drillable) and the raw Q&A appends to `research/clarify.<project>.jsonl`. It then regenerates the HTML, which renders both back under each decision (a "terms you asked about" block + an FAQ). Re-absorbing the same export is a no-op (deduped).
