"""The controller's HTTP API with a fake Cloud in place of Modal (implementation.md §6.3, §9.1, §9.2)."""

import pytest
from fastapi.testclient import TestClient

from t3modal_controller.api import create_api
from t3modal_controller.controller import Controller
from t3modal_controller.machines import Endpoint
from t3modal_controller.store import StateStore

ADMIN = "admin-token-for-tests"
AUTH = {"Authorization": f"Bearer {ADMIN}"}


class FakeVolume:
    def reload(self):
        pass

    def commit(self):
        pass


class FakeCloud:
    """Stands in for Modal. Records each sandbox request and hands out connect tokens."""

    def __init__(self, fail_with: str | None = None, fail_connect: str | None = None):
        self.fail_with = fail_with
        self.fail_connect = fail_connect
        self.created: list[dict] = []
        self.terminated: list[str] = []

    def create_sandbox(self, *, t3_version: str, args: dict) -> str:
        if self.fail_with:
            raise RuntimeError(self.fail_with)
        self.created.append({"t3_version": t3_version, **args})
        return f"sb-{len(self.created)}"

    def connect(self, sandbox_id: str, port: int) -> Endpoint:
        if self.fail_connect:
            raise RuntimeError(self.fail_connect)
        return Endpoint(url=f"https://{sandbox_id}-{port}.example", token=f"tok-{sandbox_id}-{port}")

    def terminate(self, sandbox_id: str) -> None:
        self.terminated.append(sandbox_id)


@pytest.fixture
def cloud():
    return FakeCloud()


def api_with(*, store, cloud, ops=None, run_op=None, admin_token=ADMIN):
    return create_api(
        admin_token=admin_token,
        store=store,
        ops={} if ops is None else ops,
        cloud=cloud,
        run_op=run_op,  # None runs each operation inline, as the tests need
        revision="test-rev",
        modal_version="1.6.4",
        clock_ms=lambda: 1_000,
    )


@pytest.fixture
def client(tmp_path, cloud):
    return TestClient(api_with(store=StateStore(tmp_path, FakeVolume()), cloud=cloud))


def create(client, **body):
    return client.post("/machines", json={"name": "dev", "size": "default", "t3Version": "0.1.10", **body}, headers=AUTH)


ROUTES = [("GET", "/version"), ("POST", "/machines"), ("GET", "/machines/m_x"), ("GET", "/ops/op_x")]


@pytest.mark.parametrize(("method", "path"), ROUTES)
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": ADMIN}])
def test_every_route_answers_401_without_the_admin_token(client, method, path, headers):
    r = client.request(method, path, headers=headers, json={"name": "dev", "size": "default", "t3Version": "0.1.10"})

    assert r.status_code == 401


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

    Controller(store=store, ops=ops, cloud=cloud, clock_ms=lambda: 2_000).perform(op_id)

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


def test_an_empty_admin_token_is_refused_at_startup(tmp_path, cloud):
    with pytest.raises(ValueError):
        api_with(store=StateStore(tmp_path, FakeVolume()), cloud=cloud, admin_token="")
