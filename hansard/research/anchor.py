#!/usr/bin/env python3
"""anchor.py — pin the exact code a decision should be REVIEWED against, in one command.

A decision's `artifact` proves something exists; an ANCHOR records WHICH code to read: file,
line range, and the commit it was written at. The report then bakes that snippet into every
decision card, so a reviewer (even off-machine, on a phone) sees the real code — not a claim.

    python3 anchor.py <project> <decision-id> <file>[:<start>[-<end>]] [more specs...]
    python3 anchor.py <project> <decision-id> --commit <sha> [--repo <dir>]   # a whole commit
    python3 anchor.py <project> <decision-id> --paper       # explicit: prose-only, no code
    python3 anchor.py <project> --list                      # anchor state of every decision

Anchors are BORN-VALID: the repo root is discovered from the file's own directory (so code in
ANY repo — not just the plugin's — anchors to its own git), the commit is the repo's HEAD, and
`git cat-file -e` refuses a pin the repo can't actually serve. A non-git file records file-only
(no commit) — the report shows the working copy and says so; it never demands SHAs git can't mint.

Writes ONLY the matched decision's physical line in plan.<project>.jsonl (same single-line
atomic rewrite as the report's edit backend); comments and other rows are kept byte-for-byte.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402
import plan as planmod  # noqa: E402


def _git(repo, *args):
    """Run git -C <repo> <args>; (rc, stdout). Never raises."""
    try:
        r = subprocess.run(["git", "-C", str(repo), *args],
                           capture_output=True, text=True, timeout=15)
        return r.returncode, r.stdout.strip()
    except Exception:
        return 1, ""


def _spec_from_file(arg):
    """Build one anchor dict from `<file>[:<start>[-<end>]]`, verified against the file's own git
    repo. Returns (spec, warnings) — spec is None only when the file itself is missing."""
    warns = []
    path_part, _, lines_part = arg.partition(":")
    fpath = Path(path_part).expanduser().resolve()
    if not fpath.is_file():
        return None, [f"no such file: {path_part}"]

    lines = None
    if lines_part:
        try:
            a, _, b = lines_part.partition("-")
            lo, hi = int(a), int(b or a)
            lines = [min(lo, hi), max(lo, hi)]
        except ValueError:
            return None, [f"bad line range {lines_part!r} (want start or start-end): {arg}"]
        total = sum(1 for _ in fpath.open(encoding="utf-8", errors="replace"))
        if lines[0] > total:
            return None, [f"{fpath.name} has {total} lines; range {lines[0]}-{lines[1]} is past the end"]
        if lines[1] > total:
            warns.append(f"{fpath.name}: end {lines[1]} > file length {total}; clamped")
            lines[1] = total

    rc, top = _git(fpath.parent, "rev-parse", "--show-toplevel")
    if rc != 0 or not top:
        warns.append(f"{fpath.name}: not in a git repo — recorded file-only (no commit pin); "
                     "the report will show the current working copy")
        spec = {"file": str(fpath)}
        if lines:
            spec["lines"] = lines
        return spec, warns

    repo = str(Path(top))
    rel = os.path.relpath(str(fpath), repo)
    _, sha = _git(repo, "rev-parse", "--short=12", "HEAD")
    spec = {"file": rel, "repo": repo}
    if lines:
        spec["lines"] = lines

    if not sha:  # repo with no commits yet
        warns.append(f"{rel}: repo has no commits — recorded file-only; commit and re-run to pin")
        return spec, warns
    rc, _ = _git(repo, "cat-file", "-e", f"{sha}:{rel}")
    if rc != 0:
        warns.append(f"{rel}: not in HEAD ({sha}) — recorded file-only; commit it and re-run to pin")
        return spec, warns
    # BORN-VALID means valid against the PIN, not the working tree: the render slices lines out of
    # `git show sha:rel`, so the range must exist THERE, and if the pinned lines differ from what's
    # on disk right now the reviewer would review code the author never wrote. Check both; a pin
    # that would lie degrades to file-only (still an anchor, honestly labeled) + a re-run hint.
    rcp, pinned = _git(repo, "show", f"{sha}:{rel}")
    if rcp != 0:
        warns.append(f"{rel}: HEAD ({sha}) unreadable — recorded file-only; commit and re-run to pin")
        return spec, warns
    prows = pinned.splitlines()
    if lines and lines[0] > len(prows):
        warns.append(f"{rel}: lines {lines[0]}-{lines[1]} don't exist at HEAD ({sha}) — your range "
                     "is uncommitted. Recorded file-only; COMMIT, then re-run to pin")
        return spec, warns
    if lines and lines[1] > len(prows):
        warns.append(f"{rel}: end {lines[1]} > HEAD's {len(prows)} lines; clamped to the pin")
        lines[1] = len(prows)
    try:
        wrows = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        wrows = []
    lo, hi = (lines[0], lines[1]) if lines else (1, len(prows))
    if prows[lo - 1:hi] != wrows[lo - 1:hi]:
        warns.append(f"{rel}: the pinned lines ({sha}) DIFFER from your working copy — you're "
                     "anchoring the committed version, not what you just wrote. COMMIT first and "
                     "re-run for an exact pin")
    spec["commit"] = sha
    return spec, warns


def _spec_from_commit(sha, repo_arg):
    """Anchor a whole commit as the evidence. Verifies the commit exists in the repo."""
    repo = str(Path(repo_arg).expanduser().resolve()) if repo_arg else os.getcwd()
    rc, top = _git(repo, "rev-parse", "--show-toplevel")
    if rc != 0 or not top:
        return None, [f"--commit needs a git repo (looked in {repo}); pass --repo <dir>"]
    repo = str(Path(top))
    rc, full = _git(repo, "rev-parse", "--short=12", f"{sha}^{{commit}}")
    if rc != 0 or not full:
        return None, [f"commit {sha} not found in {repo}"]
    return {"commit": full, "repo": repo}, []


def _merge_anchor_line(name, dec_id, new_specs, replace=False):
    """Single-line atomic merge into plan.<name>.jsonl (pattern: chat_backend._rewrite_jsonl_field).
    Appends new_specs to the row's `anchors` (dedup by identity), or replaces when replace=True."""
    p = paths.resolve(f"plan.{name}.jsonl")
    if not p.exists():
        return None, f"no plan file: {p}"
    raw = p.read_text(encoding="utf-8")
    lines = raw.split("\n")
    idx, obj = None, None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        try:
            o = json.loads(s)
        except Exception:
            continue
        if isinstance(o, dict) and str(o.get("id", "")) == dec_id:
            idx, obj = i, o
            break
    if obj is None:
        known = ", ".join(str(r.get("id", "?")) for r in planmod.load(name)) or "(plan empty)"
        return None, f"decision '{dec_id}' not in plan.{name}.jsonl — ids: {known}"

    cur = obj.get("anchors")
    cur = cur if isinstance(cur, list) else ([cur] if cur else [])
    merged = [] if replace else list(cur)
    for spec in new_specs:
        if spec not in merged:
            merged.append(spec)
    obj["anchors"] = merged
    lines[idx] = json.dumps(obj, ensure_ascii=False)

    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tl-anchor-", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp, p)
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None, f"write failed: {e}"
    return obj, None


def _fmt_spec(s):
    if s.get("paper"):
        return "✎ paper (prose-only)"
    loc = s.get("file", "")
    if s.get("lines"):
        loc += f":{s['lines'][0]}-{s['lines'][1]}"
    if s.get("commit"):
        loc = (loc + "@" if loc else "commit ") + s["commit"]
    if s.get("repo"):
        loc += f"  (repo {s['repo']})"
    return loc


def _list(name):
    pl = planmod.load(name)
    if not pl:
        print(f"no plan for '{name}'")
        return 1
    for n in pl:
        built = planmod.artifact_exists(n)
        specs = planmod._anchor_specs(n)
        if planmod.has_anchor(n):
            tag = "⛓"
        elif planmod.anchor_is_paper(n):
            tag = "✎"
        elif built:
            tag = "✗"  # built but nothing to review — the gap this tool exists to close
        else:
            tag = "·"
        print(f"{tag} {n.get('id','?'):<24} {n.get('status','open'):<9}"
              f"{'built' if built else '     '}  {len(specs)} anchor(s)")
        for s in specs:
            print(f"    {_fmt_spec(s)}")
    print("\n⛓ anchored · ✎ declared paper-only · ✗ BUILT but unanchored (backfill me) · · open/paper")
    return 0


USAGE = """usage:
  python3 anchor.py <project> <decision-id> <file>[:<start>[-<end>]] [more specs...]
  python3 anchor.py <project> <decision-id> --commit <sha> [--repo <dir>]
  python3 anchor.py <project> <decision-id> --paper
  python3 anchor.py <project> --list"""


def main(argv):
    if len(argv) < 2:
        print(USAGE)
        return 2
    name = argv[0]
    if argv[1] == "--list":
        return _list(name)
    if len(argv) < 3:
        print("need: <decision-id> and at least one <file>[:<lines>] / --commit / --paper")
        return 2
    dec_id, rest = argv[1], argv[2:]

    specs, warns, errs = [], [], []
    if rest[0] == "--paper":
        row = next((r for r in planmod.load(name) if str(r.get("id")) == dec_id), None)
        if row is not None and planmod.artifact_exists(row):
            print(f"✗ '{dec_id}' has an artifact on disk — it is BUILT, so there IS code to anchor. "
                  "Point me at it instead of declaring paper.")
            return 1
        specs, replace = [{"paper": True}], True
    elif rest[0] == "--commit":
        sha = rest[1] if len(rest) > 1 else ""
        repo_arg = rest[3] if len(rest) > 3 and rest[2] == "--repo" else None
        spec, w = _spec_from_commit(sha, repo_arg)
        warns += w
        if spec is None:
            errs += w or ["bad --commit"]
        else:
            specs.append(spec)
        replace = False
    else:
        replace = False
        for arg in rest:
            spec, w = _spec_from_file(arg)
            (warns if spec is not None else errs).extend(w)
            if spec is not None:
                specs.append(spec)

    for w in warns:
        print(f"⚠ {w}")
    if not specs:
        for e in errs:
            print(f"✗ {e}")
        return 1

    obj, err = _merge_anchor_line(name, dec_id, specs, replace=replace)
    if err:
        print(f"✗ {err}")
        return 1
    print(f"⛓ {dec_id} ← {len(specs)} anchor(s) recorded:")
    for s in specs:
        print(f"    {_fmt_spec(s)}")
    if errs:
        for e in errs:
            print(f"✗ skipped: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
