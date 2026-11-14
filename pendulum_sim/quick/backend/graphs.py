"""QML context object ``graphs``: the Graphs screen's selection and every live graph's data.

A graph item in QML (``GraphLines``, ``lineitem.py``) is given a :class:`GraphView` –
``graphs.view("angle")`` for a Graphs-screen card, ``panel.view(tileId)`` for a side-
panel graph tile – tells it its size and whether it is visible, and draws the view's
:attr:`GraphView.output`. Only active views are computed, at the graph rate (20–30 Hz,
driven by shown frames), from one telemetry read for all of them. The maths is
``curves.GraphState`` (Qt-free, incremental).
"""

from __future__ import annotations

import time

from PySide6.QtCore import Property, QObject, QSettings, QTimer, Signal, Slot

from ...mobile.plot_math import DEFAULT_SELECTION, GRAPHS, MAX_VISIBLE, graph_available, graph_series
from ...session import rules
from .curves import SMOOTHING_S, WINDOWS_S, GraphSpec, GraphState, Output, Style
from .lineitem import NOTES
from .signals import by_key, catalogue
from .simulation import SimulationBridge

DEFAULT_RATE_HZ = 25.0


class GraphView(QObject):
    """One live graph: a Graphs-screen card or a side-panel graph tile."""

    updated = Signal()
    legendChanged = Signal()
    axesChanged = Signal()

    def __init__(self, key: str, spec: GraphSpec, owner: GraphsBridge, title: str = "", parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.owner = owner
        self._title = title
        self.width = 0.0
        self.height = 0.0
        self.dpr = 1.0
        self.active = False
        self._wanted: set[int] = set()        # the items that show this view (a card being replaced and
                                              # its successor can both be there for a moment)
        self.state = GraphState(spec, owner.catalogue, owner.rate_telemetry)
        self.output: Output | None = None
        self._ticks: list[list[dict]] = [[], []]
        self._units: list[str] = []

    title = Property(str, lambda self: self._title, constant=True)
    legend = Property("QVariantList", lambda self: self.state.legend, notify=legendChanged)
    units = Property("QVariantList", lambda self: self._units, notify=axesChanged)
    ticks = Property("QVariantList", lambda self: self._ticks[0], notify=axesChanged)
    ticksRight = Property("QVariantList", lambda self: self._ticks[1], notify=axesChanged)
    unit = Property(str, lambda self: self._units[0] if self._units else "", notify=axesChanged)
    unitRight = Property(str, lambda self: self._units[1] if len(self._units) > 1 else "", notify=axesChanged)
    window = Property(float, lambda self: float(self.state.spec.window_s), notify=legendChanged)

    @Slot(float, float)
    def setSize(self, width: float, height: float) -> None:  # noqa: N802 - QML API
        self.width, self.height = float(width), float(height)

    def setActive(self, on: bool, owner: object | None = None) -> None:  # noqa: N802 - item API
        """Shown or not by ``owner`` (a GraphLines item); computed while any item shows it.

        A single flag was wrong when cards are rebuilt: the old card, torn down after its
        successor started, switched the shared view off – the new card then never got
        curves (blank, or frozen) until the screen was left and opened again.
        """
        key = id(owner) if owner is not None else 0
        if on:
            self._wanted.add(key)
        else:
            self._wanted.discard(key)
        self.active = bool(self._wanted)

    @Slot(result="QVariantList")
    def ranges(self) -> list:
        """The axes' current ``[min, max]`` (display units), e.g. to start a fixed scale from."""
        axes = self.output.axes if self.output is not None else []
        return [[float(a.lo), float(a.hi)] for a in axes if a.lo is not None and a.hi is not None]

    def set_style(self, style: Style) -> None:
        self.state.set_style(style)

    def configure(self, spec: GraphSpec) -> None:
        self.state.configure(spec, self.owner.catalogue)
        self.output = None
        self.legendChanged.emit()

    def compute(self, data, now: float) -> None:
        out = self.state.update(data, self.width, self.height, self.dpr, now)
        self.output = out
        ticks = [[{"y": y, "text": text} for y, text in ax.ticks] for ax in out.axes] + [[], []]
        units = [ax.unit for ax in out.axes]
        if ticks[:2] != self._ticks or units != self._units:
            self._ticks, self._units = ticks[:2], units
            self.axesChanged.emit()
        self.updated.emit()


class GraphsBridge(QObject):
    selectionChanged = Signal()
    availableChanged = Signal()
    windowChanged = Signal()
    loopChanged = Signal()
    rateChanged = Signal()
    smoothingChanged = Signal()
    catalogueChanged = Signal()
    problemChanged = Signal()

    def __init__(self, sim: SimulationBridge, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim = sim
        self.settings = QSettings()
        stored = self.settings.value("quick/graphs/selected", DEFAULT_SELECTION)
        self._selection = [str(k) for k in (stored if isinstance(stored, list) else [stored]) if str(k) in GRAPHS]
        self._window = int(self.settings.value("quick/graphs/window_s", 10))
        smoothing = str(self.settings.value("quick/graphs/smoothing", "off"))
        self._smoothing = smoothing if smoothing in SMOOTHING_S else "off"
        self._loop = 0
        self._rate = DEFAULT_RATE_HZ
        self.rate_telemetry = float(sim.session.config.simulation.telemetry_rate_hz)
        self.catalogue = {}
        self._views: dict[str, GraphView] = {}            # Graphs-screen cards
        self._tiles: dict[str, GraphView] = {}            # side-panel graph tiles
        self.ms = 0.0                                     # last refresh, for reports
        self.recording: list[float] | None = None
        self._due = 0.0
        self._problem = ""
        # Refreshed from the frame loop (on_frame) once a frame driver is attached, so a
        # refresh never triggers a render between paced frames; the timer is the fallback.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(int(1000 / self._rate))
        sim.structureChanged.connect(self._on_structure)
        sim.controllerChanged.connect(self._on_structure)
        sim.configChanged.connect(self._on_config)
        self._on_structure()

    # ------------------------------------------------------------ choices
    def _labels(self) -> list[str]:
        return rules.loop_labels(self.sim.session.config.controller)

    def _available(self) -> list[str]:
        plant = self.sim.session.config.plant
        return [k for k in GRAPHS if graph_available(k, plant.n_segments, plant.has_cart)]

    available = Property("QVariantList", lambda self: [{"key": k, "title": GRAPHS[k].title, "unit": GRAPHS[k].unit}
                                                       for k in self._available()], notify=availableChanged)
    selection = Property("QVariantList", lambda self: [k for k in self._selection if k in self._available()],
                         notify=selectionChanged)
    maxVisible = Property(int, lambda self: MAX_VISIBLE, constant=True)
    windows = Property("QVariantList", lambda self: list(WINDOWS_S), constant=True)
    # a drawing or computing error (shown on the Graphs screen instead of silently blank graphs)
    problem = Property(str, lambda self: self._problem, notify=problemChanged)

    @staticmethod
    def _notes_text() -> str:
        return "; ".join(f"{k}: {v}" for k, v in NOTES.items())
    smoothings = Property("QVariantList", lambda self: list(SMOOTHING_S), constant=True)
    loopLabels = Property("QVariantList", lambda self: self._labels(), notify=availableChanged)
    signals = Property("QVariantList", lambda self: [s.to_dict() for s in self.catalogue.values()],
                       notify=catalogueChanged)

    @Slot(str, bool)
    def select(self, key: str, on: bool) -> None:
        if key not in GRAPHS:
            return
        sel = [k for k in self._selection if k != key]
        if on:
            sel.append(key)
            sel = sel[-MAX_VISIBLE:]
        if sel != self._selection:
            self._selection = sel
            self.settings.setValue("quick/graphs/selected", sel)
            self.selectionChanged.emit()

    def _set_window(self, seconds: int) -> None:
        if int(seconds) != self._window:
            self._window = int(seconds)
            self.settings.setValue("quick/graphs/window_s", self._window)
            self._reconfigure_cards()
            self.windowChanged.emit()
            self.refresh()                           # new curves now, not one graph period later

    windowS = Property(int, lambda self: self._window, _set_window, notify=windowChanged)

    def _set_smoothing(self, level: str) -> None:
        if level in SMOOTHING_S and level != self._smoothing:
            self._smoothing = level
            self.settings.setValue("quick/graphs/smoothing", level)
            self._reconfigure_cards()
            self.smoothingChanged.emit()
            self.refresh()

    smoothing = Property(str, lambda self: self._smoothing, _set_smoothing, notify=smoothingChanged)

    def _set_loop(self, k: int) -> None:
        if int(k) != self._loop:
            self._loop = int(k)
            self._reconfigure_cards()
            self.loopChanged.emit()

    pidLoop = Property(int, lambda self: self._loop, _set_loop, notify=loopChanged)

    def _set_rate(self, hz: float) -> None:
        hz = min(max(float(hz), 5.0), 60.0)
        if hz != self._rate:
            self._rate = hz
            self.timer.setInterval(int(1000 / hz))
            self.rateChanged.emit()

    rateHz = Property(float, lambda self: self._rate, _set_rate, notify=rateChanged)

    def drive_from_frames(self) -> None:
        """The frame loop calls :meth:`on_frame` from now on."""
        self.timer.stop()

    def on_frame(self, now: float) -> None:
        """A frame is being shown: refresh if a refresh is due (graph rate)."""
        if now >= self._due:
            interval = 1.0 / self._rate
            self._due += interval                        # keeps the average rate exact …
            if self._due <= now:                         # … but no catching up after a pause
                self._due = now + interval
            self.refresh()

    # -------------------------------------------------------------- views
    def card_spec(self, key: str) -> GraphSpec:
        plant = self.sim.session.config.plant
        signals = [s.column for s in graph_series(key, plant.n_segments, self._labels(), self._loop)]
        return GraphSpec(signals, float(self._window), "auto", [], self._smoothing)

    @Slot(str, result=QObject)
    def view(self, key: str) -> QObject:
        """The view of a Graphs-screen card."""
        if key not in self._views:
            self._views[key] = GraphView(key, self.card_spec(key), self, GRAPHS[key].title if key in GRAPHS else key,
                                         self)
        return self._views[key]

    def tile_view(self, tile_id: str, spec: GraphSpec) -> GraphView:
        """The view of a side-panel graph tile (created or reconfigured)."""
        view = self._tiles.get(tile_id)
        if view is None:
            view = self._tiles[tile_id] = GraphView(tile_id, spec, self, "", self)
        elif view.state.spec != spec:
            view.configure(spec)
        return view

    def drop_tiles(self, keep: set[str]) -> None:
        """Views of removed tiles stop at once and are deleted a little later, when the QML
        items that showed them are surely gone."""
        for tile_id in [k for k in self._tiles if k not in keep]:
            view = self._tiles.pop(tile_id)
            view.active = False
            QTimer.singleShot(5000, view.deleteLater)

    def _reconfigure_cards(self) -> None:
        for key, v in self._views.items():
            if key in GRAPHS:
                v.configure(self.card_spec(key))

    # ----------------------------------------------------------- updates
    def _on_structure(self) -> None:
        plant = self.sim.session.config.plant
        labels = self._labels()
        self._loop = min(self._loop, max(len(labels) - 1, 0))
        self.catalogue = by_key(catalogue(plant.n_segments, plant.has_cart, labels))
        self.rate_telemetry = float(self.sim.session.config.simulation.telemetry_rate_hz)
        self._reconfigure_cards()
        for v in self._tiles.values():
            v.configure(v.state.spec)
        self.catalogueChanged.emit()
        self.availableChanged.emit()
        self.selectionChanged.emit()

    def _on_config(self) -> None:
        rate = float(self.sim.session.config.simulation.telemetry_rate_hz)
        if rate != self.rate_telemetry:
            self._on_structure()

    def refresh(self) -> None:
        """Compute the active views (one telemetry read for all of them)."""
        available = set(self._available())
        active = [v for k, v in self._views.items() if v.active and k in available]
        active += [v for v in self._tiles.values() if v.active]
        if not active:
            return
        started = time.perf_counter()
        names = ["t"]
        window = 0.0
        for v in active:
            names += [c for c in v.state.columns() if c not in names]
            window = max(window, float(v.state.spec.window_s))
        data = self.sim.source.tail(names, window + 0.5)
        now = time.monotonic()
        for v in active:
            try:
                v.compute(data, now)
            except Exception as exc:  # noqa: BLE001 - one graph must not stop the others
                NOTES[f"graph {v.key}"] = f"{type(exc).__name__}: {exc}"
        if NOTES and self._problem != self._notes_text():
            self._problem = self._notes_text()
            self.problemChanged.emit()
        self.ms = (time.perf_counter() - started) * 1000.0
        if self.recording is not None:
            self.recording.append(self.ms)
