"""The configurable side panel of the Simulate screen: tiles and saved layouts (Qt-free).

A layout is a list of tiles, stored as JSON:

* ``value`` – one live number: ``signal``, ``size`` (``half`` | ``full``), ``color``
  (a theme token), ``angle_unit`` (``deg`` | ``rad``);
* ``graph`` – 1–4 curves: ``signals``, ``window`` (s), ``scale`` (``auto`` |
  ``symmetric`` | ``fixed``), ``fixed`` (per axis ``[min, max]``), ``smoothing``
  (``off`` | ``light`` | ``medium`` | ``strong``), ``height`` (``small`` | ``medium`` |
  ``large`` | ``fill``), ``angle_unit``;
* ``status`` – controller type and gains: ``show_scenario``, ``show_controller``,
  ``show_gains``.

Every tile has an ``id`` (stable while editing). :func:`clean` repairs whatever is
loaded (unknown keys dropped, missing values filled in), so an old or hand-edited layout
never breaks the panel. :class:`PanelModel` holds the current layout and the named
layouts in a key–value store (QSettings in the app, a dict in the tests).
"""

from __future__ import annotations

import copy
import json
import uuid

from .curves import MAX_SIGNALS, SMOOTHING_S, WINDOWS_S

TYPES = ("value", "graph", "status")
SIZES = ("half", "full")
HEIGHTS = ("small", "medium", "large", "fill")
SCALES = ("auto", "symmetric", "fixed")
COLORS = ("text", "accent", "angle", "segment2", "segment3", "success",
          "palette0", "palette1", "palette2", "palette3", "palette4")
DEFAULT_NAME = "Default"
MAX_TILES = 16

DEFAULT_LAYOUT = [
    {"type": "value", "signal": "theta1", "size": "half", "color": "angle"},
    {"type": "value", "signal": "omega1", "size": "half", "color": "text"},
    {"type": "value", "signal": "x", "size": "half", "color": "accent"},
    {"type": "value", "signal": "actuator", "size": "half", "color": "text"},
    {"type": "graph", "signals": ["theta1", "x"], "window": 10, "scale": "auto", "smoothing": "off",
     "height": "fill"},
]


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def defaults(kind: str, smoothing: str = "off") -> dict:
    """A new tile of ``kind`` with every option at its default."""
    if kind == "value":
        return {"type": "value", "signal": "theta1", "size": "half", "color": "text", "angle_unit": "deg"}
    if kind == "graph":
        return {"type": "graph", "signals": ["theta1"], "window": 10, "scale": "auto", "fixed": [],
                "smoothing": smoothing, "height": "fill", "angle_unit": "deg"}
    return {"type": "status", "show_scenario": True, "show_controller": True, "show_gains": True}


def _number(v, default: float) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f == f and abs(f) < 1e12 else default


def clean_tile(tile: dict, smoothing: str = "off") -> dict | None:
    """A valid tile from ``tile`` (``None`` if it is not a tile at all)."""
    if not isinstance(tile, dict) or tile.get("type") not in TYPES:
        return None
    out = defaults(tile["type"], smoothing)
    for key in out:
        if key in tile:
            out[key] = tile[key]
    if out["type"] == "value":
        out["signal"] = str(out["signal"])
        out["size"] = out["size"] if out["size"] in SIZES else "half"
        out["color"] = out["color"] if out["color"] in COLORS else "text"
        out["angle_unit"] = out["angle_unit"] if out["angle_unit"] in ("deg", "rad") else "deg"
    elif out["type"] == "graph":
        signals = [str(s) for s in out["signals"]] if isinstance(out["signals"], list) else []
        out["signals"] = list(dict.fromkeys(signals))[:MAX_SIGNALS] or ["theta1"]
        out["window"] = min(WINDOWS_S, key=lambda w: abs(w - _number(out["window"], 10)))
        out["scale"] = out["scale"] if out["scale"] in SCALES else "auto"
        fixed = out["fixed"] if isinstance(out["fixed"], list) else []
        good = []
        for pair in fixed[:2]:
            if isinstance(pair, (list, tuple)) and len(pair) == 2:
                lo, hi = _number(pair[0], 0.0), _number(pair[1], 1.0)
                good.append([min(lo, hi), max(lo, hi)] if lo != hi else [lo - 1.0, lo + 1.0])
        out["fixed"] = good
        out["smoothing"] = out["smoothing"] if out["smoothing"] in SMOOTHING_S else "off"
        out["height"] = out["height"] if out["height"] in HEIGHTS else "fill"
        out["angle_unit"] = out["angle_unit"] if out["angle_unit"] in ("deg", "rad") else "deg"
    else:
        for key in ("show_scenario", "show_controller", "show_gains"):
            out[key] = bool(out[key])
    out["id"] = str(tile.get("id") or new_id())
    return out


def clean(tiles, smoothing: str = "off") -> list[dict]:
    """A valid layout from anything (empty → the default layout)."""
    if not isinstance(tiles, list):
        tiles = []
    out = [t for t in (clean_tile(t, smoothing) for t in tiles[:MAX_TILES]) if t is not None]
    seen = set()
    for t in out:                                      # ids must be unique
        if t["id"] in seen:
            t["id"] = new_id()
        seen.add(t["id"])
    return out or default_layout()


def default_layout() -> list[dict]:
    return [clean_tile(t) for t in copy.deepcopy(DEFAULT_LAYOUT)]


def rows(tiles: list[dict], columns: int = 2) -> list[list[int]]:
    """Tile indices per row: half-size values share a row, everything else is a row."""
    out: list[list[int]] = []
    current: list[int] = []
    for i, t in enumerate(tiles):
        if t["type"] == "value" and t["size"] == "half":
            current.append(i)
            if len(current) == columns:
                out.append(current)
                current = []
            continue
        if current:
            out.append(current)
            current = []
        out.append([i])
    if current:
        out.append(current)
    return out


class PanelModel:
    """The current layout, its edits, and the named layouts (persisted in ``store``)."""

    KEY_CURRENT = "quick/panel/current"
    KEY_NAME = "quick/panel/current_name"
    KEY_SAVED = "quick/panel/saved"

    def __init__(self, store) -> None:
        self.store = store
        self.tiles = clean(self._load(self.KEY_CURRENT, []))
        self.name = str(store.get(self.KEY_NAME, DEFAULT_NAME) or DEFAULT_NAME)

    def _load(self, key: str, default):
        raw = self.store.get(key, None)
        if raw in (None, ""):
            return default
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return default

    def _save(self) -> None:
        self.store.set(self.KEY_CURRENT, json.dumps(self.tiles))
        self.store.set(self.KEY_NAME, self.name)

    # ------------------------------------------------------------ editing
    def index(self, tile_id: str) -> int:
        return next((i for i, t in enumerate(self.tiles) if t["id"] == tile_id), -1)

    def add(self, kind: str, smoothing: str = "off") -> str:
        if kind not in TYPES or len(self.tiles) >= MAX_TILES:
            return ""
        tile = clean_tile(defaults(kind, smoothing), smoothing)
        self.tiles.append(tile)
        self._save()
        return tile["id"]

    def remove(self, tile_id: str) -> None:
        i = self.index(tile_id)
        if i >= 0:
            del self.tiles[i]
            self._save()

    def move(self, tile_id: str, to: int) -> None:
        i = self.index(tile_id)
        if i < 0:
            return
        to = max(0, min(int(to), len(self.tiles) - 1))
        if to != i:
            self.tiles.insert(to, self.tiles.pop(i))
            self._save()

    def update(self, tile_id: str, changes: dict) -> None:
        i = self.index(tile_id)
        if i < 0 or not isinstance(changes, dict):
            return
        merged = dict(self.tiles[i])
        merged.update({k: v for k, v in changes.items() if k not in ("id", "type")})
        tile = clean_tile(merged)
        if tile is not None:
            self.tiles[i] = tile
            self._save()

    def reset(self) -> None:
        self.tiles = default_layout()
        self.name = DEFAULT_NAME
        self._save()

    # ------------------------------------------------------- named layouts
    def saved(self) -> dict[str, list[dict]]:
        data = self._load(self.KEY_SAVED, {})
        return data if isinstance(data, dict) else {}

    def names(self) -> list[str]:
        return sorted(self.saved(), key=str.lower)

    def save_as(self, name: str) -> bool:
        name = " ".join(str(name).split())[:40]
        if not name:
            return False
        data = self.saved()
        data[name] = self.tiles
        self.store.set(self.KEY_SAVED, json.dumps(data))
        self.name = name
        self._save()
        return True

    def load(self, name: str) -> bool:
        if name == DEFAULT_NAME and name not in self.saved():
            self.reset()
            return True
        layout = self.saved().get(name)
        if layout is None:
            return False
        self.tiles = clean(layout)
        self.name = name
        self._save()
        return True

    def delete(self, name: str) -> None:
        data = self.saved()
        if data.pop(name, None) is not None:
            self.store.set(self.KEY_SAVED, json.dumps(data))


# ------------------------------------------------------------- status tile
def _gain(v: float) -> str:
    if v == 0:
        return "0"
    a = abs(v)
    return f"{v:.3g}" if a < 1000 else f"{v:.0f}"


def status_lines(config, tile: dict) -> list[dict]:
    """``{label, value}`` rows of a status tile for the simulation ``config``."""
    rows_: list[dict] = []
    c = config.controller
    if tile.get("show_scenario", True):
        rows_.append({"label": "Scenario", "value": config.name})
    if tile.get("show_controller", True):
        loops = [loop for loop in c.loops if loop.enabled]
        kind = "off" if not c.enabled else f"{len(loops)}-loop PID" if len(loops) > 1 else "PID"
        rows_.append({"label": "Controller", "value": f"{kind} · {c.frequency_hz:g} Hz · "
                                                      f"{c.output_min:g} … {c.output_max:g} N"})
    if tile.get("show_gains", True):
        for loop in c.loops:
            if not loop.enabled:
                continue
            terms = [f"Kp {_gain(loop.kp)}" if loop.p_enabled else "",
                     f"Ki {_gain(loop.ki)}" if loop.i_enabled else "",
                     f"Kd {_gain(loop.kd)}" if loop.d_enabled else ""]
            rows_.append({"label": loop.name, "value": "  ".join(t for t in terms if t) or "—"})
    return rows_
