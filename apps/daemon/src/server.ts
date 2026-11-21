/**
 * The daemon's HTTP server on port 3774 (implementation.md §7.3). Modal's proxy checks the connect
 * token, then every request must carry the machine secret as a bearer token.
 */
import { createHash, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";

export type DaemonOptions = {
  secret: string;
  machineId: string;
  sandboxId: string;
  startedAt: number;
  clock: () => number;
  log: (line: string) => void;
};

/** After MAX_FAILURES failed requests within FAILURE_WINDOW_MS, every request gets 429 for LOCKOUT_MS. */
export const MAX_FAILURES = 20;
export const FAILURE_WINDOW_MS = 60_000;
export const LOCKOUT_MS = 5 * 60_000;

export function createDaemon(opts: DaemonOptions): Server {
  if (!opts.secret) throw new Error("createDaemon needs a machine secret");
  const expected = digest(`Bearer ${opts.secret}`);
  let failures: number[] = [];
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
    // Modal's proxy may pass query parameters through, so routes match on the path alone.
    const path = new URL(req.url ?? "/", "http://daemon").pathname;
    if (req.method === "GET" && path === "/status") {
      return send(res, 200, { machineId: opts.machineId, sandboxId: opts.sandboxId, phase: "active", startedAt: opts.startedAt });
    }
    return send(res, 404, { error: "not_found" });
  });
}

/** Comparing digests keeps the lengths equal, so timingSafeEqual never throws and the time doesn't depend on the input. */
function digest(text: string): Buffer {
  return createHash("sha256").update(text).digest();
}

function send(res: ServerResponse, status: number, body: unknown, headers: Record<string, string> = {}): void {
  res.writeHead(status, { "content-type": "application/json", ...headers });
  res.end(JSON.stringify(body));
}
