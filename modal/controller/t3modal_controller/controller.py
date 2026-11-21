"""What the controller does for each request, with Modal behind the Cloud interface (implementation.md §6, §9)."""

import re
import secrets
import traceback
from collections.abc import Callable, MutableMapping
from typing import Any, Protocol

from .machines import Endpoint, Machine, new_machine, start_failed, started
from .sizes import Resources, resources
from .store import StateStore

SANDBOX_TAG = "t3modal"  # the tag key on every sandbox, with the machine id as its value (§6.4)
SANDBOX_TIMEOUT_S = 24 * 3600
T3_PORT = 3773
DAEMON_PORT = 3774


# A release such as 0.1.10, or a prerelease such as 0.1.11-nightly.20261118.4. The version reaches a
# shell command in the image build, so nothing else gets through. [0-9], because \d also matches
# digits from other scripts.
_T3_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?")


class InvalidT3Version(ValueError):
    pass


def check_t3_version(t3_version: str) -> None:
    if not _T3_VERSION.fullmatch(t3_version):
        raise InvalidT3Version(f"t3Version {t3_version!r} is not a version number such as 0.1.10")


class Cloud(Protocol):
    """The Modal calls the controller makes. ModalCloud in app.py is the real one."""

    def create_sandbox(self, *, t3_version: str, args: dict[str, Any]) -> str: ...
    def connect(self, sandbox_id: str, port: int) -> Endpoint: ...
    def terminate(self, sandbox_id: str) -> None: ...


def sandbox_name(machine_id: str) -> str:
    return "m-" + machine_id.removeprefix("m_")


def sandbox_args(m: Machine, res: Resources) -> dict[str, Any]:
    """Sandbox.create arguments for a machine (§9.2). No encrypted_ports: machines are reached by connect tokens (ADR 0003)."""
    return {
        "name": sandbox_name(m.id),
        "tags": {SANDBOX_TAG: m.id},
        "runtime": "vm",  # ADR 0004, no fallback
        "cpu": res.cpu,
        "memory": res.memory_mib,
        "timeout": SANDBOX_TIMEOUT_S,
        "_experimental_enable_snapshot": True,
    }


class Controller:
    def __init__(
        self,
        *,
        store: StateStore,
        ops: MutableMapping[str, dict[str, Any]],
        cloud: Cloud,
        clock_ms: Callable[[], int],
        new_id: Callable[[str], str] = lambda prefix: f"{prefix}_{secrets.token_hex(6)}",
    ):
        self.store = store
        self.ops = ops
        self.cloud = cloud
        self.clock_ms = clock_ms
        self.new_id = new_id

    def begin_create(self, *, name: str, size: str, t3_version: str) -> tuple[str, str]:
        """Records a new machine and an operation to start it. Raises UnknownSize or InvalidT3Version before writing anything."""
        resources(size)
        check_t3_version(t3_version)
        m = new_machine(id=self.new_id("m"), name=name, size=size, t3_version=t3_version)
        self.store.put(m)
        op_id = self.new_id("op")
        self.ops[op_id] = {"id": op_id, "kind": "create", "machineId": m.id, "status": "running", "error": None}
        return m.id, op_id

    def perform(self, op_id: str) -> None:
        """Runs an operation to its end. On Modal this runs in its own function call, past the web timeout.

        Modal can run the call again after a worker is lost, so a machine that is no longer starting
        means an earlier run finished, and this one only reports how it ended.
        """
        op = self.ops[op_id]
        m = self.store.get(op["machineId"])
        assert m is not None, f"operation {op_id} names a missing machine"
        if m.power != "starting":
            if m.power == "running":
                self.ops[op_id] = {**op, "status": "done"}
            else:
                self.ops[op_id] = {**op, "status": "failed", "error": m.error or f"machine is {m.power}"}
            return
        sandbox_id = None
        try:
            res = resources(m.size)
            sandbox_id = self.cloud.create_sandbox(t3_version=m.t3_version, args=sandbox_args(m, res))
            endpoints = {"t3": self.cloud.connect(sandbox_id, T3_PORT), "daemon": self.cloud.connect(sandbox_id, DAEMON_PORT)}
            self.store.put(started(m, sandbox_id=sandbox_id, endpoints=endpoints, resources=res, now_ms=self.clock_ms()))
            self.ops[op_id] = {**op, "status": "done"}
        except Exception as e:
            if sandbox_id is not None:
                try:
                    self.cloud.terminate(sandbox_id)
                except Exception:
                    traceback.print_exc()
            self.fail(op_id, f"{type(e).__name__}: {e}")

    def fail(self, op_id: str, error: str) -> None:
        """Ends a create operation as failed and stops its machine. The operation ends failed even if
        the state file can't be written, so nobody polls it forever."""
        op = self.ops[op_id]
        try:
            m = self.store.get(op["machineId"])
            if m is not None and m.power == "starting":
                self.store.put(start_failed(m, error=error, now_ms=self.clock_ms()))
        except Exception:
            traceback.print_exc()
        self.ops[op_id] = {**op, "status": "failed", "error": error}

    def op(self, op_id: str) -> dict[str, Any] | None:
        return self.ops.get(op_id)

    def machine(self, machine_id: str) -> Machine | None:
        return self.store.get(machine_id)
