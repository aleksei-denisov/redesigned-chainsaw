"""Golden reference data for the C++ port (Phase 1, docs/CPP_PORT.md).

Runs the original Python package and writes JSON files to ``cpp/reference/data``. The C++
tests load them and must reproduce every number within the tolerances in
``data/manifest.json``.

    python cpp/reference/generate.py            # all groups
    python cpp/reference/generate.py physics    # one or more groups

Groups (one file per group):

physics      per scenario plant: mass matrix, bias forces, generalised forces,
             accelerations, push arms, energies, momentum and one RK4 step at seeded
             random states, with and without actuator, tip and point forces
control      linearisation (A, B), CARE solution P (NumPy and SciPy backends) and LQR gain
             for every scenario plant; PID loop outputs over a scripted input sequence
simulation   every scenario simulated with ``Simulator``: telemetry rows every 50 ms,
             with a mid-run point push (v2 path) as a second case
experiment   ``run_experiment`` metrics for every scenario
tuning       ``max_recoverable_push`` and ``tuning2.evaluate_controller`` for fixed
             candidate controllers, plus the candidates and scores of one seeded
             ``tuning2.tune`` run (workers = 1)

Not covered on purpose: sensor noise (all scenarios have it off; NumPy's generator is not
reproduced in C++) and the random sampling inside auto-tune. For auto-tune the C++ code
must reproduce the deterministic parts exactly — each candidate's evaluation — and the
search as a whole is compared by result quality, not candidate by candidate.
"""

from __future__ import annotations

import dataclasses
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pendulum_sim.control import MultiLoopController, PIDLoop, linearize, lqr_gain  # noqa: E402
from pendulum_sim.control import riccati  # noqa: E402
from pendulum_sim.control.lqr_tuning import LQRWeights  # noqa: E402
from pendulum_sim.physics import MultiLinkPendulum  # noqa: E402
from pendulum_sim.simulation import SCENARIOS, Simulator, config_to_dict, run_experiment, scenario  # noqa: E402
from pendulum_sim.simulation import tuning, tuning2  # noqa: E402
from pendulum_sim.simulation.telemetry import COLUMNS  # noqa: E402

DATA = Path(__file__).resolve().parent / "data"
FORMAT_VERSION = 1

TOLERANCES = {
    "physics": {"rel": 1e-12, "abs": 1e-12, "note": "single evaluations"},
    "control": {"rel": 1e-8, "abs": 1e-10, "note": "linearisation uses finite differences; CARE solvers differ"},
    "simulation": {"rel": 1e-7, "abs": 1e-9, "note": "thousands of RK4 steps"},
    "experiment": {"rel": 1e-6, "abs": 1e-9, "note": "metrics from long runs"},
    "tuning": {"rel": 1e-9, "abs": 1e-12, "note": "push forces from bisection; must match exactly up to rounding"},
}


def clean(value: Any) -> Any:
    """JSON-safe copy: arrays to lists, NaN/inf to strings the C++ reader understands."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return clean(dataclasses.asdict(value))
    if isinstance(value, np.ndarray):
        return clean(value.tolist())
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (np.floating, float)):
        f = float(value)
        if math.isnan(f):
            return "nan"
        if math.isinf(f):
            return "inf" if f > 0 else "-inf"
        return f
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def write(name: str, payload: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    payload = {"format": FORMAT_VERSION, "group": name, "tolerance": TOLERANCES[name], **payload}
    path = DATA / f"{name}.json"
    path.write_text(json.dumps(clean(payload), separators=(",", ":")) + "\n")
    print(f"{path}: {path.stat().st_size / 1024:.0f} KiB")


def scenarios() -> list[tuple[str, Any]]:
    return [(name, scenario(name)) for name in SCENARIOS]


# ----------------------------------------------------------------- physics
def gen_physics() -> None:
    rng = np.random.default_rng(20261125)
    cases = []
    for name, cfg in scenarios():
        model = MultiLinkPendulum(cfg.plant)
        n1 = model.n + 1
        samples = []
        for k in range(6):
            q = np.concatenate([[rng.uniform(-0.5, 0.5)], rng.uniform(-math.pi, math.pi, model.n)])
            qd = rng.normal(0.0, 2.0, n1)
            if not model.has_cart:
                q[0], qd[0] = 0.0, 0.0
            if k == 0:  # the upright equilibrium
                q[:], qd[:] = 0.0, 0.0
            state = np.concatenate([q, qd])
            u = float(rng.uniform(-1.0, 1.0) * cfg.plant.actuator.max_output)
            f = float(rng.uniform(-5.0, 5.0))
            seg = int(rng.integers(0, model.n))
            ratio = float(rng.uniform(0.0, 1.0))
            arms = model.push_arms(seg, ratio) * f
            dt = cfg.simulation.dt
            samples.append({
                "state": state, "actuator": u, "tip_force": f,
                "push": {"segment": seg, "ratio": ratio, "arms_unit": model.push_arms(seg, ratio), "arms": arms},
                "mass_matrix": model.mass_matrix(q),
                "bias_forces": model.bias_forces(q, qd),
                "joint_friction": model.joint_friction_torques(qd),
                "generalized_forces": model.generalized_forces(q, qd, u, f),
                "accelerations_free": model.accelerations(state),
                "accelerations": model.accelerations(state, u, f),
                "accelerations_push": model.accelerations(state, u, 0.0, arms),
                "derivative": model.derivative(state, u, f),
                "kinetic_energy": model.kinetic_energy(state),
                "potential_energy": model.potential_energy(state),
                "horizontal_momentum": model.horizontal_momentum(state),
                "dt": dt,
                "rk4": model.rk4_step(state, u, f, dt),
                "rk4_push": model.rk4_step(state, u, 0.0, dt, arms),
                "initial_state": model.initial_state(),
            })
        cases.append({"scenario": name, "config": config_to_dict(cfg), "samples": samples})
    write("physics", {"cases": cases})


# ----------------------------------------------------------------- control
def gen_control() -> None:
    plants = []
    for name, cfg in scenarios():
        A, B, idx = linearize(cfg.plant)
        w = LQRWeights()
        K, kidx = lqr_gain(cfg.plant, w)
        Q = np.diag(np.ones(A.shape[0]))
        R = np.eye(B.shape[1]) if B.ndim == 2 else np.eye(1)
        Bm = B if B.ndim == 2 else B.reshape(-1, 1)
        entry = {"scenario": name, "plant": config_to_dict(cfg)["plant"], "A": A, "B": Bm, "state_index": idx,
                 "lqr": {"weights": w.__dict__, "K": K, "index": kidx}}
        try:
            entry["care"] = {"Q": Q, "R": R,
                             "P_numpy": riccati.solve_care(A, Bm, Q, R, backend="numpy"),
                             "P_scipy": riccati.solve_care(A, Bm, Q, R, backend="scipy") if riccati.HAVE_SCIPY else None}
        except Exception as exc:  # an uncontrollable plant (e.g. hanging) – the C++ code must fail too
            entry["care"] = {"Q": Q, "R": R, "error": str(exc)}
        plants.append(entry)

    rng = np.random.default_rng(7)
    loops = []
    for name, cfg in scenarios():
        for lc in cfg.controller.loops:
            loop = PIDLoop(lc)
            ts = MultiLoopController(cfg.controller).period
            steps = []
            for _ in range(200):
                m, r = float(rng.normal(0.0, 0.3)), float(rng.normal(0.0, 1.0))
                out = loop.compute(m, r, ts, 1.0)
                sat = float(np.clip(out.total, -cfg.plant.actuator.max_output, cfg.plant.actuator.max_output))
                loop.update_integral(ts, out.total, sat)
                steps.append([m, r, out.error, out.p, out.i, out.d, out.total, sat])
            loops.append({"scenario": name, "loop": lc.__dict__ if not hasattr(lc, "to_dict") else lc.to_dict(),
                          "ts": ts, "columns": ["measurement", "rate", "error", "p", "i", "d", "total", "saturated"],
                          "steps": steps})
    write("control", {"plants": plants, "pid": loops})


# -------------------------------------------------------------- simulation
def run_sim(cfg, seconds: float, push: dict | None) -> dict:
    sim = Simulator(cfg, telemetry_window_s=None)
    if push:
        sim.apply_disturbance(push["force"], push["duration"], start=push["start"],
                              segment=push["segment"], ratio=push["ratio"])
    every = int(round(0.05 / cfg.simulation.dt))
    rows, diverged = [], None
    try:
        for step in range(int(round(seconds / cfg.simulation.dt))):
            sim.step()
            if (step + 1) % every == 0:
                rows.append(sim.state.copy())
    except Exception as exc:
        diverged = {"time": sim.time, "message": str(exc)}
    tel = sim.telemetry.array() if hasattr(sim.telemetry, "array") else None
    out = {"state_every_s": 0.05, "states": rows, "diverged": diverged, "final_time": sim.time, "steps": sim.steps}
    if tel is not None:
        out["telemetry_columns"] = list(COLUMNS)
        out["telemetry"] = tel[::10]  # every 10th recorded row (20 Hz)
    return out


def gen_simulation() -> None:
    cases = []
    for name, cfg in scenarios():
        n = cfg.plant.n_segments
        cases.append({"scenario": name, "config": config_to_dict(cfg), "case": "plain",
                      **run_sim(cfg, 4.0, None)})
        push = {"force": 1.0, "duration": 0.1, "start": 1.0, "segment": n - 1, "ratio": 0.5}
        cases.append({"scenario": name, "config": config_to_dict(cfg), "case": "point_push", "push": push,
                      **run_sim(cfg, 4.0, push)})
    write("simulation", {"cases": cases})


# -------------------------------------------------------------- experiment
def gen_experiment() -> None:
    cases = []
    for name, cfg in scenarios():
        r = run_experiment(cfg, name)
        m = r.metrics
        cases.append({"scenario": name, "config": config_to_dict(cfg),
                      "diverged": getattr(r, "diverged", False),
                      "metrics": m})
    write("experiment", {"cases": cases})


# ------------------------------------------------------------------ tuning
def gen_tuning() -> None:
    quick = tuning.TuningSettings.preset("quick")
    settings = replace(quick, workers=1)
    pushes, evals = [], []
    for name in ("single_cart_full", "double_cart_lqr", "single_fixed_pid"):
        cfg = scenario(name)
        d = config_to_dict(cfg)
        pushes.append({"scenario": name, "config": d, "settings": settings.__dict__,
                       "max_recoverable_push": tuning.max_recoverable_push(cfg, settings)})
        for log_w in ([0.0, 0.0, 0.0, 0.0, 0.0], [0.5, -0.3, 0.2, 0.0, -0.5]):
            ctrl = tuning2.full_controller(cfg, tuning2._weights(np.array(log_w)))
            c2 = cfg.copy()
            c2.controller = ctrl
            cd = config_to_dict(c2)
            evals.append({"scenario": name, "log_weights": log_w, "config": cd, "settings": settings.__dict__,
                          "result": tuning2.evaluate_controller(cd, settings)})

    cfg = scenario("single_cart_full")
    seen: list[dict] = []
    orig = tuning2.evaluate_controller

    def recording(config_dict, s, min_push=0.0, hold=False):
        res = orig(config_dict, s, min_push, hold)
        seen.append({"config": config_dict, "min_push": min_push, "hold": hold, "result": res})
        return res

    tuning2.evaluate_controller = recording
    try:
        result = tuning2.tune(cfg, settings, tuning2.TuneOptions())
    finally:
        tuning2.evaluate_controller = orig
    run = {"scenario": "single_cart_full", "settings": settings.__dict__, "options": "default",
           "max_push": result.max_push, "baseline_max_push": result.baseline_max_push,
           "recommended_push": result.recommended_push, "evaluations_count": result.evaluations,
           "history": result.history, "weights": result.weights,
           "controller": result.controller, "evaluations": seen}
    write("tuning", {"max_push": pushes, "evaluate_controller": evals, "tune_run": run})


GROUPS: dict[str, Callable[[], None]] = {
    "physics": gen_physics,
    "control": gen_control,
    "simulation": gen_simulation,
    "experiment": gen_experiment,
    "tuning": gen_tuning,
}


def main(argv: list[str]) -> None:
    names = argv or list(GROUPS)
    for name in names:
        GROUPS[name]()
    manifest = {"format": FORMAT_VERSION, "numpy": np.__version__, "groups": TOLERANCES,
                "files": sorted(p.name for p in DATA.glob("*.json") if p.name != "manifest.json")}
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
