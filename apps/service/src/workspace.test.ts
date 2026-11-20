import { expect, test } from "bun:test";
import { SANDBOX_TAG } from "@t3modal/shared";

test("the service reads values from the shared package", () => {
  expect(SANDBOX_TAG).toBe("t3modal");
});
