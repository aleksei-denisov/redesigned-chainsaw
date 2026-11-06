# Development guide

## Architecture

```
            ┌──────────────── pendulum_sim/ui (PySide6, pyqtgraph) ────────────────┐
            │ MainWindow ─ PendulumView (QPainter) ─ TelemetryPanel ─ panels ─     │
            │ ExperimentPanel (QThread worker)                                     │
            └───────────────┬──────────────────────────────────────────────────────┘
                            │ edits AppConfig, calls Simulator / run_experiment
            ┌───────────────▼──────────── pendulum_sim/simulation ─────────────────┐
            │ AppConfig + JSON io · Simulator (fixed step) · TelemetryRecorder ·   │
            │ run_experiment · metrics · scenarios                                 │
            └───────┬──────────────────────────────────────┬───────────────────────┘
                    │                                      │
   ┌────────────────▼──── physics ─────┐   ┌──────────────▼──── control ─────────┐
   │ MultiLinkPendulum · Actuator ·    │   │ PIDLoop · MultiLoopController ·     │
   │ rk4_step · PlantParams            │◄──│ lqr_tuning (linearise + Riccati)    │
   └───────────────────────────────────┘   └─────────────────────────────────────┘
```

- `physics`, `control` and `simulation` never import Qt; they are fully tested headless.
- The GUI timer (16 ms) asks the `Simulator` to advance `wall_time × speed`; the simulator
  executes whole fixed steps. A per-frame compute budget (10 ms) keeps the GUI responsive; if
  the budget is exceeded the backlog is dropped and the run becomes slower than real time.
- Parameter edits replace dataclasses in the shared `AppConfig`; `Simulator.update_plant` /
  `update_controller` apply them between steps without touching the state. Structural changes
  (segment count, base type) require `reset()`.
- Experiments run the same `Simulator` in a `QThread`; results are deterministic.

## Adding things

- **A new scenario**: add a function to `simulation/scenarios.py`, register it in `SCENARIOS`,
  run `python scripts/export_examples.py`, add a row to RUNNING.md.
- **A new telemetry signal**: add a column in `simulation/telemetry.py`, fill it in
  `Simulator._record`, plot it in `ui/telemetry_panel.py`.
- **A new controller**: implement the `step(t, q, qd) -> ControllerOutput` interface of
  `MultiLoopController` and select it in `Simulator`.
- **More segments**: `MAX_SEGMENTS` in `physics/params.py`; the dynamics are general, the UI
  colours/tabs and telemetry columns assume ≤ 3.

## Tests

```bash
python -m pytest                    # all tests
python -m pytest tests/test_physics.py tests/test_controller.py tests/test_simulation.py
```

| File | Covers |
|---|---|
| `tests/test_physics.py` | dynamics vs. independent Lagrangian, analytic results, energy/momentum, SciPy cross-check, actuator |
| `tests/test_controller.py` | PID terms, sample rate/ZOH, limits, anti-windup, derivative filter, bumpless retuning, LQR stability, closed-loop balancing |
| `tests/test_simulation.py` | engine determinism, live updates, disturbances, noise, telemetry, JSON round trip, example files, metrics, experiments |
| `tests/test_gui_smoke.py` | offscreen GUI: window, painting every scenario, live edits, structural change, LQR seeding, experiments, config save |

## Continuous integration

`.github/workflows/ci.yml` runs on every push:

1. Tests on Ubuntu, Windows and macOS with Python 3.10 and 3.12, installing exactly as
   documented (`pip install -r requirements.txt`). Ubuntu + Python 3.10 is informational
   (`continue-on-error`): PySide6 6.12 aborts inside Qt in the offscreen GUI tests there.
2. `python main.py --smoke-test 2` (offscreen launch + one experiment).
3. `pip install .` and the `pendulum-sim` entry point.
4. The `start.sh` / `start.bat` launchers.
5. PyInstaller builds for Windows and macOS, smoke-tested, uploaded as artifacts.

## Documentation figures

```bash
python -m pip install matplotlib
python scripts/make_figures.py      # writes docs/images/*.png from real runs
```
