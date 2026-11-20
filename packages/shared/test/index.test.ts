import { expect, test } from "bun:test";
import { SANDBOX_TAG } from "../src/index.ts";

test("sandboxes are tagged t3modal, as the plan's cleanup and cost stats expect", () => {
  expect(SANDBOX_TAG).toBe("t3modal");
});
