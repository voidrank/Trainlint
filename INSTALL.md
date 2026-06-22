# Installing Trainlint

## Quickest — install from the marketplace (two lines)

Trainlint's repo *is* a Claude Code plugin marketplace, so once it's public anyone can:

```
/plugin marketplace add voidrank/Trainlint
/plugin install trainlint@trainlint
/reload-plugins
```

That's it — no clone, no editing `settings.json`. The plugin ships **both layers**: the
action doorman (`UserPromptSubmit`/`PreToolUse`) *and* the research lint
(`SessionStart`→lint, `PreCompact`/`SessionEnd`→harvest).

> Needs a normal Claude Code session (`/plugin` is unavailable over Remote Control).

## Even simpler — once listed in the official community directory

If Trainlint is accepted into Anthropic's built-in `claude-plugins-community` marketplace
(submit at <https://platform.claude.com/plugins/submit>), there's nothing to add — it's
already available to every Claude Code user:

```
/plugin install trainlint@claude-plugins-community
```

## Requirements

Pure Python **standard library — zero dependencies**. The only optional extra is the
opt-in small-model classifier: `pip install anthropic` + `ANTHROPIC_API_KEY` (without it
it falls back to the regex floor; nothing else is affected).

---

## Form A — settings.json hooks (advanced: Remote Control, or no plugin system)

Use this only when `/plugin` isn't available (e.g. Remote Control) or you don't want the
plugin system. Route through a **stable symlink** so moving/renaming later can't lock you
out (see the footgun):

```bash
git clone git@github.com:voidrank/Trainlint.git ~/Trainlint
ln -sfn ~/Trainlint/trainlint ~/trainlint
```

Add to the `hooks` block of `~/.claude/settings.json` (use **absolute paths** — `~` may
not expand there; replace `<user>`):

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

> Using Form A and the plugin together double-injects — after installing the plugin,
> remove this `hooks` block.

---

## Verify

```bash
cd ~/Trainlint/trainlint && python3 tests/run.py              # 21/21
cd ~/Trainlint/trainlint/research && python3 test_research.py # 9/9
```

## Opt-in knobs (default off)

- `HARNESS_MODEL=1` (+ `ANTHROPIC_API_KEY`) — small-model semantic recall booster (a Haiku
  selector over the vetted rule catalog; it never invents advice).

(The concept-gap quiz needs no knob — it fires automatically as a popup the moment you ask what a
term means. The old opt-in `HARNESS_QUIZ` / `.quiz-gate` mid-action gate was removed.)

## Use it on another project (not MiMo)

Default project is `mimo`. Point it at a new one:

```bash
echo myproj > ~/Trainlint/trainlint/.active-project   # or: export HARNESS_PROJECT=myproj
```

Then write the facts (mechanism unchanged — you only swap facts):
`trainlint/project.myproj.json` (action-rule facts), `trainlint/research/facts.myproj.json`,
`trainlint/research/knowledge.myproj.jsonl`, and an empty `trainlint/research/log.myproj.jsonl`.
See `trainlint/DESIGN.md` §10.

---

## ⚠️ Footgun (Form A only)

Never move/delete the script a settings.json hook points at without first making the new
path valid. A missing script makes `python3` exit 2, which Claude Code treats as a
**block** → every Bash/Edit/Write (incl. subagents) is denied, unrecoverable from inside
the session. Order: **new path exists → change settings → remove old.** Routing through the
stable symlink `~/trainlint` avoids this — future moves just re-point the symlink.
