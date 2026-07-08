#!/usr/bin/env python3
"""Upload generated reports to the serverless hansard report server.

Fail-silent and zero-setup:
- If TRAINLINT_REPORT_TOKEN is 'none', upload is disabled.
- If TRAINLINT_REPORT_TOKEN is set, it uses it.
- If not set, it automatically generates a secure, private local token in paths.data_root()
  and uses it to upload, giving you zero-configuration cloud hosting instantly.
"""
import os
import sys
import secrets
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path


def get_or_create_token() -> str:
    """Read or generate a secure, persistent 32-character local token."""
    try:
        # Resolve path to stable data directory
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import paths
        token_file = paths.data_root() / ".token"
        if token_file.exists():
            return token_file.read_text(encoding="utf-8").strip()
        
        # Generate new random 128-bit hex token
        tok = secrets.token_hex(16)
        token_file.write_text(tok, encoding="utf-8")
        return tok
    except Exception:
        return ""


def push_report(project: str, html_path: Path, slides_path: Path):
    is_explicit_token = True
    token = os.environ.get("HANSARD_REPORT_TOKEN") or os.environ.get("TRAINLINT_REPORT_TOKEN", "").strip()
    if token.lower() == "none":
        # Explicit opt-out
        return

    if not token:
        is_explicit_token = False
        # Zero-setup automatic token resolution
        token = get_or_create_token()
        if not token:
            return

    server = os.environ.get("HANSARD_REPORT_SERVER") or os.environ.get("TRAINLINT_REPORT_SERVER", "https://secondfoundationlabs.com").strip().rstrip("/")
    
    # 1. Upload main report (kind=html)
    try:
        _upload(server, token, project, "html", html_path)
    except Exception as e:
        # Fail-silent but log to stderr
        print(f"[hansard-push] Upload warning: failed to push report to {server}: {e}", file=sys.stderr)
        return

    # 2. Upload slides (kind=slides)
    try:
        _upload(server, token, project, "slides", slides_path)
    except Exception as e:
        # Fail-silent
        print(f"[hansard-push] Upload warning: failed to push slides to {server}: {e}", file=sys.stderr)
        return

    # Print the direct magic links and pairing instructions
    if is_explicit_token:
        # If user explicitly set their token, they are already authenticated, print direct dashboard links
        print(f"\n[hansard-push] ☁️  Cloud report: {server}/{project}.html")
        print(f"[hansard-push] ☁️  Cloud slides: {server}/{project}.slides.html")
    else:
        # If using local automatic anonymous token, print the PAIRING link
        print(f"\n[hansard-push] ☁️  Anonymous report uploaded!")
        print(f"[hansard-push] 🔗 One-click link to Google Account: {server}/link?token={token}&project={urllib.parse.quote(project)}")


def _upload(server: str, token: str, project: str, kind: str, file_path: Path):
    if not file_path.exists():
        return
    
    url = f"{server}/api/upload?project={urllib.parse.quote(project)}&kind={kind}"
    data = file_path.read_text(encoding="utf-8")
    
    req = urllib.request.Request(
        url,
        data=data.encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/html; charset=utf-8",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        },
        method="POST"
    )
    
    # Add a short timeout (10s) so it doesn't hang the linter / close flow
    with urllib.request.urlopen(req, timeout=10) as response:
        if response.status != 200:
            body = response.read().decode("utf-8")
            raise Exception(f"HTTP {response.status}: {body}")


def _resolve_token_server():
    """(token, server) for the feedback API, or (None, None) when uploads are opted out."""
    token = os.environ.get("HANSARD_REPORT_TOKEN") or os.environ.get("TRAINLINT_REPORT_TOKEN", "").strip()
    if token.lower() == "none":
        return None, None
    if not token:
        token = get_or_create_token()
    if not token:
        return None, None
    server = os.environ.get("HANSARD_REPORT_SERVER") or os.environ.get("TRAINLINT_REPORT_SERVER", "https://secondfoundationlabs.com").strip().rstrip("/")
    return token, server


def pull_feedback():
    """[(key, blob_dict)] — the operator feedback the report pages filed on the server for this
    machine's token (both its paired and anonymous namespaces). Fail-silent: [] on any error."""
    import json
    token, server = _resolve_token_server()
    if not token:
        return []
    req = urllib.request.Request(f"{server}/api/feedback",
                                 headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            items = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    out = []
    for it in items or []:
        try:
            out.append((it["key"], json.loads(it["blob"])))
        except Exception:
            continue  # one malformed blob must not block the rest
    return out


def delete_feedback(key: str) -> bool:
    """Delete one consumed feedback object on the server. Call ONLY after absorbing it."""
    import json  # noqa: F401  (parity with pull; key is already a plain string)
    token, server = _resolve_token_server()
    if not token or not key:
        return False
    req = urllib.request.Request(
        f"{server}/api/feedback?key={urllib.parse.quote(key, safe='')}",
        headers={"Authorization": f"Bearer {token}"}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False
