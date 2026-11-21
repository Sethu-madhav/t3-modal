"""The size table, from implementation.md §13: every size requests 0.25 cores and 1 GiB, with limits by size."""

import pytest

from t3modal_controller.sizes import UnknownSize, resources


@pytest.mark.parametrize(
    ("size", "cpu", "memory_mib"),
    [
        ("small", (0.25, 2), (1024, 4096)),
        ("default", (0.25, 4), (1024, 8192)),
        ("large", (0.25, 8), (1024, 16384)),
    ],
)
def test_each_size_requests_a_quarter_core_and_1_gib_with_its_own_limit(size, cpu, memory_mib):
    r = resources(size)

    assert r.cpu == cpu
    assert r.memory_mib == memory_mib


def test_an_unknown_size_is_rejected():
    with pytest.raises(UnknownSize):
        resources("huge")
