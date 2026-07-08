#!/usr/bin/env python3
"""Agentic digest — one headless Claude Code agent per operator-feedback item.

The lightweight digest (feedback.py) makes ONE batched codex call to classify all feedback into
confusion/correction/readability. This module is the heavyweight alternative the report's "Deal
with all requests" button can drive: it spawns ONE read-only Claude Code agent per NEW feedback
item, each of which INVESTIGATES the real project code + the report substrate (grounded in
file:line) like a developer picking up a ticket, and returns a structured verdict + proposal. The
full agent transcript is saved as the "development record".

SAFETY MODEL (matches the operator's chosen design — read-only agents + a deterministic applier):
  * Agents run `claude -p --allowedTools "Read Grep Glob" --disallowedTools "Bash Write Edit
    NotebookEdit"`. The allowed three cannot write; the disallow removes Bash too (which is
    otherwise auto-approved for read-only shell and COULD write) — so there is NO write path at all,
    verified: the agent reports "no file-writing tool available". Its blast radius is read + return-
    text only. (No --dangerously-skip-permissions.)
  * The real writes are done HERE, serially, by deterministic Python (never by a racing agent):
      - confusion  -> auto-apply the additive glossary entries it proposed, deduped by term.
      - correction -> NEVER auto-applied; recorded pending (kind=correction) with the agent's
                      verdict + record path, surfaced by goalcheck for human review.
      - readability-> recorded pending; the concrete report change waits for the agent/operator.
  * Corrections are verified against the real code BY the agent (operator_right/operator_wrong) so
    a wrong dispute is refused with evidence, not rubber-stamped.

Records per item under data_root/feedback_runs/<project>/<key>/:
  transcript.jsonl  — the full stream-json agent session (every read/grep/reasoning step)
  outcome.json      — the parsed structured verdict + proposal
Status is tracked in data_root/.digest_status.json ({state, total, done, applied, pending, …}) so
the button's poll shows "3/8 agents done".

Run:  python3 feedback_agent.py <project>            (orchestrate all new feedback)
      TRAINLINT_DIGEST_AGENTS=<n>  bounds concurrency (default 3)
      TRAINLINT_AGENT_MODEL=<id>   overrides the agent model (default: inherit CLI default)
"""
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import paths      # noqa: E402
import feedback   # noqa: E402  — reuse collect_new / _feedback_path / _merge

CLAUDE = os.environ.get("HANSARD_CLAUDE_BIN") or os.environ.get("TRAINLINT_CLAUDE_BIN", "claude")
# Read/Grep/Glob cannot write. We ALSO --disallow Bash/Write/Edit: without --disallow, Bash stays
# available (Claude Code auto-approves read-only shell like `ls`/`sed -n`), and Bash CAN write — so
# the read-only guarantee would rest on a read-vs-write heuristic, not on the absence of a write
# path. Disallowing them removes the escape hatch entirely (verified: the agent reports "no
# file-writing tool available"). Investigation quality is unaffected — Read/Grep/Glob cover it.
READ_ONLY_TOOLS = ["Read", "Grep", "Glob"]
DENY_TOOLS = ["Bash", "Write", "Edit", "NotebookEdit"]
PER_AGENT_TIMEOUT = int(os.environ.get("HANSARD_AGENT_TIMEOUT") or os.environ.get("TRAINLINT_AGENT_TIMEOUT", "600"))  # seconds
_status_lock = threading.Lock()


def _runs_dir(project):
    d = paths.data_root() / "feedback_runs" / project
    d.mkdir(parents=True, exist_ok=True)
    return d


def _repo_dirs(project):
    """The real project code dirs the agent may read (from project.<name>.json), existing only."""
    try:
        proj = json.loads((paths.data_root() / f"project.{project}.json").read_text(encoding="utf-8"))
    except Exception:
        proj = {}
    out = []
    for k in ("repo_root", "home"):
        d = proj.get(k)
        if d and Path(d).is_dir() and d not in out:
            out.append(d)
    return out


def _status(obj):
    """Atomic .digest_status.json write (shared with chat_backend + viz), fail-silent."""
    with _status_lock:
        try:
            p = paths.data_root() / ".digest_status.json"
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
            tmp.replace(p)
        except Exception:
            pass


AGENT_SYS = (
    "You are the hansard 'feedback developer': you handle ONE piece of operator feedback left on a "
    "research status report, exactly like a developer picking up a ticket. You investigate the REAL "
    "project code and the report substrate (grounded in file:line) and return a STRUCTURED VERDICT. "
    "You are READ-ONLY (Read/Grep/Glob only) — you propose, you do not apply. A deterministic step "
    "applies the safe part later, so your job is to be RIGHT and GROUNDED, not to edit anything.")


def _agent_prompt(item, project, sub_dirs, data_root):
    return f"""{AGENT_SYS}

THE FEEDBACK ITEM:
- kind so far (pre-classified, may be wrong): {item.get('kind') or 'unknown'}
- left on / context: {item.get('quote') or '(no anchor context recorded)'}
- operator wrote/asked: "{item.get('note')}"

READ FIRST — the report substrate for project '{project}' (under {data_root}/):
  goal.{project}.txt · purpose.{project}.txt · plan.{project}.jsonl (the decisions) ·
  surprises.{project}.jsonl · focus.{project}.jsonl · glossary.{project}.jsonl · clarify.{project}.jsonl
THE REAL PROJECT CODE you may read: {', '.join(sub_dirs) or '(none configured — substrate only)'}

DO, like a dev:
1. If the referent is ambiguous, LOCATE it in the substrate first (which decision/surprise/card).
2. Classify: confusion | correction | readability.
3. If it's a CORRECTION (operator says something is WRONG), VERIFY the disputed claim against the
   REAL code before deciding. Set claim_verdict: operator_right (there IS an error) /
   operator_wrong (the disputed thing is actually correct/deliberate) / na (not a dispute).
   NEVER endorse a change you verified to be wrong — refuse it with evidence.
4. Produce the concrete resolution you WOULD apply (someone else applies it):
   - confusion  -> glossary entries that would have prevented it (term/plain/why), and/or a wording note.
   - correction (operator_right) -> the fix, described precisely with file:line.
   - correction (operator_wrong) -> the evidence the current state is correct; propose NO change.
   - readability -> the concrete report/wording change.
   Ground EVERY claim in file:line (substrate AND real code).

RETURN — your FINAL message must be ONLY this JSON (no prose, no code fence), small and valid:
{{
  "kind": "confusion|correction|readability",
  "claim_verdict": "operator_right|operator_wrong|na",
  "diagnosis": "2-5 sentences: what you found and how you concluded, grounded",
  "change_summary": "ONE line naming the change you would apply (or why you refuse)",
  "glossary_add": [{{"term": "...", "plain": "one jargon-free sentence", "why": "why it matters HERE, with file:line"}}],
  "proposal": "the full concrete change (diff / wording / refusal+evidence), copy-pasteable",
  "evidence": ["file:line", "..."],
  "confidence": "high|medium|low"
}}
Only include glossary_add for genuine CONFUSION gaps (empty list otherwise). Keep proposal focused."""


def _parse_result(transcript_path):
    """Pull the agent's final structured JSON from the stream-json transcript."""
    final = None
    try:
        for line in transcript_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") == "result" and "result" in ev:
                final = ev.get("result")
    except Exception:
        return None
    if not isinstance(final, str):
        return None
    s, e = final.find("{"), final.rfind("}")
    if s < 0 or e <= s:
        return None
    try:
        return json.loads(final[s:e + 1])
    except Exception:
        return None


def _run_one(project, item, sub_dirs):
    """Spawn one read-only agent for `item`; return (item, outcome_dict_or_None, record_dir)."""
    key = item.get("key") or ""
    slug = "".join(c if (c.isalnum() or c in "._-") else "_" for c in key)[:80] or "item"
    rec = _runs_dir(project) / slug
    rec.mkdir(parents=True, exist_ok=True)
    prompt = _agent_prompt(item, project, sub_dirs, str(paths.data_root()))
    cmd = [CLAUDE, "-p", prompt, "--output-format", "stream-json", "--verbose",
           "--allowedTools", *READ_ONLY_TOOLS, "--disallowedTools", *DENY_TOOLS,
           "--add-dir", str(paths.data_root())]
    for d in sub_dirs:
        cmd += ["--add-dir", d]
    model = os.environ.get("HANSARD_AGENT_MODEL") or os.environ.get("TRAINLINT_AGENT_MODEL")
    if model:
        cmd += ["--model", model]
    tpath = rec / "transcript.jsonl"
    cwd = sub_dirs[0] if sub_dirs else str(paths.data_root())
    try:
        with open(tpath, "wb") as tf, open(os.devnull, "rb") as devnull:
            subprocess.run(cmd, stdin=devnull, stdout=tf, stderr=subprocess.DEVNULL,
                           cwd=cwd, timeout=PER_AGENT_TIMEOUT)
    except Exception as e:
        (rec / "outcome.json").write_text(json.dumps({"error": str(e)[:300]}), encoding="utf-8")
        return item, None, rec
    outcome = _parse_result(tpath)
    if outcome is not None:
        outcome["key"] = key
        outcome["src"] = item.get("src")
        (rec / "outcome.json").write_text(json.dumps(outcome, ensure_ascii=False, indent=2), encoding="utf-8")
    return item, outcome, rec


def _apply_confusion(project, outcomes):
    """SERIAL, deterministic apply of the SAFE part: additive glossary from confusion items,
    deduped by term. Corrections/readability are never auto-applied here. Returns terms_added."""
    gpath = paths.wfile(f"glossary.{project}.jsonl")
    from tree import _load_jsonl
    have = {str(e.get("term") or "").lower() for e in (_load_jsonl(gpath) if gpath.exists() else [])
            if isinstance(e, dict)} - {""}
    added = 0
    with gpath.open("a", encoding="utf-8") as f:
        for o in outcomes:
            if o.get("kind") != "confusion":
                continue
            for t in o.get("glossary_add") or []:
                if not isinstance(t, dict):
                    continue
                term = str(t.get("term") or "").strip()
                plain = str(t.get("plain") or "").strip()
                if not term or not plain or term.lower() in have:
                    continue
                f.write(json.dumps({"term": term, "plain": plain, "why": str(t.get("why") or "").strip()},
                                   ensure_ascii=False) + "\n")
                have.add(term.lower())
                added += 1
    return added


def _record_verdicts(project, results):
    """Write each item's verdict into feedback.<name>.jsonl (line-preserving merge), so corrections
    surface in goalcheck/compass for human review and re-digesting is idempotent. record_path lets
    the report/operator open the full worklog. Confusion resolved when a glossary term was added."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    recs = []
    for item, o, rec in results:
        base = {"src": item.get("src"), "key": item.get("key"), "quote": item.get("quote", ""),
                "note": item.get("note", ""), "ts": item.get("ts", ""), "digested": now,
                "agentic": True, "record": str(rec)}
        if not o:
            base.update({"kind": "unclassified", "insight": "agent produced no verdict", "action": ""})
            recs.append(base)
            continue
        kind = o.get("kind") if o.get("kind") in ("confusion", "correction", "readability") else "unclassified"
        base.update({"kind": kind, "insight": o.get("diagnosis", "")[:500],
                     "action": o.get("change_summary", "")[:500],
                     "claim_verdict": o.get("claim_verdict", "na"),
                     "confidence": o.get("confidence", ""),
                     "proposal": (o.get("proposal", "") or "")[:2000]})
        # confusion with an applied glossary term is resolved; corrections/readability stay pending
        if kind == "confusion" and (o.get("glossary_add") or []):
            base.update({"resolved": True, "resolution": "auto-glossary (agentic)", "resolved_at": now})
        recs.append(base)
    p = feedback._feedback_path(project)
    # merge by (src,key): replace an existing row, append new ones (line-preserving)
    updated = {(r["src"], r["key"]): r for r in recs}
    feedback._merge(p, updated, recs)
    return recs


def _log_update(project, results, added):
    """Append ONE 'update' event to log.<project>.jsonl summarizing the requests this digest
    handled — so the report's Timeline tab carries a permanent record of what the button did
    (the transient requests are cleared page-side once handled). Renders via viz.KIND['update']."""
    from datetime import datetime, timezone
    outs = [o for _i, o, _r in results if o]
    if not outs:
        return
    conf = [o for o in outs if o.get("kind") == "confusion"]
    corr = [o for o in outs if o.get("kind") == "correction"]
    read = [o for o in outs if o.get("kind") == "readability"]
    terms = [str(t.get("term") or "") for o in conf for t in (o.get("glossary_add") or []) if isinstance(t, dict)]
    bits = [f"{len(outs)} request(s) handled"]
    if added:
        bits.append(f"+{added} glossary" + (f" ({', '.join(t for t in terms if t)[:120]})" if terms else ""))
    for o in corr:
        v = o.get("claim_verdict")
        tag = ("verified WRONG → refused" if v == "operator_wrong"
               else "verified RIGHT → fix proposed" if v == "operator_right" else "reviewed")
        bits.append(f"correction: {str(o.get('change_summary') or '')[:90]} [{tag}]")
    if read:
        bits.append(f"{len(read)} readability note(s) pending")
    pend = len([o for o in corr if o.get("claim_verdict") != "operator_wrong"]) + len(read)
    if pend:
        bits.append(f"{pend} pending your review")
    note = "🤖 Deal with all requests — " + "; ".join(bits) + "."
    ev = {"ts": datetime.now(timezone.utc).date().isoformat(), "kind": "update",
          "direction": "operator-requests", "note": note}
    try:
        lp = paths.wfile(f"log.{project}.jsonl")
        with lp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


def run(project):
    """Orchestrate the agentic digest for one project: one read-only agent per NEW feedback item
    (bounded concurrency), then a serial deterministic apply. Returns a summary dict."""
    items, recs = feedback.collect_new(project)
    # collect_new returns (llm_item_strings, skeleton_records); we want the rich records
    if not recs:
        _status({"state": "done", "project": project, "summary": f"{project}: no new feedback"})
        return {"project": project, "items": 0, "applied": 0, "pending": 0}
    sub_dirs = _repo_dirs(project)
    total = len(recs)
    cap = max(1, int(os.environ.get("HANSARD_DIGEST_AGENTS") or os.environ.get("TRAINLINT_DIGEST_AGENTS", "3")))
    done = [0]
    import time as _t
    _status({"state": "running", "mode": "agentic", "project": project, "total": total,
             "done": 0, "started": _t.time(), "pid": os.getpid(), "via": "agentic"})

    def _task(it):
        r = _run_one(project, it, sub_dirs)
        with _status_lock:
            done[0] += 1
        _status({"state": "running", "mode": "agentic", "project": project, "total": total,
                 "done": done[0], "started": _t.time(), "pid": os.getpid(), "via": "agentic"})
        return r

    with ThreadPoolExecutor(max_workers=cap) as ex:
        results = list(ex.map(_task, recs))

    outcomes = [o for _it, o, _rec in results if o]
    added = _apply_confusion(project, outcomes)
    _record_verdicts(project, results)
    _log_update(project, results, added)  # summary 'update' -> Timeline tab (before regen so it renders)
    corrections = [o for o in outcomes if o.get("kind") == "correction"]
    pending = [o for o in corrections if o.get("claim_verdict") != "operator_wrong"]
    summary = (f"{project}: {len(outcomes)}/{total} agents ok -> +{added} glossary term(s); "
               f"{len(corrections)} correction(s) ({len(pending)} need review)")
    try:
        import viz
        viz.generate(project)  # re-render + re-upload with the applied glossary + verdict box
    except Exception as e:
        summary += f"  [regen failed: {str(e)[:120]}]"
    _status({"state": "done", "mode": "agentic", "project": project, "summary": summary,
             "finished": _t.time()})
    print(summary)
    return {"project": project, "items": total, "applied": added,
            "corrections": len(corrections), "pending": len(pending)}


if __name__ == "__main__":
    nm = sys.argv[1] if len(sys.argv) > 1 else None
    if not nm:
        sys.exit("usage: feedback_agent.py <project>")
    run(nm)
