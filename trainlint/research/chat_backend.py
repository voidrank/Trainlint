#!/usr/bin/env python3
"""chat_backend.py — a LIVE local backend for the report's chat widget (replaces the dumb static
http.server). Per question it reads the CURRENT full substrate (goal/purpose/every decision in full/
log/surprises/focus/glossary) AND greps the project's code+data for terms in the question, builds a
rich context, and answers via the local LLM entries (viz._llm: codex/kimi/claude — no browser API key).
Any ```memory``` block the model emits is folded back into the LOCAL glossary/clarify files, so the
learning loop closes on this one box. Serves the rendered HTML statically too.

Run:  python3 chat_backend.py <viz_dir> <port>   (serve.ensure launches it detached)
"""
import json
import re
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import paths  # noqa: E402
import plan as planlib  # noqa: E402
import viz  # noqa: E402  — reuse _llm (codex/kimi/claude) + the LLM entries

STOP = set("the a an and or of to in on for is are be with that this it its as at by from we our you "
           "what how why does do can could is are was were will would should i me my your their".split())


def _read(fn):
    p = paths.resolve(fn)
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def _jl(fn):
    p = paths.resolve(fn)
    out = []
    if p.exists():
        for x in p.read_text(encoding="utf-8").splitlines():
            x = x.strip()
            if x and not x.startswith("#"):
                try:
                    out.append(json.loads(x))
                except Exception:
                    pass
    return out


def _grep_code(project, question, maxhits=12):
    """grep the project's code + data dirs for the question's salient terms; return file:line excerpts."""
    terms = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_.]{3,}", question) if w.lower() not in STOP][:6]
    if not terms:
        return ""
    try:
        proj = json.load(open(paths.resolve(f"project.{project}.json")))
    except Exception:
        proj = {}
    dirs = [d for d in (proj.get("home"), proj.get("repo_root")) if d and Path(d).is_dir()]
    if not dirs:
        return ""
    pat = "|".join(re.escape(t) for t in terms)
    hits = []
    for d in dirs:
        try:
            r = subprocess.run(["grep", "-rInE", "--include=*.py", "--include=*.jsonl",
                                "--include=*.yaml", "--include=*.md", pat, d],
                               capture_output=True, text=True, timeout=15)
            for line in r.stdout.splitlines():
                hits.append(line[:300])
                if len(hits) >= maxhits:
                    break
        except Exception:
            pass
        if len(hits) >= maxhits:
            break
    return ("\n\nRELEVANT CODE/DATA (grepped for " + ", ".join(terms) + "):\n" + "\n".join(hits)) if hits else ""


def build_context(project, question, decision_id=None, focus_text=None):
    pl = planlib.load(project)
    focus = (f"THE USER IS ASKING ABOUT THIS SPECIFIC ITEM (answer about it first, use the rest only "
             f"to cross-reference):\n{focus_text}\n\n") if focus_text else ""
    decs = []
    for d in pl:
        mark = " «THIS SECTION»" if d.get("id") == decision_id else ""
        decs.append(f"- [{d.get('id')}] ({d.get('status')}){mark}: {d.get('plain') or d.get('decision')}\n"
                    f"    chose: {d.get('choice','')[:400]}\n    why: {d.get('why','')[:300]}")
    gl = "\n".join(f"- {g.get('term')}: {g.get('plain')} ({g.get('why','')})" for g in _jl(f"glossary.{project}.jsonl"))
    log = "\n".join(f"  {e.get('ts')} [{e.get('kind')}] {e.get('direction')}: {e.get('note')}" for e in _jl(f"log.{project}.jsonl"))
    surp = "\n".join(f"- ({s.get('valence')}) {s.get('headline')} — {s.get('detail')}" for s in _jl(f"surprises.{project}.jsonl"))
    foc = "\n".join(f"- {f.get('title')}: trying={f.get('trying')} next={f.get('next')}" for f in _jl(f"focus.{project}.jsonl"))
    ctx = (focus + f"PROJECT: {project}\nPURPOSE: {_read(f'purpose.{project}.txt')}\nGOAL: {_read(f'goal.{project}.txt')}\n\n"
           f"ALL DECISIONS (full):\n" + "\n".join(decs) + f"\n\nGLOSSARY:\n{gl}\n\nDATED LOG (what we did):\n{log}\n\n"
           f"SURPRISES:\n{surp}\n\nCURRENT FOCUS:\n{foc}")
    return ctx + _grep_code(project, question)


MEMTAIL = ("\nIf the exchange clarified a concept the user didn't know, append AT THE VERY END a fenced "
           "block:\n```memory\n{\"terms\":[{\"term\":\"...\",\"plain\":\"one-line\",\"why\":\"why here\"}]}\n```\n"
           "Only for genuinely-clarified concepts; omit otherwise.")


def answer(project, question, decision_id=None, history=None, provider=None, focus_text=None):
    import os
    provider = provider or os.environ.get("TRAINLINT_CHAT_LLM") or os.environ.get("TRAINLINT_REPORT_LLM") or "codex"
    sysp = ("You are a tutor embedded in the live report for a research project. Answer the user's "
            "question grounded ONLY in the context below (the current full project substrate + grepped "
            "code/data). Be concrete, cite file:line or the decision id when relevant, define jargon "
            "once, say UNKNOWN if the context doesn't cover it. " + MEMTAIL + "\n\nCONTEXT:\n"
            + build_context(project, question, decision_id, focus_text))
    convo = ""
    for h in (history or []):
        convo += f"{h.get('role','user').upper()}: {h.get('content','')}\n"
    convo += f"USER: {question}"
    raw = viz._llm(provider, sysp, convo)
    # fold any ```memory``` block back into the LOCAL glossary (closed loop, no cloud)
    m = re.search(r"```memory\s*(\{.*?\})\s*```", raw, re.S)
    clean = raw
    if m:
        clean = raw.replace(m.group(0), "").strip()
        try:
            terms = json.loads(m.group(1)).get("terms", [])
            gp = paths.resolve(f"glossary.{project}.jsonl")
            with gp.open("a", encoding="utf-8") as f:
                for t in terms:
                    if t.get("term"):
                        f.write(json.dumps({"term": t["term"], "plain": t.get("plain", ""),
                                            "why": t.get("why", ""), "dec": decision_id}, ensure_ascii=False) + "\n")
            cp = paths.resolve(f"clarify.{project}.jsonl")
            with cp.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"q": question, "a": clean, "dec": decision_id}, ensure_ascii=False) + "\n")
        except Exception:
            pass
    return clean


# ---- password gate (the tunnel exposes this server publicly; Cloudflare Zero Trust couldn't be
# enabled, so the gate lives HERE): one shared password -> HttpOnly cookie. The secret lives in
# data_root/report_password.txt (auto-generated once); rotate by editing that file. --------------
import hashlib
import secrets as _secrets


def _password():
    p = paths.data_root() / "report_password.txt"
    if not p.exists():
        p.write_text(_secrets.token_urlsafe(12), encoding="utf-8")
    return p.read_text(encoding="utf-8").strip()


def _cookie_val():
    return hashlib.sha256(("tl-report:" + _password()).encode()).hexdigest()


# per-user invite links: data_root/report_users.jsonl, one {"name","code","projects":["p1"]|"*"} per
# line. A user opens /invite?c=<code> once -> cookie. Revoke = delete the line (cookie dies with it).
def _users():
    p = paths.data_root() / "report_users.jsonl"
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    u = json.loads(line)
                    if u.get("code"):
                        out[hashlib.sha256(("tl-user:" + u["code"]).encode()).hexdigest()] = u
                except Exception:
                    pass
    return out


# relay-internal auth: requests the local relay agent (relay_agent.py) replays carry
# x-trainlint-relay: <secret>; the cloud worker already Google-authenticated the viewer, so they
# skip the password gate. Secret file = data_root/relay_internal_secret.txt (relay_agent creates it).
def _relay_secret():
    p = paths.data_root() / "relay_internal_secret.txt"
    try:
        return p.read_text(encoding="utf-8").strip() if p.exists() else ""
    except Exception:
        return ""


def _project_of(path):
    """'/asr_rewrite.html' / '/asr_rewrite.slides.html' -> 'asr_rewrite'; None for non-report paths."""
    m = re.match(r"^/([\w.-]+?)(\.slides)?\.html$", path.split("?")[0])
    return m.group(1) if m else None


LOGIN_HTML = ("<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>"
              "<body style='font-family:sans-serif;display:flex;justify-content:center;padding-top:18vh;background:#0f1115;color:#e8eaed'>"
              "<form method='POST' action='/login' style='text-align:center'>"
              "<h3>Trainlint report</h3><input type='password' name='p' placeholder='password' autofocus "
              "style='padding:9px 12px;border-radius:8px;border:1px solid #444;background:#171a21;color:#eee'> "
              "<button style='padding:9px 16px;border-radius:8px;border:0;background:#4f46e5;color:#fff'>Enter</button>"
              "{msg}</form></body>")


# ============================================================================================
# IN-REPORT EDIT — the report OWNER writes ONE field of ONE structured item back to the LOCAL
# substrate. Owner-only is enforced UPSTREAM in the worker (index.js): it relays an /edit only when
# targetNs === the caller's own ns (stricter than the read routes — no admins, no shared grants).
# This backend trusts the x-trainlint-relay header exactly like /chat's admin path and just performs
# the guarded write. Every write is: (1) project name guarded by SAFE_NAME (no path traversal),
# (2) kind + field whitelisted EXACTLY via EDIT_SPEC (an edit can NEVER address an arbitrary
# file/key), (3) optimistic-locked on body.prev (409 {error:"stale"} on drift, never a silent
# clobber), (4) atomic (temp file in the SAME dir + os.replace), preserving every OTHER key / line /
# comment / order in the file.
# --------------------------------------------------------------------------------------------
import os as _os
import tempfile as _tempfile

# project name -> filename component. Must be a bare token: starts alnum, then alnum/._-, <=64 chars.
# No '/', so f"plan.{project}.jsonl" can never escape data_root (path-traversal guard).
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# The COMPLETE set of writes an edit may make. kind -> (target file template, allowed fields, how to
# locate the row). Anything not here is rejected 400 — this dict IS the security boundary.
#   locate="id"    : match the JSONL object whose "id"   == body.id   (decision)
#   locate="term"  : match the JSONL object whose "term" == body.id   (glossary)
#   locate="line"  : match by stable_line_id(kind, obj) == body.id    (surprise, focus)
#   locate="whole" : no row; body.value overwrites the whole .txt     (goal, purpose)
EDIT_SPEC = {
    "decision": {"file": "plan.{p}.jsonl",      "fields": ("decision", "choice", "why", "status"),           "locate": "id"},
    "surprise": {"file": "surprises.{p}.jsonl", "fields": ("headline", "detail", "valence", "direction"),    "locate": "line"},
    "focus":    {"file": "focus.{p}.jsonl",     "fields": ("title", "trying", "next", "status"),              "locate": "line"},
    "glossary": {"file": "glossary.{p}.jsonl",  "fields": ("term", "plain", "why"),                           "locate": "term"},
    "goal":     {"file": "goal.{p}.txt",        "fields": (),                                                 "locate": "whole"},
    "purpose":  {"file": "purpose.{p}.txt",     "fields": (),                                                 "locate": "whole"},
}


def stable_line_id(kind, obj):
    """Deterministic id for a surprises/focus row. THE FRONTEND (viz.py) MUST COMPUTE THIS
    IDENTICALLY — copy this body verbatim so the id in data-e-id matches what the backend recomputes.
    Contract:
        if the row already carries a non-empty "id" (focus rows do) -> that id wins, unchanged;
        else  id = kind[0] + "-" + sha1( kind + ":" + canon ).hexdigest()[:12]
        where canon = json.dumps({k: v for k, v in obj.items() if k != "id"},
                                 sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    Both sides parse the SAME jsonl line into the SAME dict, so sort_keys makes key order irrelevant
    and the hashes agree. On the FIRST successful edit the backend PERSISTS this id into the row
    ("id": <this>), so it stays stable forever after even though the edit just changed the hashed
    content (subsequent renders read the persisted "id" and skip the hash)."""
    if obj.get("id"):
        return str(obj["id"])
    canon = json.dumps({k: v for k, v in obj.items() if k != "id"},
                       sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return kind[:1] + "-" + hashlib.sha1((kind + ":" + canon).encode("utf-8")).hexdigest()[:12]


def _norm(x):
    return ("" if x is None else str(x)).strip()


def _atomic_write(path, text):
    """Write text to path atomically: a temp file in the SAME directory (so os.replace is a rename on
    one filesystem, never a cross-device copy that could half-write), then os.replace over target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = _tempfile.mkstemp(dir=str(path.parent), prefix=".tl-edit-", suffix=path.suffix or ".tmp")
    try:
        with _os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        _os.replace(tmp, path)
    except Exception:
        try:
            _os.unlink(tmp)
        except Exception:
            pass
        raise


def _rewrite_jsonl_field(path, match_fn, field, value, prev, persist_id=None):
    """Locate the ONE json row where match_fn(obj) is True, optimistic-check obj[field]==prev, set
    obj[field]=value (and obj["id"]=persist_id when given, to pin a hashed line id), then rewrite ONLY
    that physical line — every other line (comments, blanks, other rows) is kept byte-for-byte, and
    the target row keeps all its other keys and their order. Returns (code, payload)."""
    raw = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = raw.split("\n")
    idx, obj = None, None
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        try:
            o = json.loads(s)
        except Exception:
            continue
        if isinstance(o, dict) and match_fn(o):
            idx, obj = i, o
            break
    if obj is None:
        return 404, {"error": "not found"}
    if _norm(obj.get(field)) != _norm(prev):
        return 409, {"error": "stale", "current": obj.get(field, "")}
    obj[field] = value
    if persist_id is not None:
        obj["id"] = persist_id
    lines[idx] = json.dumps(obj, ensure_ascii=False)
    _atomic_write(path, "\n".join(lines))
    return 200, {"ok": True, "value": value}


def _write_whole(path, value, prev):
    """goal / purpose: overwrite the whole .txt (optimistic-checked on the stripped current text)."""
    cur = path.read_text(encoding="utf-8") if path.exists() else ""
    if _norm(cur) != _norm(prev):
        return 409, {"error": "stale", "current": cur.strip()}
    _atomic_write(path, value.rstrip("\n") + "\n")
    return 200, {"ok": True, "value": value}


def apply_edit(body):
    """Whitelisted write dispatcher for POST /edit. Returns (http_code, json_payload):
    400 on any project/kind/field/value not on the whitelist, 404 if the item id isn't found, 409 on
    optimistic-lock drift (body.prev != stored), 200 {ok:true, value:<stored>} on success."""
    project = body.get("project", "")
    if not isinstance(project, str) or not SAFE_NAME.match(project):
        return 400, {"error": "bad project"}
    spec = EDIT_SPEC.get(body.get("kind", ""))
    if not spec:
        return 400, {"error": "bad kind"}
    value, prev = body.get("value", ""), body.get("prev", "")
    if not isinstance(value, str) or not isinstance(prev, str):
        return 400, {"error": "bad value/prev"}
    path = paths.resolve(spec["file"].format(p=project))
    locate = spec["locate"]
    if locate == "whole":                       # goal / purpose — whole-file overwrite, no field/id
        return _write_whole(path, value, prev)
    field = body.get("field", "")
    if field not in spec["fields"]:
        return 400, {"error": "bad field"}
    item_id = str(body.get("id", ""))
    if locate == "id":                          # decision — match the row whose "id" == body.id
        return _rewrite_jsonl_field(path, lambda o: str(o.get("id", "")) == item_id, field, value, prev)
    if locate == "term":                        # glossary — match the row whose "term" == body.id
        return _rewrite_jsonl_field(path, lambda o: str(o.get("term", "")) == item_id, field, value, prev)
    # locate == "line": surprise / focus — match by the deterministic stable_line_id, and PIN it so a
    # content-hashed id never drifts after this edit changes the hashed content.
    return _rewrite_jsonl_field(path, lambda o: stable_line_id(body["kind"], o) == item_id,
                                field, value, prev, persist_id=item_id)


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _auth(self):
        """None if unauthenticated; {'projects':'*'} for the admin password or a relayed request
        (worker-authenticated viewer); else the user record."""
        sec = _relay_secret()
        if sec and _secrets.compare_digest(self.headers.get("x-trainlint-relay", ""), sec):
            return {"name": "relay", "projects": "*"}
        c = self.headers.get("cookie", "").replace(" ", "")
        m = re.search(r"tl_auth=([a-f0-9]{64})", c)
        if not m:
            return None
        if m.group(1) == _cookie_val():
            return {"name": "admin", "projects": "*"}
        return _users().get(m.group(1))

    def _allowed(self, user, project):
        return bool(user) and (user.get("projects") == "*" or project in (user.get("projects") or []))

    def _login_page(self, msg=""):
        body = LOGIN_HTML.format(msg=f"<p style='color:#f87171'>{msg}</p>" if msg else "").encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code, obj):
        out = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        if self.path.startswith("/invite"):  # one-click magic link: /invite?c=<code>
            import urllib.parse as up
            code = up.parse_qs(up.urlparse(self.path).query).get("c", [""])[0]
            h = hashlib.sha256(("tl-user:" + code).encode()).hexdigest()
            u = _users().get(h)
            if not u:
                self._login_page("invalid or revoked invite link")
                return
            dest = (u.get("projects") or ["/"])
            dest = "/" if dest == "*" or not isinstance(dest, list) else f"/{dest[0]}.html"
            self.send_response(303)
            self.send_header("set-cookie", f"tl_auth={h}; Path=/; Max-Age=7776000; HttpOnly; SameSite=Lax")
            self.send_header("location", dest)
            self.end_headers()
            return
        user = self._auth()
        if not user:
            self._login_page()
            return
        proj = _project_of(self.path)
        if proj and not self._allowed(user, proj):
            self.send_error(403, "not your project")
            return
        super().do_GET()

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/login":
            body = self.rfile.read(int(self.headers.get("content-length", 0))).decode()
            import urllib.parse as up
            pw = up.parse_qs(body).get("p", [""])[0]
            if pw == _password():
                self.send_response(303)
                self.send_header("set-cookie",
                                 f"tl_auth={_cookie_val()}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax")
                self.send_header("location", "/")
                self.end_headers()
            else:
                self._login_page("wrong password")
            return
        user = self._auth()
        if not user:
            self.send_error(403)
            return
        # In-report edit — trust the relay (the worker already enforced owner-only) exactly like /chat.
        # Accept the flat /edit and the namespaced /<email>/edit the worker may relay.
        if path == "/edit" or re.match(r"^/[^/]+/edit$", path):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                if not self._allowed(user, body.get("project", "")):
                    return self._send_json(403, {"error": "editing is owner-only"})
                code, payload = apply_edit(body)
                return self._send_json(code, payload)
            except Exception as e:
                return self._send_json(400, {"error": str(e)[:300]})
        if path != "/chat":
            self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
            if not self._allowed(user, body.get("project", "")):
                raise PermissionError("not your project")
            ans = answer(body["project"], body["question"], body.get("decision_id"), body.get("history"),
                         focus_text=body.get("focus"))
            out = json.dumps({"answer": ans}).encode()
        except Exception as e:
            out = json.dumps({"answer": "", "error": str(e)[:300]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(out)


def run(viz_dir, port):
    import functools
    h = functools.partial(Handler, directory=str(viz_dir))
    ThreadingHTTPServer(("127.0.0.1", int(port)), h).serve_forever()


def adduser(name, projects):
    """Append a user and print their one-click invite link. projects: list of names, or '*'."""
    code = _secrets.token_urlsafe(16)
    rec = {"name": name, "code": code, "projects": projects}
    with (paths.data_root() / "report_users.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"invite link for {name}: https://report.secondfoundationlabs.com/invite?c={code}")
    print(f"  projects: {projects}   (revoke: delete their line in data_root/report_users.jsonl)")


if __name__ == "__main__":
    if sys.argv[1:2] == ["adduser"]:
        # python3 chat_backend.py adduser <name> [proj1,proj2 | *]
        projs = sys.argv[3] if len(sys.argv) > 3 else "*"
        adduser(sys.argv[2], "*" if projs == "*" else projs.split(","))
    else:
        run(sys.argv[1], sys.argv[2])
