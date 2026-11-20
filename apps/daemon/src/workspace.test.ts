import assert from "node:assert/strict";
import { test } from "node:test";
import { SANDBOX_TAG, type Size } from "@t3modal/shared";

test("the daemon loads TypeScript from the shared package under Node", () => {
  const size: Size = "default";
  assert.equal(SANDBOX_TAG, "t3modal");
  assert.equal(size, "default");
});
