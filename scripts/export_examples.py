"""Regenerate the example configuration files in ``configs/`` from the built-in scenarios.

Usage (from the repository root)::

    python scripts/export_examples.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pendulum_sim.simulation import SCENARIOS, save_config, scenario  # noqa: E402


def main() -> None:
    out = ROOT / "configs"
    out.mkdir(exist_ok=True)
    for key in SCENARIOS:
        path = out / f"{key}.json"
        save_config(scenario(key), path)
        print(f"wrote {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
