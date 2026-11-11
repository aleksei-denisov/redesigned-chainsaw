"""CPU time per thread of this process, read from ``/proc`` (Linux and Android; Qt-free).

Lets the benchmark report what each displayed frame costs on the GUI thread and on the
scene graph's render thread without running any Python on the render thread. Qt names
that thread ``QSGRenderThread``; its presence is also how the spike confirms that the
threaded render loop is active.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

RENDER_THREAD = "QSGRenderThread"
TASKS = Path("/proc/self/task")
CPUFREQ = Path("/sys/devices/system/cpu/cpufreq")


def _cpu_seconds(task: Path) -> float:
    try:  # nanoseconds on CPU (needs scheduler statistics in the kernel)
        value = int((task / "schedstat").read_text(encoding="ascii").split()[0])
        if value > 0:
            return value / 1e9
    except (OSError, ValueError, IndexError):
        pass
    stat = (task / "stat").read_text(encoding="ascii")
    fields = stat[stat.rindex(")") + 2:].split()          # the name may contain spaces
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")   # utime + stime


def thread_times() -> dict[str, float]:
    """CPU seconds so far: ``gui`` (main thread), ``render`` (if it exists) and ``other``.

    Empty where ``/proc`` is not available.
    """
    out: dict[str, float] = {}
    # the thread running Python's main thread is Qt's GUI thread; on Android that is not
    # the process's first thread (the Java UI thread)
    gui = str(threading.main_thread().native_id)
    try:
        tasks = list(TASKS.iterdir())
    except OSError:
        return out
    for task in tasks:
        try:
            seconds = _cpu_seconds(task)
            name = (task / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, ValueError, IndexError):
            continue                                       # the thread ended meanwhile
        key = "gui" if task.name == gui else "render" if name == RENDER_THREAD else "other"
        out[key] = out.get(key, 0.0) + seconds
    return out


def render_loop() -> str:
    """``threaded``, ``not threaded`` or ``unknown`` (no /proc)."""
    times = thread_times()
    if not times:
        return "unknown (no /proc on this platform)"
    if "render" in times:
        return "threaded (separate QSGRenderThread)"
    return "not threaded (no QSGRenderThread: rendering runs on the GUI thread)"


def cpu_frequencies(root: Path = CPUFREQ) -> str:
    """Clock state per group of cores, e.g. ``cpu 0-3: 1.80 of 1.80 GHz · cpu 4-6: 1.9 of 2.42 GHz (limit 2.00)``.

    ``now of maximum``; a *limit* below the hardware maximum is shown when the system caps
    the group (thermal throttling, battery saver). Empty where sysfs gives no information.
    """
    def read(path: Path) -> int | None:
        try:
            return int(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    parts = []
    try:
        policies = sorted(root.glob("policy[0-9]*"), key=lambda p: int(p.name[6:]))
    except OSError:
        return ""
    for policy in policies:
        hardware, limit = read(policy / "cpuinfo_max_freq"), read(policy / "scaling_max_freq")
        now = read(policy / "scaling_cur_freq")
        if hardware is None:
            continue
        try:
            cpus = (policy / "related_cpus").read_text(encoding="ascii").split()
            name = cpus[0] if len(cpus) == 1 else f"{cpus[0]}-{cpus[-1]}"
        except (OSError, IndexError):
            name = policy.name[6:]
        text = f"cpu {name}: " + (f"{now / 1e6:.2f} of " if now is not None else "") + f"{hardware / 1e6:.2f} GHz"
        if limit is not None and limit < hardware:
            text += f" (limit {limit / 1e6:.2f})"
        parts.append(text)
    return " · ".join(parts)
