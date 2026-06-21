# Installing Trainlint

Two ways to install. **Form A** (settings.json hooks) is the lightest and works on a
single machine without the plugin system. **Form B** (plugin) is for distribution and
requires a normal Claude Code session (`/plugin` is unavailable over Remote Control).

## Requirements

The action layer and the research layer are **pure Python standard library — zero
dependencies**. The only optional extra is the opt-in small-model classifier, which
needs `pip install anthropic` + `ANTHROPIC_API_KEY` (without it, it falls back to the
regex floor — no effect on anything else).

```bash
git clone git@github.com:voidrank/Trainlint.git ~/Trainlint
```

---

## Form A — settings.json (single machine, lightest)

Route the hooks through a **stable symlink** so that later moving/renaming the repo
never risks a lockout (see the footgun at the bottom):

```bash
ln -sfn ~/Trainlint/trainlint ~/trainlint
```

Add this to the `hooks` block of `~/.claude/settings.json` (use **absolute paths** —
`~` may not expand inside settings.json; replace `<user>` with your username):

```json
"hooks": {
  "UserPromptSubmit": [
    { "hooks": [ { "type": "command", "command": "python3 /home/<user>/trainlint/hooks/router.py" } ] }
  ],
  "PreToolUse": [
    { "matcher": "Bash|Edit|Write|SendUserFile",
      "hooks": [ { "type": "command", "command": "python3 /home/<user>/trainlint/hooks/router.py" } ] }
  ],
  "PreCompact":   [ { "hooks": [ { "type": "command", "command": "python3 /home/<user>/trainlint/research/harvest.py" } ] } ],
  "SessionEnd":   [ { "hooks": [ { "type": "command", "command": "python3 /home/<user>/trainlint/research/harvest.py" } ] } ],
  "SessionStart": [ { "hooks": [ { "type": "command", "command": "python3 /home/<user>/trainlint/research/lint.py" } ] } ]
}
```

- `UserPromptSubmit` + `PreToolUse` → the **action doorman** (silent/coach/escalate/reject).
- `PreCompact` + `SessionEnd` → **harvest** research judgments into the durable log
  before the session is compacted/deleted.
- `SessionStart` → the **research-lint** surfaces the current search-shape each session.

> Using Form A and Form B together double-injects — after installing the plugin (B),
> remove this `hooks` block.

## Form B — plugin (for distribution)

The repo root is a marketplace (`.claude-plugin/marketplace.json`); the plugin is the
`trainlint/` subdir. In a normal Claude Code session:

```
/plugin marketplace add ~/Trainlint
/plugin install trainlint@trainlint
/reload-plugins
```

(Both the marketplace and the plugin are named `trainlint`.)

---

## Verify

```bash
cd ~/Trainlint/trainlint && python3 tests/run.py              # 21/21
cd ~/Trainlint/trainlint/research && python3 test_research.py # 9/9

# action layer smoke test (should print a training checklist as additionalContext):
echo '{"hook_event_name":"UserPromptSubmit","prompt":"重新train吧"}' | python3 ~/trainlint/hooks/router.py

# research lint smoke test (should print the reconstructed search shape):
python3 ~/trainlint/research/lint.py mimo
```

## Opt-in knobs (default off)

- `HARNESS_MODEL=1` (+ `ANTHROPIC_API_KEY`) — small-model semantic recall booster (a
  Haiku selector over the vetted rule catalog; it never invents advice).
- `HARNESS_QUIZ=1` or a `.quiz-gate` file — surface a relevant knowledge question at
  high-stakes moments (never blocks).

---

## Use it on another project (not MiMo)

The default project is `mimo`. Point it at a new one:

```bash
echo myproj > ~/Trainlint/trainlint/.active-project   # or: export HARNESS_PROJECT=myproj
```

Then write the facts (the mechanism is unchanged — you only swap facts):

- `trainlint/project.myproj.json` — action-rule facts (bad-storage regex, locked
  configs, preprocessing traps, reference impl, examples).
- `trainlint/research/facts.myproj.json` — research facts (thresholds, `runs_glob`,
  `direction_regex`, trunk-checks, candidate moves).
- `trainlint/research/knowledge.myproj.jsonl` — papers/refs indexed by the problem they solve.
- `trainlint/research/log.myproj.jsonl` — start it empty (harvest fills it).

See `trainlint/DESIGN.md` §10 for the full porting guide.

---

## ⚠️ Footgun (we got locked out once)

Never move/delete the script a settings.json hook points at without first making the
new path valid. A missing script makes `python3` exit 2, which Claude Code treats as a
**block** → every Bash/Edit/Write (including subagents) is denied, unrecoverable from
inside the session (recover only from a shell outside Claude Code). Order:
**new path exists → change settings → remove old.** Routing Form A through the stable
symlink `~/trainlint` avoids this entirely — future moves just re-point the symlink.
