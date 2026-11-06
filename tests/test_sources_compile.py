"""Every source file must compile – also the GUI modules, which are otherwise only
imported when PySide6 is installed."""

from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted([*(ROOT / "pendulum_sim").rglob("*.py"), *(ROOT / "scripts").glob("*.py"), ROOT / "main.py"])


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(ROOT)))
def test_source_compiles(path: Path, tmp_path: Path) -> None:
    py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"), doraise=True)
