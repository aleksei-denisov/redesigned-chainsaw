"""CPU time per thread of this process, read from ``/proc`` (Linux and Android; Qt-free).

Lets the benchmark report what each displayed frame costs on the GUI thread and on the
scene graph's render thread without running any Python on the render thread. Qt names
that thread ``QSGRenderThread``; its presence is also how the spike confirms that the
threaded render loop is active.
"""

from __future__ import annotations

import os
from pathlib import Path

RENDER_THREAD = "QSGRenderThread"
TASKS = Path("/proc/self/task")


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
    pid = os.getpid()
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
        key = "gui" if task.name == str(pid) else "render" if name == RENDER_THREAD else "other"
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
