#!/usr/bin/env python3
"""Project-flow hook — context / per-turn hint / viz-on-tree-change / quiz kickoff.

Everything is anchored to the PROJECT (the active-project name), to work events, and to
each turn — NEVER to session boundaries (a session may never end, and SessionEnd is not
guaranteed). One entry, dispatched by hook_event_name:

  SessionStart      -> context briefing (re-established after each compaction too)
  UserPromptSubmit  -> (1) per-turn hint (deduped)  (2) viz when the tree changed
                       (3) quiz kickoff, once per project

It only emits text to inject; the agent acts on the viz/quiz directives. Always exits 0,
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
        s = plan.summary(pl)
        nxt = (s["open"] or s["unverified"])
        tail = f" — next decision: {nxt[0]['decision']}" if nxt else ""
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
        # (1) per-turn hint, deduped (only when it changed)
        h = lint.brief(name)
        if h and h != _read(STATE / f"{name}.hint"):
            _write(STATE / f"{name}.hint", h)
            out.append(h)
        # (2) viz when the tree changed (baseline silently on first sight; nudge on later changes)
        fp = _tree_fp(nodes)
        prev = _read(STATE / f"{name}.treefp")
        if fp != prev:
            _write(STATE / f"{name}.treefp", fp)
            if prev and nodes:
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
