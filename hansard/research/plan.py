#!/usr/bin/env python3
"""Project PLAN — the ordered list of DECISIONS that defines a project, and the
"floor plan" both machines consume.

Until now Hansard only ever saw individual tool calls and matched keywords in
whatever file was being touched. The mistakes that actually cost weeks are not
keystrokes — they are DECISIONS (which ckpt to bootstrap, how to eval, what the
loss weights are). The plan is the missing representation of those decisions.

It feeds two distinct machines off ONE artifact:
  - the plan-quiz walks these decisions to teach the operator (deliberate, offline);
  - the plan-aware doorman LOCATES a live action on this plan and routes/escalates
    by the decision's state + governing principle (online) — instead of keyword spray.

Authored by the agent (drafted from goal+facts) and confirmed by the user; durable.
One JSONL line per decision:
  id | phase | decision | choice | principle | why | status | match?
    status   : open (undecided) | decided (chosen + rationale) | verified (checked it holds)
    principle: the governing law id — links to quiz.jsonl principles + the rule layer
    match    : regex recognising an action that touches this decision (used by the doorman)

Pure & read-only. Degrades to [] when a project has no plan yet.
"""
import glob as _glob
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import sys as _sys  # noqa: E402
_sys.path.insert(0, str(ROOT))
import paths  # noqa: E402  — per-project data lives outside the versioned plugin dir

STATUSES = ("open", "decided", "verified")


def _artifact_specs(node):
    """Raw artifact path/glob specs a decision declares to prove it was BUILT. A decision becomes
    `built` not by typing a choice but by naming a DURABLE artifact (a file path or glob) that the
    choice produced. Accepts a string, a list of strings, or a dict with 'path'/'glob'. A dict 'cmd'
    is a runnable CHECK, not a static artifact, so it does NOT count toward `built` — proving a run
    holds is exactly what the `verified` status is for. Returns [] when none declared."""
    a = node.get("artifact")
    if not a:
        return []
    out = []

    def _add(x):
        if isinstance(x, str) and x.strip():
            out.append(x.strip())
        elif isinstance(x, dict):
            for k in ("path", "glob"):
                v = x.get(k)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())

    if isinstance(a, list):
        for x in a:
            _add(x)
    else:
        _add(a)
    return out


def artifact_exists(node, base=None):
    """True iff the decision names a durable artifact AND at least one match exists on disk. Paths
    resolve relative to `base` (default: the cwd — the project repo the operator runs from); absolute
    paths are honored. This is the test that separates a DECIDED decision (a choice on paper) from a
    BUILT one (a choice that produced something). Fail-open: any error -> False."""
    specs = _artifact_specs(node)
    if not specs:
        return False
    bases = []
    if base:
        bases.append(Path(base))
    bases.append(Path.cwd())
    for spec in specs:
        try:
            p = Path(spec)
            if p.is_absolute():
                if p.exists() or _glob.glob(spec):
                    return True
                continue
            for b in bases:
                if (b / spec).exists() or _glob.glob(str(b / spec)):
                    return True
        except Exception:
            continue
    return False


# --- anchors: the REVIEWABLE evidence behind a built decision ---------------------------------
# `artifact` proves something exists; an ANCHOR pins the exact code a reviewer should read:
# a file + line range + the commit it was reviewed at. Report cards bake the anchored snippet in,
# so "what code is this decision?" is answerable off-machine. Record them with research/anchor.py.
#
# Accepted forms (string, dict, or a list of either):
#   "src/auth.js:36-53@01139f6"     file:start-end@commit  (lines and @commit each optional)
#   {"file": "src/auth.js", "lines": [36, 53], "commit": "01139f6", "repo": "/path", "note": "..."}
#   {"commit": "280d440"}           a whole commit as the evidence
#   "paper"                         explicit claim: this decision is prose-only, no code to review
#     (distinct from MISSING — "paper" is an answer, absence is a gap the report gate flags)

_ANCHOR_SHA_RE = re.compile(r"^[0-9a-fA-F]{6,40}$")
_ANCHOR_LINES_RE = re.compile(r"^(\d+)(?:-(\d+))?$")


def _anchor_file_ok(f):
    """A plausible FILE PATH, not prose: no whitespace, contains a '/' or an extension dot, and
    doesn't start with '-' (a leading dash would read as a git OPTION at render time — the specs
    feed `git show` argv, so this is the injection guard, enforced at the parse layer for every
    consumer). Junk like "see the PR" must NOT satisfy the anchor gate."""
    return (bool(f) and not re.search(r"\s", f) and not f.startswith("-")
            and ("/" in f or "." in f))


def _parse_anchor_string(s):
    """"file[:a[-b]][@sha]" -> spec dict, parsed from the END (rsplit) so paths containing '@' or
    ':' survive: a trailing @suffix only counts as a commit when it's 6-40 hex chars, a trailing
    :suffix only as lines when it's digits[-digits]. Returns None for junk."""
    sha = None
    body, _, tail = s.rpartition("@")
    if body and _ANCHOR_SHA_RE.match(tail):
        sha = tail
    else:
        body = s
    lines = None
    f, _, tail = body.rpartition(":")
    m = _ANCHOR_LINES_RE.match(tail) if f else None
    if m:
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        lines = [min(lo, hi), max(lo, hi)]
    else:
        f = body
    if not _anchor_file_ok(f):
        return None
    d = {"file": f}
    if lines:
        d["lines"] = lines
    if sha:
        d["commit"] = sha
    return d


def _anchor_specs(node):
    """Normalized anchor dicts a decision declares ({file?, lines?, commit?, repo?, note?}), or the
    sentinel [{"paper": True}]. Same shape-tolerance and fail-open stance as _artifact_specs:
    accepts a string, a dict, or a list of either; garbage entries are dropped, never raised.
    VALIDATION IS HERE, once, for every consumer (gate, compass, badge, render): commits must be
    hex (they land in `git show` argv — see _anchor_file_ok for the injection guard), files must
    look like paths."""
    a = node.get("anchors", node.get("anchor"))
    if not a:
        return []
    out = []

    def _add(x):
        if isinstance(x, str):
            s = x.strip()
            if not s:
                return
            if s.lower() == "paper":
                out.append({"paper": True})
                return
            d = _parse_anchor_string(s)
            if d:
                out.append(d)
        elif isinstance(x, dict):
            if x.get("paper"):
                out.append({"paper": True})
                return
            d = {}
            if isinstance(x.get("file"), str) and _anchor_file_ok(x["file"].strip()):
                d["file"] = x["file"].strip()
            ln = x.get("lines")
            if (isinstance(ln, (list, tuple)) and len(ln) == 2
                    and all(isinstance(v, int) for v in ln)):
                d["lines"] = [min(ln), max(ln)]
            # dict-form commit gets the SAME hex check as the string form — an arbitrary string
            # here would reach `git show <commit>` as a positional arg (option injection).
            if isinstance(x.get("commit"), str) and _ANCHOR_SHA_RE.match(x["commit"].strip()):
                d["commit"] = x["commit"].strip()
            for k in ("repo", "note"):
                if isinstance(x.get(k), str) and x[k].strip():
                    d[k] = x[k].strip()
            if d.get("file") or d.get("commit"):
                out.append(d)

    if isinstance(a, list):
        for x in a:
            _add(x)
    else:
        _add(a)
    return out


def has_anchor(node):
    """True iff the decision carries at least one CODE anchor (file or commit). The "paper"
    sentinel does NOT count: a built decision (artifact on disk) claiming "paper" is still
    unanchored — there IS code, it just wasn't pinned for review."""
    return any(not s.get("paper") for s in _anchor_specs(node))


def anchor_is_paper(node):
    """True iff the decision explicitly declares itself prose-only (anchors: "paper")."""
    specs = _anchor_specs(node)
    return bool(specs) and all(s.get("paper") for s in specs)


def built(plan=None, name=None, base=None):
    """Decisions that actually PRODUCED something — status decided/verified AND a declared artifact
    that exists. The missing lens the whole workflow lacked: `decided` only means 'chosen on paper',
    `built` means 'the code/data is on disk'. A plan can sit at 8/9 decided with 0 built — every fork
    reasoned out, nothing made yet — and until now no surface said so."""
    if plan is None:
        plan = load(name)
    return [n for n in plan
            if n.get("status") in ("decided", "verified") and artifact_exists(n, base)]


def _active(name=None):
    return name or paths.active_project()


def load(name=None):
    """Ordered list of plan decision nodes. [] if there is no plan file."""
    name = _active(name)
    p = paths.resolve(f"plan.{name}.jsonl")
    rows = []
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return rows


_ADAPTER_CACHE = {}


def canonical_principle(pid, name=None):
    """Map a project-LOCAL principle id to its canonical id in the single rules bank
    (principles.jsonl / quiz.jsonl) via the per-project adapter, research/principle_adapter.json.
    Rules live in ONE place; each project only writes an adapter that translates its local
    vocabulary onto them (the same shape as codex_compat / kimi_compat for hosts). Lookup tries the
    project scope then '*'; absent => identity (the local id already IS canonical). Fail-open."""
    if not pid:
        return pid
    name = _active(name)
    if "map" not in _ADAPTER_CACHE:
        try:
            _ADAPTER_CACHE["map"] = json.loads(
                (ROOT / "principle_adapter.json").read_text(encoding="utf-8"))
        except Exception:
            _ADAPTER_CACHE["map"] = {}
    amap = _ADAPTER_CACHE["map"]
    for scope in (name, "*"):
        m = amap.get(scope)
        if isinstance(m, dict) and pid in m:
            return m[pid]
    return pid


def by_id(plan, pid):
    for n in plan:
        if n.get("id") == pid:
            return n
    return None


def locate(haystack, plan=None, name=None):
    """Which plan decisions does this action text touch? (its `match` regex hit).
    This is the bridge the plan-aware doorman uses: action text -> decision(s)."""
    if plan is None:
        plan = load(name)
    if not haystack:
        return []
    out = []
    for n in plan:
        m = n.get("match")
        if not m:
            continue
        try:
            if re.search(m, haystack, re.IGNORECASE):
                out.append(n)
        except re.error:
            continue
    return out


def summary(plan=None, name=None):
    """Counts by status + the still-risky decisions, for briefings / viz."""
    if plan is None:
        plan = load(name)
    counts = {s: 0 for s in STATUSES}
    for n in plan:
        counts[n.get("status", "open")] = counts.get(n.get("status", "open"), 0) + 1
    # `built` keys are SEPARATE from `counts` on purpose: counts must still sum to total (open +
    # decided + verified), while built is an orthogonal lens (decided/verified WITH an artifact).
    decided_built = [n for n in plan if n.get("status") == "decided" and artifact_exists(n)]
    paper_only = [n for n in plan if n.get("status") == "decided" and not artifact_exists(n)]
    return {
        "total": len(plan),
        "counts": counts,
        "built": len(built(plan)),
        "decided_built": len(decided_built),
        "paper_only": paper_only,
        "open": [n for n in plan if n.get("status", "open") == "open"],
        "unverified": [n for n in plan if n.get("status") == "decided"],
    }


def main_thread(plan=None, name=None):
    """The single driving decision — the 'main thread' the work should focus on right now:
    the load-bearing OPEN decision if one is marked (the one that most gates the plan / the
    cheapest test that could invalidate it), else the first open, else the first decided-but-
    unverified. None when everything is verified. This is what keeps work focused instead of a
    flat menu of every decision."""
    if plan is None:
        plan = load(name)
    # The load-bearing decision stays the thread until it is VERIFIED — typing a `choice` (decided),
    # or even building it, does NOT retire it, because it is the cheapest test that could sink the
    # whole plan. (The old code required it to be `open`, so the moment a choice was typed the thread
    # jumped off it to the first remaining decision by file order — that's the bouncing main thread.)
    lb = [n for n in plan if n.get("load_bearing") and n.get("status") != "verified"]
    if lb:
        return lb[0]
    opens = [n for n in plan if n.get("status", "open") == "open"]
    if opens:
        return opens[0]
    # Everything open is settled: drive the cheapest UNBUILT decided decision (a paper choice with no
    # artifact) before a merely-unverified one — you BUILD before you verify.
    unbuilt = [n for n in plan if n.get("status") == "decided" and not artifact_exists(n)]
    if unbuilt:
        return unbuilt[0]
    unver = [n for n in plan if n.get("status") == "decided"]
    return unver[0] if unver else None


def pillars(plan=None, name=None):
    """The project's CORE dimensions — decisions marked `pillar: true`. The compass shows these
    EVERY turn, EVEN WHEN they're decided, so a core dimension (e.g. the text/audio interleave)
    can't vanish from view just because its decision is settled. Unlike main_thread (the one open
    thing to drive NEXT), pillars are about what the project fundamentally IS — they keep the whole
    shape in view instead of collapsing it to a single thread or a one-line goal."""
    if plan is None:
        plan = load(name)
    return [n for n in plan if n.get("pillar")]


def avoided(plan=None, name=None):
    """The explicitly REJECTED options (anti-prior decisions) the agent must not drift back into.
    A decision pins one with two fields:
      not_this: human-readable rejected option (e.g. "use the reference codebase's pipeline as the impl")
      not_re:   regex that recognizes an action DRIFTING toward it (specific to the rejected
                *usage*, not the legitimate reference — so 'borrow the reference recipe' doesn't trip it)
    Used by the compass (ambient reminder) + the plan-aware doorman (action-level catch)."""
    if plan is None:
        plan = load(name)
    return [{"id": n.get("id", ""), "not_this": n.get("not_this", ""),
             "choice": n.get("choice", ""), "not_re": n.get("not_re", ""), "why": n.get("why", "")}
            for n in plan if n.get("not_re") and n.get("not_this")]


def brief(name=None):
    """One-line plan status, or '' if no plan exists for this project."""
    plan = load(name)
    if not plan:
        return ""
    s = summary(plan)
    c = s["counts"]
    # Lead with BUILT-of-decided — the honest one-glance state. "0/8 built" says, at a glance, that
    # eight forks were chosen on paper and none produced anything yet; "verified" and "open" trail it.
    return (f"plan: {len(plan)} decisions "
            f"({s['decided_built']}/{c.get('decided', 0)} decided built · "
            f"{c.get('verified', 0)} verified · {c.get('open', 0)} open)")


if __name__ == "__main__":
    import sys
    nm = sys.argv[1] if len(sys.argv) > 1 else None
    pl = load(nm)
    if not pl:
        print(f"(no plan for project '{_active(nm)}' — draft one with /hansard:plan)")
        sys.exit(0)
    # Compact, phase-grouped MAP — the "overview" layer of the plan report (Shneiderman: overview
    # first). Scannable by id + status icon; details stay on demand (read plan.<name>.jsonl / quiz).
    mt = main_thread(pl)
    head = brief(nm)
    if mt:
        head += f"  ·  main thread → {mt.get('id','')}"
    print(head)
    ICON = {"verified": "✓", "decided": "●", "open": "○"}
    width = max((len(n.get("id", "")) for n in pl), default=0)

    def _mark(n):
        # ✎ = decided on paper but NOT built (no artifact on disk) — the state the old map hid by
        # showing every chosen fork as a solid ●. ● now means decided AND produced an artifact.
        if n.get("status") == "decided" and not artifact_exists(n):
            return "✎"
        return ICON.get(n.get("status", "open"), "?")
    phases = []  # phases in first-appearance order — no hard-coded list, generic across projects
    for n in pl:
        if n.get("phase", "") not in phases:
            phases.append(n.get("phase", ""))
    for ph in phases:
        print(f"\n  {ph}")
        for n in pl:
            if n.get("phase", "") != ph:
                continue
            mark = _mark(n)
            tags = ("★" if n.get("load_bearing") else " ") + ("◆" if n.get("pillar") else "")
            print(f"    {mark} {n.get('id',''):<{width}}  {tags}")
    print("\n  ✓=verified ●=decided+built ✎=decided on paper (not built) ○=open · ★=main thread ◆=pillar"
          "  ·  detail: read research/plan.<name>.jsonl or /hansard:plan")
