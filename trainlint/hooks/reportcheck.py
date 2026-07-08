#!/usr/bin/env python3
"""Stage — the REPORT doorman (Stop event).

The one surface the doorman was blind to: the agent emitting a closing/status
REPORT as free prose. It is not a tool call (no PreToolUse) and not a user prompt
(no UserPromptSubmit), so no hook ever saw it — the explain-like-a-person voice
rules (commands/plan.md step 6) were pure persuasion the model drops at large
context. This binds to the Stop event and turns that persuasion into enforcement:
when the final message is a plan REPORT that skips the spec's structural anchors
(stance line / phase map / plain-language voice / the `HTML: <path>` sign-off),
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
import paths  # noqa: E402  — per-project data lives outside the versioned plugin dir
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


def _sent_report_html(transcript_path, tail_lines=90):
    """Which report views the close ACTUALLY delivered to the phone. Returns (report, slides):
    `report` = a `SendUserFile` of the interactive report `<name>.html`; `slides` = a `SendUserFile`
    of the `<name>.slides.html` deck. BOTH travel now (which-html: ship both) — the Claude mobile app
    renders each HTML inline via display:'render', so the phone gets the full report AND the glanceable
    deck; a path in the prose is only a path, this confirms the FILES were sent. Bounded to the
    transcript TAIL (the sends + report are adjacent in the close turn) so sends from a much earlier
    turn can't false-satisfy. Conservative + fail-open: any parse error -> not-confirmed, never raises.
    Note `.slides.html` also contains the substring `.html`; the report leg strips `.slides.html`
    first so a slides-only send does NOT count as the report."""
    report = slides = False
    try:
        p = Path(transcript_path)
        if not p.exists():
            return (False, False)
        lines = p.read_text(encoding="utf-8").splitlines()
        for line in lines[-tail_lines:]:
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
            if not isinstance(content, list):
                continue
            for b in content:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                if b.get("name") != "SendUserFile":
                    continue
                blob = json.dumps(b.get("input", {}), ensure_ascii=False).lower()
                if ".slides.html" in blob:
                    slides = True
                if ".html" in blob.replace(".slides.html", ""):
                    report = True
        return (report, slides)
    except Exception:
        return (report, slides)


def _used_askuserquestion(transcript_path, tail_lines=60):
    """True if this turn actually put a choice to the operator via the AskUserQuestion TOOL (a
    multiple-choice pop-up), not a plain-text question. Mirrors _sent_mobile; tail-bounded."""
    try:
        p = Path(transcript_path)
        if not p.exists():
            return False
        for line in p.read_text(encoding="utf-8").splitlines()[-tail_lines:]:
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
            if not isinstance(content, list):
                continue
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "AskUserQuestion":
                    return True
        return False
    except Exception:
        return False


# A plain-text message that puts a CHOICE to the operator — should have been an AskUserQuestion
# (multiple-choice pop-up) instead. Conservative: needs an option-solicitation pattern AND a '?'.
# Two false-positive classes bit real sessions and are excluded by construction:
#   • ①②③ used as ANSWER/section headers ("① 为什么…? <long answer> ② …") — a real option list
#     puts its markers NEAR each other, so the marker branch requires an adjacent PAIR (≤80 chars).
#   • solicit verbs inside a NEGATION ("没有需要你拍板的事", "免得…在等你选择") — matches with a
#     negating prefix within 12 chars are discarded in _plaintext_decision_gap.
_DECISION_SOLICIT = re.compile(
    r"要我[^。\n]{0,40}吗\s*[?？]"                 # 要我…吗？
    r"|二选一|三选一|你(来)?(选|定|拍板|决定)"       # 二选一 / 你选 / 你定 / 你拍板
    r"|哪(一)?(种|个|条)[^。\n]{0,20}[?？]"          # 哪种/哪个…？
    r"|[①②③][\s\S]{0,80}[①②③]"                    # an ADJACENT pair of circled markers
    r"|\b[Aa]\)\s[\s\S]{0,80}\b[Bb]\)\s"            # A) … B) as a real option pair
    r"|\b1\)\s[\s\S]{0,80}\b2\)\s"                  # 1) … 2) likewise
    r"|which (option|one|approach|do you)",
    re.I)

# a solicit-verb hit is NOT a solicitation when it sits inside a negation/denial
_SOLICIT_NEGATION = re.compile(r"(没有|不需要|无需|无须|不用|不必|免得|不是|并非|而不是)[^。\n？?]{0,12}$")


def _plaintext_decision_gap(text, transcript_path):
    """Fragment if the final message poses a CHOICE to the operator in plain text but the turn did
    NOT use the AskUserQuestion tool — else None. Enforces: user decisions go as multiple-choice."""
    if len(text) < 120:
        return None
    if not ("?" in text or "？" in text):
        return None
    real_hit = any(not _SOLICIT_NEGATION.search(text[max(0, m.start() - 15):m.start()])
                   for m in _DECISION_SOLICIT.finditer(text))
    if not real_hit:
        return None
    if _used_askuserquestion(transcript_path):
        return None
    return ("a user DECISION as multiple-choice — you put a choice to me in plain text (e.g. "
            "'要我…吗? / 二选一 / ①②') but didn't use the AskUserQuestion tool. Every choice that "
            "needs my decision goes through AskUserQuestion (a pop-up with the options + 'Other'), "
            "not a plain-text question, so I can't miss it and you get a clean answer")


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


# --- DATA-FORMAT gate -------------------------------------------------------------
# When a message DESCRIBES a data format, it must SHOW 3-4 concrete examples — an
# abstract field/grammar description with no real samples is the exact thing that reads
# as "看不懂". Universal (not gated on plan-id citations): any substantial final message
# that explains a format and under-illustrates it bounces once for examples.
_DATA_FORMAT_MIN_CHARS = 300
_MIN_FORMAT_EXAMPLES = 3


def _format_details(text):
    """Distinct STRUCTURAL signals that the text is describing a data format. >=2 => it is
    explaining a format (one passing mention of e.g. 'grammar' is not enough)."""
    d = set()
    if re.search(r"<\|speaker:|speaker\s+(?:tag|marker|turn|id)", text, re.I):
        d.add("speaker")
    if re.search(r"\[tag\]|\[\.\.\.\]|bracket\s+tag|inline\s+tag|\[laugh|\[music", text, re.I):
        d.add("tag")
    if re.search(r"\bjsonl?\b|\.jsonl\b", text, re.I):
        d.add("jsonl")
    if re.search(r"\bschema\b", text, re.I):
        d.add("schema")
    if re.search(r'"?\w+"?\s+field|\bfields?\b\s*[:：]|required\s+\w+\s+field|\bmax_length\b', text, re.I):
        d.add("field")
    if re.search(r"training\s+pairs?|input\s*[-=/→>]+\s*output|\(\s*input\s*,?\s*output\s*\)", text, re.I):
        d.add("pair")
    if re.search(r"\bgrammar\b", text, re.I):
        d.add("grammar")
    return d


def _count_examples(text):
    """How many concrete example UNITS the text shows. A fenced ``` block counts as the number
    of record-ish lines inside it (>=1 per block); a record-ish line outside a fence (a
    `<|speaker:...|>` sample, a JSON object, a `"key": ...` row) counts as one."""
    rec = re.compile(r"<\|speaker:|^\s*\{.*\".+\"\s*:|^\s*\"[^\"]+\"\s*:")
    n, in_fence, cur = 0, False, 0
    for ln in text.split("\n"):
        if ln.lstrip().startswith("```"):
            if not in_fence:
                in_fence, cur = True, 0
            else:
                in_fence = False
                n += max(1, cur)
            continue
        is_rec = bool(rec.search(ln))
        if in_fence:
            if is_rec:
                cur += 1
        elif is_rec:
            n += 1
    if in_fence:                      # text ended inside an unclosed fence
        n += max(1, cur)
    return n


def _data_format_fragment(text):
    """Fragment for the miss list if the text explains a data format but shows <3 examples,
    else None. Conservative: needs >=2 structural format signals before it fires."""
    if len(text) < _DATA_FORMAT_MIN_CHARS:
        return None
    # Fire only when the message is genuinely SHOWING format structure, not a summary that
    # name-drops "jsonl"/"speaker tags" in passing: need >=3 distinct format signals, OR >=2
    # signals AND an actual token literal (<|speaker:..|> or a [lowercase] tag) present.
    details = _format_details(text)
    has_literal = bool(re.search(r"<\|speaker:|<\|\w{1,12}\|>|\[[a-z][a-z ]{1,20}\]", text))
    if not (len(details) >= 3 or (len(details) >= 2 and has_literal)):
        return None
    n = _count_examples(text)
    if n >= _MIN_FORMAT_EXAMPLES:
        return None
    return (f"3-4 concrete EXAMPLES of the data format — you describe the format "
            f"(speaker/tag/jsonl structure) but show only {n} real sample(s). Paste 3-4 actual "
            f"examples (real `<|speaker:0|> ... [laughs] ...` lines and/or real JSONL rows), not just "
            f"a field-by-field description, so the shape is unambiguous")


def _plan_format_gaps():
    """The PLAN-SOURCE half: the heavy format content lives in the decisions that viz.py renders
    into the HTML, NOT in the chat summary. Scan each decision's own text (decision+choice+why);
    return the ids of those that DESCRIBE a data format (>=2 structural signals) but carry <3
    concrete examples — so 'explain a format, show examples' is enforced where the content actually
    is, not only in the chat reply. Fail-open: any error -> no gaps."""
    gaps = []
    try:
        for d in planlib.load():
            # Focus on the decision(s) that DEFINE the data format (the 'format' phase / id), not
            # every decision that merely references speaker/tag tokens in passing.
            if d.get("phase") != "format" and "format" not in str(d.get("id", "")):
                continue
            # Newline-join so each example/field is its own line (the example counter is per-line);
            # expand list fields (e.g. `examples`) element-by-element.
            parts = []
            for k in ("decision", "choice", "why", "note", "notes", "examples"):
                v = d.get(k)
                if isinstance(v, (list, tuple)):
                    parts.extend(str(x) for x in v)
                elif v:
                    parts.append(str(v))
            blob = "\n".join(parts)
            if len(_format_details(blob)) >= 2 and _count_examples(blob) < _MIN_FORMAT_EXAMPLES:
                did = d.get("id")
                if did:
                    gaps.append(did)
    except Exception:
        return []
    return gaps


def _pillar_level_gaps():
    """Pillars are the project's CORE — they must be high-level strategy, not implementation
    detail. Return the ids of any `pillar` decision tagged `level: impl`, so a code-level contract
    can't masquerade as a core dimension. Fail-open."""
    gaps = []
    try:
        for d in planlib.load():
            if d.get("pillar") and str(d.get("level", "high")).lower() == "impl":
                did = d.get("id")
                if did:
                    gaps.append(did)
    except Exception:
        return []
    return gaps


_GOAL_HEADLINE_MAX_WORDS = 25


def _goal_too_long():
    """The goal's first sentence is the report's WHAT-WE-WANT headline. Keep it ONE short, clear
    sentence — the pillars carry the detail as bullets. Return the headline word count if it blows
    past the cap, else 0. Fail-open."""
    try:
        active = paths.active_project()
        p = paths.resolve(f"goal.{active}.txt")
        if not p.exists():
            return 0
        g = " ".join(p.read_text(encoding="utf-8").split())
        # headline = up to the first sentence end or the DONE clause, whichever comes first
        head = re.split(r"(?<=[.])\s+|\bDONE\b", g, maxsplit=1, flags=re.I)[0]
        n = len(head.split())
        return n if n > _GOAL_HEADLINE_MAX_WORDS else 0
    except Exception:
        return 0


def _plan_plain_gaps():
    """Every decision must carry a one-sentence PLAIN-language summary (`plain` field) — that's
    what the report leads each decision with, so a teammate gets the meaning before the dense
    rationale. Return the ids of decisions missing a non-empty `plain`. Fail-open."""
    gaps = []
    try:
        for d in planlib.load():
            if not str(d.get("plain", "")).strip():
                did = d.get("id")
                if did:
                    gaps.append(did)
    except Exception:
        return []
    return gaps


def _plan_anchor_gaps():
    """Gate G data: (new_gaps, legacy_gaps) — BUILT decisions that pin NO reviewable code (no
    `anchors`, no explicit `anchors:"paper"` claim; and "paper" never satisfies a BUILT decision —
    there IS code, it just wasn't pinned). File-only anchors DO satisfy: never demand SHAs git
    can't mint (non-git dirs). LEGACY GRACE: everything already built-unanchored the first time
    this gate sees a project is grandfathered into .state/anchorseen.<name>.json — those surface
    as the report's ✗ badge + backfill stub, never as a bounce; only decisions that BECOME built
    after that snapshot hard-bounce. Fail-open."""
    try:
        active = paths.active_project()
        if not active or not hasattr(planlib, "has_anchor"):
            return [], []
        # base=project_home: hooks run from whatever cwd the session happens to be in, and the
        # grandfather snapshot below is written ONCE — a first run from a foreign directory must
        # not bake a wrong (relative-artifact-invisible) legacy list forever.
        home = paths.project_home(active) or None
        cur = [d.get("id") for d in planlib.load()
               if d.get("id")
               and d.get("status") in ("decided", "verified")
               and planlib.artifact_exists(d, home)
               and not planlib.has_anchor(d)]  # note: "paper" is NOT exempt here — an artifact
        #      on disk means there IS code; "paper" only excuses genuinely artifact-less decisions
        #      (which the artifact_exists filter already skips).
        snap = paths.state_dir() / f"anchorseen.{active}.json"
        if not snap.exists():
            snap.write_text(json.dumps({"legacy": sorted(cur)}), encoding="utf-8")
            return [], cur
        legacy = set(json.loads(snap.read_text(encoding="utf-8")).get("legacy", []))
        return [i for i in cur if i not in legacy], [i for i in cur if i in legacy]
    except Exception:
        return [], []


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

        # UNIVERSAL gates — run on ANY substantial final message (need not cite plan ids). If one
        # fires and the message is NOT a plan report, bounce on it alone.
        df_fragment = _data_format_fragment(text)
        dec_fragment = _plaintext_decision_gap(text, data.get("transcript_path", ""))

        def _universal_only():
            frs = [f for f in (df_fragment, dec_fragment) if f]
            if not frs:
                return []
            return [{"name": "universal-gate", "level": "reject", "certain": True,
                     "message": "📐 " + " ".join("— " + f for f in frs)
                                + ". (This bounces once; the rewrite goes straight through.)"}]

        if len(text) < _MIN_REPORT_CHARS:
            return _universal_only()
        ids = [d.get("id", "") for d in planlib.load()]
        cited = _cited(ids, text)
        if len(cited) < _MIN_CITED_IDS:
            return _universal_only()  # not report-shaped: a normal answer

        misses = []
        if df_fragment:
            misses.append(df_fragment)
        if dec_fragment:
            misses.append(dec_fragment)
        gaps = _plan_format_gaps()
        if gaps:
            misses.append("these PLAN decisions describe a data format but show <3 examples: "
                          + ", ".join("`%s`" % g for g in gaps)
                          + " — add 3-4 real samples to each one's choice/notes (they're the heavy "
                          "content viz.py renders into the HTML, where the lint couldn't reach before)")
        pillar_lvl = _pillar_level_gaps()
        if pillar_lvl:
            misses.append("these decisions are PILLARS but tagged `level: impl`: "
                          + ", ".join("`%s`" % g for g in pillar_lvl)
                          + " — a pillar must be high-level strategy; either raise it to `level: high` "
                          "or drop its pillar status so implementation detail isn't treated as core")
        long_head = _goal_too_long()
        if long_head:
            misses.append(f"the goal's headline is {long_head} words (cap {_GOAL_HEADLINE_MAX_WORDS}) — "
                          "make the first sentence ONE short, clear line; move the detail into the "
                          "pillars and the `DONE = …` clause so WHAT-WE-WANT reads at a glance")
        plain_gaps = _plan_plain_gaps()
        if plain_gaps:
            misses.append("these PLAN decisions have no plain-language summary (`plain` field): "
                          + ", ".join("`%s`" % g for g in plain_gaps)
                          + " — add a one-sentence, jargon-free summary to each so the report can lead "
                          "with the meaning before the dense rationale")
        # G. the ANCHOR gate (an item without its code is unreviewable). A decision that was BUILT
        # this session must pin the exact code it produced — file:lines@commit — so the report card
        # can bake the snippet in and a reviewer sees the real code, not a claim. One command records
        # a born-valid anchor. Pre-feature legacy decisions are grandfathered (✗ badge, no bounce);
        # staleness/drift NEVER gates (code evolving after review shouldn't bounce reports).
        anchor_new, _anchor_legacy = _plan_anchor_gaps()
        if anchor_new:
            misses.append("these decisions are BUILT but pin no code a reviewer can open: "
                          + ", ".join("`%s`" % g for g in anchor_new)
                          + " — run `python3 $CLAUDE_PLUGIN_ROOT/research/anchor.py <project> <id> "
                          "<file>:<start>-<end>` on the exact code each produced (it stamps the "
                          "commit and verifies the pin), so every report item carries its code")
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
        # E. the HTML sign-off (commands/plan.md step 6: "End by returning the HTML report path").
        # `/trainlint:plan` and `/trainlint:execute-and-report` BOTH close on viz's `HTML: <path>`
        # line — the one-glance picture (decision spine + per-decision chatbot/quiz) to open. That
        # too was prose-only persuasion the model drops at large context (this very gate exists
        # because that happens); bind it here so a plan walk that forgets to render the report bounces.
        html_signed = (bool(re.search(r"\bHTML:\s*\S+\.html\b", text, re.I))
                       or bool(re.search(r"\bviz\.[\w.-]*\.html\b", text, re.I)))
        if not html_signed:
            misses.append("the HTML sign-off — run `python3 research/viz.py <project>` and end on its "
                          "`HTML: <path>` line so I always have the one-glance report to open")
        # E2. the DELIVERY gate — the close must put the report ON MY PHONE, not just print a path.
        # A `/home/.../viz/<name>.html` address is useless on a phone, but the Claude mobile app
        # RENDERS an HTML file sent with display:'render' inline — so the close must actually
        # `SendUserFile` the report `.html`, and I get the full report on my phone. Required only on a
        # rendered close (when the HTML sign-off is present) — a report that never rendered viz is
        # already bounced by E above, so this doesn't double-fire on it.
        if html_signed:
            report_sent, slides_sent = _sent_report_html(data.get("transcript_path", ""))
            if not (report_sent and slides_sent):
                missing = ([] if report_sent else ["the report `<name>.html`"]) + \
                          ([] if slides_sent else ["the slides deck `<name>.slides.html`"])
                misses.append("the DELIVERY gate — a path I can't open from my phone isn't delivered, "
                              "and BOTH views travel now. `SendUserFile` " + " and ".join(missing) +
                              " with display:'render' so each renders inline on my phone (don't just "
                              "name the path)")
        # F. the BUILT lens (decided≠built). The failure this whole gate exists to stop in reports:
        # a plan sitting at 8/9 "decided" with nothing produced reads as almost-done. If ANY decision
        # is decided-on-paper (a choice typed, no artifact on disk), a plan report must SAY so —
        # surface built-of-decided, not a bare "decided" count. Only required when there's something
        # paper-only to disclose (an all-verified/all-built report needn't mention it).
        try:
            paper_only = [d for d in planlib.load()
                          if d.get("status") == "decided" and not planlib.artifact_exists(d)]
        except Exception:
            paper_only = []
        if paper_only:
            has_built_lens = (bool(re.search(r"\bbuilt\b", text, re.I))
                              or "纸面" in text or "未造" in text or "没造" in text)
            if not has_built_lens:
                misses.append(
                    f"the BUILT lens — {len(paper_only)} decision(s) here are decided on PAPER with no "
                    f"artifact on disk; the report must surface built-of-decided (e.g. `0/8 built`) and "
                    f"say what this run actually produced, not a bare `decided` count that reads as done")

        if not misses:
            return []
        msg = ("📋 REPORT gate — this reads like a plan REPORT but skips the explain-like-a-person "
               "standard (commands/plan.md step 6). Write it for a teammate who did NOT build this. "
               "Before finishing, revise to add/fix: " + "; ".join("— " + m for m in misses)
               + ". (This bounces once; the rewrite goes straight through.)")
        return [{"name": "report-readability", "level": "reject", "certain": True, "message": msg}]
    except Exception:
        return []
