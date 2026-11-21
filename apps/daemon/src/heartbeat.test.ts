import assert from "node:assert/strict";
import { createServer, type IncomingHttpHeaders } from "node:http";
import type { AddressInfo } from "node:net";
import { after, test } from "node:test";
import { createHeartbeat } from "./heartbeat.ts";
import { sign } from "./signing.ts";

const SECRET = "b3fb340d8e907cf8e3f170ae47ddae81133e3fe4de72f292a82333dfb7f25289";
const NOW = 1_763_600_000_500;

type Seen = { method: string; url: string; headers: IncomingHttpHeaders; body: Buffer };
const closers: (() => void)[] = [];
after(() => closers.forEach((close) => close()));

/** A fake controller that records each request and answers with the next status in the list. */
async function controller(statuses: number[] = [200]) {
  const seen: Seen[] = [];
  const server = createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      seen.push({ method: req.method!, url: req.url!, headers: req.headers, body: Buffer.concat(chunks) });
      res.writeHead(statuses.shift() ?? 200, { "content-type": "application/json" });
      res.end("{}");
    });
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  closers.push(() => server.close());
  return { seen, url: `http://127.0.0.1:${(server.address() as AddressInfo).port}` };
}

function heartbeat(controllerUrl: string, logs: string[] = []) {
  return createHeartbeat({
    controllerUrl,
    machineId: "m_1",
    sandboxId: "sb-1",
    secret: SECRET,
    clock: () => NOW,
    log: (line) => logs.push(line),
  });
}

test("a heartbeat is a signed POST naming the sandbox", async () => {
  const c = await controller();

  await heartbeat(c.url).beat();

  const [req] = c.seen;
  assert.equal(req.method, "POST");
  assert.equal(req.url, "/machines/m_1/heartbeat");
  assert.equal(req.headers["content-type"], "application/json");
  assert.deepEqual(JSON.parse(req.body.toString()), { sandboxId: "sb-1", nextDueAt: null, keepAwakeUntil: 0 });
  assert.equal(req.headers["x-t3modal-machine"], "m_1");
  assert.equal(req.headers["x-t3modal-timestamp"], "1763600000");
  const nonce = req.headers["x-t3modal-nonce"] as string;
  assert.match(nonce, /^[0-9a-f]{32}$/);
  const expected = sign(SECRET, { method: "POST", path: "/machines/m_1/heartbeat", timestamp: 1763600000, nonce, body: req.body });
  assert.equal(req.headers["x-t3modal-signature"], expected);
});

test("each heartbeat uses a new nonce", async () => {
  const c = await controller([200, 200]);
  const hb = heartbeat(c.url);

  await hb.beat();
  await hb.beat();

  assert.notEqual(c.seen[0].headers["x-t3modal-nonce"], c.seen[1].headers["x-t3modal-nonce"]);
});

test("a refused heartbeat is logged with its status, and the next one still goes", async () => {
  const c = await controller([409, 200]);
  const logs: string[] = [];
  const hb = heartbeat(c.url, logs);

  await hb.beat();
  await hb.beat();

  assert.equal(c.seen.length, 2);
  assert.equal(logs.length, 1);
  assert.match(logs[0], /409/);
});

test("a controller that can't be reached is logged, not thrown", async () => {
  const logs: string[] = [];

  await heartbeat("http://127.0.0.1:1", logs).beat();

  assert.equal(logs.length, 1);
  assert.match(logs[0], /heartbeat failed/);
});

test("start beats at once, then every 60 s, until stopped", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const c = await controller([200, 200, 200, 200]);
  const hb = heartbeat(c.url);
  // Waits for the nth request, polling real time, since only setInterval is mocked. When the test
  // expects no new request, it waits 50 ms. Otherwise it waits up to 0.5 s.
  const settle = async (n: number, expectRequest: boolean) => {
    const tries = expectRequest ? 100 : 10;
    for (let i = 0; i < tries && c.seen.length < n; i++) await new Promise((r) => setTimeout(r, 5));
  };

  const stop = hb.start();
  await settle(1, true);
  t.mock.timers.tick(59_999);
  await settle(2, false);
  assert.equal(c.seen.length, 1);
  t.mock.timers.tick(1);
  await settle(2, true);
  assert.equal(c.seen.length, 2);
  stop();
  t.mock.timers.tick(60_000);
  await settle(3, false);
  assert.equal(c.seen.length, 2);
});
