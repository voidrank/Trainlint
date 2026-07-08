#!/usr/bin/env python3
"""OFFLINE verifier the hook can't do: does the val manifest leak training data?

The hook can only sniff patterns; this does the REAL check — load both manifests
and compute (session, window) overlap. Dedup on session_id alone misses the same
session split across train/val by time window (eval looks better than it is).

Usage:  python3 check_manifest_leak.py train.jsonl val.jsonl
Exit 1 (+ prints offending keys) if any overlap; exit 0 if clean.
"""
import json
import sys


def keys(path):
    out = set()
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        sess = r.get("session") or r.get("session_id") or r.get("id")
        win = r.get("window_t_start", r.get("window_start", r.get("t_start", "")))
        out.add((sess, win))
    return out


def main():
    if len(sys.argv) != 3:
        print("usage: check_manifest_leak.py train.jsonl val.jsonl")
        sys.exit(2)
    tr, va = keys(sys.argv[1]), keys(sys.argv[2])
    overlap = tr & va
    sess_overlap = {s for s, _ in va} & {s for s, _ in tr}
    print(f"train keys={len(tr)} val keys={len(va)}")
    print(f"(session,window) overlap = {len(overlap)}  | session-only overlap = {len(sess_overlap)}")
    if overlap:
        for k in list(overlap)[:10]:
            print("  LEAK:", k)
        sys.exit(1)
    if sess_overlap:
        print("  WARN: sessions shared across train/val (windows differ) — confirm this is intended.")
    print("OK: no (session,window) leak.")
    sys.exit(0)


if __name__ == "__main__":
    main()
