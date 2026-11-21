import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import { after, test } from "node:test";
import { createDaemon } from "./server.ts";

const SECRET = "b3fb340d8e907cf8e3f170ae47ddae81133e3fe4de72f292a82333dfb7f25289";
const T0 = 1_763_600_000_000;

const servers: { close(): void }[] = [];
after(() => servers.forEach((s) => s.close()));

/** A daemon on a free port, with a clock the test moves. */
async function daemon() {
  const clock = { now: T0 };
  const logs: string[] = [];
  const server = createDaemon({
    secret: SECRET,
    machineId: "m_1",
    sandboxId: "sb-1",
    startedAt: T0,
    clock: () => clock.now,
    log: (line) => logs.push(line),
  });
  servers.push(server);
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const call = (path: string, secret?: string, init: RequestInit = {}) =>
    fetch(base + path, { ...init, headers: secret === undefined ? {} : { authorization: `Bearer ${secret}` } });
  return { clock, logs, call };
}

test("GET /status with the machine secret describes the machine", async () => {
  const { call } = await daemon();

  const r = await call("/status", SECRET);

  assert.equal(r.status, 200);
  assert.deepEqual(await r.json(), { machineId: "m_1", sandboxId: "sb-1", phase: "active", startedAt: T0 });
});

for (const [label, secret] of [
  ["no secret", undefined],
  ["a wrong secret", "0".repeat(64)],
  ["a prefix of the secret", SECRET.slice(0, 32)],
  ["the secret with extra text", SECRET + "0"],
] as const) {
  test(`a request with ${label} is refused`, async () => {
    const { call } = await daemon();

    const r = await call("/status", secret);

    assert.equal(r.status, 401);
    assert.deepEqual(await r.json(), { error: "unauthorized" });
  });
}

test("an unknown route needs the secret first, then answers 404", async () => {
  const { call } = await daemon();

  assert.equal((await call("/nope")).status, 401);
  assert.equal((await call("/nope", SECRET)).status, 404);
  assert.equal((await call("/status", SECRET, { method: "POST" })).status, 404);
});

async function fail(call: (path: string, secret?: string) => Promise<Response>, times: number) {
  for (let i = 0; i < times; i++) assert.equal((await call("/status", "wrong")).status, 401);
}

test("20 failures in a minute lock the daemon for 5 minutes, even for the right secret", async () => {
  const { clock, logs, call } = await daemon();
  await fail(call, 20);

  const locked = await call("/status", SECRET);
  assert.equal(locked.status, 429);
  assert.equal(locked.headers.get("retry-after"), "300");
  assert.deepEqual(await locked.json(), { error: "locked_out" });
  assert.equal(logs.filter((l) => l.includes("locked out")).length, 1);

  clock.now += 5 * 60_000 - 1;
  assert.equal((await call("/status", SECRET)).status, 429);
  clock.now += 1;
  assert.equal((await call("/status", SECRET)).status, 200);
});

test("19 failures don't lock the daemon", async () => {
  const { call } = await daemon();
  await fail(call, 19);

  assert.equal((await call("/status", SECRET)).status, 200);
});

test("failures more than a minute old don't count toward the lockout", async () => {
  const { clock, call } = await daemon();
  await fail(call, 10);
  clock.now += 60_000;
  await fail(call, 10);

  assert.equal((await call("/status", SECRET)).status, 200);
});

test("Retry-After counts down the time left in the lockout", async () => {
  const { clock, call } = await daemon();
  await fail(call, 20);
  clock.now += 100_500;

  assert.equal((await call("/status", SECRET)).headers.get("retry-after"), "200");
});

test("requests during a lockout don't extend it", async () => {
  const { clock, call } = await daemon();
  await fail(call, 20);
  clock.now += 4 * 60_000;
  for (let i = 0; i < 30; i++) await call("/status", "wrong");
  clock.now += 60_000;

  assert.equal((await call("/status", SECRET)).status, 200);
});

test("GET /status answers when the request has a query string", async () => {
  const { call } = await daemon();

  const r = await call("/status?_modal_connect_token=tok-1", SECRET);

  assert.equal(r.status, 200);
});

test("an empty secret is refused at startup", () => {
  assert.throws(() => createDaemon({ secret: "", machineId: "m_1", sandboxId: "sb-1", startedAt: T0, clock: () => T0, log: () => {} }), /secret/);
});
