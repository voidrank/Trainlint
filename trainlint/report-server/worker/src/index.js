// index.js — the entire server side of the multi-tenant report service.
//
// One Cloudflare Worker, three concerns, every one scoped to the caller's namespace so cross-tenant
// reads/writes are impossible by construction:
//   POST /api/upload   — machine upload from the plugin. Auth = HMAC upload token (NOT Access; a
//                        headless POST can't do the browser login). Writes <ns>/<project>.<kind>.html.
//   GET  /             — the logged-in user's index: lists THEIR reports + shows THEIR upload token.
//   GET  /<name>.html  — streams one of the caller's reports from R2 (404 if it isn't in their ns).
//
// Live relay (see relay.js): the operator's box dials wss://<host>/agent (upload-token auth) into
// a per-namespace AgentHub Durable Object. Viewer routes (/<name>.html, /chat, /login,
// /r/<ns>/<anything>) relay through that socket when the agent is online; POST /api/share grants a
// customer email viewer access to the caller's namespace via an R2 _shares/<email> record.
//
// Access protects the GET routes. If Access is not configured (or billing blocks it), we support
// a self-contained Password-to-Token login gate to protect GET routes. The namespace is always
// derived server-side from a verified credential.

import { verifyAccessJwt, verifyToken, mintToken, nsForEmail } from "./auth.js";

// The Durable Object class must be exported from the Worker's main module for the AGENTS binding.
export { AgentHub } from "./relay.js";

const SAFE_NAME = /^[A-Za-z0-9._-]{1,128}$/; // project/file names we accept into a key

function html(body, status = 200, headers = {}) {
  return new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8", ...headers } });
}
function text(body, status = 200) {
  return new Response(body, { status, headers: { "content-type": "text/plain; charset=utf-8" } });
}

// admin allow-list from env.ADMIN_EMAILS (comma-separated). Membership => may access ANY /<user>/…
// route and sees every registered operator in the dashboard. Everything else stays ns-isolated.
function isAdmin(email, env) {
  if (!email) return false;
  const list = String(env.ADMIN_EMAILS || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
  return list.includes(String(email).trim().toLowerCase());
}

function htmlEscape(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function getCookie(request, name) {
  const cookieHeader = request.headers.get("Cookie") || "";
  const cookies = cookieHeader.split(";").map(c => c.trim());
  for (const cookie of cookies) {
    const [k, v] = cookie.split("=");
    if (k === name) return decodeURIComponent(v);
  }
  return null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    // ---- upload: the ONE write path, guarded by the signed upload token --------------------------
    if (path === "/api/upload" && request.method === "POST") {
      const auth = request.headers.get("Authorization") || "";
      const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
      const ident = await verifyToken(token, env);
      if (!ident) return text("invalid or missing upload token", 401);

      const project = url.searchParams.get("project") || "";
      const kind = url.searchParams.get("kind") === "slides" ? "slides.html" : "html";
      if (!SAFE_NAME.test(project)) return text("bad project name", 400);

      const bodyText = await request.text();
      if (!bodyText || bodyText.length > 5_000_000) return text("empty or oversized report", 400);

      // KEY is built ONLY from the token's namespace — never from client input — so an upload can
      // never target another tenant's space.
      const key = `${ident.ns}/${project}.${kind}`;
      await env.REPORTS.put(key, bodyText, { httpMetadata: { contentType: "text/html; charset=utf-8" } });
      return text(`stored ${project}.${kind}`, 200);
    }

    // ---- feedback round-trip -----------------------------------------------------------------------
    // The report page POSTs the operator's margin notes / chat Q&A back here; the plugin machine
    // GETs them down (then DELETEs the consumed keys), absorbs, digests and re-renders.
    //
    // WRITE auth = the VIEWER'S OWN verified credential ONLY (Access JWT or the signed trainlint
    // cookie). Feedback is filed under the VIEWER's namespace — the same ns their own machine
    // pulls — so the primary loop (you view your own report, leave notes, your machine picks them
    // up) works, while nobody can write into a tenant they can't authenticate as. We deliberately
    // do NOT accept a raw ns= capability: for email/paired tenants ns = sha256(public email), so a
    // ?ns= write is forgeable by anyone who knows the email. No credential -> no write.
    // Pull/Delete auth = the machine's Bearer upload token, scoped to that token's own namespace.
    const FB_MAX = 200; // hard cap on pending blobs per tenant — bounds R2 growth + digest cost
    if (path === "/api/feedback" && request.method === "POST") {
      const bodyText = await request.text();
      if (!bodyText || bodyText.length > 512_000) return text("empty or oversized feedback", 400);
      let fbNs = null;
      const fbWho = await verifyAccessJwt(request, env);
      if (fbWho) fbNs = await nsForEmail(fbWho.email);
      if (!fbNs) {
        const cookieToken = getCookie(request, "trainlint_token");
        if (cookieToken) {
          const id = await verifyToken(cookieToken, env);
          if (id) fbNs = id.ns;
        }
      }
      if (!fbNs) return text("sign in to leave feedback", 401);
      const project = url.searchParams.get("project") || "";
      if (!SAFE_NAME.test(project)) return text("bad project name", 400);
      // count-cap: one cheap list, reject once the tenant's queue is full (drains on next digest)
      const existing = await env.REPORTS.list({ prefix: `${fbNs}/_feedback/` });
      if ((existing.objects || []).length >= FB_MAX) return text("feedback queue full", 429);
      const stamp = `${Date.now()}.${crypto.randomUUID().slice(0, 8)}`;
      await env.REPORTS.put(`${fbNs}/_feedback/${project}.${stamp}.json`, bodyText,
        { httpMetadata: { contentType: "application/json" } });
      return text("feedback stored", 200);
    }

    if (path === "/api/feedback" && (request.method === "GET" || request.method === "DELETE")) {
      const auth = request.headers.get("Authorization") || "";
      const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
      const ident = await verifyToken(token, env);
      if (!ident) return text("invalid or missing token", 401);
      // Serve the token's OWN namespace, and — for a raw local token — also the sha256-derived
      // anon namespace (a page viewed before pairing filed under anon). A signed token has no raw
      // preimage, so it only gets its user ns (matches how push.py's default raw token is set).
      const nss = new Set([ident.ns]);
      if (/^[a-f0-9]{32}$/i.test(token)) {
        const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(token.trim()));
        nss.add([...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32));
      }
      if (request.method === "DELETE") {
        const key = url.searchParams.get("key") || "";
        const okPrefix = [...nss].some((n) => key.startsWith(`${n}/_feedback/`));
        if (!okPrefix) return text("bad key", 400);
        await env.REPORTS.delete(key);
        return text("deleted", 200);
      }
      // Cap total R2 gets to stay well under the Workers subrequest limit even with 2 namespaces:
      // a bounded page drains over successive digests instead of 500-ing on a large queue.
      const GET_BUDGET = 40;
      const out = [];
      for (const n of nss) {
        if (out.length >= GET_BUDGET) break;
        const listed = await env.REPORTS.list({ prefix: `${n}/_feedback/` });
        for (const o of listed.objects) {
          if (out.length >= GET_BUDGET) break;
          const obj = await env.REPORTS.get(o.key);
          if (obj) out.push({ key: o.key, blob: await obj.text() });
        }
      }
      return new Response(JSON.stringify(out), { headers: { "content-type": "application/json" } });
    }
    // ---- live relay: agent dial-in ----------------------------------------------------------------
    // The operator's local box connects OUT with its upload token (Authorization header, or ?token=
    // for WS clients that cannot set headers). We verify the token, then hand the upgrade to that
    // namespace's AgentHub Durable Object. Must run before the browser ?token= magic-link handling.
    if (path === "/agent") {
      if ((request.headers.get("Upgrade") || "").toLowerCase() !== "websocket") {
        return text("expected websocket upgrade", 426);
      }
      const auth = request.headers.get("Authorization") || "";
      let agentToken = auth.startsWith("Bearer ") ? auth.slice(7) : "";
      if (!agentToken) agentToken = url.searchParams.get("token") || "";
      const ident = await verifyToken(agentToken, env);
      if (!ident) return text("invalid or missing upload token", 401);
      const hub = env.AGENTS.get(env.AGENTS.idFromName(ident.ns));
      // Pass the already-verified identity into the DO on the /connect subrequest. The DO can't
      // re-verify the token, so it trusts index.js: email → hello/dashboard label, ns → R2 key.
      const connectUrl = `https://agent-hub/connect?email=${encodeURIComponent(ident.email || "unknown")}&ns=${encodeURIComponent(ident.ns)}`;
      return hub.fetch(new Request(connectUrl, request));
    }

    // ---- Google OAuth routes: handled BEFORE the who-gate so an already-authenticated user (or a
    // callback that re-fires) never falls through to a 404. They only need env + url, not `who`. -----
    if (path === "/auth/google") {
      const clientId = env.GOOGLE_CLIENT_ID;
      if (!clientId) return html(loginPage("Google OAuth is not configured (GOOGLE_CLIENT_ID missing)."), 500);
      const state = url.searchParams.get("state") || "/";
      const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?response_type=code` +
        `&client_id=${encodeURIComponent(clientId)}` +
        `&redirect_uri=${encodeURIComponent(`https://${url.host}/auth/google/callback`)}` +
        `&scope=${encodeURIComponent("openid email")}&state=${encodeURIComponent(state)}&prompt=select_account`;
      return new Response("", { status: 302, headers: { "Location": authUrl } });
    }

    if (path === "/auth/google/callback") {
      const code = url.searchParams.get("code");
      const state = url.searchParams.get("state") || "/";
      if (!code) return html(loginPage("OAuth failed: no authorization code from Google."), 400);
      const clientId = env.GOOGLE_CLIENT_ID, clientSecret = env.GOOGLE_CLIENT_SECRET;
      if (!clientId || !clientSecret) return html(loginPage("Google OAuth secrets missing on server."), 500);
      try {
        const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
          method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" },
          body: new URLSearchParams({ code, client_id: clientId, client_secret: clientSecret,
            redirect_uri: `https://${url.host}/auth/google/callback`, grant_type: "authorization_code" }),
        });
        const tokenData = await tokenRes.json();
        if (!tokenData.id_token) return html(loginPage(`Google token exchange failed: ${JSON.stringify(tokenData)}`), 400);
        const parts = tokenData.id_token.split(".");
        if (parts.length < 2) return html(loginPage("Malformed ID token from Google."), 400);
        const payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/")));
        if (!payload.email) return html(loginPage("No email in the Google account."), 400);
        const token = await mintToken(payload.email, env);
        const cookieValue = `trainlint_token=${encodeURIComponent(token)}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000`;
        return new Response("", { status: 302, headers: { "Location": state, "Set-Cookie": cookieValue } });
      } catch (e) {
        return html(loginPage(`Google login error: ${e.message}`), 500);
      }
    }

    // ---- share: INVITE a customer email to view MY namespace (upload-token auth) -----------------
    // Consent-required: this writes only a PENDING invite (_pending/<email>). It does NOT grant
    // access — the invitee must explicitly accept (GET /accept?ns=...) before the share goes live.
    // Writing an active _shares record here would let an attacker bind their ns onto a victim's
    // email and hijack the victim's implicit viewer routes (account takeover), so we never do.
    if (path === "/api/share" && request.method === "POST") {
      const auth = request.headers.get("Authorization") || "";
      const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
      const ident = await verifyToken(token, env);
      if (!ident) return text("invalid or missing upload token", 401);

      let payload;
      try { payload = await request.json(); } catch { return text("bad json body", 400); }
      const email = String(payload.email || "").trim().toLowerCase();
      if (!email || !email.includes("@") || email.length > 254) return text("bad email", 400);

      const pendingKey = `_pending/${email}`;
      let record = { ns: [] };
      const existing = await env.REPORTS.get(pendingKey);
      if (existing) {
        try { record = await existing.json(); } catch { record = { ns: [] }; }
      }
      if (!Array.isArray(record.ns)) record.ns = [];
      if (!record.ns.includes(ident.ns)) record.ns.push(ident.ns);
      await env.REPORTS.put(pendingKey, JSON.stringify(record), { httpMetadata: { contentType: "application/json" } });
      return text(`invited ${email}`, 200);
    }

    // ---- Browser read auth -----------------------------------------------------------------------
    // Attempt 1: Cloudflare Access JWT (best practice if Zero Trust is active)
    let who = await verifyAccessJwt(request, env);
    
    // Attempt 2: Cookie-based signed trainlint token (fallback for zero-trust-less setup)
    if (!who) {
      const cookieToken = getCookie(request, "trainlint_token");
      if (cookieToken) {
        const ident = await verifyToken(cookieToken, env);
        if (ident) {
          who = { email: ident.email, ns: ident.ns };  // carry AUTHORITATIVE ns (raw tokens: sha256(token) != sha256(email))
        }
      }
    }

    // ---- Browser read: Magic Link / Google Pairing Route -----------------------------------------
    const queryToken = url.searchParams.get("token");
    if (queryToken && request.method === "GET" && path === "/link") {
      const project = url.searchParams.get("project") || "";
      
      // 1. If not authenticated with a REAL (Google) identity yet, redirect to Google Login with
      // state. An anonymous magic-link cookie (…@trainlint.local) does NOT count — pairing to it
      // would bind the token to the anonymous ns instead of the user's Google account (the bug that
      // left the agent stranded on a third namespace). Force a real Google login before pairing.
      if (!who || String(who.email).endsWith("@trainlint.local")) {
        const clientId = env.GOOGLE_CLIENT_ID;
        const redirectUri = `https://${url.host}/auth/google/callback`;
        if (!clientId) {
          return html(loginPage("Google OAuth is not configured on this host. GOOGLE_CLIENT_ID missing."), 500);
        }
        
        // Pass the token and optional project in the state so Google redirects it back to us
        const state = encodeURIComponent(`/link?token=${queryToken}${project ? `&project=${encodeURIComponent(project)}` : ""}`);
        const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?` +
          `response_type=code` +
          `&client_id=${encodeURIComponent(clientId)}` +
          `&redirect_uri=${encodeURIComponent(redirectUri)}` +
          `&scope=${encodeURIComponent("openid email")}` +
          `&state=${state}` +
          `&prompt=select_account`;
          
        return new Response("", { status: 302, headers: { "Location": authUrl } });
      }

      // 2. If authenticated, we link this token to the logged-in email
      const cleanToken = queryToken.trim();
      if (!/^[a-f0-9]{32}$/i.test(cleanToken)) {
        return html(loginPage("Invalid token format for linking."), 400);
      }

      const userNs = await nsForEmail(who.email);
      
      // Calculate original anonymous namespace
      const enc = new TextEncoder();
      const digest = await crypto.subtle.digest("SHA-256", enc.encode(cleanToken));
      const bytesToHex = (bytes) => [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
      const anonNs = bytesToHex(digest).slice(0, 32);

      // Save pairing mapping: _tokens/<token> -> { email }
      await env.REPORTS.put(`_tokens/${cleanToken}`, JSON.stringify({ email: who.email }));

      // Migrate existing reports from anonymous namespace to user's private namespace
      const listed = await env.REPORTS.list({ prefix: `${anonNs}/` });
      let migratedCount = 0;
      for (const o of listed.objects) {
        const rest = o.key.slice(anonNs.length + 1);
        const obj = await env.REPORTS.get(o.key);
        if (obj) {
          await env.REPORTS.put(`${userNs}/${rest}`, obj.body, { httpMetadata: { contentType: "text/html; charset=utf-8" } });
          await env.REPORTS.delete(o.key);
          migratedCount++;
        }
      }

      // Set cookie and redirect to clean dashboard
      const cookieValue = `trainlint_token=${encodeURIComponent(cleanToken)}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000`;
      
      const htmlEscape = (str) => String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
      const safeProject = htmlEscape(project);
      
      const buttonAndRedirect = project 
        ? `<p id="countdown">Redirecting to your <strong>${safeProject}</strong> report in <span id="secs" style="font-weight:bold;color:#2563eb">3</span> seconds...</p>` +
          `<a href="/${encodeURIComponent(project)}.html" style="background:#10b981;margin-bottom:12px;display:block">View ${safeProject} Report Now</a>` +
          `<a href="/" style="background:#6b7280;display:block">Go to Dashboard</a>` +
          `<script>` +
          `  let s = 3;` +
          `  const timer = setInterval(() => {` +
          `    s--;` +
          `    document.getElementById('secs').innerText = s;` +
          `    if (s <= 0) {` +
          `      clearInterval(timer);` +
          `      location.href = "/${encodeURIComponent(project)}.html";` +
          `    }` +
          `  }, 1000);` +
          `</script>`
        : `<p>All current and future reports from this machine will now automatically stream privately into your account dashboard.</p>` +
          `<a href="/">Go to Dashboard</a>`;

      return html(
        `<!doctype html><meta charset=utf-8><title>Pairing Success - trainlint</title>` +
        `<style>body{font:15px/1.5 system-ui,sans-serif;background:#f3f4f6;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}` +
        `.card{background:#fff;padding:36px;border-radius:12px;box-shadow:0 4px 6px -1px rgba(0,0,0,0.1);width:100%;max-width:420px;text-align:center}` +
        `h2{color:#10b981;margin-top:0;margin-bottom:12px;font-size:24px}` +
        `p{color:#374151;font-size:14px;margin-bottom:24px;line-height:1.6}` +
        `code{background:#f3f4f6;padding:3px 6px;border-radius:4px;word-break:break-all;font-family:monospace;font-size:13px;display:block;margin:12px 0}` +
        `a{display:inline-block;padding:10px 24px;color:#fff;text-decoration:none;border-radius:6px;font-weight:600;font-size:14px;transition:background 0.2s}` +
        `a[href^="/"][href$=".html"]:hover{background:#059669}` +
        `a[href="/"]{background:#2563eb}` +
        `a[href="/"]:hover{background:#1d4ed8}` +
        `a[href="/"] + a[href="/"]{background:#6b7280}` +
        `a[href="/"] + a[href="/"]:hover{background:#4b5563}</style>` +
        `<div class="card">` +
        `  <h2>🔗 Machine Paired Successfully!</h2>` +
        `  <p>We have successfully linked this local machine to your Google Account:</p>` +
        `  <strong>${who.email}</strong>` +
        `  ${migratedCount > 0 ? `<p style="color:#059669;font-weight:500;margin-top:12px">✨ Automatically migrated ${migratedCount} report(s) into your dashboard!</p>` : ""}` +
        `  <div style="margin-top:24px">${buttonAndRedirect}</div>` +
        `</div>`,
        200,
        { "Set-Cookie": cookieValue }
      );
    }

    // Direct magic login route using URL query: /?token=<token>
    if (queryToken && request.method === "GET") {
      const ident = await verifyToken(queryToken, env);
      if (ident) {
        const cleanUrl = new URL(request.url);
        cleanUrl.searchParams.delete("token");
        const cookieValue = `trainlint_token=${encodeURIComponent(queryToken)}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000`;
        return new Response("", {
          status: 302,
          headers: {
            "Location": cleanUrl.pathname + cleanUrl.search,
            "Set-Cookie": cookieValue
          }
        });
      }
    }

    // Auth Login Gate (only if not authenticated)
    if (!who) {
      // Serve login page for any other route
      return html(loginPage());
    }

    // Logout route
    if (path === "/logout") {
      return new Response("", {
        status: 302,
        headers: {
          "Location": "/login",
          "Set-Cookie": "trainlint_token=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0"
        }
      });
    }

    const ns = who.ns || await nsForEmail(who.email);  // token path already carries the authoritative ns

    // ---- accept: the invitee consents, promoting a PENDING invite into an ACTIVE share -----------
    // Requires an authenticated viewer (who.email above). Moves ns from _pending/<who.email> into
    // _shares/<who.email> (create/append, uniq), clears it from pending, then redirects into the
    // now-authorized explicit-ns relay. A ns not in the caller's pending list is rejected (400).
    if (path === "/accept" && request.method === "GET") {
      const acceptNs = (url.searchParams.get("ns") || "").toLowerCase();
      if (!/^[a-f0-9]{32}$/.test(acceptNs)) return text("bad ns", 400);
      const emailKey = who.email.trim().toLowerCase();

      const pendingKey = `_pending/${emailKey}`;
      let pending = { ns: [] };
      const pendingObj = await env.REPORTS.get(pendingKey);
      if (pendingObj) {
        try { pending = await pendingObj.json(); } catch { pending = { ns: [] }; }
      }
      if (!Array.isArray(pending.ns)) pending.ns = [];
      if (!pending.ns.includes(acceptNs)) return text("no such pending invitation", 400);

      const shareKey = `_shares/${emailKey}`;
      let shares = { ns: [] };
      const shareObj = await env.REPORTS.get(shareKey);
      if (shareObj) {
        try { shares = await shareObj.json(); } catch { shares = { ns: [] }; }
      }
      if (!Array.isArray(shares.ns)) shares.ns = [];
      if (!shares.ns.includes(acceptNs)) shares.ns.push(acceptNs);
      await env.REPORTS.put(shareKey, JSON.stringify(shares), { httpMetadata: { contentType: "application/json" } });

      pending.ns = pending.ns.filter((n) => n !== acceptNs);
      if (pending.ns.length) {
        await env.REPORTS.put(pendingKey, JSON.stringify(pending), { httpMetadata: { contentType: "application/json" } });
      } else {
        await env.REPORTS.delete(pendingKey);
      }

      return new Response("", { status: 303, headers: { "Location": `/r/${acceptNs}/` } });
    }

    // ---- live relay viewer routes ------------------------------------------------------------------

    // explicit-ns variant for multi-share: /r/<ns>/<anything> relays "/<anything>" to that ns's
    // agent, after checking the share record grants who.email that namespace (own ns always allowed).
    const rm = path.match(/^\/r\/([a-f0-9]{32})(\/.*)?$/);
    if (rm && (request.method === "GET" || request.method === "POST")) {
      const targetNs = rm[1];
      const rest = (rm[2] || "/") + url.search;
      if (targetNs !== ns && !isAdmin(who.email, env)) {
        const granted = await grantedNamespaces(who.email, env);
        if (!granted.includes(targetNs)) return text("not found", 404);
      }
      // Inline edits are OWNER-ONLY even through this shared-viewer relay. A granted viewer (or admin)
      // reaches this line for another operator's ns — they may READ the report but must NEVER write
      // back into that operator's substrate. The report page fetch('edit') resolves to /r/<ns>/edit
      // when viewed here, so gate the /edit sub-path with the same rule as the /edit routes below:
      // allowed only when this IS the caller's own ns. (Returns the 403 the editor UI expects.)
      if ((rm[2] || "/") === "/edit" && targetNs !== ns) return text("editing is owner-only", 403);
      if (!(await agentConnected(targetNs, env))) return text("operator offline", 503);
      return relayToAgent(targetNs, rest, request, env);
    }

    // ---- in-report inline edit: OWNER-ONLY write relay -------------------------------------------
    // Two shapes, one rule. POST /edit targets the caller's OWN ns. POST /<email>/edit resolves
    // targetNs = nsForEmail(email) and is allowed ONLY when it equals the caller's own ns.
    //
    // This is DELIBERATELY STRICTER than every read route above: the /r/<ns>/ and /<email>/<report>
    // routes let an admin or an accepted-share viewer READ another operator's report, but an edit
    // writes back into that operator's LOCAL substrate files (plan/goal/purpose/glossary/…). Silently
    // clobbering another operator's research is the one catastrophe we forbid outright — so there is
    // NO admin bypass and NO shared-grant bypass here: targetNs must === the caller's own ns, full stop.
    //
    // Placement: this sits BEFORE the two-segment /<email>/<seg> matcher because "edit" is neither a
    // .html (so its report regex won't catch it) nor "chat"/"login" (so its isControl branch won't
    // either) — without an explicit branch /<email>/edit would fall through to 404. It sits AFTER the
    // /r/<ns>/ handler (whose own /edit sub-path we owner-gated above) so shared-viewer relay keeps
    // priority. The flat /edit is a single segment, so the /<email>/<seg> matcher never sees it.
    // On success we relay to that ns's live agent exactly like /chat (method/body/content-type
    // forwarded, always to the backend's /edit); no live agent → 503.
    if (path === "/edit" && request.method === "POST") {
      // Flat: the target is, by construction, the caller's own ns — inherently owner-only.
      const targetNs = ns;
      if (!(await agentConnected(targetNs, env))) return text("operator offline", 503);
      return relayToAgent(targetNs, `/edit${url.search}`, request, env);
    }
    const editRoute = path.match(/^\/([^\/]+)\/edit$/);
    if (editRoute && request.method === "POST") {
      let email = "";
      try { email = decodeURIComponent(editRoute[1]).trim().toLowerCase(); } catch { email = ""; }
      if (email.includes("@") && email.length <= 254 && !email.includes("/")) {
        const targetNs = await nsForEmail(email);
        // OWNER-ONLY: not admin, not a shared grant. Anyone but the owner gets 403 (the editor UI
        // catches this to drop into read-only mode).
        if (targetNs !== ns) return text("editing is owner-only", 403);
        if (!(await agentConnected(targetNs, env))) return text("operator offline", 503);
        return relayToAgent(targetNs, `/edit${url.search}`, request, env);
      }
    }

    // ---- per-user cross-namespace route: /<email>/<project>.html and POST /<email>/(chat|login) ----
    // <user> is a URL-encoded email → targetNs = nsForEmail(email). TWO segments only, so it never
    // shadows the flat /<project>.html (single segment) nor the exact /chat, /login, / routes; and it
    // sits after the /r/<ns>/ handler so that keeps priority. Access: own ns OR admin OR an accepted
    // share for targetNs. The ns here is EXPLICIT (no single-live inference) — relay live, else R2.
    const userRoute = path.match(/^\/([^\/]+)\/([^\/]+)$/);
    if (userRoute) {
      let email = "";
      try { email = decodeURIComponent(userRoute[1]).trim().toLowerCase(); } catch { email = ""; }
      const seg = userRoute[2];
      const reportMatch = seg.match(/^([A-Za-z0-9._-]+)\.(?:slides\.html|html)$/);
      const isReport = request.method === "GET" && !!reportMatch && SAFE_NAME.test(reportMatch[1]);
      const isControl = request.method === "POST" && (seg === "chat" || seg === "login");
      const emailOk = email.includes("@") && email.length <= 254 && !email.includes("/");
      if (emailOk && (isReport || isControl)) {
        const targetNs = await nsForEmail(email);
        const allowed = targetNs === ns
          || isAdmin(who.email, env)
          || (await grantedNamespaces(who.email, env)).includes(targetNs);
        if (!allowed) return text("not authorized for this user", 403);
        const rel = `/${seg}${url.search}`;
        if (await agentConnected(targetNs, env)) return relayToAgent(targetNs, rel, request, env);
        if (isReport) {
          const obj = await env.REPORTS.get(`${targetNs}/${seg}`);
          if (obj) return new Response(obj.body, { headers: { "content-type": "text/html; charset=utf-8" } });
          return text("no such report", 404);
        }
        return text("operator offline", 503);
      }
    }

    // /chat and /login: relay live to the resolved agent (own ns if its agent is connected, else
    // the single shared ns with a live agent). POST bodies are b64-forwarded verbatim.
    if ((path === "/chat" || path === "/login") && (request.method === "GET" || request.method === "POST")) {
      const liveNs = await resolveLiveNs(ns, who.email, env);
      if (!liveNs) return text("operator offline", 503);
      return relayToAgent(liveNs, path + url.search, request, env);
    }

    // index: the caller's own reports + their upload token (onboarding = copy this token)
    if (path === "/" || path === "/index.html") {
      // Admin dashboard: enumerate every operator from the durable R2 _agents/<ns> registry so an
      // operator can debug any user's projects. Live vs last-seen comes from agentConnected().
      if (isAdmin(who.email, env)) {
        const agents = await env.REPORTS.list({ prefix: "_agents/" });
        const cards = [];
        for (const o of agents.objects) {
          const recNs = o.key.slice("_agents/".length);
          let rec = {};
          try { const obj = await env.REPORTS.get(o.key); if (obj) rec = await obj.json(); } catch {}
          const email = String(rec.email || "unknown");
          const projects = Array.isArray(rec.projects) ? rec.projects : [];
          const live = await agentConnected(recNs, env);
          const status = live
            ? `<span style="color:#059669;font-weight:600">🟢 live</span>`
            : `<span style="color:#9ca3af">⚪ offline</span>`;
          const projLinks = projects.length
            ? projects.map((p) =>
                `<a href="/${encodeURIComponent(email)}/${encodeURIComponent(p)}.html">${htmlEscape(p)}</a>` +
                ` &middot; <a href="/${encodeURIComponent(email)}/${encodeURIComponent(p)}.slides.html">slides</a>`
              ).join("<br>")
            : "<em>no projects</em>";
          cards.push(
            `<li style="margin-bottom:16px"><b>${htmlEscape(email)}</b> ${status}` +
            `<div style="color:#9ca3af;font-size:12px">ns ${htmlEscape(recNs.slice(0, 8))}…</div>` +
            `<div style="margin-top:4px">${projLinks}</div></li>`
          );
        }
        return html(
          `<!doctype html><meta charset=utf-8><title>trainlint · admin</title>` +
          `<style>body{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;background:#f9fafb;color:#111827}` +
          `a{color:#2563eb;text-decoration:none}a:hover{text-decoration:underline}ul{list-style:none;padding:0}` +
          `.header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #e5e7eb;padding-bottom:12px;margin-bottom:24px}` +
          `.btn-logout{background:#ef4444;color:#fff;padding:4px 10px;border-radius:6px;font-size:13px;font-weight:500;text-decoration:none}</style>` +
          `<div class="header"><h1>All operators (admin)</h1><a href="/logout" class="btn-logout">Logout</a></div>` +
          `<p>Signed in as <b>${htmlEscape(who.email)}</b> · admin view of every registered operator.</p>` +
          `<ul>${cards.join("") || "<li><em>no operators have connected yet</em></li>"}</ul>`
        );
      }

      const listed = await env.REPORTS.list({ prefix: `${ns}/` });
      const projects = new Set();
      for (const o of listed.objects) {
        const rest = o.key.slice(ns.length + 1);
        if (rest.endsWith(".slides.html")) projects.add(rest.slice(0, -".slides.html".length));
        else if (rest.endsWith(".html")) projects.add(rest.slice(0, -".html".length));
      }
      
      const isRawToken = who.email.endsWith("@trainlint.local");
      const activeToken = isRawToken ? getCookie(request, "trainlint_token") : await mintToken(who.email, env);
      const userDisplay = isRawToken ? "Local Secure Token (Anonymous)" : who.email;

      const links = [...projects].sort().map(
        (p) => `<li><a href="/${encodeURIComponent(p)}.html">${p}</a>` +
               ` &middot; <a href="/${encodeURIComponent(p)}.slides.html">slides</a></li>`
      ).join("") || "<li><em>no reports yet — configure the plugin with your token below</em></li>";

      // shared namespaces with a live agent → links into the explicit-ns relay
      const grantedList = (await grantedNamespaces(who.email, env)).filter((g) => g !== ns);
      const sharedLinks = [];
      for (const g of grantedList) {
        if (await agentConnected(g, env)) {
          sharedLinks.push(`<li><a href="/r/${g}/">operator ${g.slice(0, 8)}…</a> <span style="color:#059669;font-weight:600">&#9679; live</span></li>`);
        }
      }
      const sharedSection = sharedLinks.length
        ? `<h2 style="font-size:17px;margin-top:28px">Shared with you (live)</h2><ul>${sharedLinks.join("")}</ul>`
        : "";

      // pending invitations awaiting THIS viewer's explicit consent (from _pending/<email>)
      let pendingNs = [];
      const pendingObj = await env.REPORTS.get(`_pending/${who.email.trim().toLowerCase()}`);
      if (pendingObj) {
        try { const rec = await pendingObj.json(); if (Array.isArray(rec.ns)) pendingNs = rec.ns; } catch {}
      }
      const pendingSection = pendingNs.length
        ? `<h2 style="font-size:17px;margin-top:28px">Pending invitations</h2><ul>` +
          pendingNs.map((g) => `<li>operator ${g.slice(0, 8)}… &middot; <a href="/accept?ns=${g}">Accept</a></li>`).join("") +
          `</ul>`
        : "";

      // SECURITY: never render the raw upload token / magic link in the dashboard HTML. A relayed
      // cross-tenant report page runs same-origin and could fetch('/') to scrape it -> ns takeover.
      // The token is a machine credential the plugin already holds; onboarding is the /link pairing.
      const tokenDisplay = isRawToken
        ? `<div class=tok><b>Anonymous session.</b> To claim these reports under your Google account, run the plugin — it prints a one-click pairing link (<code>/link?token=…</code>). The token is never shown here.</div>`
        : `<div class=tok><b>You're signed in.</b> Your local plugin already holds your upload token — reports it renders appear here automatically. The token is a machine secret and is never displayed.</div>`;

      return html(
        `<!doctype html><meta charset=utf-8><title>trainlint reports</title>` +
        `<style>body{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;background:#f9fafb;color:#111827}` +
        `a{color:#2563eb;text-decoration:none}a:hover{text-decoration:underline}` +
        `code{background:#f3f4f6;padding:2px 6px;border-radius:4px;word-break:break-all;font-family:monospace}` +
        `.tok{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 14px;margin-top:24px}` +
        `.header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #e5e7eb;padding-bottom:12px;margin-bottom:24px}` +
        `.btn-logout{background:#ef4444;color:#fff;padding:4px 10px;border-radius:6px;font-size:13px;font-weight:500;text-decoration:none}</style>` +
        `<div class="header">` +
        `  <h1>Your trainlint reports</h1>` +
        `  <a href="/logout" class="btn-logout">Logout</a>` +
        `</div>` +
        `<p>Signed in as <b>${userDisplay}</b>.</p><ul>${links}</ul>` +
        sharedSection +
        pendingSection +
        tokenDisplay
      );
    }

    // serve one report — relay live when an agent is resolvable (own ns first, else the single
    // shared live ns); otherwise fall through to R2 in the caller's OWN namespace, and report the
    // operator offline when neither source has it.
    const m = path.match(/^\/([A-Za-z0-9._-]+\.(?:slides\.html|html))$/);
    if (m && request.method === "GET") {
      const liveNs = await resolveLiveNs(ns, who.email, env);
      if (liveNs) return relayToAgent(liveNs, `/${m[1]}${url.search}`, request, env);
      const obj = await env.REPORTS.get(`${ns}/${m[1]}`);
      if (!obj) return text("operator offline", 503);
      return new Response(obj.body, { headers: { "content-type": "text/html; charset=utf-8" } });
    }

    return text("not found", 404);
  },
};

// ---- live relay helpers ---------------------------------------------------------------------------

function bytesToB64(bytes) {
  let bin = "";
  const CHUNK = 8192; // avoid call-stack limits on large bodies
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

// share lookup: R2 _shares/<lowercased viewer email> -> { ns: [granted namespaces] }
async function grantedNamespaces(email, env) {
  try {
    const obj = await env.REPORTS.get(`_shares/${String(email).trim().toLowerCase()}`);
    if (!obj) return [];
    const record = await obj.json();
    return Array.isArray(record.ns) ? record.ns : [];
  } catch {
    return [];
  }
}

async function agentConnected(nsTarget, env) {
  try {
    const hub = env.AGENTS.get(env.AGENTS.idFromName(nsTarget));
    const res = await hub.fetch("https://agent-hub/status");
    if (!res.ok) return false;
    const data = await res.json();
    return !!data.connected;
  } catch {
    return false;
  }
}

// ns resolution for implicit viewer routes: own ns if its agent is connected; else if EXACTLY ONE
// shared ns has a live agent, use it; else null (caller falls back to R2 / "operator offline").
async function resolveLiveNs(ownNs, email, env) {
  if (await agentConnected(ownNs, env)) return ownNs;
  const granted = await grantedNamespaces(email, env);
  const live = [];
  for (const g of granted) {
    if (g === ownNs) continue;
    if (await agentConnected(g, env)) live.push(g);
  }
  return live.length === 1 ? live[0] : null;
}

// Relay one viewer request to nsTarget's agent. Only method/path/body/content-type cross the relay
// — hop-by-hop, cookie and authorization headers are dropped by construction (the worker already
// authenticated the viewer; the local backend trusts x-trainlint-relay added on the agent side).
// The agent reply's status and content-type are passed back verbatim by the AgentHub.
async function relayToAgent(nsTarget, relPath, request, env) {
  const method = request.method === "POST" ? "POST" : "GET";
  let bodyB64 = null;
  let ct = null;
  if (method === "POST") {
    bodyB64 = bytesToB64(new Uint8Array(await request.arrayBuffer()));
    ct = request.headers.get("content-type") || null;
  }
  const hub = env.AGENTS.get(env.AGENTS.idFromName(nsTarget));
  return hub.fetch("https://agent-hub/relay", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ method, path: relPath, body: bodyB64, ct }),
  });
}

function loginPage(error = "") {
  return `<!doctype html><meta charset=utf-8><title>Login - trainlint reports</title>` +
    `<style>body{font:15px/1.5 system-ui,sans-serif;background:#f3f4f6;display:flex;align-items:center;justify-content:center;height:100vh;margin:0}` +
    `.card{background:#fff;padding:32px;border-radius:12px;box-shadow:0 4px 6px -1px rgba(0,0,0,0.1), 0 2px 4px -1px rgba(0,0,0,0.06);width:100%;max-width:380px}` +
    `h2{margin-top:0;margin-bottom:4px;font-size:22px;font-weight:700;color:#111827;text-align:center}` +
    `p{color:#4b5563;font-size:14px;margin-top:0;margin-bottom:28px;text-align:center}` +
    `.btn-google{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;padding:12px;background:#fff;color:#374151;border:1px solid #e5e7eb;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;text-decoration:none;box-sizing:border-box;margin-bottom:12px;transition:background 0.2s, border-color 0.2s;box-shadow:0 1px 2px 0 rgba(0,0,0,0.05)}` +
    `.btn-google:hover{background:#f9fafb;border-color:#d1d5db}` +
    `.error{color:#ef4444;font-size:13px;margin-bottom:16px;background:#fef2f2;border:1px solid #fca5a5;padding:10px;border-radius:6px}` +
    `.notice{font-size:12px;color:#9ca3af;text-align:center;line-height:1.4;margin-top:24px;border-top:1px solid #f3f4f6;padding-top:16px}</style>` +
    `<div class="card">` +
    `  <h2>Trainlint Portal</h2>` +
    `  <p>Secure Multi-Tenant Report Hosting</p>` +
    `  ${error ? `<div class="error">${error}</div>` : ""}` +
    `  ` +
    `  <a href="/auth/google" class="btn-google">` +
    `    <svg width="18" height="18" viewBox="0 0 18 18"><path fill="#4285F4" d="M17.64 9.2c0-.63-.06-1.25-.16-1.84H9v3.47h4.84c-.21 1.12-.84 2.07-1.79 2.73v2.27h2.9c1.7-2.67 2.69-6.61 2.69-10.63z"/><path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.27c-.8.54-1.83.86-3.06.86-2.34 0-4.33-1.58-5.04-3.71H.93v2.33C2.42 15.61 5.48 18 9 18z"/><path fill="#FBBC05" d="M3.96 10.7c-.18-.54-.28-1.12-.28-1.7s.1-1.16.28-1.7V4.97H.93C.33 6.18 0 7.54 0 9s.33 2.82.93 4.03l3.03-2.33z"/><path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35L15 2.1C13.46.66 11.42 0 9 0 5.48 0 2.42 2.39.93 5.4l3.03 2.33c.71-2.13 2.7-3.71 5.04-3.71z"/></svg>` +
    `    Sign in with Google` +
    `  </a>` +
    `  ` +
    `  <div class="notice">` +
    `    Need access? Sign in with an authorized Google Account, or use a local token generated by running trainlint.` +
    `  </div>` +
    `</div>`;
}
