# hansard report UI — live multi-tenant report viewer

Turns each operator's **local** hansard reports into a page teammates can open at
`https://secondfoundationlabs.com`, log into once with Google, and read live — including an
in-page chatbot that answers from that operator's *current* local context. No inbound port, tunnel,
or DNS on the operator's side: the operator's box dials **out**; a central Cloudflare Worker relays
each authenticated request back down that connection.

```
  teammate's browser              Cloudflare Worker                    operator's machine
  secondfoundationlabs.com        (auth + relay, stores nothing)       (zero inbound config)
        │                               │                                     │
        │                               │◄──── outbound WebSocket ────────────│  relay_agent.py
        │                               │      (per-namespace AgentHub DO)     │   (cron-kept-alive)
        │  Google login (once) ────────►│  who = email → ns = sha256(email)    │
        │  GET /<email>/<proj>.html ───►│  access-check → relay down the WS ──►│  chat_backend.py :8420
        │  ◄──────── report / chat ─────│◄─────────────────────────────────────│   renders + answers from
        │                               │                                     │   local substrate + grep
```

- **The Worker stores no report content.** It authenticates the viewer and forwards the request to
  the operator's live backend, so the context is always current and any learning stays local.
- **Ownership = a namespace (`ns`)**, `ns = sha256(lowercased-email)[:32]`. Every report, upload, and
  live agent is keyed by `ns`; cross-`ns` reads are impossible except for admins and consented shares.

> Note: this replaced an earlier *push-to-R2 + Cloudflare Access* design. R2 is now only a fallback
> cache; the live path is the outbound relay above, and auth is Google OAuth (not Access).

---

## The pieces

**Local (per operator)** — `../research/`
| file | role |
|---|---|
| `viz.py` | renders the report + slide deck HTML into `data_root/viz/<project>.html`. Slides are LLM-authored (`TRAINLINT_SLIDES_LLM`, default `codex`). |
| `chat_backend.py` | loopback HTTP server on `127.0.0.1:8420`; serves the rendered reports and answers the in-page chatbot from the full local substrate + code grep. |
| `relay_agent.py` | dials the Worker's `/agent` WebSocket and replays each relayed request against `:8420`. Also `relay_agent.py share <email>` to invite a viewer. |
| `serve.py` | `ensure()` spawns the backend + relay on every render (idempotent, silent). |
| `push.py` / `paths.py` | upload-token resolution; per-project data-root paths. |

**Central (deployed once)** — `worker/`
| file | role |
|---|---|
| `src/index.js` | routing, Google OAuth, access control, the per-user `/<email>/<project>` route, admin dashboard. |
| `src/relay.js` | `AgentHub` Durable Object — one per `ns`, holds the operator's live WebSocket, relays frames. |
| `src/auth.js` | `nsForEmail`, `mintToken` / `verifyToken` (HMAC upload tokens), Access-JWT verify (optional/legacy). |
| `wrangler.toml` | bindings: R2 `REPORTS`, DO `AGENTS`, apex route, `ADMIN_EMAILS`. |

**Operator state** — `data_root` (`/home/shiyil/.claude/plugins/data/trainlint-trainlint/`)
- `.token` — this box's upload token (the machine credential the plugin uploads/relays with). Pairing
  binds it to a Google email so the agent registers under `nsForEmail(email)`.
- `relay_internal_secret.txt` (0600) — the `x-hansard-relay` header the backend trusts as admin
  (the Worker already authenticated the viewer).
- `relay_agent.lock` — flock; guarantees a single relay agent (duplicates flap the connection).
- `keepalive.sh` / `launch.sh` — cron-driven supervisor (see *Persistence*).

---

## First-time Worker setup (once; already done for secondfoundationlabs.com)

```bash
cd worker
export CLOUDFLARE_API_TOKEN=...        # needs Workers Scripts:Edit, R2:Edit, (Workers Routes:Edit)
export CLOUDFLARE_ACCOUNT_ID=...

wrangler r2 bucket create trainlint-reports                     # 1. storage (fallback cache)
openssl rand -hex 32 | wrangler secret put TOKEN_SIGNING_KEY    # 2. upload-token HMAC key
wrangler secret put GOOGLE_CLIENT_ID                            # 3. Google OAuth (from Google Cloud
wrangler secret put GOOGLE_CLIENT_SECRET                        #    console; redirect URI below)
# edit wrangler.toml → ADMIN_EMAILS = "you@…,teammate@…"
wrangler deploy                                                 # 4. ships code + DO migration + route
```

- **Google OAuth**: create an OAuth client (type *Web*), authorized redirect URI
  `https://secondfoundationlabs.com/auth/google/callback`. Only `openid email` scope is used.
- **Domain**: `wrangler.toml`'s `[[routes]] custom_domain = true` binds the apex; DNS must already be
  on Cloudflare. The route-rebind step needs `Zone:Workers Routes:Edit` — if your token lacks it the
  deploy prints a harmless error but the *code* still ships (the route persists once created).
- `ACCESS_AUD` / `ACCESS_TEAM_DOMAIN` in `[vars]` are **legacy** (Cloudflare Access). The live auth is
  Google OAuth + tokens; Access is an optional first-pass and can be left at the placeholders.

## Operator onboarding (per person who wants to publish reports)

1. Run hansard normally — `viz.py` renders and `serve.py` auto-starts the backend + relay. The relay
   dials out with an anonymous token, so reports are already viewable via the magic link the plugin
   prints.
2. **Bind to Google** (so you log in with your account, no token in URLs): open
   `https://secondfoundationlabs.com/link?token=<your .token>&project=<any>`, sign in with Google.
   This pairs the token to your email; the agent then registers under `nsForEmail(you)`.
   **After binding, restart the relay once** so it reconnects under the new namespace.
3. Log in at `secondfoundationlabs.com` → the dashboard lists your projects → open one.

## Sharing a report with someone

```bash
python3 ../research/relay_agent.py share teammate@company.com
```
Writes a **pending** invite (consent-required — it does not grant access). The invitee logs in with
Google, sees *Pending invitations* on their dashboard, clicks **Accept**, then can open your reports.
Re-share after you change namespaces (e.g. after first binding), since a share points at a specific `ns`.

---

## URLs

| URL | who | what |
|---|---|---|
| `/<email>/<project>.html` | owner · admin · shared | the per-user report (also `.slides.html`) |
| `/<project>.html` | owner | flat shortcut to your own report |
| `/r/<ns>/<path>` | owner · admin · shared | explicit-namespace relay |
| `/` | anyone logged in | dashboard: your projects (+ every operator, if admin) |
| `/link?token=…` | — | pair a machine token to your Google account |
| `/auth/google` · `/logout` | — | login / logout |
| `POST /<email>/chat` · `/chat` | same as report | the in-page chatbot |

Admins (in `ADMIN_EMAILS`) may open **any** `/<email>/<project>` and see all operators in the dashboard.

---

## Config reference

**Worker** — `wrangler.toml` `[vars]` + secrets:
`ADMIN_EMAILS` (comma list) · `TOKEN_SIGNING_KEY` (secret) · `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`
(secrets). Change admins by editing `[vars]` and `wrangler deploy`.

**Local** — environment variables read by the research scripts:
| var | default | effect |
|---|---|---|
| `TRAINLINT_REPORT_TOKEN` | `data_root/.token` | upload token (`none` = opt out of publishing) |
| `TRAINLINT_RELAY` | `1` | `0` disables the outbound relay |
| `TRAINLINT_RELAY_URL` | `wss://secondfoundationlabs.com/agent` | which Worker to dial |
| `TRAINLINT_SERVE_PORT` | `8420` | local backend port |
| `TRAINLINT_REPORT_LLM` | `codex` | LLM backend for report prose (`kimi`/`gemini`/`claude`) |
| `TRAINLINT_SLIDES_LLM` | (falls back to `TRAINLINT_REPORT_LLM`) | LLM backend that authors the slide deck |

---

## Persistence (survives reboots / crashes)

`systemd --user` is unavailable on this headless box, so a cron supervisor is used instead:
```
@reboot        data_root/keepalive.sh    # start on boot
* * * * *      data_root/keepalive.sh    # restart backend/relay if down (idempotent, flock-guarded)
```
`keepalive.sh` resolves the newest installed plugin version, so it keeps working across upgrades.
Manual restart of the relay:
```bash
pkill -f "relay_agent.py run"; sleep 2
setsid python3 ../research/relay_agent.py run </dev/null >/tmp/tl_relay.log 2>&1 &
```

---

## Modifying it

- **Worker logic** (routes, access, dashboard): edit `worker/src/*.js`, then from `worker/`:
  `node --check src/index.js && wrangler deploy`. Verify locally first with `wrangler dev --local`.
- **Report / slides look** (layout, chatbot widget, slide generation): edit `../research/viz.py`, then
  re-render: `python3 ../research/viz.py <project>`.
- **Chatbot answers / context**: `../research/chat_backend.py` (`build_context`, `answer`).
- ⚠️ **Plugin upgrades overwrite the marketplace tree.** Keep a copy of every edit in
  `data_root/viz_patches/` and reapply after upgrading (see the memory note on hansard customizations).

## Troubleshooting

| symptom | cause → fix |
|---|---|
| **operator offline** | relay not connected. Check `ps -ef \| grep relay_agent`; ensure exactly one; the flock stops duplicates that flap. |
| **no reports / empty dashboard after Google login** | your token is on a different `ns` than your email. Bind via `/link` (must be signed in with *Google*, not an anonymous magic-link cookie), then restart the relay. |
| **`/auth/google` → not found** | stale build; the auth routes must sit before the who-gate. Redeploy current `index.js`. |
| **shared user still offline** | they accepted an invite for your *old* `ns`. Re-run `relay_agent.py share <email>`; they Accept again. |
| **deploy prints a `workers/routes` auth error** | token lacks `Zone:Workers Routes:Edit`; harmless if the route already exists (code still ships). |

## Verify isolation before onboarding others

A single cross-tenant read is the catastrophe, so test it: with two Google accounts A and B, confirm
A cannot open `/<B-email>/<project>.html` (403) unless A is an admin or B has shared+consented, and
that a non-admin dashboard never lists another operator. This is exercised by the worker's local
`wrangler dev` access-matrix test.
