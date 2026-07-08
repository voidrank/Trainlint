#!/usr/bin/env python3
"""Bind THIS session to a project — the explicit-switch half of session-project-lock.

  python3 use.py <name> [--home DIR]

Sticky + explicit: a session stays on whatever it's bound to until you run this again (or
/hansard:plan <other>). No cwd auto-switch [switch-semantics]. It does two things:
  1. stamps the project's `home` (= --home, else its existing home, else cwd) — the context->project
     link [project-home-field];
  2. writes the per-session lock data_root()/sessions/<session_id>.json [session-lock-store], keyed by
     the session id from $CLAUDE_CODE_SESSION_ID (the CLI exposes it, so a plain script can bind).
There is NO global .active-project write — the resolver reads only the session lock / cwd-home
inference, so a bind here never affects any other session [remove-global].
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402


def bind(name, home=None, session_id=None):
    """Bind `name` to this session. Returns (ok, message). Fail-soft: reports what it couldn't do
    rather than raising."""
    if not name:
        return False, "usage: use.py <name> [--home DIR]"
    # the project must exist (registered) — /use switches to an EXISTING project, it doesn't create one
    if not paths.resolve(f"project.{name}.json").exists():
        return False, (f"no project '{name}' — register it with /hansard:plan first "
                       f"(nothing to switch to)")
    home = (home or paths.project_home(name) or os.getcwd())
    paths.set_project_home(name, home)
    sid = session_id or os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    lock = paths.write_session_lock(sid, name, home=home, bound_by="use") if sid else None
    if not lock:
        # no session id in env -> can't bind THIS session. home is stamped, so cwd-inference will
        # still resolve it when you're in its tree; there is no global pointer to fall back on.
        return True, (f"stamped home for '{name}': {home}\n  (no CLAUDE_CODE_SESSION_ID — couldn't bind "
                      f"this session; run inside {home} to resolve it by cwd, or set the env id)")
    return True, f"bound '{name}' -> this session ({sid[:8]})\n  home: {home}"


def main():
    args = sys.argv[1:]
    home = None
    if "--home" in args:
        i = args.index("--home")
        home = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    name = args[0] if args else None
    ok, msg = bind(name, home=home)
    print(msg)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
