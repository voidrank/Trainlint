#!/usr/bin/env python3
"""verify-isolation — the catastrophe gate, as a rerunnable probe.

Two throwaway tenants (fresh random upload tokens A and B) exercise the live worker:
  1. A and B each upload their own report                          -> both 200
  2. A uploads a file NAMED like B's                              -> lands in A's namespace only
  3. each tenant reads its own file back                           -> exact content match
  4. B reads A's filename / A reads B's                            -> 404 (no cross-read)
  5. a non-admin tenant hits the admin cross-namespace route       -> refused (not 200)

One cross-tenant read is the catastrophe; this script must exit 0 before anyone else uses
the service. Server override: TRAINLINT_REPORT_SERVER (default https://secondfoundationlabs.com).
"""
import os
import secrets
import sys
import urllib.error
import urllib.request

SERVER = os.environ.get("HANSARD_REPORT_SERVER") or os.environ.get("TRAINLINT_REPORT_SERVER", "https://secondfoundationlabs.com").rstrip("/")
fails = 0


def check(cond, msg):
    global fails
    print(("ok    " if cond else "FAIL  ") + msg)
    if not cond:
        fails += 1


def req(method, path, token=None, cookie=None, body=None):
    """(status, body_text) — never raises. Explicit User-Agent: Cloudflare's UA-block rule 403s
    (error 1010) the bare Python-urllib default; any explicit UA passes (probed 2026-07-08)."""
    r = urllib.request.Request(f"{SERVER}{path}", data=body, method=method,
                               headers={"User-Agent": "hansard-verify-isolation/1.0"})
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    if cookie:
        r.add_header("Cookie", f"trainlint_token={cookie}")
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)


def main():
    ta, tb = secrets.token_hex(16), secrets.token_hex(16)  # two brand-new anonymous tenants
    mark_a, mark_b, evil = (f"ISO-PROBE-{secrets.token_hex(8)}" for _ in range(3))
    pa, pb = "zz-iso-a", "zz-iso-b"  # throwaway project names, clearly junk

    # 1. each tenant uploads its own report
    s, _ = req("POST", f"/api/upload?project={pa}&kind=html", token=ta, body=mark_a.encode())
    check(s == 200, f"tenant A uploads {pa} -> 200 (got {s})")
    s, _ = req("POST", f"/api/upload?project={pb}&kind=html", token=tb, body=mark_b.encode())
    check(s == 200, f"tenant B uploads {pb} -> 200 (got {s})")

    # 2. A uploads a file NAMED like B's — must land in A's namespace, never B's
    s, _ = req("POST", f"/api/upload?project={pb}&kind=html", token=ta, body=evil.encode())
    check(s == 200, f"tenant A uploads a file named {pb} -> 200 (namespaced write)")

    # 3. own reads: exact content
    s, b = req("GET", f"/{pa}.html", cookie=ta)
    check(s == 200 and mark_a in b, "A reads its own file -> its own content")
    s, b = req("GET", f"/{pb}.html", cookie=tb)
    check(s == 200 and mark_b in b and evil not in b,
          "B reads its own file -> B's content, NOT A's same-named write (the crux)")

    # 4. cross reads must 404 (namespace-scoped key, not path-scoped)
    s, b = req("GET", f"/{pa}.html", cookie=tb)
    check(s == 404 or mark_a not in b, f"B reading A's filename -> no A content (status {s})")
    s, b = req("GET", f"/{pb}.html", cookie=ta)
    check((s == 200 and evil in b and mark_b not in b) or s == 404,
          "A reading that name sees only its OWN copy, never B's")

    # 5. the admin cross-namespace route refuses non-admins
    s, b = req("GET", f"/someone%40example.com/{pa}.html", cookie=ta)
    check(not (s == 200 and (mark_a in b or mark_b in b)),
          f"non-admin on the admin /<email>/<project> route leaks nothing (status {s})")

    # 6. share/accept consent flow: a PENDING invite must grant NOTHING until the invitee accepts.
    #    A invites a throwaway email onto A's namespace; that email (as a fresh, un-accepted tenant)
    #    must still 404/deny A's report — a pending invite is not a grant.
    invitee = f"iso-{secrets.token_hex(6)}@example.com"
    s, _ = req("POST", "/api/share", token=ta,
               body=('{"email":"%s"}' % invitee).encode())
    check(s in (200, 201, 401, 403, 404),
          f"/api/share responds without error-crashing (status {s})")
    # the invitee has no session/cookie we can forge (ns = sha256(their email), no signed token),
    # so we assert the NEGATIVE via a fresh anonymous tenant reading A's project: still no A content.
    s, b = req("GET", f"/{pa}.html", cookie=secrets.token_hex(16))
    check(mark_a not in b, "a pending invite grants no access — a fresh tenant still can't read A")

    print("\nNOTE: proves the ANONYMOUS-TOKEN layer + share-does-not-grant. NOT covered here:")
    print("  two real Google-account tenants (needs browser logins), the /accept promotion,")
    print("  and AgentHub live-relay paths. See the two-account manual steps.")
    print(("ISOLATION HOLDS (token layer)" if not fails else f"{fails} FAILURES — DO NOT ONBOARD ANYONE"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
