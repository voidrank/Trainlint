#!/usr/bin/env python3
"""OFFLINE verifier: what scheduler/LR ACTUALLY takes effect?

The sbatch may say `--lr_scheduler_type cosine` while the DeepSpeed config's
`scheduler` block silently overrides it (e.g. WarmupDecayLR = linear). The hook
can only remind; this reads both sources and reports the real effective value.

Usage:  python3 effective_lr.py <sbatch_or_args_file> <deepspeed_config.json>
"""
import json
import re
import sys


def main():
    if len(sys.argv) != 3:
        print("usage: effective_lr.py <sbatch/args file> <ds_config.json>")
        sys.exit(2)
    args_txt = open(sys.argv[1], encoding="utf-8").read()
    declared = None
    m = re.search(r"--lr_scheduler_type[ =]+(\S+)", args_txt)
    if m:
        declared = m.group(1)
    m = re.search(r"--learning_rate[ =]+(\S+)", args_txt)
    declared_lr = m.group(1) if m else None

    ds = {}
    try:
        ds = json.load(open(sys.argv[2], encoding="utf-8"))
    except Exception as e:
        print("could not read ds_config:", e)
    ds_sched = (ds.get("scheduler") or {}).get("type")
    ds_lr = (((ds.get("optimizer") or {}).get("params") or {}).get("lr"))

    print(f"sbatch declares : scheduler={declared}  lr={declared_lr}")
    print(f"ds_config has   : scheduler={ds_sched}  lr={ds_lr}")
    if ds_sched and declared and ds_sched.lower() not in declared.lower():
        print(f"⚠️  OVERRIDE: DeepSpeed scheduler '{ds_sched}' WINS over sbatch '{declared}'. "
              f"Effective = {ds_sched} (NOT {declared}).")
        sys.exit(1)
    if ds_lr and declared_lr and str(ds_lr) != str(declared_lr):
        print(f"⚠️  LR mismatch: ds_config lr={ds_lr} vs sbatch lr={declared_lr}.")
        sys.exit(1)
    print("OK: no silent scheduler/LR override detected.")
    sys.exit(0)


if __name__ == "__main__":
    main()
