"""The golden data for the C++ port (cpp/reference/data) still matches the Python code.

If a change to physics or control alters these numbers on purpose, regenerate the data with
``python cpp/reference/generate.py`` and commit it together with the change, so the C++
port is checked against the new behaviour.
"""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "cpp" / "reference" / "generate.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("cpp_reference_generate", GEN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _close(a, b, rel: float, abs_: float, path: str = "") -> None:
    if isinstance(b, dict):
        assert isinstance(a, dict) and a.keys() == b.keys(), path
        for k in b:
            _close(a[k], b[k], rel, abs_, f"{path}.{k}")
    elif isinstance(b, list):
        assert isinstance(a, list) and len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, rel, abs_, f"{path}[{i}]")
    elif isinstance(b, float) and isinstance(a, (int, float)):
        assert math.isclose(a, b, rel_tol=rel, abs_tol=abs_), f"{path}: {a} != {b}"
    else:
        assert a == b, f"{path}: {a!r} != {b!r}"


@pytest.mark.parametrize("group", ["physics", "control", "experiment", "session"])
def test_reference_data_is_current(tmp_path: Path, group: str) -> None:
    gen = _load_generator()
    gen.DATA = tmp_path
    gen.GROUPS[group]()
    fresh = json.loads((tmp_path / f"{group}.json").read_text())
    stored = json.loads((ROOT / "cpp" / "reference" / "data" / f"{group}.json").read_text())
    tol = stored["tolerance"]
    # Scenario gains come from an LQR design, and LAPACK differs slightly between platforms
    # (~1e-12); chaotic runs ("sensitive") amplify that. Inputs are therefore compared at
    # 1e-8 and chaotic cases only for their structure – the C++ tests use the stored inputs.
    rel, abs_ = max(tol["rel"], 1e-8), max(tol["abs"], 1e-10)
    for f, s in zip(fresh.get("cases", []), stored.get("cases", [])):
        if s.get("sensitive"):
            f["metrics"] = s["metrics"] = None
    _close(fresh, stored, rel, abs_, group)
