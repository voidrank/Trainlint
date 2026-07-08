#!/usr/bin/env python3
"""Silently keep a LOOPBACK http server up for the rendered reports.

The reports are JS-heavy (the per-block/per-decision chatbots + quizzes call the Anthropic API from
the browser). A JS-stripped inline preview shows none of that, and a file:// path is awkward to open;
a local server lets you browse every project's interactive report in a real browser.

Design: idempotent + SILENT. `ensure(viz_dir)` starts `python -m http.server` detached ONLY if the
port isn't already answering, and returns the base URL — safe to call on every render. It binds
127.0.0.1 ONLY (never 0.0.0.0), fixing http.server's unsafe default; reach it via an SSH port-forward.
Any error -> "" (fail-silent), never raises, never blocks a render.
"""
import os
import socket
import subprocess
import sys
from pathlib import Path

HOST = "127.0.0.1"


def _port():
    try:
        return int(os.environ.get("HANSARD_SERVE_PORT") or os.environ.get("TRAINLINT_SERVE_PORT", "8420"))
    except Exception:
        return 8420


def _listening(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex((HOST, port)) == 0
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _disabled():
    return os.environ.get("HANSARD_SERVE") or os.environ.get("TRAINLINT_SERVE", "1").strip().lower() in ("0", "off", "false", "no")


def _relay_disabled():
    return os.environ.get("HANSARD_RELAY") or os.environ.get("TRAINLINT_RELAY", "1").strip().lower() in ("0", "off", "false", "no")


def _relay_running():
    try:
        return subprocess.run(["pgrep", "-f", "relay_agent.py run"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    except Exception:
        return False


def ensure_relay():
    """Ensure the outbound relay agent (relay_agent.py) runs — it dials the report worker so
    authenticated viewers reach this box's live backend. Silent + idempotent; TRAINLINT_RELAY=0
    disables. Never raises."""
    if _relay_disabled():
        return
    try:
        if not _relay_running():
            _ra = str(Path(__file__).resolve().parent / "relay_agent.py")
            subprocess.Popen(
                [sys.executable, _ra, "run"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)  # detached — survives this process
    except Exception:
        pass


def ensure(viz_dir):
    """Ensure a loopback server serves `viz_dir`; return the base URL (or '' on failure). Silent +
    idempotent — a no-op if something already answers on the port. This is the ONLY spawn point;
    call it once per render (from generate).

    Spawns chat_backend.py (the LIVE backend: /chat, /edit, /digest + static, loopback-only like
    us) — NOT plain http.server: a dumb static server that wins this race squats the port 501'ing
    every POST, and the cron keepalive never replaces it because the port "answers". http.server
    remains only as the fallback for a stripped install without chat_backend.py."""
    if _disabled():
        return ""
    port = _port()
    try:
        if not _listening(port):
            _cb = Path(__file__).resolve().parent / "chat_backend.py"
            use_backend = _cb.exists()
            cmd = ([sys.executable, str(_cb), str(viz_dir), str(port)] if use_backend else
                   [sys.executable, "-m", "http.server", str(port), "--bind", HOST,
                    "--directory", str(viz_dir)])
            # Log the backend's stderr (append) rather than DEVNULL it: the loser of a :8420 bind race
            # OR a genuine startup bug otherwise vanishes into the void. We deliberately do NOT fall
            # back to http.server when chat_backend crashes — a static server would 501 every POST and
            # squat the port so the cron keepalive never replaces it (the bug ensure() exists to avoid).
            # A crash here is loud in the log + retried by keepalive.sh every minute; that's the safety net.
            try:
                _err = open("/tmp/tl_backend_serve.log", "ab") if use_backend else subprocess.DEVNULL
            except Exception:
                _err = subprocess.DEVNULL
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=_err,
                start_new_session=True)  # detached — survives this process
        ensure_relay()
        return f"http://{HOST}:{port}"
    except Exception:
        return ""


def url():
    """The base URL if the server is actually up — checks, never spawns (so callers past ensure()
    can surface the address without racing a second server into existence). '' if down/disabled."""
    if _disabled():
        return ""
    port = _port()
    return f"http://{HOST}:{port}" if _listening(port) else ""
