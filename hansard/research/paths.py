#!/usr/bin/env python3
"""Where a PROJECT's data lives — decoupled from the plugin's versioned code dir.

Per-project files (goal / plan / log / focus / pipeline / glossary / facts / knowledge /
motivation / clarify / tag_* / project.<name>.json / sessions / .state) must NOT sit
under the versioned plugin cache (`.../hansard/<version>/`), which an upgrade wipes. They live
in a STABLE data dir:

    $HANSARD_DATA_DIR (or legacy $TRAINLINT_DATA_DIR)  if set (point it at your own repo to keep
                                                       the project's data WITH the project)
    else ~/.claude/plugins/data/hansard-hansard        (Claude Code's persistent plugin-data dir;
                                                       falls back to the legacy trainlint-trainlint
                                                       dir until the one-time migration mv runs)

The plugin's CODE and SHARED files (plan.py, viz.py, quiz.jsonl, principles.jsonl, …) stay in the
plugin — only per-project DATA moves out.

MIGRATION-SAFE: `resolve()` returns the data-dir path if the file is there, else falls back to the
LEGACY in-plugin location, so a half-migrated tree (or an un-updated module) still reads correctly.
WRITES always target the data dir via `wfile()`.
"""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

_RESEARCH = Path(__file__).resolve().parent          # .../hansard/<version>/research
_PLUGIN = _RESEARCH.parent                            # .../hansard/<version>


def data_root() -> Path:
    d = os.environ.get("HANSARD_DATA_DIR") or os.environ.get("TRAINLINT_DATA_DIR", "").strip()
    if d:
        base = Path(d).expanduser()
    else:
        plugins_data = Path.home() / ".claude" / "plugins" / "data"
        base = plugins_data / "hansard-hansard"
        legacy = plugins_data / "trainlint-trainlint"
        # pre-migration fallback: until the one-time `mv trainlint-trainlint hansard-hansard`
        # runs, keep using the legacy dir so live daemons and this code agree on ONE substrate
        # (identity = sha256 of .token CONTENT, so the eventual move is invisible to the server).
        if not base.exists() and legacy.exists():
            base = legacy
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base


def resolve(fname: str) -> Path:
    """READ path for a per-project data file: data_root()/fname if it exists, else the legacy
    in-plugin path (research/ then plugin root), else data_root()/fname (a not-yet-created file)."""
    new = data_root() / fname
    if new.exists():
        return new
    for legacy in (_RESEARCH / fname, _PLUGIN / fname):
        if legacy.exists():
            return legacy
    return new


def wfile(fname: str) -> Path:
    """WRITE path — always the data dir. Ensures the parent exists."""
    p = data_root() / fname
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def state_dir() -> Path:
    """Per-project runtime state (progress etc.) — under the data dir, not the plugin."""
    s = data_root() / ".state"
    try:
        s.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return s


# --- per-SESSION project lock -----------------------------------------------------------
# session-project-lock: there is no machine-wide "current project" pointer. Each session binds its
# OWN project via a lock keyed by session_id, so concurrent sessions never clobber. active_project()
# below reads these (session lock, then cwd->home inference); the old global .active-project is gone.
# Kept in data_root() so it survives plugin version bumps (the 0.3.x cache scar). Hooks already
# receive session_id (and the CLI exposes $CLAUDE_CODE_SESSION_ID), so no new plumbing.

def sessions_dir() -> Path:
    """Where per-session locks live — data_root()/sessions/, created on demand."""
    d = data_root() / "sessions"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d


def _session_lock_path(session_id: str) -> Path:
    """One JSON file per session. session_id is sanitized to a safe filename (it comes from the
    harness, but never trust it into a path); empty -> 'default'."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "")).strip("_") or "default"
    return sessions_dir() / f"{safe}.json"


def read_session_lock(session_id: str):
    """This session's lock as a dict {project, home, bound_by, ts}, or None if unbound / unreadable.
    Fail-open: any error -> None, never raises (a hook must never break on a corrupt lock)."""
    if not session_id:
        return None
    p = _session_lock_path(session_id)
    try:
        if p.exists():
            rec = json.loads(p.read_text(encoding="utf-8"))
            return rec if isinstance(rec, dict) and rec.get("project") else None
    except Exception:
        return None
    return None


def write_session_lock(session_id: str, project: str, home: str = "", bound_by: str = "plan"):
    """Bind this session to `project`. `home` is the project's dir (the context->project link);
    `bound_by` records HOW it was bound ('plan' | 'infer' | 'use'). Returns the lock path, or None
    if it couldn't write. Overwrites any existing lock for the session (sticky-but-explicit switch:
    callers decide WHEN to rebind; the store just persists it)."""
    if not session_id or not project:
        return None
    rec = {"project": str(project), "home": str(home or ""), "bound_by": str(bound_by),
           "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    p = _session_lock_path(session_id)
    try:
        p.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")
        return p
    except Exception:
        return None


# --- a project's HOME (the context->project link) ---------------------------------------
# project-home-field: the resolver can only map cwd/a touched path back to a project if each project
# records the directory it belongs to. `home` is stamped at registration (new_project) and re-stampable
# at plan/use time. Stored IN project.<name>.json so it travels with the rest of a project's facts.

def _read_json(fname):
    p = resolve(fname)
    try:
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def project_home(name: str) -> str:
    """The directory a project belongs to (the context->project link), or '' if unstamped."""
    return str(_read_json(f"project.{name}.json").get("home") or "")


def set_project_home(name: str, home: str):
    """Stamp/replace project.<name>.json's `home`, PRESERVING every other key (the doorman's danger
    patterns /hansard:plan fills). Returns the written path, or None. Fail-open."""
    if not name or not home:
        return None
    fname = f"project.{name}.json"
    d = _read_json(fname)
    d["home"] = str(home)
    p = wfile(fname)
    try:
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        return p
    except Exception:
        return None


def _all_project_homes():
    """{project_name: home} for every registered project that has a `home` stamped (across data_root
    and the legacy plugin dirs). The table the cwd-inference resolves against."""
    out = {}
    for base in (data_root(), _RESEARCH, _PLUGIN):
        try:
            for p in base.glob("project.*.json"):
                name = p.name[len("project."):-len(".json")]
                if name in out or name == "example":
                    continue
                home = _read_json(p.name).get("home")
                if home:
                    out[name] = str(home)
        except Exception:
            continue
    return out


def _infer_project_from_cwd(cwd):
    """The project whose `home` is the LONGEST prefix of cwd (a session working inside project X's
    tree resolves to X), or '' if none matches. Fail-open."""
    if not cwd:
        return ""
    try:
        cwd = str(Path(cwd).resolve())
    except Exception:
        cwd = str(cwd)
    best, best_len = "", -1
    for name, home in _all_project_homes().items():
        try:
            h = str(Path(home).resolve())
        except Exception:
            h = str(home)
        if (cwd == h or cwd.startswith(h.rstrip("/") + "/")) and len(h) > best_len:
            best, best_len = name, len(h)
    return best


def active_project() -> str:
    """The active project for THIS session, resolved from context (session-project-lock).
    Order: (1) $HARNESS_PROJECT override; (2) this session's lock, keyed by $CLAUDE_CODE_SESSION_ID
    -- so concurrent sessions never clobber; (3) infer from cwd = the project whose home contains it
    (longest prefix), persisting the lock so it's stable for the rest of the session; (4) '' -> silent.
    There is NO global .active-project fallback: an unbound session in no project's tree resolves to ''
    and the compass/doorman stay silent rather than narrate a stale machine-wide pointer (remove-global)."""
    n = os.environ.get("HARNESS_PROJECT", "").strip()
    if n:
        return n
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    if sid:
        rec = read_session_lock(sid)
        if rec and rec.get("project"):
            return rec["project"]
    inferred = _infer_project_from_cwd(os.getcwd())
    if inferred:
        if sid:
            write_session_lock(sid, inferred, home=project_home(inferred), bound_by="infer")
        return inferred
    return ""
