"""QML context object ``sim``: the running simulation – state per frame, read-outs, run control.

Owns the edited configuration (a shared :class:`~pendulum_sim.session.Session`) and the
physics source (``sim_source.py``: a forked process by default). Every edit made
anywhere in the app ends in :meth:`SimulationBridge.apply_operations`, which forwards the
session's operations to the physics process.

Per displayed frame QML's ``FrameAnimation`` calls :meth:`SimulationBridge.advance`: one
read of the interpolated state into a preallocated row, a handful of float properties,
one ``frame`` signal (QML positions the pendulum in its handler). Read-outs are
formatted at 15 Hz, messages from the physics process (divergence, refused edits) are
picked up at the same rate.
"""

from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import Property, QObject, Signal, Slot

from ...physics import MultiLinkPendulum
from ...session import Session, rules
from ...simulation import AppConfig
from .sim_source import MSG_DIVERGED, R_APPLIED, R_EPOCH, R_STATE, R_T, R_TIP, ROW, ForkSource, make_source

READOUT_HZ = 15.0
PUSH_DURATION_S = 0.1


class FrameClock:
    """Frame gaps for the on-screen fps and the pacer; GUI-thread CPU per frame."""

    def __init__(self) -> None:
        self.fps = 0.0
        self.last = 0.0
        self.gap_ms = 0.0
        self.recording: list[tuple[float, float]] | None = None    # (gap ms, Python ms) for reports
        self._count = 0
        self._second = time.perf_counter()

    def tick(self, now: float, busy_ms: float) -> None:
        if self.last:
            self.gap_ms = (now - self.last) * 1000.0
            if self.recording is not None:
                self.recording.append((self.gap_ms, busy_ms))
        self.last = now
        self._count += 1
        if now - self._second >= 0.5:
            self.fps = self._count / (now - self._second)
            self._count = 0
            self._second = now


class SimulationBridge(QObject):
    frame = Signal()
    readoutsChanged = Signal()
    runningChanged = Signal()
    structureChanged = Signal()
    speedChanged = Signal()
    pushForceChanged = Signal()
    physicsChanged = Signal()
    configChanged = Signal()                 # a new configuration was loaded (scenario, file)
    controllerChanged = Signal()             # the session's controller changed (any reason)
    plantChanged = Signal(bool)              # the plant changed; True = structural
    message = Signal(str, str, arguments=["kind", "text"])   # kind: "info" | "warning" | "error"

    def __init__(self, config: AppConfig, physics: str = "process", parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = Session(config)
        self._physics = physics if (physics != "process" or ForkSource.available()) else "thread"
        self.source = make_source(self._physics, self.session.config)
        self.source.start()
        self.row = np.zeros(ROW)
        self.clock = FrameClock()
        self.n = 1
        self._lengths: list[float] = []
        self._structure()
        self._cart = 0.0
        self._th = [0.0, 0.0, 0.0]
        self._omega = 0.0
        self._applied = 0.0
        self._tip = 0.0
        self._t = 0.0
        self._epoch = 0
        self._running = False
        self._suspended_running = False
        self._speed = 1.0
        self._readout_due = 0.0
        self._busy_ms = 0.0
        self._texts = {"angle": "+0.00", "rate": "+0.0", "cart": "+0.000", "force": "+0.00",
                       "time": "T 000.00 s", "fps": "0 FPS", "speed": "", "energy": ""}
        self.on_frame = None                  # optional per-frame hook (pacer, performance hints)

    # ----------------------------------------------------------- structure
    def _structure(self) -> None:
        cfg = self.session.config
        model = MultiLinkPendulum(cfg.plant)
        self.n = model.n
        self._lengths = [float(v) for v in model.lengths]

    segments = Property(int, lambda self: self.n, notify=structureChanged)
    lengths = Property("QVariantList", lambda self: self._lengths, notify=structureChanged)
    hasCart = Property(bool, lambda self: self.session.config.plant.has_cart, notify=structureChanged)
    trackLength = Property(float, lambda self: float(self.session.config.plant.track_length), notify=structureChanged)
    maxOutput = Property(float, lambda self: float(self.session.config.plant.actuator.max_output),
                         notify=structureChanged)
    scenarioName = Property(str, lambda self: self.session.config.name, notify=configChanged)
    scenarioDescription = Property(str, lambda self: self.session.config.description, notify=configChanged)
    processAvailable = Property(bool, lambda self: ForkSource.available(), constant=True)
    speeds = Property("QVariantList", lambda self: list(rules.SPEEDS), constant=True)

    # ------------------------------------------------------------ per frame
    @Slot()
    def advance(self) -> None:
        """One displayed frame: read the interpolated state and notify QML."""
        started = time.perf_counter()
        row = self.row
        if self.source.read(row):
            n = self.n
            self._cart = row[R_STATE]
            th = self._th
            for i in range(n):
                th[i] = row[R_STATE + 1 + i]
            self._omega = row[R_STATE + n + 2]
            self._applied = row[R_APPLIED]
            self._tip = row[R_TIP]
            self._t = row[R_T]
            self._epoch = int(row[R_EPOCH])
        self.clock.tick(started, self._busy_ms)
        self.frame.emit()                         # QML places its items in this call
        if started >= self._readout_due:
            self._readout_due = started + 1.0 / READOUT_HZ
            self._readouts()
        if self.on_frame is not None:
            self.on_frame(started)
        self._busy_ms = (time.perf_counter() - started) * 1000.0

    def _readouts(self) -> None:
        t = self._texts
        t["angle"] = f"{math.degrees(self._wrap(self._th[0])):+.2f}"
        t["rate"] = f"{math.degrees(self._omega):+.1f}"
        t["cart"] = f"{self._cart:+.3f}"
        t["force"] = f"{self._applied:+.2f}"
        t["time"] = f"T {self._t:06.2f} s"
        t["fps"] = f"{self.clock.fps:.0f} FPS"
        stats = self.source.stats()
        if self._running:
            rtf = stats["realtime_factor"]
            t["speed"] = f"{rtf:.2f}×" + (" (slower than real time)" if rtf < 0.95 * stats["speed"] else "")
        else:
            t["speed"] = ""
        energy = self.source.latest("energy")
        t["energy"] = "" if math.isnan(energy) else f"E {energy:.4f} J"
        running = bool(stats["running"])
        if running != self._running:              # stopped by the physics (divergence)
            self._running = running
            self.runningChanged.emit()
        self.readoutsChanged.emit()
        msg = self.source.message()
        if msg is not None:
            kind, text = msg
            if kind == MSG_DIVERGED:
                self.message.emit("error", f"{text}\n\nThe simulation was paused. Reset it, or reduce the "
                                           "time step / gains.")
            else:
                self.message.emit("warning", f"⚠ {text}")

    @staticmethod
    def _wrap(a: float) -> float:
        return (a + math.pi) % (2.0 * math.pi) - math.pi

    cartX = Property(float, lambda self: self._cart, notify=frame)
    th1 = Property(float, lambda self: self._th[0], notify=frame)
    th2 = Property(float, lambda self: self._th[1], notify=frame)
    th3 = Property(float, lambda self: self._th[2], notify=frame)
    simTime = Property(float, lambda self: self._t, notify=frame)
    applied = Property(float, lambda self: self._applied, notify=frame)
    tipForce = Property(float, lambda self: self._tip, notify=frame)

    angleText = Property(str, lambda self: self._texts["angle"], notify=readoutsChanged)
    rateText = Property(str, lambda self: self._texts["rate"], notify=readoutsChanged)
    cartText = Property(str, lambda self: self._texts["cart"], notify=readoutsChanged)
    forceText = Property(str, lambda self: self._texts["force"], notify=readoutsChanged)
    timeText = Property(str, lambda self: self._texts["time"], notify=readoutsChanged)
    fpsText = Property(str, lambda self: self._texts["fps"], notify=readoutsChanged)
    speedText = Property(str, lambda self: self._texts["speed"], notify=readoutsChanged)
    energyText = Property(str, lambda self: self._texts["energy"], notify=readoutsChanged)
    fps = Property(float, lambda self: self.clock.fps, notify=readoutsChanged)

    # ----------------------------------------------------------- run control
    def _get_running(self) -> bool:
        return self._running

    def _set_running(self, on: bool) -> None:
        if on != self._running:
            self._running = on
            self.source.set_running(on)
            self.runningChanged.emit()

    running = Property(bool, _get_running, _set_running, notify=runningChanged)
    paused = Property(bool, lambda self: not self._running and self._t > 0.0, notify=runningChanged)

    @Slot()
    def toggle(self) -> None:
        self._set_running(not self._running)

    @Slot()
    def start(self) -> None:
        self._set_running(True)

    @Slot()
    def pause(self) -> None:
        self._set_running(False)

    @Slot()
    def reset(self) -> None:
        """Back to the initial state, stopped."""
        self.source.send("reset")
        if self._running:
            self._running = False
            self.runningChanged.emit()

    @Slot()
    def restart(self) -> None:
        """Back to the initial state and run."""
        self.source.send("restart")
        if not self._running:
            self._running = True
            self.runningChanged.emit()

    @Slot()
    def step(self) -> None:
        """One controller period (pauses)."""
        self.source.send("step")
        if self._running:
            self._running = False
            self.runningChanged.emit()

    @Slot(int)
    def push(self, direction: int) -> None:
        force = (1.0 if direction >= 0 else -1.0) * self.session.config.push_force
        self.source.push(force, PUSH_DURATION_S)
        self.message.emit("info", f"Tip push {force:+.2f} N for {PUSH_DURATION_S:g} s")

    @Slot(int, int, float)
    def pushAt(self, direction: int, segment: int, ratio: float) -> None:  # noqa: N802 - QML API
        """A push pulse at ``ratio`` (0 … 1) along ``segment`` (0-based) – a tap on the pendulum."""
        force = (1.0 if direction >= 0 else -1.0) * self.session.config.push_force
        self.source.push(force, PUSH_DURATION_S, int(segment), float(ratio))
        where = "tip" if segment < 0 or (segment == self.n - 1 and ratio >= 0.999) \
            else f"segment {segment + 1} at {ratio * 100:.0f} %"
        self.message.emit("info", f"Push {force:+.2f} N for {PUSH_DURATION_S:g} s at the {where}")

    @Slot(float)
    def drag(self, offset: float) -> None:
        """Dragging the pendulum by the tip; ``offset`` relative to the pendulum's total length."""
        self.source.send("drag", rules.drag_force(self.session.config.push_force, offset))

    @Slot(float, int, float)
    def dragAt(self, offset: float, segment: int, ratio: float) -> None:  # noqa: N802 - QML API
        """Dragging the pendulum by a point (``segment``, ``ratio``; -1: the tip): a pull that
        grows with the finger's distance, at most the push force (``rules.point_drag_force``)."""
        force = rules.point_drag_force(self.session.config.push_force, offset)
        self.source.send("drag", force, int(segment), float(ratio))

    @Slot()
    def endDrag(self) -> None:  # noqa: N802 - QML API
        self.source.send("drag", 0.0)

    def _set_speed(self, value: float) -> None:
        self.source.send("speed", float(value))
        self._speed = float(value)
        self.speedChanged.emit()

    speed = Property(float, lambda self: self._speed, _set_speed, notify=speedChanged)

    def _set_push_force(self, value: float) -> None:
        self.session.set_push_force(float(value))
        self.pushForceChanged.emit()

    pushForce = Property(float, lambda self: float(self.session.config.push_force), _set_push_force,
                         notify=pushForceChanged)

    # --------------------------------------------------------- configuration
    def apply_operations(self, ops: list[tuple]) -> None:
        """Forward the session's operations to the physics process."""
        for op in ops:
            self.source.send(*op)

    def load(self, config: AppConfig) -> bool:
        """Replace the whole configuration (scenario, file); ``False`` if it is invalid."""
        try:
            ops = self.session.load(config)
        except ValueError as exc:
            self.message.emit("error", f"Invalid configuration: {exc}")
            return False
        self._running = False
        self.apply_operations(ops)
        self._structure()
        self.runningChanged.emit()
        self.structureChanged.emit()
        self.configChanged.emit()
        self.controllerChanged.emit()
        self.pushForceChanged.emit()
        if config.description:
            self.message.emit("info", config.description)
        return True

    def structure_changed(self) -> None:
        """The plant's structure changed through an edit (segments, base)."""
        self._structure()
        self.structureChanged.emit()

    # ------------------------------------------------------- physics variant
    def _get_physics(self) -> str:
        return self._physics

    def _set_physics(self, kind: str) -> None:
        if kind == self._physics or (kind == "process" and not ForkSource.available()):
            return
        self.source.stop()
        self._physics = kind
        self.source = make_source(kind, self.session.config)
        self.source.start()
        self.source.set_running(self._running)
        self.physicsChanged.emit()

    physics = Property(str, _get_physics, _set_physics, notify=physicsChanged)

    # ------------------------------------------------------------- lifecycle
    def suspend(self) -> None:
        """The app went to the background: stop the physics (resumed by :meth:`resume`)."""
        self._suspended_running = self._running
        self._set_running(False)
        self.source.send("drag", 0.0)

    def resume(self) -> None:
        if self._suspended_running:
            self._suspended_running = False
            self._set_running(True)

    def state(self) -> tuple[float, list[float]]:
        return self._cart, self._th

    def shutdown(self) -> None:
        self.source.stop()
