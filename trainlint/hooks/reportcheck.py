#!/usr/bin/env python3
"""Stage — the REPORT doorman (Stop event).

The one surface the doorman was blind to: the agent emitting a closing/status
REPORT as free prose. It is not a tool call (no PreToolUse) and not a user prompt
(no UserPromptSubmit), so no hook ever saw it — the explain-like-a-person voice
rules (commands/plan.md step 6) were pure persuasion the model drops at large
context. This binds to the Stop event and turns that persuasion into enforcement:
when the final message is a plan REPORT that skips the spec's structural anchors,
it bounces ONCE (decision:block) for a rewrite.

DETERMINISTIC + CONSERVATIVE. It fires only on a report-shaped message (cites >=2
of the ACTIVE project's plan-decision ids, long-form) and only on objective,
spec-mandated misses — never on a judgement about "good prose". Limits, stated
honestly: it can police the codenames it has a registry for (plan ids) and the
required anchors (stance line / map); it CANNOT catch arbitrary domain jargon
(`cu_seqlens`, `TP=4/EP=8`) — no deterministic check can enumerate that.

LOOP-SAFE: `stop_hook_active` means we already bounced once this turn -> never
bounce again. FAIL-OPEN: any error -> no items, never raises, never blocks.
"""
import json
import re
import sys
from pathlib import Path

RESEARCH = Path(__file__).resolve().parent.parent / "research"
sys.path.insert(0, str(RESEARCH))
try:
    import plan as planlib  # noqa: E402
except Exception:  # pragma: no cover
    planlib = None

# A message shorter than this is an answer, not a report — never gate it.
_MIN_REPORT_CHARS = 600
# Report-shaped = walks the plan: cites at least this many distinct decision ids.
_MIN_CITED_IDS = 2
# Patois floor: this many distinct STRONG jargon tokens (snake_case / file.ext / config=N)
# means the report is leaning on raw identifiers instead of plain language. Calibrated on the
# a real report (8 strong) vs a compliant rewrite (0) — 4 separates them with margin.
_MAX_JARGON = 4
# STRONG markers: a reader who didn't build this can't decode these from the prose.
# (Bare ALLCAPS acronyms — AR, LR, PPL — are deliberately EXCLUDED: they appear in good prose
# too, so counting them would nag. We police the identifiers only a builder would recognise.)
_JARGON_PATTERNS = [
    r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b",                  # snake_case: cu_seqlens, vq_mask, loss_mask
    r"\b[A-Za-z_][\w]*\.(?:py|jsonl?|ya?ml|sh|txt)\b",  # file.ext: rows.py, collator.py
    r"\b[A-Za-z]{1,5}=\d+\b",                          # inline config: TP=4, EP=8
]


def _last_assistant_text(transcript_path):
    """Concatenated text of the LAST assistant turn in the transcript, or ''.
    Best-effort: the transcript is JSONL, one event per line."""
    try:
        p = Path(transcript_path)
        if not p.exists():
            return ""
        last = ""
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message", obj)
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                last = content
            elif isinstance(content, list):
                parts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
                if any(parts):
                    last = "\n".join(parts)
        return last
    except Exception:
        return ""


def _leads_a_line(token, text):
    """True if `token` opens any line — i.e. it is used as a bare codename/subject
    rather than a trailing tag. Strips markdown bullet/heading/bold/backtick markers
    first, so `- **eff-batch:** ...` and `\\`eff-batch\\` is ...` both count as leading,
    but `... — \\`eff-batch\\` (it's now ...)` (mid-sentence, glossed) does NOT."""
    esc = re.escape(token)
    # line start, optional markdown markers, optional backtick/bold, then the token.
    pat = re.compile(r"^[ \t]*[-*>#]*[ \t]*[`*]*" + esc + r"\b", re.I | re.M)
    return bool(pat.search(text))


def _jargon(text):
    """Distinct STRONG jargon tokens — raw identifiers a non-builder can't decode."""
    found = set()
    for p in _JARGON_PATTERNS:
        for m in re.finditer(p, text):
            found.add(m.group(0))
    return found


def _cited(ids, text):
    out = []
    for i in ids:
        if not i:
            continue
        if re.search(r"(?<![\w-])" + re.escape(i) + r"(?![\w-])", text, re.I):
            out.append(i)
    return out


def check(data):
    """Stop-event report gate. Returns [] for every non-report case."""
    if data.get("hook_event_name") not in ("Stop", "SubagentStop"):
        return []
    if data.get("stop_hook_active"):
        return []  # we already bounced this turn — one forced rewrite, never a loop
    if planlib is None:
        return []
    try:
        text = _last_assistant_text(data.get("transcript_path", ""))
        if len(text) < _MIN_REPORT_CHARS:
            return []
        ids = [d.get("id", "") for d in planlib.load()]
        cited = _cited(ids, text)
        if len(cited) < _MIN_CITED_IDS:
            return []  # not report-shaped: a normal answer, not a plan walk

        misses = []
        # A. the stance line  —  "<N>/<total> decided · <k> pillars · main thread → ..."
        if not re.search(r"\d+\s*/\s*\d+[^\n]{0,40}decid", text, re.I):
            misses.append("the one-line stance — `<N>/<total> decided · <k> pillars · "
                          "main thread → <plain name of the load-bearing decision>`")
        # B. the phase-grouped map (its glyphs ● ○ ✓, or the "main thread →" header)
        if not (re.search(r"[●○✓◆★]", text) or re.search(r"main thread\s*[→\->]", text, re.I)):
            misses.append("the phase-grouped map — paste `python3 research/plan.py`, don't hand-format it")
        # C. bare codenames (voice rule 2: lead with the meaning, keep the id a trailing tag)
        bare = [c for c in cited if _leads_a_line(c, text)]
        if len(bare) >= 2:
            misses.append("these read as codenames, not meanings — lead each with what it IS and "
                          "keep the id a trailing tag: " + ", ".join("`%s`" % b for b in bare))
        # D. undefined jargon (voice rule 1: write from the reader's chair). Raw identifiers the
        # plan-id check can't see (cu_seqlens, modded_dac_vq, rows.py, TP=4) — gloss on first use or cut.
        jargon = sorted(_jargon(text))
        if len(jargon) >= _MAX_JARGON:
            shown = ", ".join("`%s`" % j for j in jargon[:6])
            more = "" if len(jargon) <= 6 else f" (+{len(jargon) - 6} more)"
            misses.append("undefined jargon a teammate can't decode — define each on first use in one "
                          "plain phrase, or cut it: " + shown + more)

        if not misses:
            return []
        msg = ("📋 REPORT gate — this reads like a plan REPORT but skips the explain-like-a-person "
               "standard (commands/plan.md step 6). Write it for a teammate who did NOT build this. "
               "Before finishing, revise to add/fix: " + "; ".join("— " + m for m in misses)
               + ". (This bounces once; the rewrite goes straight through.)")
        return [{"name": "report-readability", "level": "reject", "certain": True, "message": msg}]
    except Exception:
        return []
