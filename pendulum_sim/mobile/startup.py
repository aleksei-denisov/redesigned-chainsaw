"""Start-up timing for the mobile app (Qt-free, imported first).

Times are measured from the start of the *process*, not of Python: on Android the
process starts when the launcher icon is tapped, then Java/Qt start, the APK's
Python files are unpacked on the first launch, and only then does ``main.py`` run.
Linux (and Android) report the process start in ``/proc/self/stat``; elsewhere the
reference is the moment this module was imported.

The steps are printed (``adb logcat`` tag ``python``) and shown in
*Settings → Debug → Start-up times*.
"""

from __future__ import annotations

import os
import time


def process_age() -> float | None:
    """Seconds since this process started, or ``None`` where /proc is not available."""
    try:
        with open("/proc/self/stat", encoding="ascii") as f:
            stat = f.read()
        with open("/proc/uptime", encoding="ascii") as f:
            uptime = float(f.read().split()[0])
        # field 22 (starttime, clock ticks since boot); the command name in field 2
        # may contain spaces, so split after its closing parenthesis
        fields = stat[stat.rindex(")") + 2:].split()
        start_ticks = int(fields[19])
        return max(0.0, uptime - start_ticks / os.sysconf("SC_CLK_TCK"))
    except (OSError, ValueError, IndexError):
        return None


class StartupTimer:
    """Marks of the start-up sequence: (step, seconds since process start)."""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        age = process_age()
        self.from_process = age is not None
        self._offset = age if age is not None else 0.0
        self.marks: list[tuple[str, float]] = [("Python running" if self.from_process else "start", self._offset)]
        self.notes: list[tuple[str, float]] = []

    def now(self) -> float:
        return self._offset + time.perf_counter() - self._t0

    def mark(self, step: str, echo: bool = True) -> float:
        t = self.now()
        self.marks.append((step, t))
        if echo:
            print(f"startup {t:7.3f} s  {step}", flush=True)
        return t

    def note(self, what: str, seconds: float) -> None:
        """A later one-off cost (e.g. a screen built on first use)."""
        self.notes.append((what, seconds))
        print(f"startup note: {what} in {seconds:.3f} s", flush=True)

    @property
    def total(self) -> float:
        return self.marks[-1][1]

    def report(self) -> str:
        ref = "process start" if self.from_process else "module import"
        lines = [f"Start-up (seconds since {ref}):"]
        prev = 0.0
        for step, t in self.marks:
            lines.append(f"  {t:6.2f}  (+{t - prev:5.2f})  {step}")
            prev = t
        for what, sec in self.notes:
            lines.append(f"  later: {what} {sec:.2f} s")
        return "\n".join(lines)


TIMER = StartupTimer()
