/**
 * The daemon's heartbeat to the controller, every 60 s (implementation.md §6.3 and §7.3). The
 * controller answers 409 until the create operation records the sandbox as running, so a failed
 * heartbeat is logged and the next one tries again (§6.7).
 */
import { randomBytes } from "node:crypto";
import { sign } from "./signing.ts";

export type HeartbeatOptions = {
  controllerUrl: string;
  machineId: string;
  sandboxId: string;
  secret: string;
  clock: () => number;
  log: (line: string) => void;
};

export function createHeartbeat(opts: HeartbeatOptions) {
  const path = `/machines/${opts.machineId}/heartbeat`;

  async function beat(): Promise<void> {
    const body = Buffer.from(JSON.stringify({ sandboxId: opts.sandboxId, nextDueAt: null, keepAwakeUntil: 0 }));
    const timestamp = Math.floor(opts.clock() / 1000);
    const nonce = randomBytes(16).toString("hex");
    let res: Response;
    try {
      res = await fetch(new URL(path, opts.controllerUrl), {
        method: "POST",
        body,
        headers: {
          "content-type": "application/json",
          "x-t3modal-machine": opts.machineId,
          "x-t3modal-timestamp": String(timestamp),
          "x-t3modal-nonce": nonce,
          "x-t3modal-signature": sign(opts.secret, { method: "POST", path, timestamp, nonce, body }),
        },
        signal: AbortSignal.timeout(30_000),
      });
    } catch (e) {
      opts.log(`heartbeat failed: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    if (!res.ok) opts.log(`heartbeat answered ${res.status}: ${(await res.text().catch(() => "")).slice(0, 200)}`);
  }

  /** Beats now and then every intervalMs. Returns a function that stops it. */
  function start(intervalMs = 60_000): () => void {
    void beat();
    const timer = setInterval(() => void beat(), intervalMs);
    return () => clearInterval(timer);
  }

  return { beat, start };
}
