"""Every source file must compile – also the GUI modules, which are otherwise only
imported when PySide6 is installed."""

from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted([*(ROOT / "pendulum_sim").rglob("*.py"), *(ROOT / "scripts").glob("*.py"),
                  *(ROOT / "android").glob("*.py"), ROOT / "main.py"])


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_source_compiles(path: Path, tmp_path: Path) -> None:
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)


QT_FREE = ["physics", "control", "simulation", "session"]


@pytest.mark.parametrize("package", QT_FREE)
def test_core_layers_never_import_qt(package: str) -> None:
    """The shared layers run headless and in the Qt Quick app's physics process."""
    import ast

    for path in sorted((ROOT / "pendulum_sim" / package).rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                assert not name.startswith(("PySide6", "shiboken6", "pyqtgraph")), f"{path.name} imports {name}"
            if isinstance(node, ast.ImportFrom) and node.level:
                assert not (node.module or "").startswith(("ui", "mobile", "quick")), \
                    f"{path.name} imports the interface module {node.module}"
