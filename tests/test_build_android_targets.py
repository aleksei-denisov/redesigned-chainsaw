"""Build targets of scripts/build_android.py: the Widgets app and the Qt Quick app.

The two apps are staged from the same repository into separate folders with separate
application ids, so both install side by side. The Qt Quick staging leaves the Widgets
UI packages out; whatever it imports from the rest of the repository must be staged.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
QUICK = ROOT / "pendulum_sim" / "quick"


@pytest.fixture(scope="module")
def build_android():
    if sys.version_info < (3, 11):
        pytest.skip("build script needs tomllib (Python 3.11+)")
    spec = importlib.util.spec_from_file_location("build_android_targets", ROOT / "scripts" / "build_android.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_targets_are_separate_apps(build_android):
    widgets, quick = build_android.TARGETS["widgets"], build_android.TARGETS["quick"]
    assert widgets.package_name == "invertedpendulum"          # unchanged: updates install over the old app
    assert quick.package_name != widgets.package_name
    assert quick.staging != widgets.staging
    assert quick.artifact != widgets.artifact
    assert (ROOT / "android" / widgets.entry).exists() and (ROOT / "android" / quick.entry).exists()


def test_widgets_staging_is_unchanged_apart_from_the_quick_package(build_android, tmp_path):
    staged = build_android.stage(build_android.TARGETS["widgets"], tmp_path / "app")
    package = staged / "pendulum_sim"
    assert not (package / "quick").exists()
    assert not list(staged.rglob("*.qml"))       # QML files would add the Qt Quick libraries to this app
    for name in ("physics", "control", "simulation", "ui", "mobile"):
        assert (package / name / "__init__.py").exists()
    assert (staged / "main.py").read_text() == (ROOT / "android" / "main.py").read_text()
    assert (staged / "configs").is_dir() and (staged / "icon.png").exists()


def test_quick_staging_holds_everything_the_app_needs(build_android, tmp_path):
    target = build_android.TARGETS["quick"]
    staged = build_android.stage(target, tmp_path / "app-quick")
    package = staged / "pendulum_sim"
    assert (staged / "main.py").read_text() == (ROOT / "android" / "main_quick.py").read_text()
    assert not (package / "ui").exists()
    assert not (package / "mobile" / "window.py").exists()
    assert (package / "quick" / "qml" / "Main.qml").exists()
    # every data file of the Qt Quick package has an extension buildozer is told to include
    for path in QUICK.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            assert path.suffix.lstrip(".") in target.include_exts, path
            assert (package / "quick" / path.relative_to(QUICK)).exists()
    # the Qt-free part imports from the staged tree alone
    code = ("import pendulum_sim.quick.backend.sim_source, pendulum_sim.quick.backend.threads;"
            "import pendulum_sim.mobile.plot_math, pendulum_sim.mobile.startup, pendulum_sim.mobile.cpu;"
            "from pendulum_sim.simulation import scenario; print(scenario('triple_cart_lqr').name)")
    out = subprocess.run([sys.executable, "-c", code], cwd=staged, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "Triple pendulum" in out.stdout


def test_quick_package_only_imports_staged_modules(build_android):
    """Static check of the Qt modules too (they cannot be imported without PySide6)."""
    parts = build_android.TARGETS["quick"].parts
    staged_dirs = {p for p in parts if "/" not in p and not p.endswith(".py")}
    staged_files = {p for p in parts if p.endswith(".py")}
    for path in QUICK.rglob("*.py"):
        package = path.relative_to(ROOT).parent.parts            # ("pendulum_sim", "quick", ...)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom) or not node.level:
                continue
            base = package[:len(package) - node.level + 1] + tuple((node.module or "").split(".") if node.module else ())
            names = [base] if node.module else [base + (alias.name,) for alias in node.names]
            for name in names:
                rel = name[1:]                                   # below pendulum_sim
                if not rel or rel[0] in staged_dirs:
                    continue
                candidates = {"/".join(rel) + ".py", "/".join(rel) + "/__init__.py"}
                assert candidates & staged_files, f"{path.name} imports {'.'.join(name)}, which is not staged"


def test_lazy_qt_libs_are_removed_from_the_start_up_list_only(build_android):
    args = ("--qt-libs=Graphs,Qml,OpenGL,Quick3D,Network,Quick,Core,Gui "
            "--load-local-libs=plugins_platforms_qtforandroid --init-classes=")
    assert build_android.lazy_qt_libs(args, ("Graphs", "Quick3D")) == (
        "--qt-libs=Qml,OpenGL,Network,Quick,Core,Gui "
        "--load-local-libs=plugins_platforms_qtforandroid --init-classes=")
    assert build_android.lazy_qt_libs(args, ()) == args
    assert build_android.TARGETS["widgets"].lazy_qt_libs == ()       # the Widgets app is built as before
