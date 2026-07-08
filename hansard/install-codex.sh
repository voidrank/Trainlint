#!/usr/bin/env bash
# install-codex.sh — install hansard into OpenAI Codex CLI.
#
# Codex cloned Claude Code's hook protocol, so the Python pipeline runs unchanged.
# This script does the plumbing Codex needs that the Claude plugin install does not:
#   1. merge hansard's hooks (with absolute paths baked in) into ~/.codex/hooks.json
#   2. render commands/*.md into ~/.codex/prompts/hansard-<name>.md  (Codex slash commands)
#
# Idempotent: re-running replaces hansard's own entries, never duplicates them, and
# never touches other hooks you have. Re-run after moving the repo (paths are baked).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$ROOT/hooks/router.py" ] || { echo "error: $ROOT/hooks/router.py not found — run this from the hansard dir" >&2; exit 1; }
command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 1; }

CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
PROMPTS="$CODEX_HOME/prompts"
HOOKS_JSON="$CODEX_HOME/hooks.json"
mkdir -p "$PROMPTS"

# --- 1. hooks: bake the absolute root, then merge into ~/.codex/hooks.json --------------
BAKED="$(sed "s|\${CLAUDE_PLUGIN_ROOT}|$ROOT|g" "$ROOT/codex/hooks.json")"
TARGET="$HOOKS_JSON" TEMPLATE="$BAKED" python3 - <<'PY'
import json, os
target = os.environ["TARGET"]
template = json.loads(os.environ["TEMPLATE"])
template.pop("_comment", None)
try:
    with open(target) as f:
        existing = json.load(f)
except Exception:
    existing = {}
existing.setdefault("hooks", {})

OURS = ("hooks/router.py", "research/flow.py", "research/harvest.py")
def is_ours(group):
    return any(any(o in h.get("command", "") for o in OURS)
               for h in group.get("hooks", []))

for event, groups in template["hooks"].items():
    keep = [g for g in existing["hooks"].get(event, []) if not is_ours(g)]  # drop our prior entries
    existing["hooks"][event] = keep + groups
    if not existing["hooks"][event]:
        del existing["hooks"][event]

with open(target, "w") as f:
    json.dump(existing, f, indent=2, ensure_ascii=False)
    f.write("\n")
print("  hooks  -> %s" % target)
PY

# --- 2. prompts: commands/*.md -> ~/.codex/prompts/hansard-<name>.md ------------------
for f in "$ROOT"/commands/*.md; do
  name="$(basename "$f" .md)"
  out="$PROMPTS/hansard-$name.md"
  sed "s|\${CLAUDE_PLUGIN_ROOT}|$ROOT|g" "$f" > "$out"
  echo "  prompt -> $out   (/hansard-$name)"
done

echo
echo "hansard installed for Codex at $CODEX_HOME"
echo "  root: $ROOT"
echo "  commands: /hansard-init /hansard-plan /hansard-quiz /hansard-viz /hansard-lint"
echo "Start a new Codex session to load the hooks. Verify with: codex  -> /hansard-lint"
