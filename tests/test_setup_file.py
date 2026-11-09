"""Full set-up files of the mobile app: configuration + app section (Qt-free)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from pendulum_sim.mobile.setup_file import (
    APP_SECTION,
    dataclass_from_dict,
    read_setup,
    setup_to_dict,
    split_setup,
    write_setup,
)
from pendulum_sim.simulation import scenario
from pendulum_sim.simulation.config import config_to_dict, load_config


def test_round_trip_with_app_section(tmp_path):
    cfg = scenario("double_cart_lqr")
    app = {"view": {"show_grid": False, "trail_length": 50}, "graphs": {"selected": ["angle"], "window_s": 20}}
    path = tmp_path / "setup.json"
    write_setup(path, cfg, app)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[APP_SECTION]["graphs"]["selected"] == ["angle"] and data[APP_SECTION]["version"] == 1
    loaded, app2 = read_setup(path)
    assert config_to_dict(loaded) == config_to_dict(cfg)
    assert app2["view"] == app["view"] and app2["graphs"] == app["graphs"]
    # the desktop loader reads the same file (it ignores the app section)
    assert config_to_dict(load_config(path)) == config_to_dict(cfg)


def test_plain_configuration_has_no_app_section(tmp_path):
    cfg = scenario("single_cart_full")
    loaded, app = split_setup(setup_to_dict(cfg))
    assert app == {} and config_to_dict(loaded) == config_to_dict(cfg)
    loaded, app = split_setup({**config_to_dict(cfg), APP_SECTION: "garbage"})
    assert app == {}
    with pytest.raises(ValueError):
        split_setup([1, 2])
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        read_setup(bad)


def test_dataclass_from_dict_skips_unknown_and_wrong_types():
    @dataclass
    class Opts:
        flag: bool = True
        count: int = 3
        width: float = 1.5
        name: str = "a"

    o = dataclass_from_dict(Opts, {"flag": False, "count": 7.0, "width": 2, "name": "b", "extra": 1})
    assert (o.flag, o.count, o.width, o.name) == (False, 7, 2.0, "b")
    o = dataclass_from_dict(Opts, {"flag": "yes", "count": True, "width": "x", "name": 5})
    assert o == Opts()
    assert dataclass_from_dict(Opts, None) == Opts()
