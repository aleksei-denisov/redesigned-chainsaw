"""File dialogs and reading/writing that also work with Android's storage picker.

On the desktop ``QFileDialog`` returns file-system paths and the core
``load_*``/``save_*``/``export_*`` functions are called with them directly
(unchanged behaviour). On Android the native picker returns ``content://`` URIs
that Python's ``open()`` cannot handle; Qt can, through :class:`QFile`. For those
the data is staged through a temporary local file, so the path-based core
functions stay untouched.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, TypeVar
from urllib.parse import unquote

from PySide6.QtCore import QFile, QIODevice, QUrl
from PySide6.QtWidgets import QFileDialog, QWidget

T = TypeVar("T")
Location = Path | str   # str only for content:// URIs


def is_uri(location: Location) -> bool:
    return isinstance(location, str) and location.startswith("content:")


def _to_location(selected: str) -> Location | None:
    if not selected:
        return None
    return selected if is_uri(selected) else Path(selected)


def open_file_name(parent: QWidget | None, title: str, start: str, file_filter: str) -> Location | None:
    selected, _ = QFileDialog.getOpenFileName(parent, title, start, file_filter)
    return _to_location(selected)


def save_file_name(parent: QWidget | None, title: str, default_name: str, file_filter: str,
                   required_suffix: str | None = None) -> Location | None:
    selected, _ = QFileDialog.getSaveFileName(parent, title, default_name, file_filter)
    location = _to_location(selected)
    if isinstance(location, Path) and required_suffix and location.suffix.lower() != required_suffix:
        location = location.with_name(location.name + required_suffix)
    return location


def display_name(location: Location) -> str:
    if is_uri(location):
        name = QUrl(location).fileName() or unquote(location.rsplit("/", 1)[-1])
        return unquote(name).rsplit(":", 1)[-1].rsplit("/", 1)[-1] or str(location)
    return str(location)


def _suffix(location: Location) -> str:
    return Path(display_name(location)).suffix


def read_with(location: Location, loader: Callable[[Path], T]) -> T:
    """Call ``loader`` with a local path holding the data at ``location``."""
    if not is_uri(location):
        return loader(Path(location))
    f = QFile(location)
    if not f.open(QIODevice.OpenModeFlag.ReadOnly):
        raise OSError(f"Cannot open {display_name(location)}: {f.errorString()}")
    try:
        data = bytes(f.readAll().data())
    finally:
        f.close()
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"data{_suffix(location)}"
        local.write_bytes(data)
        return loader(local)


def write_with(location: Location, writer: Callable[[Path], None]) -> None:
    """Call ``writer`` with a local path, then store the result at ``location``."""
    if not is_uri(location):
        writer(Path(location))
        return
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / f"data{_suffix(location)}"
        writer(local)
        data = local.read_bytes()
    f = QFile(location)
    if not f.open(QIODevice.OpenModeFlag.WriteOnly | QIODevice.OpenModeFlag.Truncate):
        raise OSError(f"Cannot write {display_name(location)}: {f.errorString()}")
    try:
        if f.write(data) != len(data):
            raise OSError(f"Writing {display_name(location)} failed: {f.errorString()}")
    finally:
        f.close()
