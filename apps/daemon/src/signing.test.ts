import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { sign } from "./signing.ts";

const vector = JSON.parse(readFileSync(new URL("../../../packages/shared/fixtures/signed-request.json", import.meta.url), "utf8"));

for (const req of vector.requests) {
  test(`a signature matches the shared vector for ${req.method} ${req.path}`, () => {
    const signature = sign(vector.machineSecret, {
      method: req.method,
      path: req.path,
      timestamp: req.timestamp,
      nonce: req.nonce,
      body: Buffer.from(req.body),
    });
    assert.equal(signature, req.signature);
  });
}
