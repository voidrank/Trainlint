#!/usr/bin/env python3
"""Project-flow hook — context / always-on compass / per-turn hint / viz-on-tree-change.

Everything is anchored to the PROJECT (the active-project name), to work events, and to
each turn — NEVER to session boundaries (a session may never end, and SessionEnd is not
guaranteed). One entry, dispatched by hook_event_name:

  SessionStart      -> context briefing (re-established after each compaction too), led by the
                       goal + MAIN THREAD; flags an un-walked / un-written plan
  UserPromptSubmit  -> (0) always-on compass (goal + main thread, agent-facing, every turn)
                       (1) per-turn hint (deduped)  (2) viz when the tree has a real search

It only emits text to inject; the agent acts on the directives. Always exits 0,
writes nothing on error — must never break a session. Markers live in research/.state/
(gitignored), keyed by project name + a tree fingerprint.
"""
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tree      # noqa: E402
import lint      # noqa: E402  (lint.brief)
import plan      # noqa: E402  (plan.brief — the project's decision floor-plan)
import progress  # noqa: E402  (plan-quiz mastery/coverage state)

STATE = HERE / ".state"


def _read(p):
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _write(p, s):
    try:
        STATE.mkdir(exist_ok=True)
        p.write_text(s, encoding="utf-8")
    except Exception:
        pass


def _emit(text, event):
    if text:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": event,
                                                  "additionalContext": text}}, ensure_ascii=False))
    sys.exit(0)


def _tree_fp(nodes):
    sig = ";".join(f"{d}:{n['status']}:{n['spend']}:{len(n['walls'])}"
                   for d, n in sorted(nodes.items()))
    return hashlib.md5(sig.encode()).hexdigest()


def context_briefing(name, nodes):
    goal = _read(HERE / f"goal.{name}.txt")
    parts = [f"[trainlint:context] project '{name}' — re-establishing context."]
    if goal:
        parts.append("goal: " + goal)
    pl = plan.load(name)
    if pl:
        # understanding-gate — LEAD with it (prominent), at the session boundary, never mid-work:
        # the user is meant to fully understand the project before working, via the plan-quiz.
        try:
            tg = progress.targets(pl, name=name)
        except Exception:
            tg = []
        if tg:
            parts.append(f"⚠️ you have NOT fully walked this project yet — {len(tg)}/{len(pl)} "
                         f"decisions un-mastered. Understanding them comes first: `/trainlint:quiz` "
                         f"(soft — it only drills the un-mastered ones).")
        mt = plan.main_thread(pl)
        tail = f" — MAIN THREAD (drive this next): {mt['decision']}" if mt else " — all decisions settled"
        parts.append(plan.brief(name) + tail)
    else:
        # backstop: a registered project (init ran -> facts/project files exist) with an empty plan
        # means the plan was "started but never written". Flag it prominently — deterministic, every
        # session start, so a derailed foreground draft can't be silently dropped.
        registered = ((HERE.parent / f"project.{name}.json").exists()
                      or (HERE / f"facts.{name}.json").exists())
        if registered:
            parts.append("⚠️ this project is registered but its PLAN is NOT written yet (started but "
                         "never landed?) — run `/trainlint:plan` to draft the decisions. Don't leave "
                         "it unwritten; a plan on disk is what the quiz + doorman build on.")
        else:
            parts.append("no plan yet — draft the project's decisions with `/trainlint:plan`")
    b = lint.brief(name)
    parts.append(f"search tree: {len(nodes)} directions"
                 + (f"; {b}" if b else "; no stalled branches / ready papers right now"))
    parts.append("full picture any time: `/trainlint:viz` (or python3 research/viz.py)")
    return "  ·  ".join(parts)


def _viz_directive():
    return ("[trainlint:viz] the search tree changed since you last saw it — render it and send "
            f"me the picture: run `python3 {HERE / 'viz.py'}` and SendUserFile the PNG it prints "
            "(on mobile it lands as a zoomable image).")


def _compass(name):
    """The always-on motivation compass: GOAL (north star) + MAIN THREAD (the load-bearing open
    decision to drive right now). Agent-facing — injected every turn so the agent stays locked on
    the goal and doesn't drift into busywork; the goal is constant, the main thread updates as
    decisions resolve. Returns '' if there's nothing to anchor to yet."""
    goal = _read(HERE / f"goal.{name}.txt")
    pl = plan.load(name)
    mt = plan.main_thread(pl) if pl else None
    av = plan.avoided(pl) if pl else []
    bits = []
    if goal:
        bits.append("🎯 goal: " + goal)
    if mt:
        bits.append("main thread (drive this, don't wander): " + mt.get("decision", ""))
    if av:
        # the anti-prior reminder: the options the user already rejected that the agent keeps
        # drifting back toward. Kept in front of the agent every turn so the prior can't win.
        bits.append("⛔ already rejected (don't drift back): "
                    + "; ".join(a["not_this"] for a in av if a.get("not_this")))
    # standing grounding discipline — always on (this fires worst on unfamiliar code, where there
    # may be no goal/thread yet): research truth is in the specific code, not your prior.
    bits.append("🔎 ground every claim in file:line; if you don't know, write UNKNOWN and go READ — "
                "don't narrate a plausible guess")
    return "[trainlint:compass] " + "  ·  ".join(bits)


def main():
    data = json.load(sys.stdin)
    event = data.get("hook_event_name", "")
    name = tree._active()
    facts = tree.load_facts(name)
    nodes = tree.build_tree(tree.load_events(name, facts), facts)

    if event == "SessionStart":
        _emit(context_briefing(name, nodes), event)

    if event == "UserPromptSubmit":
        out = []
        # (0) the ALWAYS-ON compass — goal + main thread, every turn, agent-facing. Keeps the agent
        # locked on the north star + the one thing to drive, so it doesn't drift into busywork.
        c = _compass(name)
        if c:
            out.append(c)
        # (1) per-turn hint, deduped (only when it changed)
        h = lint.brief(name)
        if h and h != _read(STATE / f"{name}.hint"):
            _write(STATE / f"{name}.hint", h)
            out.append(h)
        # (2) viz when the tree changed — but ONLY for a project with a REAL search (branching or a
        # wall). A pre-experiment project's tree is empty/trivial; nudging viz there is busywork.
        fp = _tree_fp(nodes)
        prev = _read(STATE / f"{name}.treefp")
        if fp != prev:
            _write(STATE / f"{name}.treefp", fp)
            worth_viz = len(nodes) > 1 or any(n.get("walls") for n in nodes.values())
            if prev and worth_viz:
                out.append(_viz_directive())
        # NOTE: the plan-quiz is intentionally NOT nudged mid-turn here. The understanding-gate
        # lives at the SessionStart briefing (a session boundary) + the plan-aware doorman flags
        # acting on an un-mastered decision. Quizzing mid-work is the exact interruption we avoid.
        _emit("\n".join(out), event)

    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
