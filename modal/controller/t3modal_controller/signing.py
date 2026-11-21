"""Machine secrets and signed daemon requests (implementation.md §6.7).

packages/shared/fixtures/signed-request.json holds a test vector that the daemon's TypeScript must match.
"""

import hashlib
import hmac
import re
from typing import Any, Protocol


def machine_secret(hmac_key: str, machine_id: str) -> str:
    """The machine's secret: hex HMAC-SHA256 of its id. The controller derives it and never stores it."""
    return hmac.new(hmac_key.encode(), machine_id.encode(), hashlib.sha256).hexdigest()


def sign(secret: str, *, method: str, path: str, timestamp: int, nonce: str, body: bytes) -> str:
    """path includes the query string, exactly as sent."""
    signed = "\n".join(["v1", method.upper(), path, str(timestamp), nonce, hashlib.sha256(body).hexdigest()])
    return hmac.new(secret.encode(), signed.encode(), hashlib.sha256).hexdigest()


MAX_SKEW_S = 60
_NONCE = re.compile(r"[0-9a-f]{16,64}")


class Refused(Exception):
    """A signed request that fails a check. reason goes back to the daemon, so it can tell a clock
    problem from a bad secret."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Nonces(Protocol):
    """Modal's Dict: put with skip_if_exists adds the key only if it is absent, and says whether it did."""

    def put(self, key: str, value: Any, *, skip_if_exists: bool = False) -> bool: ...


def check(
    hmac_key: str,
    *,
    machine_id: str,
    timestamp: str,
    nonce: str,
    signature: str,
    method: str,
    path: str,
    body: bytes,
    now_s: int,
    nonces: Nonces,
) -> None:
    """Raises Refused unless the request carries a valid signature from machine_id's secret, a
    timestamp within 60 s, and a nonce this machine hasn't used. A nonce outlives the 60 s window,
    so a replay fails the timestamp check once its key is gone."""
    if not timestamp.isascii() or not timestamp.isdigit() or not _NONCE.fullmatch(nonce):
        raise Refused("malformed")
    if abs(now_s - int(timestamp)) > MAX_SKEW_S:
        raise Refused("clock_skew")
    expected = sign(machine_secret(hmac_key, machine_id), method=method, path=path, timestamp=int(timestamp), nonce=nonce, body=body)
    if not hmac.compare_digest(expected.encode(), signature.encode()):
        raise Refused("bad_signature")
    if not nonces.put(f"{machine_id}:{nonce}", int(timestamp), skip_if_exists=True):
        raise Refused("reused_nonce")
