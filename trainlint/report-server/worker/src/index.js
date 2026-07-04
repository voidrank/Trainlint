// index.js — the entire server side of the multi-tenant report service.
//
// One Cloudflare Worker, three concerns, every one scoped to the caller's namespace so cross-tenant
// reads/writes are impossible by construction:
//   POST /api/upload   — machine upload from the plugin. Auth = HMAC upload token (NOT Access; a
//                        headless POST can't do the browser login). Writes <ns>/<project>.<kind>.html.
//   GET  /             — the logged-in user's index: lists THEIR reports + shows THEIR upload token.
//   GET  /<name>.html  — streams one of the caller's reports from R2 (404 if it isn't in their ns).
//
// Access protects the GET routes. If Access is not configured (or billing blocks it), we support
// a self-contained Password-to-Token login gate to protect GET routes. The namespace is always
// derived server-side from a verified credential.

import { verifyAccessJwt, verifyToken, mintToken, nsForEmail } from "./auth.js";

const SAFE_NAME = /^[A-Za-z0-9._-]{1,128}$/; // project/file names we accept into a key

function html(body, status = 200, headers = {}) {
  return new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8", ...headers } });
}
function text(body, status = 200) {
  return new Response(body, { status, headers: { "content-type": "text/plain; charset=utf-8" } });
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

    // ---- Browser read auth -----------------------------------------------------------------------
    // Attempt 1: Cloudflare Access JWT (best practice if Zero Trust is active)
    let who = await verifyAccessJwt(request, env);
    
    // Attempt 2: Cookie-based signed trainlint token (fallback for zero-trust-less setup)
    if (!who) {
      const cookieToken = getCookie(request, "trainlint_token");
      if (cookieToken) {
        const ident = await verifyToken(cookieToken, env);
        if (ident) {
          who = { email: ident.email };
        }
      }
    }

    // ---- Browser read: Magic Link / Google Pairing Route -----------------------------------------
    const queryToken = url.searchParams.get("token");
    if (queryToken && request.method === "GET" && path === "/link") {
      const project = url.searchParams.get("project") || "";
      
      // 1. If not authenticated with Google yet, redirect to Google Login with state
      if (!who) {
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
      const cookieValue = `trainlint_token=${encodeURIComponent(cleanToken)}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=31536000`;
      
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
        const cookieValue = `trainlint_token=${encodeURIComponent(queryToken)}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=31536000`;
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
      // 1. Google OAuth Authentication Routes ------------------------------------------------------
      if (path === "/auth/google") {
        const clientId = env.GOOGLE_CLIENT_ID;
        const redirectUri = `https://${url.host}/auth/google/callback`;
        if (!clientId) {
          return html(loginPage("Google OAuth is not configured on this host. GOOGLE_CLIENT_ID missing."), 500);
        }
        
        // If state is not provided, default to redirecting to root "/"
        const state = url.searchParams.get("state") || "/";
        const authUrl = `https://accounts.google.com/o/oauth2/v2/auth?` +
          `response_type=code` +
          `&client_id=${encodeURIComponent(clientId)}` +
          `&redirect_uri=${encodeURIComponent(redirectUri)}` +
          `&scope=${encodeURIComponent("openid email")}` +
          `&state=${encodeURIComponent(state)}` +
          `&prompt=select_account`;
          
        return new Response("", { status: 302, headers: { "Location": authUrl } });
      }

      if (path === "/auth/google/callback") {
        const code = url.searchParams.get("code");
        const state = url.searchParams.get("state") || "/";
        if (!code) return html(loginPage("OAuth failed: authorization code not returned from Google."), 400);

        const clientId = env.GOOGLE_CLIENT_ID;
        const clientSecret = env.GOOGLE_CLIENT_SECRET;
        const redirectUri = `https://${url.host}/auth/google/callback`;

        if (!clientId || !clientSecret) {
          return html(loginPage("Google OAuth secrets missing on server."), 500);
        }

        try {
          // Exchange code for ID token
          const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
            method: "POST",
            headers: { "Content-Type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({
              code,
              client_id: clientId,
              client_secret: clientSecret,
              redirect_uri: redirectUri,
              grant_type: "authorization_code"
            })
          });

          const tokenData = await tokenRes.json();
          if (!tokenData.id_token) {
            return html(loginPage(`Google Token Exchange failed: ${JSON.stringify(tokenData)}`), 400);
          }

          // Decode JWT ID Token payload (unsafe client-side is fine here since it came directly from Google HTTPS)
          const parts = tokenData.id_token.split(".");
          if (parts.length < 2) return html(loginPage("Malformed ID token returned from Google."), 400);
          
          const payloadRaw = atob(parts[1].replace(/-/g, "+").replace(/_/g, "/"));
          const payload = JSON.parse(payloadRaw);
          const email = payload.email;

          if (!email) return html(loginPage("No email address returned from Google account."), 400);

          // Success: Mint trainlint token and store in cookie
          const token = await mintToken(email, env);
          const cookieValue = `trainlint_token=${encodeURIComponent(token)}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=31536000`;
          
          // Redirect to the original path saved in state (e.g. /link?token=...)
          return new Response("", {
            status: 302,
            headers: {
              "Location": state,
              "Set-Cookie": cookieValue
            }
          });
        } catch (e) {
          return html(loginPage(`Google Login error: ${e.message}`), 500);
        }
      }

      // Serve login page for any other route
      return html(loginPage());
    }

    // Logout route
    if (path === "/logout") {
      return new Response("", {
        status: 302,
        headers: {
          "Location": "/login",
          "Set-Cookie": "trainlint_token=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0"
        }
      });
    }

    const ns = await nsForEmail(who.email);

    // index: the caller's own reports + their upload token (onboarding = copy this token)
    if (path === "/" || path === "/index.html") {
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
      
      const tokenDisplay = isRawToken
        ? `<div class=tok><b>Your Magic Link</b> — keep this secret. Use it to bookmark or open your reports from other browsers:<p><code>https://${url.host}/?token=${activeToken}</code></p></div>`
        : `<div class=tok><b>Your upload token</b> — set it in the plugin as <code>TRAINLINT_REPORT_TOKEN</code> so your reports appear here:<p><code>${activeToken}</code></p></div>`;

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
        tokenDisplay
      );
    }

    // serve one report — scoped to the caller's namespace, 404 if it isn't theirs
    const m = path.match(/^\/([A-Za-z0-9._-]+\.(?:slides\.html|html))$/);
    if (m && request.method === "GET") {
      const obj = await env.REPORTS.get(`${ns}/${m[1]}`);
      if (!obj) return text("not found", 404);
      return new Response(obj.body, { headers: { "content-type": "text/html; charset=utf-8" } });
    }

    return text("not found", 404);
  },
};

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
