"""How a machine's state file changes as the controller creates and starts it (implementation.md §6.2, §9.1, §9.2)."""

import pytest

from t3modal_controller.machines import (
    Endpoint,
    IllegalTransition,
    Machine,
    StaleSandbox,
    heartbeat,
    new_machine,
    start_failed,
    started,
)
from t3modal_controller.sizes import resources

ENDPOINTS = {
    "t3": Endpoint(url="https://t3.example", token="tok-t3"),
    "daemon": Endpoint(url="https://daemon.example", token="tok-daemon"),
}


def a_new_machine() -> Machine:
    return new_machine(id="m_1", name="dev", size="default", t3_version="0.1.10")


def test_a_new_machine_is_starting_from_the_base_image_with_no_sandbox():
    m = a_new_machine()

    assert m.power == "starting"
    assert m.sandbox_id is None
    assert m.images == []
    assert m.endpoints is None


def test_a_started_sandbox_makes_the_machine_running_with_both_connect_urls():
    m = started(a_new_machine(), sandbox_id="sb-1", endpoints=ENDPOINTS, resources=resources("default"), now_ms=5_000)

    assert m.power == "running"
    assert m.sandbox_id == "sb-1"
    assert m.started_at == 5_000
    assert m.endpoints == ENDPOINTS
    assert m.runs == [{"sandboxId": "sb-1", "start": 5_000, "end": 0, "cpuLimit": 4, "memLimitGiB": 8}]


def test_a_failed_start_leaves_the_machine_stopped_with_the_error():
    m = start_failed(a_new_machine(), error="VM sandbox could not start", now_ms=7_000)

    assert m.power == "stopped"
    assert m.sandbox_id is None
    assert m.stopped_at == 7_000
    assert m.error == "VM sandbox could not start"


def test_a_running_machine_cannot_be_started_again():
    m = started(a_new_machine(), sandbox_id="sb-1", endpoints=ENDPOINTS, resources=resources("default"), now_ms=5_000)

    with pytest.raises(IllegalTransition):
        started(m, sandbox_id="sb-2", endpoints=ENDPOINTS, resources=resources("default"), now_ms=6_000)


def a_running_machine() -> Machine:
    return started(a_new_machine(), sandbox_id="sb-1", endpoints=ENDPOINTS, resources=resources("default"), now_ms=5_000)


def test_a_heartbeat_from_the_current_sandbox_records_its_time_and_schedule():
    m = heartbeat(a_running_machine(), sandbox_id="sb-1", next_due_at=90_000, keep_awake_until=60_000, now_ms=8_000)

    assert m.power == "running"
    assert m.last_heartbeat_at == 8_000
    assert m.next_due_at == 90_000
    assert m.keep_awake_until == 60_000


def test_a_heartbeat_can_clear_the_schedule():
    m = heartbeat(a_running_machine(), sandbox_id="sb-1", next_due_at=90_000, keep_awake_until=60_000, now_ms=8_000)

    m = heartbeat(m, sandbox_id="sb-1", next_due_at=None, keep_awake_until=0, now_ms=9_000)

    assert m.next_due_at is None
    assert m.keep_awake_until == 0


@pytest.mark.parametrize(
    ("machine", "sandbox_id"),
    [(a_running_machine(), "sb-old"), (a_new_machine(), "sb-1"), (start_failed(a_new_machine(), error="x", now_ms=1), "sb-1")],
    ids=["an older sandbox", "a starting machine", "a stopped machine"],
)
def test_a_heartbeat_from_a_sandbox_that_is_not_current_is_refused(machine, sandbox_id):
    with pytest.raises(StaleSandbox):
        heartbeat(machine, sandbox_id=sandbox_id, next_due_at=None, keep_awake_until=0, now_ms=8_000)


def test_the_state_file_round_trips_through_json_with_the_plans_field_names():
    m = heartbeat(a_running_machine(), sandbox_id="sb-1", next_due_at=90_000, keep_awake_until=60_000, now_ms=8_000)

    doc = m.to_json()

    assert doc["id"] == "m_1"
    assert doc["sandboxId"] == "sb-1"
    assert doc["endpoints"]["t3"] == {"url": "https://t3.example", "token": "tok-t3"}
    assert (doc["lastHeartbeatAt"], doc["nextDueAt"], doc["keepAwakeUntil"]) == (8_000, 90_000, 60_000)
    assert Machine.from_json(doc) == m
