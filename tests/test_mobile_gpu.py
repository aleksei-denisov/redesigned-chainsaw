"""GPU drawing of the mobile interface (pendulum view and graphs through QOpenGLWidget).

With the offscreen platform there is usually no OpenGL context: the window must then fall
back to software drawing without crashing. CI also runs this file under Xvfb with Mesa
(``PENDULUM_REQUIRE_GPU=1``), where the GPU path itself is exercised.
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pendulum_sim.mobile import gpu, theme  # noqa: E402
from pendulum_sim.mobile.performance import PerformanceSettings  # noqa: E402
from pendulum_sim.mobile.window import MobileWindow  # noqa: E402
from pendulum_sim.simulation import scenario  # noqa: E402

REQUIRE = os.environ.get("PENDULUM_REQUIRE_GPU") == "1"
# In-process GL tests only where a display with OpenGL is expected (CI's Xvfb job); with the
# offscreen platform a QOpenGLWidget may abort Qt, so there the fallback runs in a subprocess.
IN_PROCESS = REQUIRE or os.environ.get("QT_QPA_PLATFORM") not in ("offscreen", "minimal")


def test_gpu_request_never_crashes_the_app(tmp_path):
    """Asking for GPU drawing where OpenGL is missing or broken must fall back cleanly."""
    import subprocess
    import sys
    from pathlib import Path

    code = (
        "import os, sys, time\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app = QApplication([])\n"
        "from pendulum_sim.mobile import theme\n"
        "theme.apply(app)\n"
        "from pendulum_sim.mobile.window import MobileWindow\n"
        "from pendulum_sim.simulation import scenario\n"
        "w = MobileWindow(scenario('single_cart_full')); w.resize(900, 600); w.show()\n"
        "mode = w.set_drawing('gpu', 0)\n"
        "w.navigate('graphs'); w.start()\n"
        "for _ in range(20):\n"
        "    w._last_frame = time.perf_counter() - 0.016; w._on_frame(); app.processEvents()\n"
        "print('mode', mode, 'active', w.view.gpu, flush=True)\n"
        "w.set_drawing('software')\n"
        "for _ in range(5):\n"
        "    w._last_frame = time.perf_counter() - 0.016; w._on_frame(); app.processEvents()\n"
        "print('ok', flush=True)\n"
        "w.close(); w.deleteLater(); app.processEvents()\n"
        "os._exit(0)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, timeout=180,
                         cwd=tmp_path)
    # "ok" = drawing in both modes worked. Windows' offscreen OpenGL can still crash while the
    # process is torn down (after "ok"; seen on CI) – that is not a fallback failure.
    assert "ok" in out.stdout, (out.returncode, out.stdout[-2000:], out.stderr[-2000:])
    assert out.returncode == 0 or sys.platform == "win32", (out.returncode, out.stderr[-2000:])


@pytest.fixture(scope="module")
def win(tmp_path_factory):
    if not IN_PROCESS:
        pytest.skip("OpenGL tests run in the Xvfb job (offscreen: subprocess test only)")
    app = QApplication.instance() or QApplication([])
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope,
                      str(tmp_path_factory.mktemp("gpu-settings")))
    theme.apply(app)
    w = MobileWindow(scenario("single_cart_full"))
    w.resize(1280, 800)
    w.show()
    app.processEvents()
    yield w
    w.apply_performance(PerformanceSettings())
    w.close()
    w.deleteLater()
    app.processEvents()


def _frames(w, n):
    app = QApplication.instance()
    for _ in range(n):
        w._last_frame = time.perf_counter() - 0.016
        w._on_frame()
        app.processEvents()


def _settle(seconds=0.3):
    app = QApplication.instance()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def test_gpu_drawing_or_clean_fallback(win):
    used = win.set_drawing("gpu", 0)
    _settle()
    if REQUIRE:
        assert gpu.available() and used == "gpu" and win.view.gpu, "OpenGL expected in this job"
    win.navigate("graphs")
    win.telemetry.set_selection(["angle", "control", "cart_position"])
    win.start()
    _frames(win, 30)
    _settle()
    if win.view.gpu:
        charts = win.telemetry.visible_charts()
        assert charts and all(c.gpu for c in charts)
        assert win.stats.view_ms.value > 0.0                     # painted by paint_gpu
        before = sum(c.points_drawn for c in charts)
        _frames(win, 10)
        assert sum(c.points_drawn for c in charts) > before
        img = win.grab().toImage()                               # composed incl. the GL widgets
        c = charts[0]
        r = c._trace_rect.toAlignedRect().translated(c.mapTo(win, c.rect().topLeft()))
        dpr = img.devicePixelRatio()
        from PySide6.QtGui import QColor

        pixels = [img.pixelColor(int(x * dpr), int(y * dpr))
                  for x in range(r.left() + 2, r.right() - 2, 2) for y in range(r.top() + 2, r.bottom() - 2, 2)]
        bg = win.palette().base().color()
        black = sum(1 for q in pixels if q.lightness() < 8)
        assert black < 0.5 * len(pixels), "plot area black (GL surface not drawn)"
        assert sum(1 for q in pixels if abs(q.lightness() - bg.lightness()) <= 6) > 0.3 * len(pixels)
        want = QColor(c.series[0].color)
        near = [q for q in pixels if abs(q.red() - want.red()) + abs(q.green() - want.green())
                + abs(q.blue() - want.blue()) < 90]
        assert near, f"no {want.name()} curve pixels in the plot"
    else:
        assert not any(c.gpu for c in win.telemetry.visible_charts())
        assert win.view.gpu_surface is None
    win.pause()
    win.navigate("simulate")


def test_switching_back_to_software(win):
    win.set_drawing("gpu", 4)
    _settle()
    assert win.set_drawing("software") == "software"
    _settle()
    assert win.view.gpu_surface is None and not win.view.gpu
    assert all(c.gpu_surface is None for c in win.telemetry.charts.values())
    win.start()
    _frames(win, 5)
    win.pause()


def test_gpu_view_paint_matches_software_roughly(win):
    """Same scene through both paths: the pendulum must appear in the same place."""
    if not gpu.available():
        pytest.skip("QtOpenGLWidgets missing")
    win.set_drawing("software")
    win.load(scenario("single_cart_full"))
    _settle()
    soft = win.view.grab().toImage()
    if win.set_drawing("gpu", 0) != "gpu":
        pytest.skip("no OpenGL context here")
    _settle()
    if not win.view.gpu:
        pytest.skip("no OpenGL context here")
    hard = win.view.grab().toImage()
    assert (soft.width(), soft.height()) == (hard.width(), hard.height())
    tip = win.view.to_screen(win.view.tip_world())
    dpr = soft.devicePixelRatio()
    x, y = int(tip.x() * dpr), int(tip.y() * dpr)
    assert soft.pixelColor(x, y).name() == hard.pixelColor(x, y).name() or \
        abs(soft.pixelColor(x, y).lightness() - hard.pixelColor(x, y).lightness()) < 40
    win.set_drawing("software")
