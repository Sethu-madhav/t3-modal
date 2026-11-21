"""Machine state files on the Volume, at <root>/machines/<id>.json (implementation.md §6.2).

Every read reloads the Volume first. Every write goes to a temporary file in the same
folder, is renamed into place, and is followed by a commit, so a reader never sees half a file.
The api serves up to 20 requests at once in threads, so one lock per process keeps a reload or
a commit from running while another thread has a temporary file open.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Callable, Protocol

from .machines import Machine

_lock = threading.Lock()


class Volume(Protocol):
    def reload(self) -> None: ...
    def commit(self) -> None: ...


class StateStore:
    def __init__(self, root: Path, volume: Volume):
        self._dir = Path(root) / "machines"
        self._volume = volume

    def get(self, machine_id: str) -> Machine | None:
        with _lock:
            return self._read(machine_id)

    def put(self, machine: Machine) -> None:
        with _lock:
            self._write(machine)

    def update(self, machine_id: str, change: Callable[[Machine], Machine]) -> Machine | None:
        """Reads the machine, applies change and writes the result, all under the lock, so another
        thread in this process can't write between the read and the write. Returns None and writes
        nothing if the machine doesn't exist. If change raises, nothing is written."""
        with _lock:
            m = self._read(machine_id)
            if m is None:
                return None
            m = change(m)
            self._write(m)
            return m

    def _read(self, machine_id: str) -> Machine | None:
        self._volume.reload()
        path = self._dir / f"{machine_id}.json"
        if not path.exists():
            return None
        return Machine.from_json(json.loads(path.read_text()))

    def _write(self, machine: Machine) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=f".{machine.id}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(machine.to_json(), f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._dir / f"{machine.id}.json")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        self._volume.commit()
