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

`pendulum_sim/mobile` is a second, touch-first front end over the same lower layers (used by
the Android app, see [ANDROID.md](ANDROID.md)). It reuses `PendulumView` (subclassed only for
touch input) and `TelemetryPanel` from `ui/`, and paces the simulator with the Qt-free
`simulation/realtime.py`. It must not contain simulation or control logic.

- `physics`, `control` and `simulation` never import Qt; they are fully tested headless.
- SciPy is optional at run time: `control/riccati.py` uses SciPy's CARE solver when installed
  and a NumPy Hamiltonian/Newton–Kleinman solver otherwise (Android ships without SciPy).
- The GUI timer (16 ms) asks the `Simulator` to advance `wall_time × speed`; the simulator
  executes whole fixed steps. A per-frame compute budget (10 ms) keeps the GUI responsive; if
  the budget is exceeded the backlog is dropped and the run becomes slower than real time.
- Performance: physics steps use the scalar fast path (`physics/fast_dynamics.py`); sensors are
  only sampled when the controller runs; graphs are drawn without antialiasing, with fixed axis
  widths (no re-layout), peak downsampling and wrapped angles, hidden graphs are skipped, and
  the graph refresh interval adapts (66–300 ms) when frames take longer than 20 ms. The status
  bar shows fps and physics ms per frame.
- Parameter edits replace dataclasses in the shared `AppConfig`; `Simulator.update_plant` /
  `update_controller` apply them between steps without touching the state. Structural changes
  (segment count, base type) require `reset()`.
- Experiments run the same `Simulator` in a `QThread`; results are deterministic.
- The robust auto-tuner (`simulation/tuning.py`) runs in a `QThread` and evaluates candidates
  in a `ProcessPoolExecutor` (spawned workers re-import the entry module, hence the
  `if __name__ == "__main__"` guards and `multiprocessing.freeze_support()` in `main.py` /
  `pendulum_sim/__main__.py`). `workers=1` evaluates serially.

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
| `tests/test_tuning.py` | push-robustness metric, robust tuner (serial and parallel), cancellation |
| `tests/test_axis_scaling.py` | calm graph scaling: expand at once, shrink slowly, no jitter, cap |
| `tests/test_fast_dynamics.py` | the fast scalar dynamics and RK4 against the NumPy reference |
| `tests/test_physics.py` | dynamics vs. independent Lagrangian, analytic results, energy/momentum, SciPy cross-check, actuator |
| `tests/test_controller.py` | PID terms, sample rate/ZOH, limits, anti-windup, derivative filter, bumpless retuning, LQR stability, closed-loop balancing |
| `tests/test_simulation.py` | engine determinism, live updates, disturbances, noise, telemetry, JSON round trip, example files, metrics, experiments |
| `tests/test_riccati.py` | NumPy CARE solver against SciPy (all plants, random systems), LQR gains without SciPy |
| `tests/test_realtime.py` | fixed-timestep pacing: frame-rate independence, speed, stall clamp, compute budget |
| `tests/test_mobile_smoke.py` | offscreen mobile UI: run/pause/reset, scenarios, background pause/resume, tap/drag/pinch, LQR, orientation, phone width |
| `tests/test_gui_smoke.py` | offscreen GUI: window, painting every scenario, live edits, structural change, LQR seeding, experiments, config save |

## Continuous integration

`.github/workflows/ci.yml` runs on every push:

1. Tests on Ubuntu and Windows with Python 3.10 and 3.12 and on macOS with 3.12 (macOS minutes
   are 10× more expensive on private repositories), installing exactly as
   documented (`pip install -r requirements.txt`). Ubuntu + Python 3.10 is informational
   (`continue-on-error`): PySide6 6.12 aborts inside Qt in the offscreen GUI tests there.
2. `python main.py --smoke-test 2` (offscreen launch + one experiment) and
   `python -m pendulum_sim.mobile --smoke-test 2` (mobile interface).
3. `pip install .` and the `pendulum-sim` entry point.
4. The `start.sh` / `start.bat` launchers.
5. PyInstaller builds for Windows and macOS, smoke-tested, uploaded as artifacts – only on
   `main` and when the workflow is started manually (GitHub → Actions → CI → Run workflow).

`.github/workflows/android.yml` builds the debug APK on every push (artifact
`inverted-pendulum-debug-apk`) and a signed AAB on `v*` tags – see [ANDROID.md](ANDROID.md).

A newer push to the same branch cancels a still-running older run.

## Documentation figures

```bash
python -m pip install matplotlib
python scripts/make_figures.py      # writes docs/images/*.png from real runs
```
