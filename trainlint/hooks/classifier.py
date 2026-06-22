#!/usr/bin/env python3
"""Stage 2 — intent classifier.

Routes the (already pre-filtered) action into coach/escalate/reject reminders.
Pluggable backend:

  - DEFAULT (no model): a regex rubric over triggers.jsonl producing COACH-level
    nudges. This is deterministic, testable, and equal to the harness's original
    behavior — so the thing keeps working with zero model dependency.

  - MODEL backend (set via set_backend): a small fast model (e.g. claude-haiku)
    reads the action + the rubric and returns richer {class, name, message}.
    It only ROUTES; it never judges correctness (that is stage 3 / the human).
    Tuned for recall on dangerous classes: when unsure, escalate.

The model backend is intentionally a slot, not yet wired — stages 1 and 3 carry
the load first, exactly as planned.
"""
import json
import os
import re
from pathlib import Path

import facts  # project-facts expansion of {{placeholders}}

ROOT = Path(__file__).resolve().parent.parent
TRIGGERS = ROOT / "triggers.jsonl"
CHECKS = ROOT / "hooks" / "checks.jsonl"
_BACKEND = None


def set_backend(fn):
    """Install a model backend: fn(data) -> [{class, name, message}, ...]."""
    global _BACKEND
    _BACKEND = fn


def load_triggers(path=TRIGGERS):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def haystack(data):
    return _haystack(data)


def _haystack(data):
    event = data.get("hook_event_name", "")
    if event == "UserPromptSubmit":
        return data.get("prompt", "")
    ti = data.get("tool_input", {}) or {}
    parts = [data.get("tool_name", "")]
    for k in ("command", "file_path", "files", "path"):
        v = ti.get(k)
        if isinstance(v, list):
            parts.extend(str(x) for x in v)
        elif v:
            parts.append(str(v))
    return " ".join(parts)


def _regex_rubric(data):
    event = data.get("hook_event_name", "UserPromptSubmit")
    hay = _haystack(data)
    if not hay:
        return []
    seen, out = set(), []
    for t in load_triggers():
        on = t.get("on")
        if on and on != event:
            continue
        pat = t.get("when")
        if not pat:
            continue
        try:
            if not re.search(facts.expand(pat), hay, re.IGNORECASE):
                continue
        except re.error:
            continue
        inj = facts.expand((t.get("inject") or "").strip())
        if inj and inj not in seen:
            seen.add(inj)
            # a trigger is a silent coach by default; one can opt up to "escalate"
            # (level field) to surface as a user-facing popup, not just an agent steer.
            # `sticky` exempts it from the plan-aware "settled decision" downgrade — for
            # escalations that aren't false alarms even when the touched decision is closed
            # (e.g. a concept-gap quiz: the user asking what a term means is always worth it).
            out.append({"class": t.get("level", "coach"), "name": t.get("name", ""),
                        "message": inj, "sticky": bool(t.get("sticky"))})
    return out


def _load_jsonl(path):
    rows = []
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    return rows


def _catalog():
    """The vetted rule catalog the model is allowed to SELECT from (it never
    writes advice — it only picks ids; we supply the text). id -> level/intent/message."""
    cat = []
    for t in _load_jsonl(TRIGGERS):
        msg = facts.expand((t.get("inject") or "").strip())
        cat.append({"id": t.get("name", ""), "level": "coach",
                    "intent": _tag(msg), "message": msg})
    for c in _load_jsonl(CHECKS):
        msg = facts.expand(c.get("message", ""))
        cat.append({"id": c.get("name", ""), "level": c.get("level", "escalate"),
                    "intent": _tag(msg), "message": msg})
    return [c for c in cat if c["id"] and c["message"]]


def _tag(msg):
    m = re.match(r"〔[^〕]*〕(.{0,48})", msg)
    return (m.group(1) if m else msg[:48]).strip()


def _action_text(data):
    ev = data.get("hook_event_name", "")
    if ev == "UserPromptSubmit":
        return "user says: " + data.get("prompt", "")[:400]
    ti = data.get("tool_input", {}) or {}
    parts = ["tool=" + data.get("tool_name", "")]
    for k in ("command", "file_path"):
        if ti.get(k):
            parts.append(f"{k}={ti[k]}")
    for k in ("new_string", "content"):
        if ti.get(k):
            parts.append("diff=" + str(ti[k])[:500])
    return " | ".join(parts)


def _model_enabled():
    return os.environ.get("HARNESS_MODEL", "").strip().lower() in ("1", "on", "true")


def _model_select(data):
    """Semantic recall booster: ask a small fast model which vetted rules apply to
    this action (catches paraphrases the regex misses). It ONLY returns ids; the
    text comes from our catalog. Off unless HARNESS_MODEL=1; fails OPEN to []."""
    if not _model_enabled() or not os.environ.get("ANTHROPIC_API_KEY"):
        return []
    try:
        import anthropic
    except Exception:
        return []
    cat = _catalog()
    rubric = "\n".join(f'{c["id"]}: {c["intent"]}' for c in cat)
    try:
        client = anthropic.Anthropic(timeout=8)
        r = client.messages.create(
            model="claude-haiku-4-5", max_tokens=160,
            system=("Route a coding agent's pending training action to known failure-mode rules. "
                    "Return ONLY JSON {\"ids\":[...]} with rule ids whose concern GENUINELY applies. "
                    "Be precise; prefer fewer; never invent ids."),
            messages=[{"role": "user", "content": f"ACTION:\n{_action_text(data)}\n\nRULES:\n{rubric}\n\nJSON:"}])
        txt = "".join(getattr(b, "text", "") for b in r.content)
        ids = set(json.loads(re.search(r"\{.*\}", txt, re.S).group(0)).get("ids", []))
    except Exception:
        return []
    by_id = {c["id"]: c for c in cat}
    return [{"class": by_id[i]["level"], "name": i, "message": by_id[i]["message"]}
            for i in ids if i in by_id]


def classify(data):
    """[{class, name, message}, ...]. Regex rubric is the floor; a model backend
    (test mock via set_backend, or the live Haiku selector) UNION-merges extra
    semantic catches on top. Default (no backend, HARNESS_MODEL off) = pure regex."""
    base = _regex_rubric(data)
    extra = []
    if _BACKEND is not None:
        try:
            extra = _BACKEND(data) or []
        except Exception:
            extra = []
    elif _model_enabled():
        extra = _model_select(data)
    if extra:
        seen = {i.get("message") for i in base}
        for e in extra:
            if e.get("message") and e["message"] not in seen:
                seen.add(e["message"])
                base.append(e)
    return base
