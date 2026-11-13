"""Does integral action make the auto-tuned controller better? (quick-core-v2 research)

For each built-in balancing scenario the full auto-tune is run twice with the same seed:
PD only (integral search off – the old behaviour) and PID (integral search on). Both
results are measured on

* robustness – the largest recoverable 0.1 s tip push (the tuner's metric);
* a constant disturbance – a steady tip force of 3 % of the smaller recoverable push for
  8 s: the cart's and segment 1's offsets at the end (a PD controller keeps a steady
  offset, an integral can remove it);
* the recovery cost (∫ angle² dt after a push of half the recoverable one).

    python scripts/research_pid_vs_pd.py [--preset quick|balanced|thorough] [--markdown out.md]
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pendulum_sim.simulation.engine import SimulationDiverged, Simulator  # noqa: E402
from pendulum_sim.simulation.scenarios import scenario  # noqa: E402
from pendulum_sim.simulation.tuning import TuningSettings, _push_test, _upright_config  # noqa: E402
from pendulum_sim.simulation.tuning2 import TuneOptions, tune  # noqa: E402

SCENARIOS = ["single_cart_pid", "double_cart_lqr", "double_fixed_lqr", "triple_cart_lqr"]


def steady_force(cfg, controller, force: float, seconds: float = 8.0) -> tuple[float, float]:
    """(|cart offset| m, |θ1 offset| °) at the end of a constant tip force; inf if it falls."""
    c = _upright_config(cfg)
    c.controller = controller
    sim = Simulator(c, telemetry_window_s=1.0)
    sim.apply_disturbance(force, seconds + 1.0, start=0.2)
    try:
        while sim.time < seconds:
            sim.step()
            if float(np.max(np.abs(sim.angles))) > math.radians(60):
                return math.inf, math.inf
    except SimulationDiverged:
        return math.inf, math.inf
    x = float(sim.state[0]) if sim.model.has_cart else 0.0
    return abs(x), abs(math.degrees(float(sim.angles[0])))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="quick")
    ap.add_argument("--markdown", default="")
    args = ap.parse_args()
    s = TuningSettings.preset(args.preset)
    rows = []
    for name in SCENARIOS:
        cfg = scenario(name)
        out = {}
        for label, integral in (("PD", False), ("PID", True)):
            t0 = time.perf_counter()
            res = tune(cfg, s, TuneOptions(mode="full", integral=integral))
            _ok, cost = _push_test(_upright_config(_with(cfg, res.controller)),
                                   0.5 * res.max_push, s, allow_early_stop=False)
            out[label] = (res, cost, time.perf_counter() - t0)
        f = 0.03 * min(out["PD"][0].max_push, out["PID"][0].max_push)
        for label in ("PD", "PID"):
            res, cost, secs = out[label]
            x, peak = steady_force(cfg, res.controller, f)
            kis = ", ".join(f"{lp.ki:.3g}" for lp in res.controller.loops)
            rows.append((name, label, res.max_push, cost, f, x, peak, kis, secs))
            print(f"{name:18s} {label:3s} push {res.max_push:8.3g} N  cost {cost:9.3g}  "
                  f"steady {f:.3g} N: |x| {x:.3g} m, |θ1| {peak:.3f}°  Ki [{kis}]  ({secs:.0f} s)", flush=True)
    if args.markdown:
        lines = ["| Scenario | Search | Recoverable push (N) | Recovery cost (rad²·s) | Steady force (N) | "
                 "Final cart offset (m) | Final θ1 offset (°) | Ki of the loops |", "|---|---|---|---|---|---|---|---|"]
        for name, label, push, cost, f, x, peak, kis, _secs in rows:
            lines.append(f"| {name} | {label} | {push:.3g} | {cost:.3g} | {f:.3g} | {x:.3g} | {peak:.3f} | {kis} |")
        Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _with(cfg, controller):
    c = cfg.copy()
    c.controller = controller
    return c


if __name__ == "__main__":
    main()
