// apps/daemon/src/heartbeat.ts
import { randomBytes } from "node:crypto";

// apps/daemon/src/signing.ts
import { createHash, createHmac } from "node:crypto";
function sign(secret, req) {
  const bodyHash = createHash("sha256").update(req.body).digest("hex");
  const signed = ["v1", req.method.toUpperCase(), req.path, String(req.timestamp), req.nonce, bodyHash].join(`
`);
  return createHmac("sha256", secret).update(signed).digest("hex");
}

// apps/daemon/src/heartbeat.ts
function createHeartbeat(opts) {
  const path = `/machines/${opts.machineId}/heartbeat`;
  async function beat() {
    const body = Buffer.from(JSON.stringify({ sandboxId: opts.sandboxId, nextDueAt: null, keepAwakeUntil: 0 }));
    const timestamp = Math.floor(opts.clock() / 1000);
    const nonce = randomBytes(16).toString("hex");
    let res;
    try {
      res = await fetch(new URL(path, opts.controllerUrl), {
        method: "POST",
        body,
        headers: {
          "content-type": "application/json",
          "x-t3modal-machine": opts.machineId,
          "x-t3modal-timestamp": String(timestamp),
          "x-t3modal-nonce": nonce,
          "x-t3modal-signature": sign(opts.secret, { method: "POST", path, timestamp, nonce, body })
        },
        signal: AbortSignal.timeout(30000)
      });
    } catch (e) {
      opts.log(`heartbeat failed: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    if (!res.ok)
      opts.log(`heartbeat answered ${res.status}: ${(await res.text().catch(() => "")).slice(0, 200)}`);
  }
  function start(intervalMs = 60000) {
    beat();
    const timer = setInterval(() => void beat(), intervalMs);
    return () => clearInterval(timer);
  }
  return { beat, start };
}

// apps/daemon/src/server.ts
import { createHash as createHash2, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
var MAX_FAILURES = 20;
var FAILURE_WINDOW_MS = 60000;
var LOCKOUT_MS = 5 * 60000;
function createDaemon(opts) {
  if (!opts.secret)
    throw new Error("createDaemon needs a machine secret");
  const expected = digest(`Bearer ${opts.secret}`);
  let failures = [];
  let lockedUntil = 0;
  return createServer((req, res) => {
    const now = opts.clock();
    if (now < lockedUntil) {
      return send(res, 429, { error: "locked_out" }, { "retry-after": String(Math.ceil((lockedUntil - now) / 1000)) });
    }
    if (!timingSafeEqual(digest(req.headers.authorization ?? ""), expected)) {
      failures = [...failures.filter((t) => t > now - FAILURE_WINDOW_MS), now];
      if (failures.length >= MAX_FAILURES) {
        lockedUntil = now + LOCKOUT_MS;
        opts.log(`${MAX_FAILURES} failed requests in ${FAILURE_WINDOW_MS / 1000} s, locked out for ${LOCKOUT_MS / 1000} s`);
      }
      return send(res, 401, { error: "unauthorized" });
    }
    const path = new URL(req.url ?? "/", "http://daemon").pathname;
    if (req.method === "GET" && path === "/status") {
      return send(res, 200, { machineId: opts.machineId, sandboxId: opts.sandboxId, phase: "active", startedAt: opts.startedAt });
    }
    return send(res, 404, { error: "not_found" });
  });
}
function digest(text) {
  return createHash2("sha256").update(text).digest();
}
function send(res, status, body, headers = {}) {
  res.writeHead(status, { "content-type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}

// apps/daemon/src/main.ts
var PORT = 3774;
var NAMES = ["T3MODAL_MACHINE_ID", "T3MODAL_MACHINE_SECRET", "T3MODAL_CONTROLLER_URL", "MODAL_SANDBOX_ID"];
function log(line) {
  console.log(`${new Date().toISOString()} ${line}`);
}
var missing = NAMES.filter((name) => !process.env[name]);
if (missing.length > 0) {
  log(`missing ${missing.join(", ")}, exiting`);
  process.exit(1);
}
var env = Object.fromEntries(NAMES.map((name) => [name, process.env[name]]));
var server = createDaemon({
  secret: env.T3MODAL_MACHINE_SECRET,
  machineId: env.T3MODAL_MACHINE_ID,
  sandboxId: env.MODAL_SANDBOX_ID,
  startedAt: Date.now(),
  clock: Date.now,
  log
});
server.listen(PORT, "0.0.0.0", () => log(`listening on ${PORT}`));
createHeartbeat({
  controllerUrl: env.T3MODAL_CONTROLLER_URL,
  machineId: env.T3MODAL_MACHINE_ID,
  sandboxId: env.MODAL_SANDBOX_ID,
  secret: env.T3MODAL_MACHINE_SECRET,
  clock: Date.now,
  log
}).start();
