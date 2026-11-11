"""Simulation off the GUI thread: fixed-step physics in a forked process or a thread (Qt-free).

The Qt Quick UI never steps the simulator. A *source* owns the unchanged
:class:`~pendulum_sim.simulation.Simulator`, advances it against the wall clock
(:class:`~pendulum_sim.simulation.realtime.RealtimeStepper`, the same pacing as the
other front ends) and publishes time-stamped state samples and the telemetry into a
block of shared numbers. Once per displayed frame the GUI reads the state at
"now − a few milliseconds", interpolated between the two samples around that moment
(:meth:`SimSource.read`, into a preallocated array), so motion is smooth whatever the
phase between display frames and physics ticks, and a dropped display frame never
slows or distorts the simulation.

Edits arrive as commands. Configuration edits are the operations of
:mod:`pendulum_sim.session` (``("controller", ctrl)``, ``("structure", plant, ctrl)`` …)
and are applied with :func:`pendulum_sim.session.apply` – the same simulator calls the
desktop window makes. Run control: ``run``, ``reset``, ``restart``, ``step`` (one
controller period), ``push``, ``drag``, ``speed``.

Two sources with one interface and one memory layout:

:class:`ForkSource` (default where ``os.fork`` exists – Android, Linux, macOS)
    The simulator runs in a forked child process; the shared block is anonymous shared
    memory and commands go over a pipe. The GUI process's interpreter is never blocked
    by physics (on the tablet this halved the GUI's per-frame Python time at p95 and
    removed the frame-gap spikes of the thread variant). The child only runs
    NumPy/Python, never touches Qt, and leaves with ``os._exit`` – the pattern the
    auto-tuner's ``ForkPool`` already uses inside the Android app.

:class:`ThreadSource`
    The same loop in a Python thread (Windows, or as a fallback). Physics and the GUI
    thread's Python then share the interpreter lock.

Results are the core's: a sample at step ``k`` equals a plain ``Simulator`` stepped
``k`` times, edits included (tests/test_quick_sim_source.py).
"""

from __future__ import annotations

import collections
import mmap
import os
import pickle
import struct
import threading
import time
from typing import Callable, Iterable, NamedTuple

import numpy as np

from ...physics.params import MAX_SEGMENTS
from ...session import apply as apply_operation
from ...session import operations
from ...simulation import AppConfig, Simulator
from ...simulation.engine import SimulationDiverged
from ...simulation.realtime import RealtimeStepper
from ...simulation.telemetry import COLUMNS, INDEX

PERIOD_S = 0.002             # physics tick (the thread/process sleeps in between)
BUDGET_S = 0.006             # physics work per tick before the backlog is dropped
HISTORY = 128                # samples kept for interpolation (≈ 0.25 s)
RENDER_DELAY_S = 0.006       # the GUI shows the state this long ago (≥ 2 ticks: always bracketed)
TELEMETRY_WINDOW_S = 30.0
TELEMETRY_RATE_CAPACITY = 400.0   # shared telemetry sized for 30 s at this rate (or the configured one)
MAX_STATE = 2 * (MAX_SEGMENTS + 1)

# one history row: wall, t, steps, applied, tip, epoch, state (MAX_STATE, zero-padded)
R_WALL, R_T, R_STEPS, R_APPLIED, R_TIP, R_EPOCH = range(6)
R_STATE = 6
ROW = R_STATE + MAX_STATE

# header (float64 slots)
(H_HIST, H_TEL, H_TEL_START, H_STEPS, H_BUSY, H_RUNNING, H_RTF, H_EPOCH, H_T, H_RATE, H_NSTATE,
 H_SPEED, H_MSG_SEQ, H_MSG_KIND, H_MSG_LEN, H_PHYS_MS) = range(16)
HEADER = 32
MSG_BYTES = 512
MSG_DIVERGED, MSG_REJECTED = 1, 2

COMMANDS = ("run", "reset", "restart", "step", "steps", "push", "drag", "speed", "config", "structure",
            "plant", "controller", "simulation", "reset_integrators", "reset_if_idle")


class Sample(NamedTuple):
    """Simulator state at one moment (convenience copy; the frame path uses :meth:`SimSource.read`)."""

    wall: float
    t: float
    steps: int
    applied: float
    tip: float
    state: np.ndarray
    epoch: int = 0


def telemetry_rows(config: AppConfig) -> int:
    return int(TELEMETRY_WINDOW_S * max(config.simulation.telemetry_rate_hz, TELEMETRY_RATE_CAPACITY)) + 64


class SharedBlock:
    """Views on the shared numbers: header, history ring, telemetry ring, message bytes."""

    def __init__(self, buffer, tel_rows: int) -> None:
        doubles = HEADER + HISTORY * ROW + tel_rows * len(COLUMNS)
        values = np.frombuffer(buffer, dtype=np.float64, count=doubles)
        self.header = values[:HEADER]
        self.hist = values[HEADER:HEADER + HISTORY * ROW].reshape(HISTORY, ROW)
        self.tel = values[HEADER + HISTORY * ROW:].reshape(tel_rows, len(COLUMNS))
        self.msg = np.frombuffer(buffer, dtype=np.uint8, count=MSG_BYTES, offset=doubles * 8)
        self.tel_rows = tel_rows

    @staticmethod
    def size(tel_rows: int) -> int:
        return (HEADER + HISTORY * ROW + tel_rows * len(COLUMNS)) * 8 + MSG_BYTES


# ------------------------------------------------------------------ the loop
class SimLoop:
    """The simulator plus pacing and commands – what runs inside the child or the thread."""

    def __init__(self, config: AppConfig) -> None:
        self.sim = Simulator(config, telemetry_window_s=TELEMETRY_WINDOW_S)
        self.stepper = RealtimeStepper(budget_s=BUDGET_S, max_frame_s=0.1, clock=time.perf_counter)
        self.running = False
        self.speed = 1.0
        self.drag = 0.0
        self.last_wall: float | None = None
        self.steps_total = 0
        self.busy_s = 0.0
        self.epoch = 0                      # incremented whenever the state jumps (reset, new config)
        self.message: tuple[int, str] | None = None   # (kind, text) not yet published
        self.on_work: Callable[[float], None] | None = None   # per-tick work time (performance hints)

    # ---------------------------------------------------------- commands
    def handle(self, command: tuple) -> None:
        name, args = command[0], command[1:]
        try:
            self._handle(name, args)
        except SimulationDiverged as exc:
            self._diverged(exc)
        except (ValueError, KeyError) as exc:   # invalid edit: refused, the simulation goes on
            self.message = (MSG_REJECTED, str(exc))

    def _handle(self, name: str, args: tuple) -> None:
        if name == "run":
            self.running, self.last_wall = bool(args[0]), None
            self.stepper.reset_clock()
        elif name in ("reset", "restart"):
            self.sim.reset()
            self._jumped()
            self.running = name == "restart"
            self.last_wall = None
        elif name == "push":
            self.sim.apply_disturbance(float(args[0]), float(args[1]))
        elif name == "drag":
            self.drag = float(args[0])
        elif name == "speed":
            self.speed = float(args[0])
        elif name == "step":                # one controller period, paused (the desktop's Step)
            self.running = False
            self._advance(lambda: operations.single_step(self.sim))
        elif name == "steps":               # exact number of steps (tests, benchmarks)
            self._advance(lambda: operations.run_steps(self.sim, int(args[0])))
        elif name == "reset_if_idle":       # initial pose edited: show it if stopped at t = 0
            if not self.running and self.sim.time == 0.0:
                self.sim.reset()
                self._jumped()
        elif name == "config":
            self.sim = Simulator(args[0], telemetry_window_s=TELEMETRY_WINDOW_S)
            self._jumped()
        else:                               # configuration edits: the session's operations
            self.sim = apply_operation(self.sim, (name, *args))
            if name == "structure":
                self._jumped()

    def _jumped(self) -> None:
        self.epoch += 1
        self.stepper.reset_clock()

    def _diverged(self, exc: SimulationDiverged) -> None:
        self.running = False
        self.drag = 0.0
        self.message = (MSG_DIVERGED, str(exc))

    def _advance(self, work: Callable[[], tuple[int, SimulationDiverged | None]]) -> int:
        started = time.perf_counter()
        done, exc = work()
        self.busy_s += time.perf_counter() - started
        self.steps_total += done
        if exc is not None:
            self._diverged(exc)
        return done

    # --------------------------------------------------------------- tick
    def tick(self, now: float) -> int:
        """Advance for the wall time since the previous tick; returns the steps taken."""
        if not self.running:
            self.last_wall = None
            return 0
        if self.last_wall is None:
            self.last_wall = now
            return 0
        wall, self.last_wall = now - self.last_wall, now
        sim = self.sim
        if self.drag != 0.0:                # dragging the pendulum: a force over this tick
            duration = max(min(wall, self.stepper.max_frame_s) * self.speed, sim.config.simulation.dt)
            sim.apply_disturbance(self.drag, duration)
        started = time.perf_counter()
        before = sim.steps
        try:
            self.stepper.tick(sim, wall, self.speed)
        except SimulationDiverged as exc:
            self._diverged(exc)
        work = time.perf_counter() - started
        self.busy_s += work
        done = sim.steps - before
        self.steps_total += done
        if self.on_work is not None:
            self.on_work(work)
        return done


class Publisher:
    """Writes a :class:`SimLoop`'s state into a :class:`SharedBlock` (single writer)."""

    def __init__(self, block: SharedBlock) -> None:
        self.block = block
        self.copied = 0                  # telemetry rows of this epoch already shared
        self.epoch = -1
        self.msg_seq = 0

    def publish(self, loop: SimLoop) -> None:
        header, hist, tel = self.block.header, self.block.hist, self.block.tel
        sim = loop.sim
        if loop.epoch != self.epoch:     # reset / new configuration: fresh telemetry
            self.epoch, self.copied = loop.epoch, 0
            header[H_TEL_START] = header[H_TEL]
        recorder = sim.telemetry
        new = recorder.version - self.copied
        if new > 0:
            size = len(recorder)
            rows = recorder._data[max(size - new, 0):size]
            written = int(header[H_TEL])
            start = written % self.block.tel_rows
            n = len(rows)
            first = min(n, self.block.tel_rows - start)
            tel[start:start + first] = rows[:first]
            if first < n:
                tel[:n - first] = rows[first:]
            header[H_TEL] = written + n
            self.copied = recorder.version
        written = int(header[H_HIST])
        row = hist[written % HISTORY]
        row[R_WALL], row[R_T], row[R_STEPS] = time.monotonic(), sim.time, sim.steps
        row[R_APPLIED], row[R_TIP], row[R_EPOCH] = sim.applied, sim.tip_force, loop.epoch
        n = sim.state.size
        row[R_STATE:R_STATE + n] = sim.state
        row[R_STATE + n:] = 0.0
        header[H_HIST] = written + 1
        header[H_STEPS], header[H_BUSY] = loop.steps_total, loop.busy_s
        header[H_RUNNING], header[H_RTF] = loop.running, loop.stepper.realtime_factor
        header[H_EPOCH], header[H_T] = loop.epoch, sim.time
        header[H_RATE], header[H_NSTATE] = sim.config.simulation.telemetry_rate_hz, n
        header[H_SPEED], header[H_PHYS_MS] = loop.speed, loop.stepper.physics_ms
        if loop.message is not None:
            kind, text = loop.message
            loop.message = None
            data = text.encode("utf-8")[:MSG_BYTES]
            self.block.msg[:len(data)] = np.frombuffer(data, dtype=np.uint8)
            header[H_MSG_KIND], header[H_MSG_LEN] = kind, len(data)
            self.msg_seq += 1
            header[H_MSG_SEQ] = self.msg_seq


def serve(loop: SimLoop, publisher: Publisher, commands: Callable[[], Iterable[tuple] | None],
          alive: Callable[[], bool]) -> None:
    """The physics loop of both sources: commands, a paced tick, publish – every ``PERIOD_S``.

    ``commands()`` returns the pending commands, or ``None`` when the GUI side is gone.
    """
    publisher.publish(loop)
    while alive():
        pending = commands()
        if pending is None:
            return
        changed = False
        for command in pending:
            if command[0] == "stop":
                return
            loop.handle(command)
            changed = True
        if loop.tick(time.monotonic()) or changed:
            publisher.publish(loop)
        time.sleep(PERIOD_S)                             # releases the interpreter lock


# ---------------------------------------------------------------- sources
class SimSource:
    """Interface of both sources (all methods are called from the GUI thread)."""

    kind = ""

    def __init__(self, config: AppConfig, buffer) -> None:
        self.config = config
        self.block = SharedBlock(buffer, telemetry_rows(config))
        self._seen_msg = 0

    def start(self) -> None: ...
    def stop(self) -> None: ...
    def send(self, *command) -> None: ...

    # ------------------------------------------------------------ frame
    def read(self, out: np.ndarray, wall: float | None = None) -> bool:
        """Interpolated history row at ``wall`` (default: now − ``RENDER_DELAY_S``) into ``out``
        (``ROW`` floats). No arrays are allocated; ``False`` before the first sample."""
        header, hist = self.block.header, self.block.hist
        if wall is None:
            wall = time.monotonic() - RENDER_DELAY_S
        for _attempt in range(3):
            written = int(header[H_HIST])
            if written == 0:
                return False
            i = written - 1
            lowest = max(written - 24, 0)
            b = hist[i % HISTORY]
            if b[R_WALL] <= wall:
                out[:] = b
            else:
                while True:
                    if i <= lowest:
                        out[:] = hist[lowest % HISTORY]
                        break
                    a = hist[(i - 1) % HISTORY]
                    if a[R_WALL] <= wall:
                        if a[R_EPOCH] != b[R_EPOCH] or b[R_WALL] <= a[R_WALL]:
                            out[:] = b                   # a reset in between: no blending
                        else:
                            np.subtract(b, a, out=out)
                            out *= (wall - a[R_WALL]) / (b[R_WALL] - a[R_WALL])
                            out += a
                            out[R_STEPS], out[R_EPOCH] = b[R_STEPS], b[R_EPOCH]
                        break
                    i -= 1
                    b = a
            if int(header[H_HIST]) - written < HISTORY - 32:  # rows not overwritten meanwhile
                return True
        return True

    def sample(self, wall: float | None = None) -> Sample | None:
        """The interpolated state as a :class:`Sample` (allocates; for tests and tools)."""
        row = np.empty(ROW)
        if not self.read(row, wall):
            return None
        n = int(self.block.header[H_NSTATE]) or MAX_STATE
        return Sample(row[R_WALL], row[R_T], int(row[R_STEPS]), row[R_APPLIED], row[R_TIP],
                      row[R_STATE:R_STATE + n].copy(), int(row[R_EPOCH]))

    # -------------------------------------------------------- telemetry
    def tail(self, names: Iterable[str], seconds: float) -> dict[str, np.ndarray]:
        """The last ``seconds`` of the current epoch's telemetry for ``names`` (copies)."""
        names = tuple(names)
        header, tel, size = self.block.header, self.block.tel, self.block.tel_rows
        written, first = int(header[H_TEL]), int(header[H_TEL_START])
        rate = float(header[H_RATE]) or self.config.simulation.telemetry_rate_hz
        n = min(written - first, int(seconds * rate) + 1, size - 32)
        if n <= 0:
            return {name: np.zeros(0) for name in names}
        cols = [INDEX[name] for name in names]
        start = (written - n) % size
        if start + n <= size:
            block = tel[start:start + n][:, cols]
        else:
            block = np.concatenate((tel[start:, cols], tel[:start + n - size, cols]))
        return {name: block[:, k] for k, name in enumerate(names)}

    def latest(self, name: str) -> float:
        """Newest telemetry value of ``name`` (NaN before the first row)."""
        header = self.block.header
        written, first = int(header[H_TEL]), int(header[H_TEL_START])
        if written <= first:
            return float("nan")
        return float(self.block.tel[(written - 1) % self.block.tel_rows, INDEX[name]])

    # -------------------------------------------------------------- state
    def stats(self) -> dict[str, float]:
        h = self.block.header
        return {"steps": float(h[H_STEPS]), "busy_s": float(h[H_BUSY]), "running": float(h[H_RUNNING]),
                "realtime_factor": float(h[H_RTF]), "epoch": float(h[H_EPOCH]), "t": float(h[H_T]),
                "speed": float(h[H_SPEED]), "physics_ms": float(h[H_PHYS_MS])}

    @property
    def running(self) -> bool:
        return bool(self.block.header[H_RUNNING])

    def message(self) -> tuple[int, str] | None:
        """A new message from the simulation – ``(MSG_DIVERGED | MSG_REJECTED, text)`` – once."""
        h = self.block.header
        seq = int(h[H_MSG_SEQ])
        if seq == self._seen_msg:
            return None
        self._seen_msg = seq
        n = int(h[H_MSG_LEN])
        return int(h[H_MSG_KIND]), bytes(self.block.msg[:n]).decode("utf-8", "replace")

    # ------------------------------------------------------- convenience
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
        super().__init__(config, bytearray(SharedBlock.size(telemetry_rows(config))))
        self.loop = SimLoop(config.copy())
        self.publisher = Publisher(self.block)
        self.commands: collections.deque[tuple] = collections.deque()
        self._alive = False
        self._thread: threading.Thread | None = None
        self.publisher.publish(self.loop)

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
        if command[0] != "stop" and command[0] not in COMMANDS:
            raise KeyError(f"unknown command {command[0]!r}")
        # a private copy, as the process source gets: later edits of the caller's
        # objects must not reach the simulator behind the session's back
        self.commands.append(pickle.loads(pickle.dumps(command, protocol=pickle.HIGHEST_PROTOCOL)))

    def _pending(self) -> list[tuple]:
        out = []
        while self.commands:
            out.append(self.commands.popleft())
        return out

    def _run(self) -> None:
        serve(self.loop, self.publisher, self._pending, lambda: self._alive)


# --------------------------------------------------------------------- fork
class ForkSource(SimSource):
    """Simulator in a forked child; samples and telemetry in shared memory (single writer)."""

    kind = "process"

    @staticmethod
    def available() -> bool:
        return hasattr(os, "fork")

    def __init__(self, config: AppConfig) -> None:
        if not self.available():
            raise RuntimeError("os.fork is not available on this platform")
        self._mem = mmap.mmap(-1, SharedBlock.size(telemetry_rows(config)))   # anonymous, shared
        super().__init__(config, self._mem)
        self.pid = 0
        self._write_fd = -1
        self.on_child_start: Callable[[SimLoop], None] | None = None   # e.g. performance hints

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
        if command[0] != "stop" and command[0] not in COMMANDS:
            raise KeyError(f"unknown command {command[0]!r}")
        if self._write_fd < 0:
            return
        payload = pickle.dumps(command, protocol=pickle.HIGHEST_PROTOCOL)
        try:
            os.write(self._write_fd, struct.pack("<I", len(payload)) + payload)
        except OSError:
            pass

    # -------------------------------------------------------------- child
    def _child(self, read_fd: int) -> None:
        os.set_blocking(read_fd, False)
        loop = SimLoop(self.config)
        if self.on_child_start is not None:
            try:
                self.on_child_start(loop)
            except Exception:  # noqa: BLE001 - optional (performance hints)
                pass
        parent = os.getppid()
        pending = bytearray()

        def commands() -> list[tuple] | None:
            if os.getppid() != parent:                   # the app died
                return None
            try:
                chunk = os.read(read_fd, 65536)
                if chunk == b"":                         # the GUI process closed the pipe
                    return None
                pending.extend(chunk)
            except BlockingIOError:
                pass
            except OSError:
                return None
            out = []
            while len(pending) >= 4:
                (size,) = struct.unpack_from("<I", pending)
                if len(pending) < 4 + size:
                    break
                out.append(pickle.loads(bytes(pending[4:4 + size])))
                del pending[:4 + size]
            return out

        serve(loop, Publisher(self.block), commands, lambda: True)


def make_source(kind: str, config: AppConfig) -> SimSource:
    """``process`` where ``os.fork`` exists (falls back to ``thread``), or ``thread``."""
    if kind == "process" and ForkSource.available():
        return ForkSource(config)
    return ThreadSource(config)
