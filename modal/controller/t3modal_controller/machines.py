"""A machine's state file and the transitions the controller makes to it (implementation.md §6.2, §9)."""

from dataclasses import dataclass, field, replace
from typing import Any, Literal

from .sizes import Resources

Power = Literal["starting", "running", "stopped"]


class IllegalTransition(Exception):
    pass


@dataclass(frozen=True)
class Endpoint:
    """A connect URL and its token for one port (ADR 0003)."""

    url: str
    token: str


@dataclass(frozen=True)
class Machine:
    id: str
    name: str
    size: str
    t3_version: str
    power: Power
    images: list[str] = field(default_factory=list)  # newest first, [] means the base image
    sandbox_id: str | None = None
    started_at: int = 0
    stopped_at: int = 0
    endpoints: dict[str, Endpoint] | None = None  # "t3" and "daemon"
    runs: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "t3Version": self.t3_version,
            "power": self.power,
            "images": list(self.images),
            "sandboxId": self.sandbox_id,
            "startedAt": self.started_at,
            "stoppedAt": self.stopped_at,
            "endpoints": None
            if self.endpoints is None
            else {k: {"url": e.url, "token": e.token} for k, e in self.endpoints.items()},
            "runs": [dict(r) for r in self.runs],
            "error": self.error,
        }

    @classmethod
    def from_json(cls, doc: dict[str, Any]) -> "Machine":
        endpoints = doc.get("endpoints")
        return cls(
            id=doc["id"],
            name=doc["name"],
            size=doc["size"],
            t3_version=doc["t3Version"],
            power=doc["power"],
            images=list(doc.get("images", [])),
            sandbox_id=doc.get("sandboxId"),
            started_at=doc.get("startedAt", 0),
            stopped_at=doc.get("stoppedAt", 0),
            endpoints=None if endpoints is None else {k: Endpoint(**e) for k, e in endpoints.items()},
            runs=[dict(r) for r in doc.get("runs", [])],
            error=doc.get("error"),
        )


def new_machine(*, id: str, name: str, size: str, t3_version: str) -> Machine:
    return Machine(id=id, name=name, size=size, t3_version=t3_version, power="starting")


def started(m: Machine, *, sandbox_id: str, endpoints: dict[str, Endpoint], resources: Resources, now_ms: int) -> Machine:
    if m.power != "starting":
        raise IllegalTransition(f"machine {m.id} is {m.power}, not starting")
    run = {
        "sandboxId": sandbox_id,
        "start": now_ms,
        "end": 0,
        "cpuLimit": resources.cpu[1],
        "memLimitGiB": resources.memory_mib[1] // 1024,
    }
    return replace(
        m, power="running", sandbox_id=sandbox_id, started_at=now_ms, endpoints=endpoints, runs=[*m.runs, run], error=None
    )


def start_failed(m: Machine, *, error: str, now_ms: int) -> Machine:
    if m.power != "starting":
        raise IllegalTransition(f"machine {m.id} is {m.power}, not starting")
    return replace(m, power="stopped", sandbox_id=None, endpoints=None, stopped_at=now_ms, error=error)
