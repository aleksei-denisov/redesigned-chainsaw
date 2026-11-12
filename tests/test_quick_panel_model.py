"""Configurable side panel: tiles, validation, saved layouts (Qt-free)."""

from __future__ import annotations

import json

from pendulum_sim.quick.backend.panel_model import (
    DEFAULT_LAYOUT,
    PanelModel,
    clean,
    clean_tile,
    default_layout,
    rows,
)


class Store(dict):
    def set(self, key, value):
        self[key] = value


def test_default_layout_is_todays_panel():
    tiles = default_layout()
    assert [t["type"] for t in tiles] == ["value"] * 4 + ["graph"]
    assert [t.get("signal") for t in tiles[:4]] == ["theta1", "omega1", "x", "actuator"]
    assert tiles[4]["signals"] == ["theta1", "x"] and tiles[4]["height"] == "fill"
    assert rows(tiles) == [[0, 1], [2, 3], [4]]
    assert len({t["id"] for t in tiles}) == 5


def test_broken_input_is_repaired():
    def strip(tiles):
        return [{k: v for k, v in t.items() if k != "id"} for t in tiles]

    assert strip(clean("nonsense")) == strip(clean([])) and len(clean([])) == len(DEFAULT_LAYOUT)
    t = clean_tile({"type": "graph", "signals": ["a", "a", "b", "c", "d", "e"], "window": 13,
                    "scale": "weird", "fixed": [[5, 1], ["x", 2]], "smoothing": "max", "height": 7, "junk": 1})
    assert t["signals"] == ["a", "b", "c", "d"] and t["window"] == 10 and t["scale"] == "auto"
    assert t["fixed"] == [[1.0, 5.0], [0.0, 2.0]] and t["smoothing"] == "off" and t["height"] == "fill"
    assert "junk" not in t
    assert clean_tile({"type": "spaceship"}) is None
    dup = clean([{"type": "status", "id": "a"}, {"type": "status", "id": "a"}])
    assert dup[0]["id"] != dup[1]["id"]


def test_rows_pair_half_values():
    tiles = clean([{"type": "value", "size": "half"}, {"type": "graph"}, {"type": "value", "size": "half"},
                   {"type": "value", "size": "half"}, {"type": "value", "size": "half"},
                   {"type": "value", "size": "full"}])
    assert rows(tiles) == [[0], [1], [2, 3], [4], [5]]
    assert rows(tiles, columns=4) == [[0], [1], [2, 3, 4], [5]]


def test_editing_is_saved_immediately():
    store = Store()
    m = PanelModel(store)
    new = m.add("status")
    assert m.tiles[-1]["type"] == "status"
    m.move(new, 0)
    assert m.tiles[0]["id"] == new
    m.update(new, {"show_gains": False, "type": "graph", "id": "x"})      # type and id cannot change
    assert m.tiles[0]["show_gains"] is False and m.tiles[0]["type"] == "status" and m.tiles[0]["id"] == new
    m.remove(m.tiles[1]["id"])
    again = PanelModel(store)
    assert [t["id"] for t in again.tiles] == [t["id"] for t in m.tiles]


def test_named_layouts():
    store = Store()
    m = PanelModel(store)
    m.add("graph")
    assert m.save_as("  Tuning   view ")
    assert m.names() == ["Tuning view"] and m.name == "Tuning view"
    m.reset()
    assert len(m.tiles) == 5 and m.name == "Default"
    assert m.load("Tuning view") and len(m.tiles) == 6
    assert not m.load("missing")
    m.delete("Tuning view")
    assert m.names() == []
    assert not m.save_as("   ")
    json.loads(store[PanelModel.KEY_CURRENT])


def test_status_lines():
    from pendulum_sim.quick.backend.panel_model import status_lines
    from pendulum_sim.simulation import scenario

    cfg = scenario("triple_cart_lqr")
    rows_ = status_lines(cfg, {"show_scenario": True, "show_controller": True, "show_gains": True})
    assert rows_[0] == {"label": "Scenario", "value": cfg.name}
    assert rows_[1]["value"].startswith("4-loop PID · 500 Hz")
    assert rows_[2]["label"] == "Cart position" and rows_[2]["value"].startswith("Kp 103")
    assert len(status_lines(cfg, {"show_scenario": False, "show_controller": False, "show_gains": False})) == 0
