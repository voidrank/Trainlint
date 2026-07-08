#!/usr/bin/env python3
"""relay_agent.py — the LOCAL half of the live report relay.

Dials OUT (so no inbound port/tunnel needed) to the report worker's /agent WebSocket, authenticated
with the SAME upload token push.py uses. The worker holds the socket in a per-namespace Durable
Object and forwards viewer requests as JSON frames:

    server->agent : {"id","method","path","body":<b64|null>,"ct":<str|null>}
    agent->server : {"id","status","ct","body":<b64>}
    keepalive     : {"type":"ping"} / {"type":"pong"} every 30s

Each relayed request is replayed against the local chat_backend (127.0.0.1:8420) WITH the header
x-hansard-relay: <secret> (data_root/relay_internal_secret.txt, auto-generated 0600) — the backend
treats that as an authenticated admin because the WORKER already authenticated the viewer.

Run:   python3 relay_agent.py run            (serve.ensure launches it detached)
Share: python3 relay_agent.py share <email>  (grant that email viewer access to MY namespace)
"""
import asyncio
import base64
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402

try:
    import push  # reuse get_or_create_token (same token verifyToken() validates)
    _get_or_create_token = push.get_or_create_token
except Exception:  # older deployments ship no push.py — same logic inline (data_root/.token)
    def _get_or_create_token():
        try:
            token_file = paths.data_root() / ".token"
            if token_file.exists():
                return token_file.read_text(encoding="utf-8").strip()
            tok = secrets.token_hex(16)
            token_file.write_text(tok, encoding="utf-8")
            return tok
        except Exception:
            return ""

DEFAULT_URL = "wss://secondfoundationlabs.com/agent"
LOCAL_HOST = "127.0.0.1"


def _relay_url():
    return os.environ.get("HANSARD_RELAY_URL") or os.environ.get("TRAINLINT_RELAY_URL", DEFAULT_URL).strip() or DEFAULT_URL


def _local_port():
    try:
        return int(os.environ.get("HANSARD_SERVE_PORT") or os.environ.get("TRAINLINT_SERVE_PORT", "8420"))
    except Exception:
        return 8420


def _token():
    """Same resolution push.push_report uses: env override ('none' = opt-out), else the
    auto-generated local token file."""
    tok = os.environ.get("HANSARD_REPORT_TOKEN") or os.environ.get("TRAINLINT_REPORT_TOKEN", "").strip()
    if tok.lower() == "none":
        return ""
    return tok or _get_or_create_token()


def get_or_create_secret():
    """The relay-internal secret chat_backend trusts — data_root/relay_internal_secret.txt,
    auto-generated once, 0600 (it grants admin locally)."""
    p = paths.data_root() / "relay_internal_secret.txt"
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    sec = secrets.token_hex(16)
    p.write_text(sec, encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except Exception:
        pass
    return sec


def _local_request(method, path, body_b64, ct):
    """Replay one relayed request against the local backend; return (status, ct, body_bytes).
    Never raises — errors become a 502 body the viewer can see."""
    if not path.startswith("/"):
        path = "/" + path
    url = f"http://{LOCAL_HOST}:{_local_port()}{path}"
    data = base64.b64decode(body_b64) if body_b64 else None
    headers = {"x-hansard-relay": get_or_create_secret()}
    if ct:
        headers["Content-Type"] = ct
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        # Worker gives a relayed request 120s max; stay under it so IT never times out first.
        with urllib.request.urlopen(req, timeout=110) as r:
            return r.status, r.headers.get("content-type") or "application/octet-stream", r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type") or "text/plain", e.read()
    except Exception as e:
        return 502, "text/plain; charset=utf-8", f"relay agent: local backend error: {e}".encode()


def _list_projects():
    """Project names for the hello frame: basenames (sans .html) of *.html files in the viz dir
    (data_root/viz), deduped, with the .slides.html variants excluded. Best-effort — any error
    yields [] rather than crashing the connect."""
    names, seen = [], set()
    try:
        viz = paths.data_root() / "viz"
        for p in sorted(viz.glob("*.html")):
            n = p.name
            if n.endswith(".slides.html"):
                continue
            base = n[: -len(".html")]
            if base and base not in seen:
                seen.add(base)
                names.append(base)
    except Exception:
        pass
    return names


def _email_from_token(token):
    """This agent generally does NOT know its own email (it holds a raw hex token) -> 'unknown'.
    But a SIGNED upload token carries it: base64url-decode the payload (the part before the first
    '.'), JSON, read .email. We do NOT verify the signature — the worker does. Fail-silent."""
    try:
        if token and "." in token:
            payload = token.split(".", 1)[0]
            pad = "=" * (-len(payload) % 4)
            body = json.loads(base64.urlsafe_b64decode(payload + pad).decode("utf-8"))
            email = body.get("email")
            if email:
                return str(email)
    except Exception:
        pass
    return "unknown"


async def _send_hello(ws, token):
    """Right after connect, announce {email, projects} so the worker's DO can register this namespace
    for the admin dashboard. Best-effort — an error here must never break the serve loop."""
    try:
        await ws.send(json.dumps({"type": "hello", "email": _email_from_token(token),
                                  "projects": _list_projects()}))
    except Exception:
        pass


async def _handle(ws, msg):
    status, ct, body = await asyncio.to_thread(
        _local_request, msg.get("method") or "GET", msg.get("path") or "/",
        msg.get("body"), msg.get("ct"))
    await ws.send(json.dumps({"id": msg["id"], "status": status, "ct": ct,
                              "body": base64.b64encode(body).decode("ascii")}))


async def _serve(ws):
    """One connected session: answer pings, send our own every 30s, relay everything with an id."""
    async def keepalive():
        while True:
            await asyncio.sleep(30)
            await ws.send(json.dumps({"type": "ping"}))
    ka = asyncio.create_task(keepalive())
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))
            elif msg.get("id"):
                asyncio.create_task(_handle(ws, msg))  # concurrent — slow /chat mustn't block pings
    finally:
        ka.cancel()


async def run():
    """Daemon loop: dial out, serve, reconnect with exponential backoff (max 60s). Fail-silent."""
    import websockets
    token = _token()
    if not token:
        return
    url = _relay_url()
    full = url + ("&" if "?" in url else "?") + "token=" + urllib.parse.quote(token)  # ?token= fallback
    backoff = 1
    while True:
        try:
            async with websockets.connect(
                    full, additional_headers={"Authorization": f"Bearer {token}"},
                    ping_interval=20, ping_timeout=30, max_size=64 * 1024 * 1024) as ws:
                backoff = 1
                await _send_hello(ws, token)  # first frame: {"type":"hello","email",...,"projects":[...]}
                await _serve(ws)
        except Exception:
            pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


def share(email):
    """Grant <email> viewer access to MY namespace: POST /api/share with the upload token."""
    token = _token()
    if not token:
        print("no upload token (TRAINLINT_REPORT_TOKEN=none?)", file=sys.stderr)
        sys.exit(1)
    host = urllib.parse.urlparse(_relay_url()).netloc
    req = urllib.request.Request(
        f"https://{host}/api/share",
        data=json.dumps({"email": email}).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "Mozilla/5.0 (hansard-relay-agent)"},  # bare urllib UA gets CF bot-blocked (1010)
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            print(f"shared with {email}: HTTP {r.status} {r.read().decode('utf-8', 'replace').strip()}")
    except urllib.error.HTTPError as e:
        print(f"share failed: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:300]}", file=sys.stderr)
        sys.exit(1)


def _single_instance_or_exit():
    """Hold an exclusive flock so only ONE relay agent ever runs — otherwise multiple agents with the
    same token keep kicking each other off the worker (1012 'replaced by newer') and the relay flaps."""
    import fcntl
    lock_path = paths.data_root() / "relay_agent.lock"
    f = open(lock_path, "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another relay agent already running — exiting", file=sys.stderr)
        sys.exit(0)
    return f  # keep the handle alive for the process lifetime


if __name__ == "__main__":
    if sys.argv[1:2] == ["share"] and len(sys.argv) > 2:
        share(sys.argv[2])
    elif sys.argv[1:2] == ["run"] or not sys.argv[1:]:
        _LOCK = _single_instance_or_exit()
        asyncio.run(run())
    else:
        print("usage: relay_agent.py run | share <email>", file=sys.stderr)
        sys.exit(2)
