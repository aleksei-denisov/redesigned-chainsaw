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
session      form fields and values, a scripted session (edits, structure changes, LQR,
             undo of the model) with the configuration and simulator state after each step,
             slider mappings, edit rules and texts
tuning       ``max_recoverable_push`` and ``tuning2.evaluate_controller`` for fixed
             candidate controllers, plus the candidates and scores of one seeded
             ``tuning2.tune`` run (workers = 1)

Not covered on purpose: sensor noise (all scenarios have it off; NumPy's generator is not
reproduced in C++) and the random sampling inside auto-tune.

Chaotic runs: some scenarios (a pendulum that falls or swings freely) amplify rounding
differences – LAPACK on another platform already changes their long-run numbers. Each
experiment case carries ``sensitive`` and each plain simulation case ``sensitive_from_s``
(found by rerunning with every gain changed by 1e-11); the C++ tests compare those cases
only up to that time, or by coarse properties.

For auto-tune the C++ code must reproduce the deterministic parts exactly — each candidate's evaluation — and the
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
    "session": {"rel": 1e-9, "abs": 1e-12, "note": "forms, edit rules, texts; states after 0.2 s of simulation"},
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


def clean_numbers(value: Any) -> list[float]:
    """All finite numbers in a cleaned structure, in order (for sensitivity checks)."""
    if isinstance(value, dict):
        return [x for v in value.values() for x in clean_numbers(v)]
    if isinstance(value, list):
        return [x for v in value for x in clean_numbers(v)]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [float(value)]
    return []


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
        plain = run_sim(cfg, 4.0, None)
        other = run_sim(nudged(cfg), 4.0, None)
        a, b = np.array(plain["states"]), np.array(other["states"])
        dev = np.abs(a - b).max(axis=1) / (np.abs(a).max(axis=1) + 1e-9) if a.shape == b.shape else None
        # first sample time from which a rounding-size change shows up beyond 1e-6
        bad = None if dev is None else np.nonzero(dev > 1e-6)[0]
        plain["sensitive_from_s"] = None if bad is None or bad.size == 0 else float((bad[0] + 1) * 0.05)
        cases.append({"scenario": name, "config": config_to_dict(cfg), "case": "plain", **plain})
        push = {"force": 1.0, "duration": 0.1, "start": 1.0, "segment": n - 1, "ratio": 0.5}
        cases.append({"scenario": name, "config": config_to_dict(cfg), "case": "point_push", "push": push,
                      **run_sim(cfg, 4.0, push)})
    write("simulation", {"cases": cases})


# -------------------------------------------------------------- experiment
def nudged(cfg):
    """Same configuration with every gain changed by 1e-11 (relative): a rounding-size change."""
    c = cfg.copy()
    for loop in c.controller.loops:
        loop.kp *= 1.0 + 1e-11
    return c


def gen_experiment() -> None:
    cases = []
    for name, cfg in scenarios():
        r = run_experiment(cfg, name)
        m = r.metrics
        m2 = run_experiment(nudged(cfg), name).metrics
        a, b = np.array(clean_numbers(clean(m))), np.array(clean_numbers(clean(m2)))
        sensitive = bool(np.nanmax(np.abs(a - b) / (np.abs(a) + 1e-9)) > 1e-6) if a.size else False
        cases.append({"scenario": name, "config": config_to_dict(cfg),
                      "diverged": getattr(r, "diverged", False),
                      "sensitive": sensitive, "metrics": m})
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


# ----------------------------------------------------------------- session
def gen_session() -> None:
    from types import SimpleNamespace

    from pendulum_sim.session import IncompatibleRun, Session, apply, rules, sliders, texts
    from pendulum_sim.session.forms import ControllerForm, ExperimentForm, PlantForm, SimulationForm

    def dump_form(form) -> dict:
        sections = [{"key": sec.key, "title": sec.title, "expanded": sec.expanded, "note": sec.note,
                     "fields": [{"key": f.key, "label": f.label, "kind": f.kind, "minimum": f.minimum,
                                 "maximum": f.maximum, "decimals": f.decimals, "unit": f.unit, "mapping": f.mapping,
                                 "step": f.step, "hint": f.hint, "options": [list(o) for o in f.options]}
                                for f in sec.fields]} for sec in form.sections]
        return {"sections": sections, "values": dict(form.values)}

    base = scenario("single_cart_full")
    pf, sf, cf, ef = PlantForm(), SimulationForm(), ControllerForm(), ExperimentForm()
    pf.set_plant(base.plant)
    sf.set_simulation(base.simulation)
    cf.set_config(scenario("double_cart_lqr").controller)
    forms = {"plant": dump_form(pf), "simulation": dump_form(sf), "controller": dump_form(cf),
             "experiment": dump_form(ef)}

    # A scripted session; after every step the operations go to a simulator that then runs 0.2 s.
    s = Session(scenario("single_cart_full"))
    sim = Simulator(s.config.copy(), telemetry_window_s=None)
    pf, cf, sf = PlantForm(), ControllerForm(), SimulationForm()
    pf.set_plant(s.config.plant)
    cf.set_config(s.config.controller)
    sf.set_simulation(s.config.simulation)
    steps = []

    def record(action: str, ops=(), extra: dict | None = None) -> None:
        nonlocal sim
        for op in ops:
            sim = apply(sim, op)
        for _ in range(200):
            sim.step()
        steps.append({"action": action, "operations": [op[0] for op in ops], "config": config_to_dict(s.config),
                      "state": sim.state.copy(), "time": sim.time, **(extra or {})})

    def plant_edit(key, value):
        plant, structural = pf.set(key, value)
        ch = s.change_plant(plant, structural)
        if ch.controller is not None:
            cf.set_config(ch.controller)
        record(f"plant {key}={value}", ch.operations, {"structural": ch.structural, "reset_if_idle": ch.reset_if_idle})

    plant_edit("seg0.mass", 0.3)
    plant_edit("seg0.rod", False)
    plant_edit("seg0.inertia", 0.01)
    plant_edit("seg0.angle", 5.0)
    plant_edit("segments", 2)
    ctrl, ops = s.seed_lqr()
    cf.set_config(ctrl)
    record("seed_lqr", ops)
    record("controller loop1.kp=12.5", s.change_controller(cf.set("loop1.kp", 12.5)))
    record("controller loop0.anti=back_calculation", s.change_controller(cf.set("loop0.anti", "back_calculation")))
    plant_edit("base", "fixed")
    ctrl, ops = s.restore_gains()
    record("restore_gains", ops)
    try:
        s.use_run_gains(scenario("double_cart_lqr"))
        incompatible = False
    except IncompatibleRun:
        incompatible = True
    record("use_run_gains double_cart_lqr", [], {"incompatible": incompatible})
    ch = s.restore_model(s.original_plant, s.original_simulation, s.original_controller)
    record("restore_model original", ch.operations, {"structural": ch.structural})
    record("simulation seed=7.6", s.change_simulation(sf.set("seed", 7.6)))
    record("simulation dt=0.5ms", s.change_simulation(sf.set("dt", 0.5)))
    ctrl, ops = s.use_run_gains(scenario("single_cart_pid"))
    record("use_run_gains single_cart_pid", ops)
    record("reset_integrators", [("reset_integrators",)])

    slider_cases = []
    for lo, hi, mapping, dec in [(0.02, 5.0, "log", 3), (-1e6, 1e6, "symlog", 3), (0.0, 5.0, "symlog", 5),
                                 (-180.0, 180.0, "linear", 2), (0.0, 1.0, "symlog", 6)]:
        pos = [sliders.from_position(t, lo, hi, mapping, dec) for t in (0.0, 0.1, 0.37, 0.5, 0.9, 1.0)]
        vals = [lo, 0.5 * (lo + hi), hi, 0.123, -0.0004, 42.0]
        slider_cases.append({"lo": lo, "hi": hi, "mapping": mapping, "decimals": dec, "from_position": pos,
                             "values": vals, "to_position": [sliders.to_position(v, lo, hi, mapping, dec) for v in vals],
                             "nudge_up": [sliders.nudge(v, 1, lo, hi, mapping, dec) for v in vals],
                             "nudge_down": [sliders.nudge(v, -1, lo, hi, mapping, dec) for v in vals]})

    run = run_experiment(scenario("single_fixed_pid"), "run")
    fake = SimpleNamespace(baseline_max_push=0.42, max_push=1.234, recommended_push=0.61, evaluations=37,
                           elapsed=83.4, cancelled=True)
    text_cases = {
        "durations": [[v, texts.fmt_duration(v)] for v in (None, float("inf"), 0.0, 4.4, 59.5, 61.0, 3599.0, 3725.0)],
        "gains": {k: texts.gains_text(scenario(k).controller) for k in ("single_cart_pid", "double_cart_lqr", "windup_demo")},
        "metric_pairs": [list(p) for p in texts.metric_pairs(run)],
        "status": texts.status_text(run),
        "headline": texts.tune_headline(fake),
        "notes": texts.tune_notes(fake),
        "applied": texts.tuning_applied(fake),
        "cores": texts.tune_cores(3, 8),
    }
    rule_cases = {
        "drag": [[p, o, rules.drag_force(p, o)] for p, o in ((1.0, 0.3), (2.5, 3.0), (2.5, -3.0), (0.4, -0.1))],
        "point_drag": [[p, o, rules.point_drag_force(p, o)] for p, o in ((1.0, 0.3), (2.5, 3.0), (-2.5, -3.0))],
        "single_step": [[per, dt, rules.single_step_count(per, dt)] for per, dt in ((0.002, 0.001), (0.0005, 0.001), (0.01, 0.0005))],
        "labels": rules.loop_labels(scenario("triple_cart_lqr").controller),
        "output_limit": rules.output_limit(scenario("windup_demo")),
    }
    write("session", {"forms": forms, "steps": steps, "sliders": slider_cases, "texts": text_cases,
                      "rules": rule_cases, "run_config": config_to_dict(run.config)})


GROUPS: dict[str, Callable[[], None]] = {
    "physics": gen_physics,
    "control": gen_control,
    "simulation": gen_simulation,
    "experiment": gen_experiment,
    "tuning": gen_tuning,
    "session": gen_session,
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
