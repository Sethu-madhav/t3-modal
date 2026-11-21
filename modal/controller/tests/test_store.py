"""The state store on the Volume: atomic writes, a commit after each write, a reload before each read (§6.2)."""

import json
import os
import threading
from dataclasses import replace

from t3modal_controller.machines import new_machine
from t3modal_controller.store import StateStore


class FakeVolume:
    """Records reloads and commits, and what the state folder held at each commit."""

    def __init__(self, root):
        self.root = root
        self.events: list[str] = []
        self.at_commit: list[dict[str, str]] = []

    def reload(self):
        self.events.append("reload")

    def commit(self):
        self.events.append("commit")
        self.at_commit.append({str(p.relative_to(self.root)): p.read_text() for p in self.root.rglob("*") if p.is_file()})


def a_machine(**changes):
    return replace(new_machine(id="m_1", name="dev", size="default", t3_version="0.1.10"), **changes)


def test_a_written_machine_reads_back(tmp_path):
    store = StateStore(tmp_path, FakeVolume(tmp_path))

    store.put(a_machine())

    assert store.get("m_1") == a_machine()


def test_an_unknown_machine_reads_as_none(tmp_path):
    assert StateStore(tmp_path, FakeVolume(tmp_path)).get("m_nope") is None


def test_each_write_commits_once_with_only_the_complete_file_in_place(tmp_path):
    volume = FakeVolume(tmp_path)
    store = StateStore(tmp_path, volume)

    store.put(a_machine())
    store.put(a_machine(name="renamed"))

    assert volume.events.count("commit") == 2
    last = volume.at_commit[-1]
    assert list(last) == ["machines/m_1.json"]
    assert json.loads(last["machines/m_1.json"])["name"] == "renamed"


def test_each_read_reloads_the_volume_first(tmp_path):
    volume = FakeVolume(tmp_path)
    store = StateStore(tmp_path, volume)
    store.put(a_machine())
    volume.events.clear()

    store.get("m_1")

    assert volume.events == ["reload"]


def test_a_write_that_fails_partway_leaves_the_previous_file_whole(tmp_path):
    volume = FakeVolume(tmp_path)
    store = StateStore(tmp_path, volume)
    store.put(a_machine())

    broken = a_machine(name="half", runs=[{"sandboxId": "sb-1", "start": object()}])
    try:
        store.put(broken)
    except TypeError:
        pass

    assert store.get("m_1") == a_machine()
    assert [p.name for p in (tmp_path / "machines").iterdir()] == ["m_1.json"]
    assert volume.events.count("commit") == 1


def test_a_read_from_another_thread_waits_until_a_write_has_committed(tmp_path, monkeypatch):
    """The api runs up to 20 requests at once in threads. A reload while a temporary file is open
    would fail on Modal, and a commit could carry the half-written file."""
    volume = FakeVolume(tmp_path)
    store = StateStore(tmp_path, volume)
    seen_at_reload: list[list[str]] = []
    reload = volume.reload

    def reload_and_look():
        seen_at_reload.append(sorted(p.name for p in (tmp_path / "machines").iterdir()))
        reload()

    volume.reload = reload_and_look
    real_fsync = os.fsync

    def fsync_then_read_elsewhere(fd):
        real_fsync(fd)
        if not seen_at_reload:
            reader = threading.Thread(target=store.get, args=("m_1",))
            reader.start()
            reader.join(timeout=0.3)
            readers.append(reader)

    readers: list[threading.Thread] = []
    monkeypatch.setattr(os, "fsync", fsync_then_read_elsewhere)

    store.put(a_machine())
    readers[0].join(timeout=5)

    assert seen_at_reload == [["m_1.json"]]
