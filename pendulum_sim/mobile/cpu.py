"""CPU-core diagnostics and pinning (Linux/Android; harmless no-ops elsewhere).

Phones and tablets mix fast "performance" cores with slow "efficiency" cores
(Xiaomi Pad 6 / Snapdragon 870: 1× 3.2 GHz + 3× 2.42 GHz Cortex-A77 and
4× 1.8 GHz Cortex-A55). The Python thread that runs the simulation and draws the
UI may be scheduled on a slow core, which makes everything – physics, drawing and
the auto-tuner – several times slower. :func:`pin_to_performance_cores` restricts
the *calling thread* to the fast cores; :func:`describe` reports where it runs.
"""

from __future__ import annotations

import os
from pathlib import Path

_SYS_CPU = Path("/sys/devices/system/cpu")


def max_frequencies() -> dict[int, int]:
    """Maximum frequency [kHz] per CPU, from sysfs (empty if unavailable)."""
    out: dict[int, int] = {}
    try:
        for d in _SYS_CPU.glob("cpu[0-9]*"):
            f = d / "cpufreq" / "cpuinfo_max_freq"
            try:
                out[int(d.name[3:])] = int(f.read_text().strip())
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return dict(sorted(out.items()))


def performance_cores(freqs: dict[int, int] | None = None, ratio: float = 0.7) -> set[int]:
    """CPUs whose maximum frequency is at least ``ratio`` × that of the fastest one.

    Empty when the cores are all alike (nothing to gain) or the information is missing.
    """
    freqs = max_frequencies() if freqs is None else freqs
    if not freqs:
        return set()
    top = max(freqs.values())
    fast = {cpu for cpu, f in freqs.items() if f >= ratio * top}
    return fast if len(fast) < len(freqs) else set()


def current_cpu() -> int | None:
    """CPU the calling thread last ran on (field 39 of /proc/thread-self/stat)."""
    for path in ("/proc/thread-self/stat", f"/proc/self/task/{_tid()}/stat"):
        try:
            text = Path(path).read_text()
        except (OSError, ValueError):
            continue
        try:
            fields = text[text.rindex(")") + 2:].split()
            return int(fields[36])           # field 39 overall; fields[0] is field 3
        except (ValueError, IndexError):
            return None
    return None


def _tid() -> int:
    try:
        import threading

        return threading.get_native_id()
    except Exception:  # noqa: BLE001
        return os.getpid()


def affinity() -> set[int]:
    try:
        return set(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return set()


def pin_to_performance_cores() -> set[int]:
    """Restrict the calling thread to the performance cores; returns the new set (or empty)."""
    fast = performance_cores()
    if not fast or not hasattr(os, "sched_setaffinity"):
        return set()
    try:
        os.sched_setaffinity(0, fast)        # pid 0 = the calling thread on Linux
    except OSError:
        return set()
    return fast


def unpin() -> None:
    if not hasattr(os, "sched_setaffinity"):
        return
    freqs = max_frequencies()
    try:
        os.sched_setaffinity(0, set(freqs) or set(range(os.cpu_count() or 1)))
    except OSError:
        pass


def describe() -> str:
    """Short text like ``cpu 7 (3.19 GHz max) · allowed 4–7``."""
    cpu = current_cpu()
    freqs = max_frequencies()
    if cpu is None:
        return f"{os.cpu_count() or '?'} cores"
    ghz = f" ({freqs[cpu] / 1e6:.2f} GHz max)" if cpu in freqs else ""
    allowed = sorted(affinity())
    span = f" · allowed {_ranges(allowed)}" if allowed else ""
    return f"cpu {cpu}{ghz}{span}"


def _ranges(cpus: list[int]) -> str:
    if not cpus:
        return ""
    parts, start, prev = [], cpus[0], cpus[0]
    for c in cpus[1:] + [None]:
        if c is not None and c == prev + 1:
            prev = c
            continue
        parts.append(f"{start}" if start == prev else f"{start}–{prev}")
        if c is not None:
            start = prev = c
    return ",".join(parts)
