#!/usr/bin/env python3
"""session-project-lock ▸ resolution-order — the rewired active_project() (research/paths.py).

The read side: active_project() resolves $HARNESS_PROJECT -> this session's lock -> cwd/home
inference (persisting the lock) -> transitional global -> ''. Throwaway TRAINLINT_DATA_DIR + a
throwaway cwd, so it never touches real data or depends on the real session.
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp())
os.environ["TRAINLINT_DATA_DIR"] = str(_TMP)
# start from a clean slate for the env knobs the resolver reads
for k in ("HARNESS_PROJECT", "CLAUDE_CODE_SESSION_ID"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "research"))
import paths  # noqa: E402

fails = 0


def check(cond, msg):
    global fails
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        fails += 1


# two registered projects with homes (nested, to test longest-prefix)
homeA = _TMP / "repos" / "projA"; homeA.mkdir(parents=True)
homeB = _TMP / "repos" / "projA" / "sub" / "projB"; homeB.mkdir(parents=True)
paths.wfile("project.projA.json").write_text('{"_c":"a"}', encoding="utf-8")
paths.wfile("project.projB.json").write_text('{"_c":"b"}', encoding="utf-8")
paths.set_project_home("projA", str(homeA))
paths.set_project_home("projB", str(homeB))

# 1. $HARNESS_PROJECT wins over everything
os.environ["HARNESS_PROJECT"] = "override-x"
check(paths.active_project() == "override-x", "HARNESS_PROJECT override is highest priority")
del os.environ["HARNESS_PROJECT"]

# 2. this session's lock resolves (keyed by CLAUDE_CODE_SESSION_ID)
os.environ["CLAUDE_CODE_SESSION_ID"] = "sessA"
paths.write_session_lock("sessA", "projA", home=str(homeA), bound_by="use")
check(paths.active_project() == "projA", "session lock resolves for this session")

# 3. session ISOLATION — a different session id sees no lock (falls through, not projA)
os.environ["CLAUDE_CODE_SESSION_ID"] = "sessOTHER"
check(paths.active_project() != "projA" or paths.read_session_lock("sessOTHER") is None,
      "a different session does NOT inherit sessA's lock")

# 4. cwd inference: no lock, cwd inside projB's home -> resolves projB AND persists the lock
os.environ["CLAUDE_CODE_SESSION_ID"] = "sessInfer"
_prev = os.getcwd()
try:
    os.chdir(homeB)
    got = paths.active_project()
    check(got == "projB", f"cwd inside projB home -> resolves projB (got {got})")
    check(paths.read_session_lock("sessInfer") and paths.read_session_lock("sessInfer")["project"] == "projB",
          "inference PERSISTS the session lock (bound_by=infer)")
    check(paths.read_session_lock("sessInfer")["bound_by"] == "infer", "persisted lock is marked bound_by=infer")
    # 5. longest-prefix: projB's home is deeper than projA's, so a cwd in projB resolves projB not projA
    check(paths._infer_project_from_cwd(str(homeB)) == "projB", "longest-prefix picks the DEEPER home (projB)")
    check(paths._infer_project_from_cwd(str(homeA)) == "projA", "a cwd in projA (not projB) resolves projA")
finally:
    os.chdir(_prev)

# 6. remove-global: a stale global .active-project is IGNORED (no fallback read)
os.environ["CLAUDE_CODE_SESSION_ID"] = "sessNoMatch"
paths.wfile(".active-project").write_text("stale-global\n", encoding="utf-8")
_prev = os.getcwd()
try:
    os.chdir(_TMP)  # not inside any project home, no lock
    check(paths.active_project() == "", "a stale global .active-project is IGNORED -> '' (remove-global)")
finally:
    os.chdir(_prev)

# 7. nothing at all -> '' (silent), not a stale guess
(_TMP / ".active-project").unlink()
os.environ["CLAUDE_CODE_SESSION_ID"] = "sessEmpty"
_prev = os.getcwd()
try:
    os.chdir(_TMP)
    check(paths.active_project() == "", "unbound + no cwd match -> '' (silent)")
finally:
    os.chdir(_prev)

print(f"\n{9 - fails}/9 passed")
sys.exit(1 if fails else 0)
