"""Full set-up files of the mobile app (Qt-free, unit-tested headless).

A set-up file is an ordinary configuration file (``config_to_dict``: plant, controller,
simulation, experiment settings, push force – what the desktop version reads and
writes) with one extra top-level section, ``"app"``, holding what the app shows:

``"app": {"view": {...}, "graphs": {"selected": [...], "window_s": 10, "pid_loop": 0}}``

The desktop version ignores unknown sections, so it opens these files unchanged; files
without the section open in the app as before. Graph images and data are saved
separately (PNG / CSV) – a set-up file never contains recorded data.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any

from ..simulation import AppConfig
from ..simulation.config import config_from_dict, config_to_dict

APP_SECTION = "app"
APP_VERSION = 1


def setup_to_dict(config: AppConfig, app: dict[str, Any] | None = None) -> dict[str, Any]:
    data = config_to_dict(config)
    if app:
        data[APP_SECTION] = {"version": APP_VERSION, **app}
    return data


def write_setup(path: str | Path, config: AppConfig, app: dict[str, Any] | None = None) -> None:
    Path(path).write_text(json.dumps(setup_to_dict(config, app), indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8")


def split_setup(data: dict[str, Any]) -> tuple[AppConfig, dict[str, Any]]:
    """(configuration, app section or ``{}``); raises ``ValueError`` on a bad configuration."""
    if not isinstance(data, dict):
        raise ValueError("configuration must be a JSON object")
    app = data.get(APP_SECTION)
    core = {k: v for k, v in data.items() if k != APP_SECTION}
    return config_from_dict(core), app if isinstance(app, dict) else {}


def read_setup(path: str | Path) -> tuple[AppConfig, dict[str, Any]]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{Path(path).name} is not valid JSON: {exc}") from exc
    return split_setup(data)


def dataclass_from_dict(cls, values: Any, base=None):
    """Instance of dataclass ``cls`` from ``values``: unknown keys and values of the wrong
    type are skipped (the defaults, or ``base``'s values, stay)."""
    out = base if base is not None else cls()
    if not isinstance(values, dict):
        return out
    for f in fields(cls):
        if f.name not in values:
            continue
        v, current = values[f.name], getattr(out, f.name)
        if isinstance(current, bool):
            if isinstance(v, bool):
                setattr(out, f.name, v)
        elif isinstance(current, int):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                setattr(out, f.name, int(v))
        elif isinstance(current, float):
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                setattr(out, f.name, float(v))
        elif isinstance(current, str) and isinstance(v, str):
            setattr(out, f.name, v)
    return out
