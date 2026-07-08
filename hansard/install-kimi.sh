#!/usr/bin/env bash
# install-kimi.sh — install hansard into the Kimi CLI (Kimi Code).
#
# Kimi has a Claude-shaped hook system but is BLOCK-ONLY (no context injection) and
# reads hooks from ~/.kimi/config.toml as a flat array. This script:
#   1. merges hansard's hooks (abs paths + TRAINLINT_HOST=kimi baked in) into the
#      `hooks = [...]` array in ~/.kimi/config.toml, preserving your other hooks
#   2. installs the 5 commands as Kimi skills (~/.kimi/skills/hansard-*/SKILL.md)
#      and registers that dir in `extra_skill_dirs`
# Idempotent (re-run replaces only hansard's entries) and non-destructive. Re-run
# after moving the repo (paths are baked).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$ROOT/hooks/router.py" ] || { echo "error: $ROOT/hooks/router.py not found" >&2; exit 1; }

# Kimi resolves its dir from KIMI_SHARE_DIR, else ~/.kimi (kimi_cli/share.py)
KIMI_DIR="${KIMI_SHARE_DIR:-$HOME/.kimi}"
CONFIG="$KIMI_DIR/config.toml"
SKILLS="$KIMI_DIR/skills"
[ -f "$CONFIG" ] || { echo "error: $CONFIG not found — is Kimi installed/initialized?" >&2; exit 1; }
mkdir -p "$SKILLS"

# a python with tomllib (Kimi's own is 3.13); fall back to system python3
KPY="$HOME/.local/share/uv/tools/kimi-cli/bin/python"
PY="python3"; [ -x "$KPY" ] && PY="$KPY"
"$PY" -c 'import tomllib' 2>/dev/null || { echo "error: need a python with tomllib (3.11+)" >&2; exit 1; }

# --- 1. merge hooks + extra_skill_dirs into config.toml (in place) ---
ROOT="$ROOT" CONFIG="$CONFIG" SKILLS="$SKILLS" "$PY" - <<'PY'
import os, re, tomllib

root, config, skills = os.environ["ROOT"], os.environ["CONFIG"], os.environ["SKILLS"]
text = open(config, encoding="utf-8").read()
data = tomllib.loads(text)

OURS = ("hooks/router.py", "research/harvest.py", "research/flow.py")
def is_ours(cmd): return any(o in cmd for o in OURS)

R = "TRAINLINT_HOST=kimi python3"
hansard_hooks = [
    {"event": "UserPromptSubmit", "command": f'{R} "{root}/hooks/router.py"', "matcher": "", "timeout": 30},
    {"event": "PreToolUse", "command": f'{R} "{root}/hooks/router.py"', "matcher": "Shell|WriteFile|StrReplaceFile", "timeout": 30},
    {"event": "Stop", "command": f'{R} "{root}/hooks/router.py"', "matcher": "", "timeout": 30},
    {"event": "PreCompact", "command": f'{R} "{root}/research/harvest.py"', "matcher": "", "timeout": 60},
    {"event": "SessionEnd", "command": f'{R} "{root}/research/harvest.py"', "matcher": "", "timeout": 60},
]
existing = [h for h in data.get("hooks", []) if not is_ours(h.get("command", ""))]
merged_hooks = existing + hansard_hooks

skill_dirs = list(data.get("extra_skill_dirs", []))
if skills not in skill_dirs:
    skill_dirs.append(skills)

def tstr(s):  # TOML string: literal single-quoted unless it contains a single quote
    return f"'{s}'" if ("'" not in s and "\n" not in s) else '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

def render_hooks(hooks):
    out = ["["]
    for h in hooks:
        out.append('  {{ event = "{ev}", command = {cmd}, matcher = "{mat}", timeout = {to} }},'.format(
            ev=h["event"], cmd=tstr(h["command"]), mat=h.get("matcher", ""), to=int(h.get("timeout", 30))))
    out.append("]")
    return "\n".join(out)

def render_list(items):
    return "[" + ", ".join(tstr(x) for x in items) + "]"

def find_array(text, key):
    """(start,end) spanning `key = [ ... ]`, bracket-matched, string-aware. None if absent."""
    m = re.search(r'(?m)^(' + re.escape(key) + r'\s*=\s*)\[', text)
    if not m:
        return None
    i = m.end() - 1  # the '['
    depth, j, n, q = 0, i, len(text), None
    while j < n:
        c = text[j]
        if q:
            if c == q and (q == "'" or text[j - 1] != "\\"):
                q = None
        elif c in "\"'":
            q = c
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return (m.start(), j + 1)
        j += 1
    return None

def splice(text, key, rendered):
    span = find_array(text, key)
    assignment = f"{key} = {rendered}"
    if span:
        return text[:span[0]] + assignment + text[span[1]:]
    # key absent: insert before the first [table] header (top-level keys must precede tables)
    th = re.search(r'(?m)^\[', text)
    pos = th.start() if th else len(text)
    return text[:pos] + assignment + "\n" + text[pos:]

text = splice(text, "hooks", render_hooks(merged_hooks))
text = splice(text, "extra_skill_dirs", render_list(skill_dirs))

# verify the result still parses before writing
tomllib.loads(text)
open(config, "w", encoding="utf-8").write(text)
print(f"  hooks  -> {config}  ({len(hansard_hooks)} hansard + {len(existing)} kept)")
print(f"  skills dir registered in extra_skill_dirs: {skills}")
PY

# --- 2. install the 5 commands as Kimi skills ---
for f in "$ROOT"/commands/*.md; do
  name="$(basename "$f" .md)"
  dir="$SKILLS/hansard-$name"
  mkdir -p "$dir"
  {
    echo "---"
    echo "name: hansard-$name"
    # carry the command's own description line if present
    grep -m1 '^description:' "$f" || echo "description: hansard $name"
    echo "---"
    # body = everything after the closing frontmatter '---', plugin-root baked to abs path
    awk 'f{print} /^---$/{c++; if(c==2) f=1}' "$f" | sed "s|\${CLAUDE_PLUGIN_ROOT}|$ROOT|g"
  } > "$dir/SKILL.md"
  echo "  skill  -> $dir/SKILL.md   (/skill:hansard-$name)"
done

echo
echo "hansard installed for Kimi at $KIMI_DIR"
echo "  root: $ROOT   ·   host channels: reject + escalate-by-block + report-doorman + harvest"
echo "  NOTE: the soft compass/coach layer is dropped on Kimi (block-only hooks)."
echo "Start a fresh Kimi session to load the hooks. Commands: /skill:hansard-plan etc."
