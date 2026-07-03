# trainlint report server (multi-tenant, serverless)

A Cloudflare Worker + R2 that hosts every plugin installer's interactive reports at
`https://secondfoundationlabs.com`, each user seeing only their own, logging in with their own
account. No always-on server or tunnel on anyone's machine.

## How it fits together

```
plugin (installer's machine)          Cloudflare                         browser
  viz.generate → data_root/viz  --POST /api/upload (Bearer token)-->  Worker → R2 (<ns>/<project>.html)
                                                                        │
  open secondfoundationlabs.com ── Cloudflare Access login ──────────> Worker → lists/serves <ns>/*
```

The namespace `<ns>` = `sha256(email)`. It is derived server-side from a **verified** credential on
every request (the Access JWT for reads, the signed upload token for writes) — never from client
input — so one tenant can never touch another's. See `worker/src/auth.js`.

## One-time Cloudflare setup (needs your account)

```bash
npm i -g wrangler
wrangler login                                    # authorizes your Cloudflare account
wrangler r2 bucket create trainlint-reports        # the storage bucket
openssl rand -hex 32 | wrangler secret put TOKEN_SIGNING_KEY   # HMAC key for upload tokens
```

In the **Zero Trust dashboard → Access**:
1. Create an **Access application** on `secondfoundationlabs.com` covering path `/*`, identity
   providers Google + GitHub, policy = an **email allowlist** (start invite-only — you'll be storing
   other people's plans). Copy its **Application Audience (AUD) tag**.
2. Create a second Access application on path `/api/*` with a **Bypass** policy (Everyone) — the
   headless plugin upload can't do a browser login, so `/api/upload` is guarded by the HMAC token
   inside the Worker instead of by Access.

Fill `worker/wrangler.toml` `[vars]` with your `ACCESS_AUD` and `ACCESS_TEAM_DOMAIN`
(`<your-team>.cloudflareaccess.com`), then:

```bash
cd worker && wrangler deploy
```

## Installer onboarding (per user, once)

1. Open `https://secondfoundationlabs.com`, log in (Google/GitHub).
2. Copy the **upload token** shown on the page.
3. Set it in the plugin: `export TRAINLINT_REPORT_TOKEN=<token>`.

After that, every `/plan` or `/execute` close uploads the report; it appears on that page, isolated
to your account. Installers who never set a token are unaffected — the upload is opt-in and
fail-silent (see `research/push.py`, plugin side).

## Verify isolation before anyone else uses it

Mint two tokens (two emails), upload under each, and confirm token A cannot write into B's namespace
and an Access session for A gets 404 on B's `<project>.html`. This is the `verify-isolation` gate — a
single cross-tenant read is the catastrophe, so it must be tested, not assumed.
