# verify-isolation — the 5-minute two-account manual test

`verify_isolation.py` proves the **anonymous-token layer** automatically (10/10). What a script
can't forge is a real Google login (the email-namespace token is server-signed). This closes that
gap with two real accounts. Run it before onboarding anyone outside your team.

**You need:** two Google accounts — yours (`voidrank@gmail.com`) and a second (`cz@2ndf.ai`, or
any other). Do the two halves in two different browsers / profiles / incognito windows so their
cookies don't mix.

## Steps

1. **Account 1** — open `https://secondfoundationlabs.com`, log in with Google account #1.
   Note the projects listed (e.g. `report-server`, `asr_rewrite`). Copy the URL of one, e.g.
   `https://secondfoundationlabs.com/report-server.html`. ✅ you can open it.

2. **Account 2** — in the *other* browser, open `https://secondfoundationlabs.com`, log in with
   Google account #2. It should list account #2's own projects (or none). 

3. **The cross-read (the catastrophe check)** — while logged in as **account 2**, paste
   **account 1's** project URL: `https://secondfoundationlabs.com/report-server.html`.
   - ✅ PASS: you see account 2's *own* `report-server` (if they have one) or "no such report" /
     "operator offline" — **never account 1's content**.
   - ❌ FAIL: account 2 sees account 1's report. Stop. Do not onboard anyone. Tell me.

4. **The admin route** — still as account 2 (a non-admin), open
   `https://secondfoundationlabs.com/voidrank@gmail.com/report-server.html`.
   - ✅ PASS: `not authorized for this user` (403).
   - ❌ FAIL: it renders account 1's report → admin gate is broken.

5. **The share consent flow (optional, if you use sharing)** — as account 1, share a project to
   account 2's email. As account 2, confirm you must explicitly **accept** (`/accept`) before it
   appears; before accepting, step 3 must still deny.

## If all pass

Flip `verify-isolation` in the plan to `verified` and log the date + which two accounts —
then the catastrophe gate is closed across BOTH the token layer (script) and the real login
layer (this test), and the service is safe to hand to people outside your team.
