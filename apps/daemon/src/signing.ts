/**
 * Signed requests to the controller (implementation.md §6.7). The controller's
 * t3modal_controller/signing.py checks the same format, and both match
 * packages/shared/fixtures/signed-request.json.
 */
import { createHash, createHmac } from "node:crypto";

export type Signable = { method: string; path: string; timestamp: number; nonce: string; body: Buffer };

/** The hex HMAC-SHA256, keyed with the machine secret's hex text. path includes the query string, exactly as sent. */
export function sign(secret: string, req: Signable): string {
  const bodyHash = createHash("sha256").update(req.body).digest("hex");
  const signed = ["v1", req.method.toUpperCase(), req.path, String(req.timestamp), req.nonce, bodyHash].join("\n");
  return createHmac("sha256", secret).update(signed).digest("hex");
}
