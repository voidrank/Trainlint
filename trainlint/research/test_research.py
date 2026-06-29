#!/usr/bin/env python3
"""Tests for the research-lint: tree reconstruction, governor shape, surfacer coupling."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tree
import governor
import surfacer
import plan
import progress
import viz


def main():
    fails = 0

    def check(cond, msg):
        nonlocal fails
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            fails += 1

    facts = tree.load_facts("example")
    # build from the durable log only (structured derive degrades to [] off-cluster)
    nodes = tree.build_tree(tree.load_annotations("example"), facts)

    check("loss-weights" in nodes, "loss-weights direction reconstructed")
    check(nodes["loss-weights"]["status"] == "stalled",
          f"loss-weights is STALLED (got {nodes['loss-weights']['status']})")
    check(nodes["layout-chunk"]["status"] == "abandoned", "layout-chunk is ABANDONED (backtracked)")
    check(nodes["layout-stream"]["status"] == "deepening", "layout-stream is DEEPENING")
    check(nodes["nofreeze"]["status"] == "deepening", "nofreeze is DEEPENING")

    gov = "\n".join(governor.report(nodes, facts))
    check("trunk-check" in gov and "loss-weights" in gov,
          "governor warns 'check trunk before judging stalled branch dead'")
    prescribes = any(p in gov for p in ("recommend abandon", "should abandon", "give up", "abandon this branch"))
    check("unexplored" in gov and not prescribes,
          "governor surfaces unexplored moves and never PRESCRIBES abandonment (lint, not prune)")

    know = tree._load_jsonl(Path(__file__).resolve().parent / "knowledge.example.jsonl")
    surf = "\n".join(surfacer.report(nodes, know))
    check("frozen-codec" in surf or "context-dependent" in surf,
          "surfacer couples the 351-OOD wall to the frozen-tokenizer entry")
    check("Inner Monologue" in surf, "surfacer couples the 'rambling' wall to Moshi inner-monologue")

    # --- plan artifact: the decision floor-plan both machines consume ---
    pl = plan.load("example")
    check(len(pl) >= 10, f"plan.example loaded ({len(pl)} decisions)")
    check(all(n.get("id") and n.get("decision") and n.get("principle")
              and n.get("status") in plan.STATUSES for n in pl),
          "every plan decision has id + decision + principle + a valid status")
    # the bridge the plan-aware doorman will use: an action's text -> the decision it touches
    hit = plan.locate("writing tf_top1.py to measure teacher-forced accuracy", pl)
    check(any(n["id"] == "eval-protocol" for n in hit),
          "locate() maps a teacher-forcing action onto the eval-protocol decision")
    check(plan.by_id(pl, "eval-protocol")["principle"] == "free-running-not-teacher-forced-is-the-test",
          "eval-protocol decision is governed by the free-running principle (the audit's central miss)")
    s = plan.summary(pl)
    check(sum(s["counts"].values()) == len(pl) and plan.brief("example").startswith("plan:"),
          "plan.summary/brief account for every decision")
    # main thread = the load-bearing open decision (the compass's focus), not just the first open
    mt = plan.main_thread(pl)
    check(mt is not None and mt.get("load_bearing") and mt["status"] == "open",
          "main_thread() picks the load_bearing OPEN decision as the thing to drive")
    # pillars = the core dimensions that stay in view EVEN WHEN decided (so none silently drops)
    pil = plan.pillars(pl)
    check(len(pil) >= 2 and any(p.get("status") != "open" for p in pil),
          "pillars() returns the core dimensions, including settled ones (kept in view)")
    check(any(p["id"] == "stream-layout" for p in pil),
          "a decided-but-core dimension (stream-layout = the interleave) is a pillar, not dropped")

    # --- plan-quiz coverage: quiz after any plan change, only the new/changed/unmastered ---
    prog0 = {}
    check(len(progress.targets(pl, prog0)) == len(pl),
          "with empty progress, EVERY decision is a quiz target")
    d0 = pl[0]
    prog1 = {d0["id"]: {"fp": progress.fingerprint(d0), "mastered": True}}
    tg1 = progress.targets(pl, prog1)
    check(len(tg1) == len(pl) - 1 and all(n["id"] != d0["id"] for n in tg1),
          "a mastered + unchanged decision drops out of the target set")
    edited = dict(d0); edited["choice"] = d0.get("choice", "") + " (revised)"
    check(any(n["id"] == d0["id"] for n in progress.targets([edited], prog1)),
          "editing a mastered decision (fingerprint change) re-opens it for quizzing")

    # --- viz: PLANNING STAGE vs MATURE rendering ---------------------------------------
    # A project with a plan but no experiments (no log events, no search tree) must render
    # the tight plan story (motivation -> goal -> main thread -> next + the decision spine),
    # NOT the mature arc/timeline/tree/pipeline-band that would all be empty.
    pln = viz.render_html("__planning_probe__", "Goal probe sentence", "the done bar",
                          pl, {}, [], {}, {}, [], motivation="MOTIVATION_PROBE_TEXT")
    check("a plan in progress" in pln, "planning report uses the plan-in-progress subtitle")
    check("MOTIVATION" in pln and "MOTIVATION_PROBE_TEXT" in pln,
          "planning report leads with the motivation beat (from motivation.<name>.txt)")
    check("Decisions — the plan" in pln, "planning report renders the decision spine as the map")
    check("Pipeline — the system" not in pln,
          "planning report DROPS the pipeline band (phases are categories, not a flow)")
    check("No dated events harvested" not in pln and "Timeline — how the search" not in pln,
          "planning report suppresses the empty timeline instead of showing an empty box")
    check("Search tree" not in pln,
          "planning report suppresses the empty search tree")

    # A mature project (real log + tree) keeps the full rich view, untouched.
    mat = viz.render_html("example", "Goal", "bar", pl, nodes, know, {}, {}, [], motivation="ignored")
    check("Pipeline — the system" in mat, "mature report keeps the pipeline band")
    check("Timeline — how the search" in mat, "mature report keeps the timeline section")
    check("Search tree" in mat, "mature report keeps the search tree")
    check("a plan in progress" not in mat and "MOTIVATION_PROBE_TEXT" not in mat,
          "mature report does NOT switch to planning mode (and ignores motivation)")

    total = 30
    print(f"\n{total - fails}/{total} passed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
