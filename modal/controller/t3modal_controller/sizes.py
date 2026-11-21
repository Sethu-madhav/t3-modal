"""Machine sizes, from implementation.md §13. Phase 0 chose mode B: small requests with limits."""

from dataclasses import dataclass

REQUEST_CPU = 0.25
REQUEST_MEMORY_MIB = 1024


@dataclass(frozen=True)
class Resources:
    """Modal's (request, limit) pairs: cores, and memory in MiB."""

    cpu: tuple[float, float]
    memory_mib: tuple[int, int]


class UnknownSize(ValueError):
    pass


_LIMITS = {"small": (2, 4), "default": (4, 8), "large": (8, 16)}  # cores, GiB


def resources(size: str) -> Resources:
    if size not in _LIMITS:
        raise UnknownSize(f"unknown size {size!r}, expected one of {', '.join(_LIMITS)}")
    cores, gib = _LIMITS[size]
    return Resources(cpu=(REQUEST_CPU, cores), memory_mib=(REQUEST_MEMORY_MIB, gib * 1024))
