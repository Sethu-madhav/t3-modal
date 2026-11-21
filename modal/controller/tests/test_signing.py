"""Machine secrets and request signatures (implementation.md §6.7), against a shared test vector."""

import json
from pathlib import Path

import pytest

from t3modal_controller.signing import machine_secret, sign

VECTOR = json.loads((Path(__file__).resolve().parents[3] / "packages/shared/fixtures/signed-request.json").read_text())


def test_a_machine_secret_is_derived_from_the_hmac_key_and_the_machine_id():
    assert machine_secret(VECTOR["hmacKey"], VECTOR["machineId"]) == VECTOR["machineSecret"]


@pytest.mark.parametrize("req", VECTOR["requests"], ids=lambda r: f"{r['method']} {r['path']}")
def test_a_request_signature_matches_the_shared_vector(req):
    signature = sign(
        VECTOR["machineSecret"], method=req["method"], path=req["path"], timestamp=req["timestamp"], nonce=req["nonce"], body=req["body"].encode()
    )

    assert signature == req["signature"]
