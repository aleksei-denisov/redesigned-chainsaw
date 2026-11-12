"""The Qt Quick app's backend objects, offscreen and without QML (skipped without PySide6).

Each edit made through a bridge must reach the physics host as the shared session's
operations, so the simulation it runs is exactly the one the core would run. The
reference is a local ``Simulator`` with the same operations applied
(``pendulum_sim.session.apply``), stepped the same number of steps.
"""

from __future__ import annotations

import copy
import os
import time

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from pendulum_sim.session import apply  # noqa: E402
from pendulum_sim.simulation import Simulator, scenario  # noqa: E402


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    app = QGuiApplication.instance() or QGuiApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_path_factory.mktemp("quick-settings")))
    app.setOrganizationName("pendulum-sim-quick-tests")
    return app


@pytest.fixture
def bridges(app):
    from pendulum_sim.quick.backend.graphs import GraphsBridge
    from pendulum_sim.quick.backend.parameters import ControllerBridge, ModelBridge
    from pendulum_sim.quick.backend.simulation import SimulationBridge

    sim = SimulationBridge(scenario("triple_cart_lqr"), "thread")
    model, controller = ModelBridge(sim), ControllerBridge(sim)
    graphs = GraphsBridge(sim)
    sent: list[tuple] = []
    forward = sim.apply_operations
    sim.apply_operations = lambda ops: (sent.extend(ops), forward(ops))   # record what reaches the host
    yield sim, model, controller, graphs, sent
    graphs.timer.stop()
    sim.shutdown()


def _wait(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QGuiApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


def _host_state(sim):
    """Stop the host and step a fixed number of steps there (deterministic comparison)."""
    sim.pause()
    sim.reset()
    assert _wait(lambda: sim.source.stats()["t"] == 0.0)
    sim.source.send("steps", 300)
    assert _wait(lambda: sim.source.stats()["steps"] >= 300 and sim.source.stats()["t"] > 0)
    _wait(lambda: False, 0.05)
    row = np.zeros_like(sim.row)
    sim.source.read(row, wall=float("inf"))
    return row


def _reference(config, ops_log):
    local = Simulator(copy.deepcopy(config))
    for op in ops_log:
        local = apply(local, op)
    local.reset()
    for _ in range(300):
        local.step()
    return local


def test_edits_reach_the_host_as_session_operations(bridges):
    sim, model, controller, _graphs, sent = bridges
    original = copy.deepcopy(sim.session.config)
    model._edit_plant("seg1.mass", 0.12)                  # plain parameter
    controller.seedLqr()                                  # LQR seeding for the new plant
    model._edit_simulation("noise_angle", 0.0)
    kinds = [op[0] for op in sent]
    assert "plant" in kinds and "controller" in kinds and "simulation" in kinds
    assert sim.session.config.plant.segments[1].mass == pytest.approx(0.12)
    # the session holds exactly the configuration the edits describe; with "uniform rod"
    # on, the editor recomputes the rod's inertia from the new mass (as the Widgets editor)
    expected = copy.deepcopy(original)
    seg = expected.plant.segments[1]
    seg.mass = 0.12
    seg.inertia = seg.mass * seg.length ** 2 / 12.0
    got = sim.session.config.plant
    assert got.segments[1].inertia == pytest.approx(seg.inertia)
    got.segments[1].inertia = seg.inertia
    assert got == expected.plant


def test_structure_change_through_the_model_bridge(bridges):
    sim, model, _controller, graphs, sent = bridges
    assert sim.segments == 3
    seen = []
    sim.structureChanged.connect(lambda: seen.append(sim.segments))
    model._edit_plant("segments", 2)
    assert sim.segments == 2 and seen == [2]
    assert sent[-1][0] == "structure"
    assert all(k in [a["key"] for a in graphs.available] for k in ("angle", "control"))
    assert len(sim.session.config.controller.loops) >= 1


def test_host_matches_the_core_after_edits(bridges):
    sim, model, controller, _graphs, sent = bridges
    original = copy.deepcopy(sim.session.config)
    model._edit_plant("cart_friction", 0.3)
    controller.seedLqr()
    row = _host_state(sim)
    local = _reference(original, [op for op in sent if op[0] != "reset_if_idle"])
    from pendulum_sim.quick.backend.sim_source import R_STATE
    n = len(local.state)
    assert np.array_equal(row[R_STATE:R_STATE + n], local.state)


def test_frame_reads_and_readouts(bridges):
    sim, *_ = bridges
    sim.start()
    assert _wait(lambda: sim.source.stats()["t"] > 0.2)
    for _ in range(5):
        sim.advance()
    assert sim.simTime > 0.0
    sim._readout_due = 0.0
    sim.advance()
    assert sim.timeText.startswith("T ")
    assert sim.angleText[0] in "+-"


def test_graph_views_only_compute_when_active(bridges):
    sim, _model, _controller, graphs, _sent = bridges
    sim.start()
    assert _wait(lambda: sim.source.stats()["t"] > 0.5)
    view = graphs.view("angle")
    view.setSize(300.0, 120.0)
    graphs.refresh()
    assert view.output is None                            # not active: nothing computed
    view.setActive(True)
    graphs.refresh()
    assert len(view.output.curves) == 3                   # θ1..θ3
    assert all(v.size > 0 and i.size > 0 for v, i in view.output.curves)
    assert [e["label"] for e in view.legend] == ["θ1", "θ2", "θ3"]
    assert view.ticks and all(0.0 <= t["y"] <= 120.0 for t in view.ticks)


def test_frame_driver_paces_vsyncs(bridges):
    from pendulum_sim.quick.backend.frames import FrameDriver

    sim, *_ = bridges
    frames = FrameDriver(sim, 120.0, "60")
    shown = []
    sim.frame.connect(lambda: shown.append(1))
    for _ in range(8):
        frames.vsync()
    assert frames.vsyncs == 8 and len(shown) == 4
    frames.set_mode("120")
    for _ in range(4):
        frames.vsync()
    assert len(shown) == 8
    frames.shutdown()


def test_graphs_refresh_on_shown_frames_at_the_graph_rate(bridges):
    from pendulum_sim.quick.backend.frames import FrameDriver

    sim, _model, _controller, graphs, _sent = bridges
    frames = FrameDriver(sim, 120.0, "120")
    frames.attach_graphs(graphs)
    assert not graphs.timer.isActive()
    calls = []
    graphs.refresh = lambda: calls.append(time.perf_counter())
    graphs.rateHz = 25
    t = 100.0
    for _ in range(120):                                  # one second of frames at 120 fps
        graphs.on_frame(t)
        t += 1 / 120
    assert 24 <= len(calls) <= 26
    frames.shutdown()


def test_settings_theme_is_remembered(app):
    from pendulum_sim.quick.backend.settings import SettingsBridge

    s = SettingsBridge()
    s.theme = "dark"
    assert s.isDark
    s.override_theme("light")                             # command-line override: not saved
    assert not s.isDark
    assert SettingsBridge().theme == "dark"
    SettingsBridge().theme = "system"


class _DictStore(dict):
    def get(self, key, default=None):
        return super().get(key, default)

    def set(self, key, value):
        self[key] = value


def test_panel_default_layout_values_and_graph_tile(bridges):
    from pendulum_sim.quick.backend.panel import PanelBridge

    sim, _model, _controller, graphs, _sent = bridges
    panel = PanelBridge(sim, graphs, _DictStore())
    kinds = [t["type"] for t in panel.tiles]
    assert kinds == ["value"] * 4 + ["graph"]                 # the layout the screen had before
    assert panel.rows == [[0, 1], [2, 3], [4]]
    sim.start()
    assert _wait(lambda: sim.source.stats()["t"] > 0.5)
    panel.on_frame(1e9)
    values = panel.values
    theta = panel.tiles[0]
    assert theta["unit"] == "°" and values[theta["id"]][0] in "+-"
    graph_id = panel.tiles[4]["id"]
    view = panel.view(graph_id)
    view.setSize(300.0, 200.0)
    view.setActive(True)
    graphs.refresh()
    assert len(view.output.curves) == 2 and view.unit == "°" and view.unitRight == "m"
    assert view.ticks and view.ticksRight
    assert len(view.ranges()) == 2


def test_panel_editing_and_layouts(bridges):
    from pendulum_sim.quick.backend.panel import PanelBridge

    sim, _model, _controller, graphs, _sent = bridges
    store = _DictStore()
    panel = PanelBridge(sim, graphs, store)
    panel.editing = True
    status = panel.add("status")
    assert panel.tiles[-1]["type"] == "status" and panel.status[status]
    assert panel.status[status][0]["label"] == "Scenario"
    panel.selected = status
    panel.update(status, {"show_gains": False})
    assert all(r["label"] in ("Scenario", "Controller") for r in panel.status[status])
    panel.move(status, 0)
    assert panel.tiles[0]["id"] == status
    assert panel.saveAs("Mine")
    panel.remove(status)
    assert panel.selected == "" and len(panel.tiles) == 5
    panel.load("Mine")
    assert panel.layoutName == "Mine" and panel.tiles[0]["type"] == "status"
    panel.editing = False
    assert panel.selected == ""
    again = PanelBridge(sim, graphs, store)                     # persisted
    assert again.layoutName == "Mine" and again.tiles[0]["type"] == "status"
    again.reset()
    assert [t["type"] for t in again.tiles] == ["value"] * 4 + ["graph"]
