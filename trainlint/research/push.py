#!/usr/bin/env python3
"""Upload generated reports to the serverless trainlint report server.

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
    token = os.environ.get("TRAINLINT_REPORT_TOKEN", "").strip()
    if token.lower() == "none":
        # Explicit opt-out
        return

    if not token:
        is_explicit_token = False
        # Zero-setup automatic token resolution
        token = get_or_create_token()
        if not token:
            return

    server = os.environ.get("TRAINLINT_REPORT_SERVER", "https://secondfoundationlabs.com").strip().rstrip("/")
    
    # 1. Upload main report (kind=html)
    try:
        _upload(server, token, project, "html", html_path)
    except Exception as e:
        # Fail-silent but log to stderr
        print(f"[trainlint-push] Upload warning: failed to push report to {server}: {e}", file=sys.stderr)
        return

    # 2. Upload slides (kind=slides)
    try:
        _upload(server, token, project, "slides", slides_path)
    except Exception as e:
        # Fail-silent
        print(f"[trainlint-push] Upload warning: failed to push slides to {server}: {e}", file=sys.stderr)
        return

    # Print the direct magic links and pairing instructions
    if is_explicit_token:
        # If user explicitly set their token, they are already authenticated, print direct dashboard links
        print(f"\n[trainlint-push] ☁️  Cloud report: {server}/{project}.html")
        print(f"[trainlint-push] ☁️  Cloud slides: {server}/{project}.slides.html")
    else:
        # If using local automatic anonymous token, print the PAIRING link
        print(f"\n[trainlint-push] ☁️  Anonymous report uploaded!")
        print(f"[trainlint-push] 🔗 One-click link to Google Account: {server}/link?token={token}&project={urllib.parse.quote(project)}")


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
