// relay.js — AgentHub Durable Object: the server half of the live relay.
//
// One AgentHub per operator namespace (AGENTS.idFromName(ns)). The operator's local box dials OUT
// to wss://<host>/agent; index.js verifies the upload token and forwards the upgrade here
// (/connect). The hub holds that single agent WebSocket. Viewer requests arrive as internal
// POST /relay carrying {method,path,body,ct}; we forward them over the socket as
//   {"id":"<uuid>","method":"GET"|"POST","path":"/...","body":<b64|null>,"ct":<string|null>}
// and await the agent's matching reply {"id","status","ct","body"} (body is b64). A request that
// gets no reply within 120s returns 504. Keepalive: the agent sends {"type":"ping"} every 30s and
// we answer {"type":"pong"}.

const RELAY_TIMEOUT_MS = 120_000;

function b64ToBytes(s) {
  const bin = atob(s || "");
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

export class AgentHub {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.pending = new Map(); // request id -> { resolve, timer }
    this.ns = null;      // this hub's namespace string, passed by index.js on /connect (?ns=)
    this.email = null;   // operator email, from ?email= on /connect and the hello frame
    this.projects = [];  // project names the agent advertised in its hello frame
  }

  agentSocket() {
    const sockets = this.state.getWebSockets("agent");
    return sockets.length ? sockets[sockets.length - 1] : null;
  }

  async fetch(request) {
    const url = new URL(request.url);

    // agent dial-in: the worker forwards the (already token-authenticated) upgrade here
    if (url.pathname === "/connect") {
      if ((request.headers.get("Upgrade") || "").toLowerCase() !== "websocket") {
        return new Response("expected websocket", { status: 426 });
      }
      // index.js verified the token and passes the resolved identity here — the DO can't re-verify
      // it, so it trusts index.js. ns keys the R2 registry record; email labels the dashboard.
      this.ns = url.searchParams.get("ns") || this.ns;
      this.email = url.searchParams.get("email") || this.email;
      // one agent per namespace — a reconnect replaces any stale socket
      for (const ws of this.state.getWebSockets("agent")) {
        try { ws.close(1012, "replaced by a newer agent connection"); } catch {}
      }
      const pair = new WebSocketPair();
      const client = pair[0];
      const server = pair[1];
      this.state.acceptWebSocket(server, ["agent"]);
      return new Response(null, { status: 101, webSocket: client });
    }

    // liveness probe used by the worker for ns resolution and the dashboard
    if (url.pathname === "/status") {
      return new Response(JSON.stringify({
        connected: !!this.agentSocket(),
        email: this.email || null,
        projects: this.projects || [],
      }), {
        headers: { "content-type": "application/json" },
      });
    }

    // internal relay: one viewer HTTP request in, one agent reply out
    if (url.pathname === "/relay" && request.method === "POST") {
      const agent = this.agentSocket();
      if (!agent) return new Response("operator offline", { status: 503 });

      let req;
      try { req = await request.json(); } catch { return new Response("bad relay body", { status: 400 }); }

      const id = crypto.randomUUID();
      const frame = JSON.stringify({
        id,
        method: req.method === "POST" ? "POST" : "GET",
        path: String(req.path || "/"),
        body: req.body || null,
        ct: req.ct || null,
      });

      const reply = await new Promise((resolve) => {
        const timer = setTimeout(() => {
          this.pending.delete(id);
          resolve({ _timeout: true });
        }, RELAY_TIMEOUT_MS);
        this.pending.set(id, { resolve, timer });
        try {
          agent.send(frame);
        } catch {
          clearTimeout(timer);
          this.pending.delete(id);
          resolve({ _gone: true });
        }
      });

      if (reply._timeout) return new Response("relay timeout: agent did not answer within 120s", { status: 504 });
      if (reply._gone) return new Response("agent disconnected", { status: 502 });

      let body;
      try { body = b64ToBytes(reply.body || ""); } catch { return new Response("agent sent malformed body", { status: 502 }); }
      return new Response(body, {
        status: Number(reply.status) || 502,
        headers: { "content-type": reply.ct || "application/octet-stream" },
      });
    }

    return new Response("not found", { status: 404 });
  }

  async webSocketMessage(ws, message) {
    if (typeof message !== "string") return;
    let msg;
    try { msg = JSON.parse(message); } catch { return; }
    if (msg.type === "ping") {
      try { ws.send(JSON.stringify({ type: "pong" })); } catch {}
      return;
    }
    if (msg.type === "pong") return;
    if (msg.type === "hello") {
      // The agent announces which projects it can serve. Keep it in memory for /status and mirror it
      // to R2 (_agents/<ns>) so a fresh isolate and the admin dashboard can read it without a live
      // socket. ns AND email were set on /connect from index.js's VERIFIED identity.
      // SECURITY: the registry email must stay the index.js-verified value — never let the agent's
      // self-claimed hello email override it, or a legit operator could bind another user's email to
      // its own ns and spoof that identity in the admin dashboard. Only fall back to the hello email
      // when /connect supplied none (in practice that isolate also lacks this.ns, so no write occurs).
      if (!this.email && msg.email) this.email = String(msg.email);
      this.projects = Array.isArray(msg.projects) ? msg.projects : [];
      if (this.ns) {
        try {
          await this.env.REPORTS.put(
            `_agents/${this.ns}`,
            JSON.stringify({ email: this.email || "unknown", projects: this.projects, updatedAt: Date.now() }),
            { httpMetadata: { contentType: "application/json" } }
          );
        } catch {}
      }
      return;
    }
    if (msg.id && this.pending.has(msg.id)) {
      const { resolve, timer } = this.pending.get(msg.id);
      clearTimeout(timer);
      this.pending.delete(msg.id);
      resolve(msg);
    }
  }

  webSocketClose() { this.failPending(); }
  webSocketError() { this.failPending(); }

  failPending() {
    for (const { resolve, timer } of this.pending.values()) {
      clearTimeout(timer);
      resolve({ _gone: true });
    }
    this.pending.clear();
  }
}
