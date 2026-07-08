#!/usr/bin/env python3
"""Real verifier for the mel-power trap — parses the ACTUAL MelSpectrogram call
args instead of a whole-file substring. A whole-file `unless power=1.0` regex is
fooled by a `power=1.0` anywhere else in the file; this checks the specific call.

Returns (fire: bool, message: str|None). message=None -> use the check's default.
Importable as verifiers.check_mel_power.mel_power; also runnable on a file.
"""
import re
import sys

_CALLS = ("MelSpectrogram(", "mel_spectrogram(", "MelScale(")


def _call_args(text, open_paren):
    depth = 0
    for j in range(open_paren, min(len(text), open_paren + 4000)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:j]
    return text[open_paren + 1:open_paren + 300]


def mel_power(text):
    if not text:
        return (False, None)
    for call in _CALLS:
        i = 0
        while True:
            k = text.find(call, i)
            if k < 0:
                break
            args = _call_args(text, k + len(call) - 1)
            m = re.search(r"power\s*=\s*([0-9][0-9.]*)", args)
            if not m:
                return (True, None)  # this mel call has no power kwarg -> default 2.0
            try:
                ok = float(m.group(1)) == 1.0
            except ValueError:
                ok = False
            if not ok:
                return (True, "mel 调用显式 power=%s ≠ 1.0 → OOD 谱(1.79× 更差)。确认是否要 power=1.0。" % m.group(1))
            i = k + len(call)  # this call is fine (power=1.0); keep scanning
    return (False, None)


if __name__ == "__main__":
    txt = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    fire, msg = mel_power(txt)
    print("FIRE" if fire else "ok", "|", msg or "(default message)")
    sys.exit(1 if fire else 0)
