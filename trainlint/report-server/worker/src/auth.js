// auth.js — the tenant-binding core (the load-bearing piece).
//
// Two credentials resolve to ONE namespace, so a machine upload and a browser view land in the same
// isolated tenant:
//   • Browser view  → Cloudflare Access injects a signed JWT (Cf-Access-Jwt-Assertion). We VERIFY it
//                     against Access's public keys (never trust the plaintext email header alone),
//                     then derive the namespace from the verified email.
//   • Machine upload → the plugin has no browser, so it can't do Access. It presents an HMAC upload
//                     token we minted for that same user; we verify the signature and read the
//                     namespace straight out of the (signed, tamper-proof) payload.
//
// The namespace is ALWAYS derived server-side (sha256 of the email) — never taken from client input —
// so cross-tenant access is impossible by construction. That is the isolation boundary the whole
// multi-tenant design rests on (see verify-isolation).

const enc = new TextEncoder();

function b64urlEncode(bytes) {
  let s = btoa(String.fromCharCode(...new Uint8Array(bytes)));
  return s.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
function b64urlToBytes(s) {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
function bytesToHex(bytes) {
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// namespace = sha256(lowercased email), hex. Deterministic + one-way, so keys never leak the email
// and both credential paths compute the identical prefix for the same person.
export async function nsForEmail(email) {
  const digest = await crypto.subtle.digest("SHA-256", enc.encode(String(email).trim().toLowerCase()));
  return bytesToHex(digest).slice(0, 32);
}

// ---- upload token: HMAC-SHA256 over a compact payload, minted for a logged-in user ----------------
async function hmacKey(secret) {
  return crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" },
    false, ["sign", "verify"]);
}

export async function mintToken(email, env) {
  const ns = await nsForEmail(email);
  const payload = b64urlEncode(enc.encode(JSON.stringify({ ns, email, iat: Math.floor(Date.now() / 1000) })));
  const key = await hmacKey(env.TOKEN_SIGNING_KEY);
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(payload));
  return `${payload}.${b64urlEncode(sig)}`;
}

// Returns { ns, email } for a valid token, else null. Constant-ish time: we let crypto.subtle.verify
// do the comparison rather than string-comparing signatures ourselves.
export async function verifyToken(token, env) {
  try {
    const [payload, sig] = String(token).split(".");
    if (!payload || !sig) return null;
    const key = await hmacKey(env.TOKEN_SIGNING_KEY);
    const ok = await crypto.subtle.verify("HMAC", key, b64urlToBytes(sig), enc.encode(payload));
    if (!ok) return null;
    const body = JSON.parse(new TextDecoder().decode(b64urlToBytes(payload)));
    if (!body.ns || !body.email) return null;
    return { ns: body.ns, email: body.email };
  } catch {
    return null;
  }
}

// ---- Cloudflare Access JWT verification (RS256 against the team's JWKS) ----------------------------
let _jwksCache = { at: 0, keys: null };

async function jwks(env) {
  const now = Date.now();
  if (_jwksCache.keys && now - _jwksCache.at < 3600_000) return _jwksCache.keys;
  const url = `https://${env.ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs`;
  const res = await fetch(url, { cf: { cacheTtl: 3600 } });
  const data = await res.json();
  _jwksCache = { at: now, keys: data.keys || [] };
  return _jwksCache.keys;
}

async function importRsaJwk(jwk) {
  return crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
}

// Verify the Access JWT and return { email } or null. Checks the signature against the JWKS, the
// audience (this Access application's AUD tag), and expiry. Never trusts the plaintext email header.
export async function verifyAccessJwt(request, env) {
  try {
    const jwt = request.headers.get("Cf-Access-Jwt-Assertion");
    if (!jwt) return null;
    const [h, p, s] = jwt.split(".");
    if (!h || !p || !s) return null;
    const header = JSON.parse(new TextDecoder().decode(b64urlToBytes(h)));
    const payload = JSON.parse(new TextDecoder().decode(b64urlToBytes(p)));

    const nowSec = Math.floor(Date.now() / 1000);
    if (payload.exp && payload.exp < nowSec) return null;
    const aud = Array.isArray(payload.aud) ? payload.aud : [payload.aud];
    if (!aud.includes(env.ACCESS_AUD)) return null;

    const jwk = (await jwks(env)).find((k) => k.kid === header.kid);
    if (!jwk) return null;
    const key = await importRsaJwk(jwk);
    const ok = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, b64urlToBytes(s),
      enc.encode(`${h}.${p}`));
    if (!ok) return null;

    const email = payload.email || payload.identity || null;
    return email ? { email } : null;
  } catch {
    return null;
  }
}
