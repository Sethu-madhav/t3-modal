/**
 * The daemon's entry point. init.sh runs it as the user t3 and restarts it when it exits (§7.2).
 * bun run build:daemon bundles it into modal/sandbox/daemon.mjs, which the machine image copies.
 */
import { createHeartbeat } from "./heartbeat.ts";
import { createDaemon } from "./server.ts";

const PORT = 3774;
const NAMES = ["T3MODAL_MACHINE_ID", "T3MODAL_MACHINE_SECRET", "T3MODAL_CONTROLLER_URL", "MODAL_SANDBOX_ID"] as const;

function log(line: string): void {
  console.log(`${new Date().toISOString()} ${line}`);
}

const missing = NAMES.filter((name) => !process.env[name]);
if (missing.length > 0) {
  log(`missing ${missing.join(", ")}, exiting`);
  process.exit(1);
}
const env = Object.fromEntries(NAMES.map((name) => [name, process.env[name]!])) as Record<(typeof NAMES)[number], string>;

const server = createDaemon({
  secret: env.T3MODAL_MACHINE_SECRET,
  machineId: env.T3MODAL_MACHINE_ID,
  sandboxId: env.MODAL_SANDBOX_ID,
  startedAt: Date.now(),
  clock: Date.now,
  log,
});
server.listen(PORT, "0.0.0.0", () => log(`listening on ${PORT}`));

createHeartbeat({
  controllerUrl: env.T3MODAL_CONTROLLER_URL,
  machineId: env.T3MODAL_MACHINE_ID,
  sandboxId: env.MODAL_SANDBOX_ID,
  secret: env.T3MODAL_MACHINE_SECRET,
  clock: Date.now,
  log,
}).start();
