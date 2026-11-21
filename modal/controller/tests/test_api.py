"""The controller's HTTP API with a fake Cloud in place of Modal (implementation.md §6.3, §9.1, §9.2)."""

import asyncio
import json
import secrets

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from t3modal_controller.api import create_api, signed_path
from t3modal_controller.controller import Controller
from t3modal_controller.machines import Endpoint
from t3modal_controller.signing import machine_secret, sign
from t3modal_controller.store import StateStore

ADMIN = "admin-token-for-tests"
HMAC_KEY = "hmac-key-for-tests"
CONTROLLER_URL = "https://controller.example"
NOW_MS = 1_763_600_000_000
AUTH = {"Authorization": f"Bearer {ADMIN}"}


class FakeVolume:
    def reload(self):
        pass

    def commit(self):
        pass


class FakeNonces(dict):
    """Modal's Dict.put with skip_if_exists, which adds a key only if it is absent."""

    def put(self, key, value, *, skip_if_exists=False):
        if skip_if_exists and key in self:
            return False
        self[key] = value
        return True


def on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class FakeCloud:
    """Stands in for Modal. Records each sandbox request and hands out connect tokens."""

    def __init__(self, fail_with: str | None = None, fail_connect: str | None = None):
        self.fail_with = fail_with
        self.fail_connect = fail_connect
        self.created: list[dict] = []
        self.terminated: list[str] = []
        self.secrets: dict[str, dict] = {}  # kind -> {"values", "setAt"}
        self.put_error: Exception | None = None
        self.put_on_event_loop: bool | None = None

    def create_sandbox(self, *, t3_version: str, args: dict, env: dict[str, str], credentials: list[str]) -> str:
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.created.append({"t3_version": t3_version, "env": env, "credentials": credentials, **args})
        return f"sb-{len(self.created)}"

    def connect(self, sandbox_id: str, port: int) -> Endpoint:
        if self.fail_connect:
            raise RuntimeError(self.fail_connect)
        return Endpoint(url=f"https://{sandbox_id}-{port}.example", token=f"tok-{sandbox_id}-{port}")

    def terminate(self, sandbox_id: str) -> None:
        self.terminated.append(sandbox_id)

    def put_credential(self, kind: str, values: dict[str, str]) -> None:
        self.put_on_event_loop = on_event_loop()
        if self.put_error:
            raise self.put_error
        self.secrets[kind] = {"values": values, "setAt": NOW_MS}

    def credentials(self) -> dict[str, int]:
        return {kind: s["setAt"] for kind, s in self.secrets.items()}

    def delete_credential(self, kind: str) -> bool:
        return self.secrets.pop(kind, None) is not None


@pytest.fixture
def cloud():
    return FakeCloud()


def api_with(*, store, cloud, ops=None, run_op=None, admin_token=ADMIN, hmac_key=HMAC_KEY):
    return create_api(
        admin_token=admin_token,
        hmac_key=hmac_key,
        controller_url=CONTROLLER_URL,
        store=store,
        ops={} if ops is None else ops,
        nonces=FakeNonces(),
        cloud=cloud,
        run_op=run_op,  # None runs each operation inline, as the tests need
        revision="test-rev",
        modal_version="1.6.4",
        clock_ms=lambda: NOW_MS,
    )


@pytest.fixture
def client(tmp_path, cloud):
    return TestClient(api_with(store=StateStore(tmp_path, FakeVolume()), cloud=cloud))


def create(client, **body):
    return client.post("/machines", json={"name": "dev", "size": "default", "t3Version": "0.1.10", **body}, headers=AUTH)


def route_paths(client):
    """Every (method, path) the API serves, with sample values in the path parameters."""
    values = {"machine_id": "m_x", "op_id": "op_x", "kind": "claude"}
    routes = [(m, r.path.format(**values)) for r in client.app.routes if isinstance(r, APIRoute) for m in r.methods]
    assert len(routes) >= 8
    return routes


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": ADMIN}])
def test_every_route_answers_401_without_the_admin_token(client, cloud, headers):
    put_claude(client)
    for method, path in route_paths(client):
        r = client.request(method, path, headers=headers, json={"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN + "2"}})

        assert r.status_code == 401, (method, path)
    assert cloud.secrets["claude"]["values"] == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}


def test_version_reports_the_controller_revision_and_modal_sdk(client):
    r = client.get("/version", headers=AUTH)

    assert r.status_code == 200
    assert r.json() == {"revision": "test-rev", "modal": "1.6.4"}


def test_creating_a_machine_returns_an_operation_that_ends_with_the_machine_running(client):
    r = create(client)

    assert r.status_code == 202
    op = client.get(f"/ops/{r.json()['opId']}", headers=AUTH).json()
    assert op["status"] == "done"
    machine = client.get(f"/machines/{op['machineId']}", headers=AUTH).json()
    assert machine["power"] == "running"
    assert machine["endpoints"]["t3"]["url"].endswith("-3773.example")
    assert machine["endpoints"]["daemon"]["url"].endswith("-3774.example")
    assert machine["endpoints"]["t3"]["token"]


def test_creating_a_machine_returns_its_secret_once(client):
    r = create(client)

    machine_id, secret = r.json()["id"], r.json()["machineSecret"]
    assert secret == machine_secret(HMAC_KEY, machine_id)
    later = [client.get(f"/machines/{machine_id}", headers=AUTH), client.get(f"/ops/{r.json()['opId']}", headers=AUTH)]
    assert all(secret not in later_r.text for later_r in later)


def test_the_sandbox_env_carries_the_machine_id_secret_and_controller_url(client, cloud):
    r = create(client)

    [req] = cloud.created
    assert req["env"] == {
        "T3MODAL_MACHINE_ID": r.json()["id"],
        "T3MODAL_MACHINE_SECRET": r.json()["machineSecret"],
        "T3MODAL_CONTROLLER_URL": CONTROLLER_URL,
    }


def test_a_new_machine_gets_the_credentials_set_when_it_is_created(client, cloud):
    create(client)
    put_claude(client)
    create(client)

    assert [req["credentials"] for req in cloud.created] == [[], ["claude"]]


def test_the_sandbox_is_a_mode_b_vm_named_and_tagged_for_its_machine(client, cloud):
    r = create(client, size="small")
    machine_id = client.get(f"/ops/{r.json()['opId']}", headers=AUTH).json()["machineId"]

    [req] = cloud.created
    assert req["t3_version"] == "0.1.10"
    assert req["name"] == "m-" + machine_id.removeprefix("m_")
    assert req["tags"] == {"t3modal": machine_id}
    assert req["runtime"] == "vm"
    assert req["cpu"] == (0.25, 2)
    assert req["memory"] == (1024, 4096)
    assert req["timeout"] == 24 * 3600
    assert req["_experimental_enable_snapshot"] is True
    assert "encrypted_ports" not in req and "unencrypted_ports" not in req


def test_a_failed_start_fails_the_operation_and_leaves_the_machine_stopped(tmp_path):
    cloud = FakeCloud(fail_with="VM sandbox could not start")
    client = TestClient(api_with(store=StateStore(tmp_path, FakeVolume()), cloud=cloud))

    op = client.get(f"/ops/{create(client).json()['opId']}", headers=AUTH).json()

    assert op["status"] == "failed"
    assert "VM sandbox could not start" in op["error"]
    assert client.get(f"/machines/{op['machineId']}", headers=AUTH).json()["power"] == "stopped"


def test_an_unknown_size_is_refused_before_anything_starts(client, cloud):
    r = create(client, size="huge")

    assert r.status_code == 400
    assert r.json()["error"] == "unknown_size"
    assert cloud.created == []


@pytest.mark.parametrize("t3_version", ["latest", "0.1.10; rm -rf /", "0.1", "$(id)", "\uff10.\uff11.\uff11\uff10"])
def test_a_t3_version_that_is_not_a_version_number_is_refused(client, cloud, t3_version):
    r = create(client, t3Version=t3_version)

    assert r.status_code == 400
    assert r.json()["error"] == "invalid_t3_version"
    assert cloud.created == []


@pytest.mark.parametrize("t3_version", ["0.1.10", "0.1.11-nightly.20261118.4"])
def test_release_and_nightly_t3_versions_are_accepted(client, t3_version):
    assert create(client, t3Version=t3_version).status_code == 202


def test_an_unknown_machine_or_operation_is_404(client):
    assert client.get("/machines/m_nope", headers=AUTH).status_code == 404
    assert client.get("/ops/op_nope", headers=AUTH).status_code == 404


class StoreThatFailsAfter(StateStore):
    """A state store whose writes fail once it has made the given number of them."""

    def __init__(self, root, writes_allowed: int):
        super().__init__(root, FakeVolume())
        self.writes_allowed = writes_allowed

    def put(self, machine):
        if self.writes_allowed == 0:
            raise OSError("Volume commit failed")
        self.writes_allowed -= 1
        super().put(machine)


def test_a_failure_after_the_sandbox_exists_terminates_it(tmp_path):
    cloud = FakeCloud(fail_connect="connect token refused")
    client = TestClient(api_with(store=StateStore(tmp_path, FakeVolume()), cloud=cloud))

    op = client.get(f"/ops/{create(client).json()['opId']}", headers=AUTH).json()

    assert op["status"] == "failed"
    assert "connect token refused" in op["error"]
    assert cloud.terminated == ["sb-1"]
    assert client.get(f"/machines/{op['machineId']}", headers=AUTH).json()["power"] == "stopped"


def test_the_operation_fails_even_when_the_state_file_cannot_be_written(tmp_path, cloud):
    client = TestClient(api_with(store=StoreThatFailsAfter(tmp_path, writes_allowed=1), cloud=cloud))

    op = client.get(f"/ops/{create(client).json()['opId']}", headers=AUTH).json()

    assert op["status"] == "failed"
    assert "Volume commit failed" in op["error"]
    assert cloud.terminated == ["sb-1"]


def test_running_an_operation_a_second_time_starts_no_second_sandbox(tmp_path, cloud):
    ops = {}
    store = StateStore(tmp_path, FakeVolume())
    client = TestClient(api_with(store=store, cloud=cloud, ops=ops))
    op_id = create(client).json()["opId"]

    Controller(
        store=store, ops=ops, cloud=cloud, clock_ms=lambda: 2_000, hmac_key=HMAC_KEY, controller_url=CONTROLLER_URL
    ).perform(op_id)

    assert len(cloud.created) == 1
    assert ops[op_id]["status"] == "done"
    assert client.get(f"/machines/{ops[op_id]['machineId']}", headers=AUTH).json()["power"] == "running"


def test_a_create_whose_operation_cannot_be_started_fails_it_and_stops_the_machine(tmp_path, cloud):
    ops = {}

    def spawn_fails(op_id):
        raise RuntimeError("spawn refused")

    client = TestClient(api_with(store=StateStore(tmp_path, FakeVolume()), cloud=cloud, ops=ops, run_op=spawn_fails))

    r = create(client)

    assert r.status_code == 503
    assert r.json()["error"] == "start_failed"
    [op] = ops.values()
    assert op["status"] == "failed"
    assert "spawn refused" in op["error"]
    assert client.get(f"/machines/{op['machineId']}", headers=AUTH).json()["power"] == "stopped"
    assert cloud.created == []


@pytest.mark.parametrize("empty", ["admin_token", "hmac_key"])
def test_an_empty_admin_token_or_hmac_key_is_refused_at_startup(tmp_path, cloud, empty):
    with pytest.raises(ValueError):
        api_with(store=StateStore(tmp_path, FakeVolume()), cloud=cloud, **{empty: ""})


def signed_headers(machine_id, secret, method, path, body=b"", *, timestamp=None, nonce=None):
    timestamp = NOW_MS // 1000 if timestamp is None else timestamp
    nonce = nonce or secrets.token_hex(16)
    return {
        "Content-Type": "application/json",
        "X-T3modal-Machine": machine_id,
        "X-T3modal-Timestamp": str(timestamp),
        "X-T3modal-Nonce": nonce,
        "X-T3modal-Signature": sign(secret, method=method, path=path, timestamp=timestamp, nonce=nonce, body=body),
    }


def a_running_machine(client):
    r = create(client).json()
    machine = client.get(f"/machines/{r['id']}", headers=AUTH).json()
    return r["id"], r["machineSecret"], machine["sandboxId"], r["opId"]


def heartbeat_body(sandbox_id, **changes):
    return json.dumps({"sandboxId": sandbox_id, "nextDueAt": 1_763_700_000_000, "keepAwakeUntil": 0, **changes}).encode()


def test_a_signed_heartbeat_from_the_machine_is_recorded(client):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)
    path = f"/machines/{machine_id}/heartbeat"
    body = heartbeat_body(sandbox_id)

    r = client.post(path, content=body, headers=signed_headers(machine_id, secret, "POST", path, body))

    assert r.status_code == 200
    machine = client.get(f"/machines/{machine_id}", headers=AUTH).json()
    assert machine["lastHeartbeatAt"] == NOW_MS
    assert machine["nextDueAt"] == 1_763_700_000_000


def heartbeat_as(client, machine_id, secret, sandbox_id, *, path_id=None, drop=None, body=None, send_body=None, **sign_args):
    path = f"/machines/{path_id or machine_id}/heartbeat"
    body = heartbeat_body(sandbox_id) if body is None else body
    headers = signed_headers(machine_id, secret, "POST", path, body, **sign_args)
    if drop:
        del headers[drop]
    return client.post(path, content=body if send_body is None else send_body, headers=headers)


def test_a_heartbeat_with_no_signature_is_refused(client):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)

    r = heartbeat_as(client, machine_id, secret, sandbox_id, drop="X-T3modal-Signature")

    assert r.status_code == 401


def test_a_heartbeat_signed_with_a_wrong_secret_is_refused(client):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)

    r = heartbeat_as(client, machine_id, "0" * 64, sandbox_id)

    assert (r.status_code, r.json()["reason"]) == (401, "bad_signature")


def test_a_heartbeat_whose_body_changed_after_signing_is_refused(client):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)

    r = heartbeat_as(client, machine_id, secret, sandbox_id, send_body=heartbeat_body(sandbox_id, keepAwakeUntil=1))

    assert (r.status_code, r.json()["reason"]) == (401, "bad_signature")


@pytest.mark.parametrize("offset_s", [-61, 61])
def test_a_heartbeat_more_than_60_s_off_the_controllers_clock_is_refused(client, offset_s):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)

    r = heartbeat_as(client, machine_id, secret, sandbox_id, timestamp=NOW_MS // 1000 + offset_s)

    assert (r.status_code, r.json()["reason"]) == (401, "clock_skew")


@pytest.mark.parametrize("offset_s", [-60, 60])
def test_a_heartbeat_60_s_off_is_still_accepted(client, offset_s):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)

    assert heartbeat_as(client, machine_id, secret, sandbox_id, timestamp=NOW_MS // 1000 + offset_s).status_code == 200


def test_a_reused_nonce_is_refused(client):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)
    nonce = secrets.token_hex(16)

    first = heartbeat_as(client, machine_id, secret, sandbox_id, nonce=nonce)
    again = heartbeat_as(client, machine_id, secret, sandbox_id, nonce=nonce)

    assert first.status_code == 200
    assert (again.status_code, again.json()["reason"]) == (401, "reused_nonce")


def test_one_machines_secret_cannot_heartbeat_for_another_machine(client):
    a_id, _, a_sandbox, _ = a_running_machine(client)
    b_id, b_secret, _, _ = a_running_machine(client)

    as_itself = heartbeat_as(client, b_id, b_secret, a_sandbox, path_id=a_id)
    posing_as_a = heartbeat_as(client, a_id, b_secret, a_sandbox)

    assert as_itself.status_code == 403
    assert (posing_as_a.status_code, posing_as_a.json()["reason"]) == (401, "bad_signature")


def test_a_heartbeat_from_a_sandbox_that_is_not_current_gets_409(client):
    machine_id, secret, _, _ = a_running_machine(client)

    r = heartbeat_as(client, machine_id, secret, "sb-old")

    assert (r.status_code, r.json()["error"]) == (409, "stale_sandbox")


def signed_request(client, machine_id, secret, method, path, body=b""):
    return client.request(method, path, content=body or None, headers=signed_headers(machine_id, secret, method, path, body))


def test_a_machine_secret_is_refused_on_every_route_except_heartbeat_and_operations(client):
    machine_id, secret, _, op_id = a_running_machine(client)
    allowed = {("POST", "/machines/{machine_id}/heartbeat"), ("GET", "/ops/{op_id}")}
    routes = [(m, r.path) for r in client.app.routes if isinstance(r, APIRoute) for m in r.methods]
    assert len(routes) >= 8

    for method, template in routes:
        if (method, template) in allowed:
            continue
        path = template.format(machine_id=machine_id, op_id=op_id, kind="claude")
        body = json.dumps({"name": "x", "size": "small", "t3Version": "0.1.10"}).encode() if method == "POST" else b""

        assert signed_request(client, machine_id, secret, method, path, body).status_code == 403, (method, template)


def test_a_machine_reads_its_own_operations_and_no_other_machines(client):
    a_id, a_secret, _, a_op = a_running_machine(client)
    _, _, _, b_op = a_running_machine(client)

    own = signed_request(client, a_id, a_secret, "GET", f"/ops/{a_op}")
    other = signed_request(client, a_id, a_secret, "GET", f"/ops/{b_op}")
    unknown = signed_request(client, a_id, a_secret, "GET", "/ops/op_nope")

    assert (own.status_code, own.json()["machineId"]) == (200, a_id)
    assert other.status_code == 403
    assert unknown.status_code == 403


def test_the_admin_token_cannot_send_a_heartbeat(client):
    machine_id, _, sandbox_id, _ = a_running_machine(client)

    r = client.post(
        f"/machines/{machine_id}/heartbeat", content=heartbeat_body(sandbox_id), headers={**AUTH, "Content-Type": "application/json"}
    )

    assert r.status_code == 403


def test_a_signed_request_with_a_query_string_is_accepted(client):
    machine_id, secret, _, op_id = a_running_machine(client)

    r = signed_request(client, machine_id, secret, "GET", f"/ops/{op_id}?wait=1")

    assert r.status_code == 200


def test_a_signature_that_left_out_the_query_string_is_refused(client):
    machine_id, secret, _, op_id = a_running_machine(client)
    headers = signed_headers(machine_id, secret, "GET", f"/ops/{op_id}")

    r = client.get(f"/ops/{op_id}?wait=1", headers=headers)

    assert (r.status_code, r.json()["reason"]) == (401, "bad_signature")


def test_a_heartbeat_records_keep_awake_until(client):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)

    body = heartbeat_body(sandbox_id, keepAwakeUntil=1_763_600_900_000)
    assert heartbeat_as(client, machine_id, secret, sandbox_id, body=body).status_code == 200

    assert client.get(f"/machines/{machine_id}", headers=AUTH).json()["keepAwakeUntil"] == 1_763_600_900_000


@pytest.mark.parametrize(
    ("header", "value"),
    [("timestamp", "-1763600000"), ("timestamp", "1763600000.5"), ("nonce", "short"), ("nonce", "NOT-HEX-0123456789")],
)
def test_a_malformed_timestamp_or_nonce_is_refused(client, header, value):
    machine_id, secret, sandbox_id, _ = a_running_machine(client)
    path = f"/machines/{machine_id}/heartbeat"
    body = heartbeat_body(sandbox_id)
    headers = signed_headers(machine_id, secret, "POST", path, body)
    headers[f"X-T3modal-{header.capitalize()}"] = value

    r = client.post(path, content=body, headers=headers)

    assert (r.status_code, r.json()["reason"]) == (401, "malformed")


def test_a_heartbeat_for_a_machine_with_no_state_file_is_404(client):
    path = "/machines/m_nope/heartbeat"
    body = heartbeat_body("sb-1")

    r = client.post(path, content=body, headers=signed_headers("m_nope", machine_secret(HMAC_KEY, "m_nope"), "POST", path, body))

    assert r.status_code == 404


@pytest.mark.parametrize(
    ("scope", "expected"),
    [
        ({"raw_path": b"/ops/op_1", "path": "/ops/op_1", "query_string": b"wait=1"}, "/ops/op_1?wait=1"),
        ({"raw_path": None, "path": "/ops/op_1", "query_string": b"wait=1"}, "/ops/op_1?wait=1"),
        ({"path": "/ops/op_1", "query_string": b""}, "/ops/op_1"),
    ],
)
def test_the_signed_path_falls_back_when_the_server_gives_no_raw_path(scope, expected):
    assert signed_path(scope) == expected


TOKEN = "sk-ant-oat01-" + "x" * 40


def put_claude(client, body=None, headers=AUTH):
    body = {"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}} if body is None else body
    return client.put("/credentials/claude", content=json.dumps(body), headers={**headers, "Content-Type": "application/json"})


def test_putting_the_claude_token_stores_it_as_a_secret(client, cloud):
    r = put_claude(client)

    assert r.status_code == 200
    assert TOKEN not in r.text
    assert cloud.secrets == {"claude": {"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}, "setAt": NOW_MS}}


@pytest.mark.parametrize(
    "body",
    [
        {"values": {}},
        {"values": {"CLAUDE_CODE_OAUTH_TOKEN": ""}},
        {"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN, "ANTHROPIC_API_KEY": TOKEN}},
        {"values": {"ANTHROPIC_API_KEY": TOKEN}},
        {"values": {"CLAUDE_CODE_OAUTH_TOKEN": 7}},
        # A token copied from a wrapped terminal line holds spaces, and Anthropic refuses it.
        {"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN[:30] + "  " + TOKEN[30:]}},
        {"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN + "\n"}},
        {"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN + "\x00"}},
        {"values": {"CLAUDE_CODE_OAUTH_TOKEN": "   "}},
        {"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN + "é"}},
        {"values": [TOKEN]},
        {"value": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN}},
        [TOKEN],
        TOKEN,
    ],
)
def test_a_bad_credential_body_is_refused_without_echoing_it(client, cloud, body):
    r = put_claude(client, body)

    assert (r.status_code, r.json()["error"]) == (400, "invalid_credential")
    assert TOKEN not in r.text
    assert cloud.secrets == {}


def test_a_credential_body_that_is_not_json_is_refused_without_echoing_it(client, cloud):
    r = client.put("/credentials/claude", content=b"{" + TOKEN.encode(), headers={**AUTH, "Content-Type": "application/json"})

    assert (r.status_code, r.json()["error"]) == (400, "invalid_credential")
    assert TOKEN not in r.text
    assert cloud.secrets == {}


@pytest.mark.parametrize("content", [json.dumps({"values": {"OPENAI_API_KEY": TOKEN}}), "{" + TOKEN])
def test_an_unknown_credential_kind_is_not_found(client, cloud, content):
    r = client.put("/credentials/openai", content=content, headers={**AUTH, "Content-Type": "application/json"})

    assert (r.status_code, r.json()["error"]) == (404, "unknown_credential_kind")
    assert TOKEN not in r.text
    assert cloud.secrets == {}


def test_listing_credentials_shows_when_each_was_set_and_never_its_value(client):
    before = client.get("/credentials", headers=AUTH)
    put_claude(client)
    after = client.get("/credentials", headers=AUTH)

    assert (before.status_code, before.json()) == (200, {"credentials": []})
    assert (after.status_code, after.json()) == (200, {"credentials": [{"kind": "claude", "setAt": NOW_MS}]})
    assert TOKEN not in after.text


def test_deleting_a_credential_removes_its_secret(client, cloud):
    put_claude(client)

    first = client.delete("/credentials/claude", headers=AUTH)
    again = client.delete("/credentials/claude", headers=AUTH)

    assert (first.status_code, first.json()) == (200, {})
    assert (again.status_code, again.json()["error"]) == (404, "not_found")
    assert cloud.secrets == {}


def test_deleting_an_unknown_credential_kind_is_not_found(client):
    r = client.delete("/credentials/openai", headers=AUTH)

    assert (r.status_code, r.json()["error"]) == (404, "unknown_credential_kind")


def test_storing_a_credential_runs_off_the_event_loop(client, cloud):
    put_claude(client)

    assert cloud.put_on_event_loop is False


def test_a_cloud_error_while_storing_a_credential_is_not_called_a_bad_body(client, cloud):
    cloud.put_error = ValueError("Modal refused the Secret")

    with pytest.raises(ValueError, match="Modal refused the Secret"):
        put_claude(client)


def test_a_body_that_is_not_utf8_is_refused(client, cloud):
    r = client.put("/credentials/claude", content=b'{"values": {"CLAUDE_CODE_OAUTH_TOKEN": "\xff"}}', headers={**AUTH, "Content-Type": "application/json"})

    assert (r.status_code, r.json()["error"]) == (400, "invalid_credential")
    assert cloud.secrets == {}


def test_putting_a_credential_again_replaces_it(client, cloud):
    put_claude(client)
    put_claude(client, {"values": {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN + "2"}})

    assert cloud.secrets["claude"]["values"] == {"CLAUDE_CODE_OAUTH_TOKEN": TOKEN + "2"}
