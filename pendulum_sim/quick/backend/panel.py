"""QML context object ``panel``: the configurable side panel of the Simulate screen.

The layout itself (tiles, validation, saved layouts) is ``panel_model.PanelModel``
(Qt-free); this bridge persists it in QSettings, gives each graph tile its live
``GraphView`` (from ``graphs``), and computes the value tiles' numbers at 15 Hz on shown
frames – one shared-memory read per value – and the status tiles' rows when the
configuration changes.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Property, QObject, QSettings, Signal, Slot

from .curves import GraphSpec
from .graphs import GraphsBridge
from .panel_model import MAX_TILES, PanelModel, rows, status_lines
from .signals import format_value
from .simulation import SimulationBridge

VALUES_HZ = 15.0


class _SettingsStore:
    """The key–value interface of ``PanelModel`` on QSettings."""

    def __init__(self) -> None:
        self.qs = QSettings()

    def get(self, key: str, default=None):
        value = self.qs.value(key, default)
        return default if value is None else value

    def set(self, key: str, value) -> None:
        self.qs.setValue(key, value)


class PanelBridge(QObject):
    tilesChanged = Signal()
    valuesChanged = Signal()
    statusChanged = Signal()
    editingChanged = Signal()
    selectedChanged = Signal()
    layoutsChanged = Signal()

    def __init__(self, sim: SimulationBridge, graphs: GraphsBridge, store=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.sim, self.graphs = sim, graphs
        self.model = PanelModel(store or _SettingsStore())
        self._editing = False
        self._selected = ""                     # the tile open in the tile editor
        self._values: dict[str, str] = {}
        self._status: dict[str, list] = {}
        self._due = 0.0
        sim.structureChanged.connect(self._on_structure)
        sim.controllerChanged.connect(self._on_structure)
        sim.configChanged.connect(self._update_status)
        graphs.catalogueChanged.connect(self._tiles_changed)
        self._tiles_changed()

    # ----------------------------------------------------------- properties
    def _display_tiles(self) -> list[dict]:
        out = []
        cat = self.graphs.catalogue
        for t in self.model.tiles:
            d = dict(t)
            if t["type"] == "value":
                sig = cat.get(t["signal"])
                if sig is None:
                    d.update(name=t["signal"], unit="", available=False)
                else:
                    _scale, unit, _span, _dec = sig.display(t["angle_unit"])
                    d.update(name=sig.name or sig.label, unit=unit, available=True)
            elif t["type"] == "graph":
                d["missing"] = [k for k in t["signals"] if k not in cat]
            out.append(d)
        return out

    tiles = Property("QVariantList", _display_tiles, notify=tilesChanged)
    rows = Property("QVariantList", lambda self: rows(self.model.tiles, 2), notify=tilesChanged)
    rowsCompact = Property("QVariantList", lambda self: rows(self.model.tiles, 4), notify=tilesChanged)
    values = Property("QVariantMap", lambda self: self._values, notify=valuesChanged)
    status = Property("QVariantMap", lambda self: self._status, notify=statusChanged)
    layoutName = Property(str, lambda self: self.model.name, notify=layoutsChanged)
    layoutNames = Property("QVariantList", lambda self: self.model.names(), notify=layoutsChanged)

    def _set_editing(self, on: bool) -> None:
        if bool(on) != self._editing:
            self._editing = bool(on)
            if not self._editing:
                self._set_selected("")
            self.editingChanged.emit()

    editing = Property(bool, lambda self: self._editing, _set_editing, notify=editingChanged)

    def _set_selected(self, tile_id: str) -> None:
        tile_id = str(tile_id or "")
        if tile_id and self.model.index(tile_id) < 0:
            tile_id = ""
        if tile_id != self._selected:
            self._selected = tile_id
            self.selectedChanged.emit()

    selected = Property(str, lambda self: self._selected, _set_selected, notify=selectedChanged)
    maxTiles = Property(int, lambda self: MAX_TILES, constant=True)
    full = Property(bool, lambda self: len(self.model.tiles) >= MAX_TILES, notify=tilesChanged)

    # -------------------------------------------------------------- editing
    @Slot(str, result=str)
    def add(self, kind: str) -> str:
        tile_id = self.model.add(kind, self.graphs.smoothing)
        self._tiles_changed()
        return tile_id

    @Slot(str)
    def remove(self, tile_id: str) -> None:
        self.model.remove(tile_id)
        if tile_id == self._selected:
            self._set_selected("")
        self._tiles_changed()

    @Slot(str, int)
    def move(self, tile_id: str, to: int) -> None:
        self.model.move(tile_id, to)
        self._tiles_changed()

    @Slot(str, "QVariantMap")
    def update(self, tile_id: str, changes: dict) -> None:
        self.model.update(tile_id, dict(changes))
        self._tiles_changed()

    @Slot(str, result="QVariantMap")
    def tile(self, tile_id: str) -> dict:
        i = self.model.index(tile_id)
        return dict(self.model.tiles[i]) if i >= 0 else {}

    @Slot()
    def reset(self) -> None:
        self._set_selected("")
        self.model.reset()
        self._tiles_changed()
        self.layoutsChanged.emit()

    @Slot(str, result=bool)
    def saveAs(self, name: str) -> bool:  # noqa: N802 - QML API
        ok = self.model.save_as(name)
        self.layoutsChanged.emit()
        return ok

    @Slot(str)
    def load(self, name: str) -> None:
        if self.model.load(name):
            self._set_selected("")
            self._tiles_changed()
            self.layoutsChanged.emit()

    @Slot(str)
    def deleteLayout(self, name: str) -> None:  # noqa: N802 - QML API
        self.model.delete(name)
        self.layoutsChanged.emit()

    @Slot(str, result=QObject)
    def view(self, tile_id: str) -> QObject:
        i = self.model.index(tile_id)
        if i < 0 or self.model.tiles[i]["type"] != "graph":
            return None
        return self.graphs.tile_view(tile_id, self._spec(self.model.tiles[i]))

    # ---------------------------------------------------------------- data
    @staticmethod
    def _spec(t: dict) -> GraphSpec:
        return GraphSpec(list(t["signals"]), float(t["window"]), t["scale"],
                         [tuple(p) for p in t["fixed"]], t["smoothing"], t["angle_unit"])

    def _tiles_changed(self) -> None:
        keep = set()
        for t in self.model.tiles:
            if t["type"] == "graph":
                self.graphs.tile_view(t["id"], self._spec(t))
                keep.add(t["id"])
        self.graphs.drop_tiles(keep)
        self.tilesChanged.emit()
        self._update_values()
        self._update_status()

    def _on_structure(self) -> None:
        self._tiles_changed()

    def _update_status(self) -> None:
        cfg = self.sim.session.config
        self._status = {t["id"]: status_lines(cfg, t) for t in self.model.tiles if t["type"] == "status"}
        self.statusChanged.emit()

    def on_frame(self, now: float) -> None:
        """A frame is being shown: refresh the value tiles at 15 Hz."""
        if now >= self._due:
            self._due = now + 1.0 / VALUES_HZ
            self._update_values()

    def _update_values(self) -> None:
        cat = self.graphs.catalogue
        source = self.sim.source
        values = {}
        for t in self.model.tiles:
            if t["type"] != "value":
                continue
            sig = cat.get(t["signal"])
            if sig is None:
                values[t["id"]] = "—"
                continue
            scale, _unit, _span, decimals = sig.display(t["angle_unit"])
            if sig.key == "time":
                v, signed = self.sim.simTime, False
            elif sig.key == "rtf":
                v, signed = float(source.stats()["realtime_factor"]), False
            elif sig.key == "fps":
                v, signed = float(self.sim.clock.fps), False
            else:
                v, signed = source.latest(sig.key), True
                if sig.wrap and math.isfinite(v):
                    v = math.atan2(math.sin(v), math.cos(v))      # (−π, π]
                v *= scale
            values[t["id"]] = format_value(v, decimals, signed)
        if values != self._values:
            self._values = values
            self.valuesChanged.emit()
