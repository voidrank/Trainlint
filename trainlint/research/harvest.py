#!/usr/bin/env python3
"""Harvester — pull the non-derivable JUDGMENTS out of an EPHEMERAL session transcript
into the DURABLE append-only research log, BEFORE the session is compacted/deleted.

Why: the search SHAPE is re-derivable from durable repo traces (run names, configs,
metrics), but the JUDGMENTS (why-abandoned / hypothesis / verdict / wall) live only in
the session — which Claude Code compacts and rotates away. So harvest them into git.

Wire to Claude Code `PreCompact` and `SessionEnd` hooks (the moment before loss), and/or
run periodically:   python3 harvest.py <transcript.jsonl> [project]

Append-only + dedup by content hash; NEVER edits → never rots. This crude keyword
extractor is the floor; the production version is an LLM pass (same model-backend
pattern as the classifier) that also assigns `direction`. Fails soft (writes nothing on error).
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PATTERNS = [
    ("abandon", r"放弃|不走这条|换方向|砍掉|drop this|abandon"),
    ("verdict", r"听着|音质|更糊|更好|机械音|定为基线|过拟合|没用了|work了|拿下"),
    ("wall", r"卡在|为什么.*(不|没)|garbage|illegal|OOD|泄漏|shortcut|捷径|乱说|抢话"),
    ("hypothesis", r"假设|试试|应该是|怀疑.*是|可能是因为"),
]


def _texts(path):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except Exception:
        return
    for line in lines:
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get("type") not in ("user", "assistant"):
            continue
        c = o.get("message", {}).get("content")
        ts = (o.get("timestamp", "") or "")[:10]
        if isinstance(c, str):
            yield ts, c
        elif isinstance(c, list):
            for p in c:
                if isinstance(p, dict) and p.get("type") == "text":
                    yield ts, p.get("text", "")


def harvest(path, name="mimo"):
    logp = ROOT / f"log.{name}.jsonl"
    seen = set()
    if logp.exists():
        for line in logp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                try:
                    seen.add(hashlib.md5(json.loads(line).get("note", "").encode()).hexdigest())
                except Exception:
                    pass
    new = []
    for ts, t in _texts(path):
        t = t.strip()
        if not t or len(t) > 400:
            continue
        for kind, pat in PATTERNS:
            if re.search(pat, t):
                h = hashlib.md5(t[:200].encode()).hexdigest()
                if h in seen:
                    break
                seen.add(h)
                new.append({"ts": ts, "kind": kind, "direction": "?", "note": t[:200]})
                break
    if new:
        with logp.open("a", encoding="utf-8") as f:
            for e in new:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(new)


def _main():
    """CLI:  harvest.py <transcript.jsonl> [project]
    Hook (PreCompact/SessionEnd): reads {transcript_path} from stdin JSON.
    Always exits 0, writes nothing on error — must never break the session."""
    import os
    try:
        if len(sys.argv) >= 2 and not sys.argv[1].startswith("-"):
            path = sys.argv[1]
            name = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("HARNESS_PROJECT", "mimo")
        else:
            path = (json.load(sys.stdin) or {}).get("transcript_path", "")
            name = os.environ.get("HARNESS_PROJECT", "") or "mimo"
        if path:
            print(f"harvested {harvest(path, name)} new annotation(s)")
    except Exception:
        pass


if __name__ == "__main__":
    _main()
    sys.exit(0)
