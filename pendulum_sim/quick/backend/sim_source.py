"""Simulation off the GUI thread: fixed-step physics in a thread or a forked process (Qt-free).

The Qt Quick UI never steps the simulator. A *source* owns the unchanged
:class:`~pendulum_sim.simulation.Simulator`, advances it against the wall clock
(:class:`~pendulum_sim.simulation.realtime.RealtimeStepper`, the same pacing as the
other front ends) and publishes time-stamped state samples. Once per displayed frame
the GUI asks for the state at "now − a few milliseconds" and gets a linear
interpolation between the two samples around that moment, so motion is smooth whatever
the phase between display frames and physics ticks, and a dropped display frame never
slows or distorts the simulation.

Two sources with one interface:

:class:`ThreadSource`
    The simulator runs in a Python thread. Simple, and configuration changes are plain
    method calls, but physics and the GUI thread's Python share the interpreter lock:
    while a batch of physics steps runs, the GUI thread's Python waits.

:class:`ForkSource`
    The simulator runs in a forked child process (Linux/Android/macOS) and writes the
    samples and the telemetry into shared memory; commands go over a pipe. The GUI
    process's interpreter is never blocked by physics. The child only runs
    NumPy/Python, never touches Qt, and leaves with ``os._exit`` – the pattern the
    auto-tuner's ``ForkPool`` already uses inside the Android app.

Results are the core's: a sample at step ``k`` equals a plain ``Simulator`` stepped
``k`` times (tests/test_quick_sim_source.py).
"""

from __future__ import annotations

import collections
import mmap
import os
import pickle
import struct
import threading
import time
from typing import NamedTuple

import numpy as np

from ...simulation import AppConfig, Simulator
from ...simulation.engine import SimulationDiverged
from ...simulation.realtime import RealtimeStepper
from ...simulation.telemetry import COLUMNS, INDEX

PERIOD_S = 0.002             # physics tick (the thread/process sleeps in between)
BUDGET_S = 0.006             # physics work per tick before the backlog is dropped
HISTORY = 128                # samples kept for interpolation (≈ 0.25 s)
RENDER_DELAY_S = 0.006       # the GUI shows the state this long ago (≥ 2 ticks: always bracketed)
TELEMETRY_WINDOW_S = 30.0


class Sample(NamedTuple):
    """Simulator state at one moment. ``wall`` is ``time.monotonic()`` at publication."""

    wall: float
    t: float
    steps: int
    applied: float
    tip: float
    state: np.ndarray


def interpolate(samples: list[Sample], wall: float) -> Sample | None:
    """State at wall time ``wall``: linear between the bracketing samples, clamped at the ends."""
    if not samples:
        return None
    if wall >= samples[-1].wall:
        return samples[-1]
    for i in range(len(samples) - 1, 0, -1):
        a, b = samples[i - 1], samples[i]
        if a.wall <= wall:
            if b.steps < a.steps or b.wall <= a.wall:      # a reset in between: no blending
                return b
            f = (wall - a.wall) / (b.wall - a.wall)
            return Sample(wall, a.t + f * (b.t - a.t), b.steps, a.applied + f * (b.applied - a.applied),
                          a.tip + f * (b.tip - a.tip), a.state + f * (b.state - a.state))
    return samples[0]


class SimLoop:
    """The simulator plus pacing and commands – what runs inside the thread or the child."""

    def __init__(self, config: AppConfig) -> None:
        self.sim = Simulator(config, telemetry_window_s=TELEMETRY_WINDOW_S)
        self.stepper = RealtimeStepper(budget_s=BUDGET_S, max_frame_s=0.1, clock=time.perf_counter)
        self.running = False
        self.speed = 1.0
        self.last_wall: float | None = None
        self.steps_total = 0
        self.busy_s = 0.0
        self.epoch = 0                      # incremented on reset / new configuration
        self.error = ""

    def handle(self, command: tuple) -> None:
        name, args = command[0], command[1:]
        if name == "run":
            self.running, self.last_wall = bool(args[0]), None
            self.stepper.reset_clock()
        elif name == "reset":
            self.sim.reset()
            self.epoch += 1
            self.error = ""
            self.stepper.reset_clock()
        elif name == "push":
            self.sim.apply_disturbance(float(args[0]), float(args[1]))
        elif name == "speed":
            self.speed = float(args[0])
        elif name == "step":                # single steps while paused
            self._advance(lambda: [self.sim.step() for _ in range(int(args[0]))])
        elif name == "config":
            self.sim = Simulator(args[0], telemetry_window_s=TELEMETRY_WINDOW_S)
            self.epoch += 1
            self.error = ""
            self.stepper.reset_clock()

    def _advance(self, work) -> int:
        before = self.sim.steps
        started = time.perf_counter()
        try:
            work()
        except SimulationDiverged as exc:
            self.running = False
            self.error = str(exc)
        self.busy_s += time.perf_counter() - started
        done = self.sim.steps - before
        self.steps_total += done
        return done

    def tick(self, now: float) -> int:
        """Advance for the wall time since the previous tick; returns the steps taken."""
        if not self.running:
            self.last_wall = None
            return 0
        if self.last_wall is None:
            self.last_wall = now
            return 0
        wall, self.last_wall = now - self.last_wall, now
        return self._advance(lambda: self.stepper.tick(self.sim, wall, self.speed))

    def sample(self, wall: float) -> Sample:
        sim = self.sim
        return Sample(wall, sim.time, sim.steps, float(sim.applied), float(sim.tip_force), sim.state.copy())


class SimSource:
    """Interface of both sources (all methods are called from the GUI thread)."""

    kind = ""

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def send(self, *command) -> None: ...
    def sample(self, wall: float | None = None) -> Sample | None: ...
    def tail(self, names: tuple[str, ...], seconds: float) -> dict[str, np.ndarray]: ...
    def stats(self) -> dict[str, float]: ...

    # convenience
    def set_running(self, on: bool) -> None:
        self.send("run", on)

    def reset(self) -> None:
        self.send("reset")

    def push(self, force: float, duration: float = 0.1) -> None:
        self.send("push", force, duration)


# ------------------------------------------------------------------- thread
class ThreadSource(SimSource):
    kind = "thread"

    def __init__(self, config: AppConfig) -> None:
        self.loop = SimLoop(config)
        self.history: collections.deque[Sample] = collections.deque(maxlen=HISTORY)
        self.lock = threading.Lock()                     # simulator + telemetry
        self.commands: collections.deque[tuple] = collections.deque()
        self._alive = False
        self._thread: threading.Thread | None = None
        self.history.append(self.loop.sample(time.monotonic()))

    def start(self) -> None:
        if self._thread is None:
            self._alive = True
            self._thread = threading.Thread(target=self._run, name="physics", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._alive = False
        if self._thread is not None:
            self._thread.join(2.0)
            self._thread = None

    def send(self, *command) -> None:
        self.commands.append(command)

    def _run(self) -> None:
        loop, history, lock, commands = self.loop, self.history, self.lock, self.commands
        while self._alive:
            changed = False
            with lock:
                while commands:
                    loop.handle(commands.popleft())
                    changed = True
                steps = loop.tick(time.monotonic())
                if steps or changed:
                    history.append(loop.sample(time.monotonic()))
            time.sleep(PERIOD_S)                         # releases the interpreter lock

    def sample(self, wall: float | None = None) -> Sample | None:
        wall = time.monotonic() - RENDER_DELAY_S if wall is None else wall
        return interpolate(list(self.history), wall)

    def tail(self, names: tuple[str, ...], seconds: float) -> dict[str, np.ndarray]:
        with self.lock:
            tel = self.loop.sim.telemetry
            t = tel.column("t")
            if t.size == 0:
                return {n: np.zeros(0) for n in names}
            start = int(np.searchsorted(t, t[-1] - seconds))
            return {n: tel.column(n)[start:].copy() for n in names}

    def stats(self) -> dict[str, float]:
        loop = self.loop
        return {"steps": float(loop.steps_total), "busy_s": loop.busy_s, "running": float(loop.running),
                "realtime_factor": loop.stepper.realtime_factor, "epoch": float(loop.epoch),
                "diverged": float(bool(loop.error)), "t": loop.sim.time}


# --------------------------------------------------------------------- fork
H_HIST, H_TEL, H_STEPS, H_BUSY, H_RUNNING, H_RTF, H_DIVERGED, H_EPOCH, H_TEL_START, H_T = range(10)
HEADER = 16


class ForkSource(SimSource):
    """Simulator in a forked child; samples and telemetry in shared memory (single writer)."""

    kind = "process"

    @staticmethod
    def available() -> bool:
        return hasattr(os, "fork")

    def __init__(self, config: AppConfig) -> None:
        if not self.available():
            raise RuntimeError("os.fork is not available on this platform")
        self.config = config
        self.nstate = 2 * (config.plant.n_segments + 1)
        self.row = 5 + self.nstate                       # wall, t, steps, applied, tip, state…
        self.tel_rows = int(TELEMETRY_WINDOW_S * config.simulation.telemetry_rate_hz) + 64
        self.tel_cols = len(COLUMNS)
        doubles = HEADER + HISTORY * self.row + self.tel_rows * self.tel_cols
        self._mem = mmap.mmap(-1, doubles * 8)           # anonymous, shared with the child
        buf = np.frombuffer(self._mem, dtype=np.float64)
        self.header = buf[:HEADER]
        self.hist = buf[HEADER:HEADER + HISTORY * self.row].reshape(HISTORY, self.row)
        self.tel = buf[HEADER + HISTORY * self.row:].reshape(self.tel_rows, self.tel_cols)
        self.pid = 0
        self._write_fd = -1

    # ------------------------------------------------------------- parent
    def start(self) -> None:
        if self.pid:
            return
        read_fd, write_fd = os.pipe()
        import warnings

        with warnings.catch_warnings():                  # "fork in a multi-threaded process"
            warnings.simplefilter("ignore", DeprecationWarning)
            pid = os.fork()
        if pid == 0:                                     # child: never returns
            try:
                os.close(write_fd)
                self._child(read_fd)
            finally:
                os._exit(0)
        os.close(read_fd)
        self.pid, self._write_fd = pid, write_fd

    def stop(self) -> None:
        if not self.pid:
            return
        try:
            self.send("stop")
            os.close(self._write_fd)
        except OSError:
            pass
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            done, _status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                break
            time.sleep(0.01)
        else:
            import signal

            try:
                os.kill(self.pid, signal.SIGKILL)
                os.waitpid(self.pid, 0)
            except OSError:
                pass
        self.pid, self._write_fd = 0, -1

    def send(self, *command) -> None:
        if self._write_fd < 0:
            return
        payload = pickle.dumps(command, protocol=pickle.HIGHEST_PROTOCOL)
        try:
            os.write(self._write_fd, struct.pack("<I", len(payload)) + payload)
        except OSError:
            pass

    def _samples(self, count: int = 12) -> list[Sample]:
        for _ in range(3):
            written = int(self.header[H_HIST])
            n = min(written, count)
            if n == 0:
                return []
            idx = np.arange(written - n, written) % HISTORY
            rows = self.hist[idx].copy()
            if int(self.header[H_HIST]) - written < HISTORY - count:   # not overwritten meanwhile
                return [Sample(r[0], r[1], int(r[2]), r[3], r[4], r[5:]) for r in rows]
        return []

    def sample(self, wall: float | None = None) -> Sample | None:
        wall = time.monotonic() - RENDER_DELAY_S if wall is None else wall
        return interpolate(self._samples(), wall)

    def tail(self, names: tuple[str, ...], seconds: float) -> dict[str, np.ndarray]:
        written, first = int(self.header[H_TEL]), int(self.header[H_TEL_START])
        rate = self.config.simulation.telemetry_rate_hz
        n = min(written - first, int(seconds * rate) + 1, self.tel_rows - 32)
        if n <= 0:
            return {name: np.zeros(0) for name in names}
        idx = np.arange(written - n, written) % self.tel_rows
        cols = [INDEX[name] for name in names]
        block = self.tel[np.ix_(idx, cols)]              # fancy indexing copies
        return {name: block[:, k] for k, name in enumerate(names)}

    def stats(self) -> dict[str, float]:
        h = self.header
        return {"steps": float(h[H_STEPS]), "busy_s": float(h[H_BUSY]), "running": float(h[H_RUNNING]),
                "realtime_factor": float(h[H_RTF]), "epoch": float(h[H_EPOCH]),
                "diverged": float(h[H_DIVERGED]), "t": float(h[H_T])}

    # -------------------------------------------------------------- child
    def _child(self, read_fd: int) -> None:
        os.set_blocking(read_fd, False)
        loop = SimLoop(self.config)
        header, hist, tel = self.header, self.hist, self.tel
        parent = os.getppid()
        pending = b""
        copied = 0                                       # telemetry rows of this epoch already shared
        epoch = -1

        def publish() -> None:
            nonlocal copied, epoch
            if loop.epoch != epoch:                      # reset / new configuration: fresh telemetry
                epoch, copied = loop.epoch, 0
                header[H_TEL_START] = header[H_TEL]
            recorder = loop.sim.telemetry
            total = recorder.version                     # rows appended since clear()
            new = total - copied
            if new > 0:
                size = len(recorder)
                rows = recorder._data[max(size - new, 0):size]
                written = int(header[H_TEL])
                idx = np.arange(written, written + len(rows)) % self.tel_rows
                tel[idx] = rows
                header[H_TEL] = written + len(rows)
                copied = total
            s = loop.sample(time.monotonic())
            written = int(header[H_HIST])
            row = hist[written % HISTORY]
            row[0], row[1], row[2], row[3], row[4] = s.wall, s.t, s.steps, s.applied, s.tip
            row[5:] = s.state
            header[H_HIST] = written + 1
            header[H_STEPS], header[H_BUSY] = loop.steps_total, loop.busy_s
            header[H_RUNNING], header[H_RTF] = loop.running, loop.stepper.realtime_factor
            header[H_DIVERGED], header[H_EPOCH], header[H_T] = bool(loop.error), loop.epoch, loop.sim.time

        publish()
        while True:
            changed = False
            try:
                chunk = os.read(read_fd, 65536)
                if chunk == b"":                         # the GUI process closed the pipe
                    return
                pending += chunk
            except BlockingIOError:
                pass
            except OSError:
                return
            while len(pending) >= 4:
                (size,) = struct.unpack("<I", pending[:4])
                if len(pending) < 4 + size:
                    break
                command = pickle.loads(pending[4:4 + size])
                pending = pending[4 + size:]
                if command[0] == "stop":
                    return
                loop.handle(command)
                changed = True
            if loop.tick(time.monotonic()) or changed:
                publish()
            if os.getppid() != parent:                   # the app died
                return
            time.sleep(PERIOD_S)


def make_source(kind: str, config: AppConfig) -> SimSource:
    if kind == "process" and ForkSource.available():
        return ForkSource(config)
    return ThreadSource(config)
