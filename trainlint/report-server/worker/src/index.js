// index.js — the entire server side of the multi-tenant report service.
//
// One Cloudflare Worker, three concerns, every one scoped to the caller's namespace so cross-tenant
// reads/writes are impossible by construction:
//   POST /api/upload   — machine upload from the plugin. Auth = HMAC upload token (NOT Access; a
//                        headless POST can't do the browser login). Writes <ns>/<project>.<kind>.html.
//   GET  /             — the logged-in user's index: lists THEIR reports + shows THEIR upload token.
//   GET  /<name>.html  — streams one of the caller's reports from R2 (404 if it isn't in their ns).
//
// Access protects the GET routes (configure the Access app to cover /* but BYPASS /api/* — the token
// guards uploads). The namespace is always derived server-side from a verified credential.

import { verifyAccessJwt, verifyToken, mintToken, nsForEmail } from "./auth.js";

const SAFE_NAME = /^[A-Za-z0-9._-]{1,128}$/; // project/file names we accept into a key

function html(body, status = 200) {
  return new Response(body, { status, headers: { "content-type": "text/html; charset=utf-8" } });
}
function text(body, status = 200) {
  return new Response(body, { status, headers: { "content-type": "text/plain; charset=utf-8" } });
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

    // ---- everything below is a BROWSER read: require a verified Access identity -------------------
    const who = await verifyAccessJwt(request, env);
    if (!who) return text("not authenticated (Cloudflare Access required)", 403);
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
      const token = await mintToken(who.email, env);
      const links = [...projects].sort().map(
        (p) => `<li><a href="/${encodeURIComponent(p)}.html">${p}</a>` +
               ` &middot; <a href="/${encodeURIComponent(p)}.slides.html">slides</a></li>`
      ).join("") || "<li><em>no reports yet — configure the plugin with your token below</em></li>";
      return html(
        `<!doctype html><meta charset=utf-8><title>trainlint reports</title>` +
        `<style>body{font:15px/1.5 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px}` +
        `code{background:#f3f4f6;padding:2px 6px;border-radius:4px;word-break:break-all}` +
        `.tok{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 14px;margin-top:24px}</style>` +
        `<h1>Your trainlint reports</h1><p>Signed in as <b>${who.email}</b>.</p><ul>${links}</ul>` +
        `<div class=tok><b>Your upload token</b> — set it in the plugin as ` +
        `<code>TRAINLINT_REPORT_TOKEN</code> so your reports appear here:` +
        `<p><code>${token}</code></p></div>`
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
