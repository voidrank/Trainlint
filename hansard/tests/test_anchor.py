#!/usr/bin/env python3
"""Anchor tests — "every report item carries the CODE it's reviewable against".

Covers the three layers: plan.py parsing (_anchor_specs / has_anchor / anchor_is_paper),
anchor.py capture (born-valid pins in a throwaway git repo), and viz.py render-time
resolution (pinned / drifted / unreachable / missing, all fail-open to captions).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "research"))
import plan  # noqa: E402
import viz  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        fails += 1


# ---- 1. plan.py parsing ---------------------------------------------------------------------
s = plan._anchor_specs({"anchors": "src/auth.js:36-53@01139f6"})
check(s == [{"file": "src/auth.js", "lines": [36, 53], "commit": "01139f6"}],
      "string shorthand file:a-b@sha parses to {file, lines, commit}")
s = plan._anchor_specs({"anchors": "src/auth.js:40"})
check(s == [{"file": "src/auth.js", "lines": [40, 40]}],
      "single line, no sha -> lines [n,n], no commit")
s = plan._anchor_specs({"anchors": ["paper"]})
check(s == [{"paper": True}], "'paper' string -> paper sentinel")
s = plan._anchor_specs({"anchors": [{"commit": "280d440"}, {"file": "a.py", "lines": [9, 3]}]})
check(s[0] == {"commit": "280d440"} and s[1]["lines"] == [3, 9],
      "dict forms pass through; reversed line range is normalized")
check(plan._anchor_specs({"anchors": [42, {"lines": [1, 2]}, ""]}) == [],
      "garbage entries (int, no file/commit, empty) drop silently — fail-open")
check(plan._anchor_specs({}) == [], "no anchors field -> []")
# review findings: parse-layer validation is the security/honesty boundary for every consumer
check(plan._anchor_specs({"anchors": "see the PR"}) == [],
      "prose ('see the PR') is NOT an anchor — junk can't discharge the gate")
check(plan._anchor_specs({"anchors": {"commit": "--output=/tmp/pwn"}}) == [],
      "dict-form commit must be hex — git option injection blocked at parse")
check(plan._anchor_specs({"anchors": {"file": "-o/evil.py"}}) == [],
      "leading-dash file rejected (would read as a git option)")
s = plan._anchor_specs({"anchors": "pkg@2/mod.py:3-9"})
check(s == [{"file": "pkg@2/mod.py", "lines": [3, 9]}],
      "'@' inside a path survives — only a trailing 6-40-hex @suffix is a commit")
s = plan._anchor_specs({"anchors": "file.py@main"})
check(s == [{"file": "file.py@main"}],
      "non-hex @suffix stays part of the file name (resolves missing, never dropped silently)")

check(plan.has_anchor({"anchors": "a.py:1-2@abc123"}), "has_anchor: code anchor -> True")
check(not plan.has_anchor({"anchors": "paper"}), "has_anchor: paper alone -> False (not code)")
check(not plan.has_anchor({}), "has_anchor: absent -> False")
check(plan.anchor_is_paper({"anchors": "paper"}), "anchor_is_paper: paper -> True")
check(not plan.anchor_is_paper({}), "anchor_is_paper: absent -> False (missing ≠ declared)")
check(not plan.anchor_is_paper({"anchors": ["paper", "a.py:1@abc123"]}),
      "anchor_is_paper: paper + code mixed -> False (the code wins)")

# ---- 2. anchor.py capture in a throwaway git repo --------------------------------------------
tmp = Path(tempfile.mkdtemp())
repo = tmp / "proj"
repo.mkdir()
subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
code = repo / "mod.py"
code.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
subprocess.run(["git", "-C", str(repo), "commit", "-qm", "seed"], check=True)
sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
                     capture_output=True, text=True).stdout.strip()

import anchor  # noqa: E402

spec, warns = anchor._spec_from_file(f"{code}:2-4")
check(spec == {"file": "mod.py", "repo": str(repo), "lines": [2, 4], "commit": sha},
      "capture: committed file -> repo-relative path + repo root + HEAD pin, no warnings"
      + ("" if not warns else f" (warns: {warns})"))
check(not warns, "capture: clean tree -> no warnings")

code.write_text("line1\nCHANGED\nline3\nline4\nline5\n", encoding="utf-8")
spec2, warns2 = anchor._spec_from_file(f"{code}:2-4")
check(spec2 and spec2.get("commit") == sha and any("DIFFER" in w for w in warns2),
      "capture: range differs from HEAD -> still pins HEAD but warns to commit first")
code.write_text("line1\nline2\nline3\nline4\nline5\n" + "new6\nnew7\n", encoding="utf-8")
spec2b, warns2b = anchor._spec_from_file(f"{code}:6-7")
check(spec2b and "commit" not in spec2b and any("don't exist at HEAD" in w for w in warns2b),
      "capture: range only in uncommitted tail -> refuses a lying pin, records file-only")
code.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")

nogit = tmp / "plain"
nogit.mkdir()
loose = nogit / "loose.py"
loose.write_text("a\nb\n", encoding="utf-8")
spec3, warns3 = anchor._spec_from_file(f"{loose}:1-2")
check(spec3 and "commit" not in spec3 and any("not in a git repo" in w for w in warns3),
      "capture: non-git file -> file-only anchor (never a fabricated sha) + warning")
spec4, warns4 = anchor._spec_from_file(str(tmp / "ghost.py"))
check(spec4 is None and any("no such file" in w for w in warns4),
      "capture: missing file -> refused (born-valid: no anchor to nothing)")
spec5, warns5 = anchor._spec_from_file(f"{code}:900-950")
check(spec5 is None and any("past the end" in w for w in warns5),
      "capture: line range past EOF -> refused")

# ---- 3. viz.py render-time resolution --------------------------------------------------------
code.write_text("line1\nCHANGED\nline3\nline4\nline5\n", encoding="utf-8")  # dirty again for drift
r = viz._resolve_one_anchor({"file": "mod.py", "lines": [2, 4], "commit": sha, "repo": str(repo)},
                            str(repo), {})
check(r["kind"] == "drifted" and "line2" in r["code"] and "CHANGED" in r["diff"],
      "resolve: pinned truth shown (line2), working-tree change surfaces as drift diff")

subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "mod.py"], check=True)
r = viz._resolve_one_anchor({"file": "mod.py", "lines": [2, 4], "commit": sha, "repo": str(repo)},
                            str(repo), {})
check(r["kind"] == "pinned" and r["code"].split("\n")[0] == "line2" and r["code_start"] == 2,
      "resolve: unchanged -> pinned; code is CLEAN (no gutter baked in), code_start pins the number")

r = viz._resolve_one_anchor({"file": "mod.py", "lines": [1, 2], "commit": "deadbeef1234",
                             "repo": str(repo)}, str(repo), {})
check(r["kind"] == "unreachable" and "working copy" in r["cap"],
      "resolve: unknown sha -> fall back to working copy, loud caption")

r = viz._resolve_one_anchor({"file": "gone.py", "lines": [1, 2]}, str(repo), {})
check(r["kind"] == "missing" and r["code"] == "",
      "resolve: missing file, no pin -> caption only, nothing baked")

r = viz._resolve_one_anchor({"commit": sha, "repo": str(repo)}, str(repo), {})
check(r["kind"] == "commit" and "seed" in r["cap"] and "mod.py" in r["code"],
      "resolve: commit-only anchor -> subject line + --stat listing")

r = viz._resolve_one_anchor({"file": str(loose), "lines": [1, 2]}, str(nogit), {})
check(r["kind"] == "fileonly" and "working copy" in r["cap"],
      "resolve: file-only (non-git) -> current copy, captioned as unpinned")

# budget: _slice keeps the FULL range (drift compares it); _cap_rows caps the display
big = "\n".join(f"x{i}" for i in range(500))
rows, lo, clipped = viz._slice(big, [1, 500])
check(clipped and len(rows) == 500,
      "budget: _slice returns the full range (uncut) and flags that display will clip")
disp, dropped = viz._cap_rows(rows)
check(len(disp) == viz._ANCH_MAX_LINES and dropped == 500 - viz._ANCH_MAX_LINES,
      f"budget: _cap_rows caps display at {viz._ANCH_MAX_LINES} lines and reports the drop count")

# drift past the display cap must still be DETECTED (review finding: truncation-blind drift)
longf = repo / "long.py"
longf.write_text("\n".join(f"l{i}" for i in range(1, 201)) + "\n", encoding="utf-8")
subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
subprocess.run(["git", "-C", str(repo), "commit", "-qm", "long"], check=True)
sha2 = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
                      capture_output=True, text=True).stdout.strip()
body = longf.read_text(encoding="utf-8").splitlines()
body[149] = "l150-CHANGED"  # line 150 — beyond the 120-line display cap
longf.write_text("\n".join(body) + "\n", encoding="utf-8")
r = viz._resolve_one_anchor({"file": "long.py", "lines": [1, 200], "commit": sha2,
                             "repo": str(repo)}, str(repo), {})
check(r["kind"] == "drifted" and "l150-CHANGED" in r["diff"],
      "drift beyond the 120-line display cap is still detected and diffed")
subprocess.run(["git", "-C", str(repo), "checkout", "-q", "--", "long.py"], check=True)

# ---- 4. reportcheck gate: legacy grace -------------------------------------------------------
sys.path.insert(0, str(ROOT / "hooks"))
import paths  # noqa: E402
import reportcheck  # noqa: E402

os.environ["HARNESS_PROJECT"] = "anchortest"
_dr = Path(tempfile.mkdtemp())
os.environ["TRAINLINT_DATA_DIR"] = str(_dr)  # isolate: plan/.state land in a throwaway data root
try:
    art = tmp / "built.txt"
    art.write_text("x", encoding="utf-8")
    rows = [{"id": "old-built", "status": "decided", "artifact": str(art)},
            {"id": "unbuilt", "status": "decided"}]
    pf = paths.wfile("plan.anchortest.jsonl")
    pf.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    new, legacy = reportcheck._plan_anchor_gaps()
    check(new == [] and legacy == ["old-built"],
          "gate: FIRST sight grandfathers existing built-unanchored (no bounce)")
    rows.append({"id": "new-built", "status": "decided", "artifact": str(art)})
    pf.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    new, legacy = reportcheck._plan_anchor_gaps()
    check(new == ["new-built"] and legacy == ["old-built"],
          "gate: decision built AFTER the snapshot -> hard gap; legacy stays soft")
    rows[-1]["anchors"] = "paper"
    pf.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    new, _ = reportcheck._plan_anchor_gaps()
    check(new == ["new-built"],
          "gate: 'paper' does NOT satisfy a BUILT decision (there IS code — pin it)")
    rows[-1]["anchors"] = "mod.py:1-2"
    pf.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    new, _ = reportcheck._plan_anchor_gaps()
    check(new == [],
          "gate: a file-only anchor satisfies (never demand SHAs git can't mint)")
finally:
    os.environ.pop("TRAINLINT_DATA_DIR", None)
    os.environ.pop("HARNESS_PROJECT", None)

print(f"\n{('ALL PASS' if not fails else f'{fails} FAILURES')}")
sys.exit(1 if fails else 0)
