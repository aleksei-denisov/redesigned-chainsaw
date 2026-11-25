# C++ / Qt Quick port (branch `cpp-core`)

Goal: one application written in **C++ with a Qt Quick (QML) interface** that has the full
feature set of both current versions — the desktop app (`pendulum_sim/ui`, Qt Widgets) and the
Qt Quick app (`pendulum_sim/quick`) — on Android, iOS/iPadOS, macOS, Windows and Linux.

The Python package is not changed by the port. It stays the **reference implementation**: Phase 1
adds scripts in `cpp/reference/` that run it and write golden data, and every C++ module must
reproduce those results within a stated tolerance.

## Decisions

| Question | Decision |
|---|---|
| Desktop interface | Same QML as mobile, with a wide (desktop) layout. Qt Widgets is not kept. |
| Default core behaviour | `quick-core-v2` (push at the tapped point, push force, auto-tune with Ki ≠ 0, gain limits, selected loops) on every platform. The classic behaviour stays available as a setting (`pendulum::CoreMode`). |
| iOS | Only after the Android build passes its tests on the Xiaomi Mi Pad 6. |
| Intermediate Python bindings (pybind11) | No — the core is rewritten in C++ directly. |
| Feature scope | The **union** of all versions: anything that exists in the desktop app, the first Android app (`pendulum_sim/mobile`) or the Qt Quick app is implemented in the C++ app. |
| Graph analyser | Modelled on the first Android app's explorer (`mobile/plot_view.py`), available on phone, tablet and desktop — see below. |

## Improvements over the Python version

Found while porting; the C++ version fixes or adds these (the Python versions stay as they are).

| # | Finding | C++ version |
|---|---|---|
| 1 | Auto-tune "All loops (LQR design)" can return a controller weaker than the current one: the current controller is measured but is not a candidate (seen: 4.48 N result vs 5.12 N before, `single_cart_full`, quick preset) | The current controller is always a candidate, so the result is never worse than before tuning |
| 2 | Saturated, near bang-bang control (e.g. `double_fixed_lqr`: Kp up to −647 against 2 N·m) makes runs extremely sensitive to rounding: a switch moves by one controller sample | Saturation indicator: share of time at the actuator limit, with a warning when it is high; graphs mark saturated stretches |
| 3 | Sensor noise and auto-tune sampling use NumPy's generator; results differ between Python and C++ | One seedable generator in the core, the same on every platform, so a seed reproduces a run on desktop, phone and tablet alike |
| 4 | Auto-tune runs in processes (Android: limited) | Thread pool on every platform, including iOS |

## Layout

```
cpp/
  CMakeLists.txt        options: PENDULUM_BUILD_TESTS (ON), PENDULUM_BUILD_APP (OFF, needs Qt 6)
  core/                 Qt-free library: physics, control, simulation, session (mirrors pendulum_sim/*)
  tests/                GoogleTest unit + reference-comparison tests
  reference/            Python golden-data generators (Phase 1) and data/
  app/                  Qt Quick application (Phase 5+)
```

The layer rules from `CLAUDE.md` apply unchanged: `core` never includes Qt. Dependencies:
Eigen 3.4 (system package if found, otherwise fetched from its GitHub mirror) and GoogleTest
(tests only). Qt 6.5+ is needed only for `app/`.

```bash
cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build cpp/build --parallel
ctest --test-dir cpp/build --output-on-failure
# with the app (Qt 6 installed):
cmake -S cpp -B cpp/build -DPENDULUM_BUILD_APP=ON
./cpp/build/pendulum_bench                 # physics + controller speed
```

CI: `.github/workflows/cpp.yml` (core tests on Linux/macOS/Windows, app build on Linux).
The JSON library is nlohmann/json (config and result files, reference data).

## Status

| Phase | State |
|---|---|
| 0 | Done — skeleton, CI, feature matrix |
| 1 | Done — golden data in `cpp/reference/data`, see [cpp/reference/README.md](../cpp/reference/README.md) |
| 2 | Done — physics and controllers, matching the reference data (physics to 1e-12) |
| 3 | Next — simulation engine, experiments, auto-tune |

### Phase 2 contents

| C++ (`cpp/core`) | Python original | Notes |
|---|---|---|
| `physics/params`, `physics/model`, `physics/actuator` | `physics/params.py`, `model.py`, `fast_dynamics.py`, `actuator.py` | Reference equations plus the fast path (`rk4Step`, Cholesky) with the same operation order; fixed-capacity vectors, no heap allocation per step |
| `control/pid`, `control/multiloop` | `control/pid.py`, `multiloop.py` | `setConfig` keeps a loop's integrator when the loop at the same position (or any loop) has the same signal |
| `control/riccati`, `control/lqr` | `control/riccati.py`, `lqr_tuning.py` | Hamiltonian eigenvectors + Newton–Kleinman; agrees with both Python backends (NumPy and SciPy) to 1e-9 |
| `io/config_json` | `simulation/config.py` (plant, controller) | Same JSON format (degrees in files, radians in memory) |

Speed (`cpp/build/pendulum_bench`, physics step + controller, dt = 1 ms, same machine):

| Segments | Python | C++ | Factor |
|---|---|---|---|
| 1 | 48 k steps/s | 3.6 M steps/s | ≈ 75× |
| 2 | 29 k steps/s | 1.8 M steps/s | ≈ 61× |
| 3 | 19 k steps/s | 1.2 M steps/s | ≈ 63× |

## Phases

| # | Phase | Python source | Estimate |
|---|---|---|---|
| 0 | Build skeleton, CI, feature matrix | — | 2–3 days |
| 1 | Golden reference data | tests, scenarios | 3–4 days |
| 2 | Physics and controllers | `physics`, `control` (~1.3k lines) | 1–1.5 weeks |
| 3 | Simulation, experiments, auto-tune (thread pool) | `simulation` (~2.8k lines) | 1–1.5 weeks |
| 4 | Session layer and files (same file formats) | `session`, `ui/file_io` | 1 week |
| 5 | Qt Quick app in C++ (QML reused) | `quick/backend` (~5.4k lines), `quick/qml` | 2–3 weeks |
| 6 | Desktop features in QML (wide layout) | `ui` | 2–3 weeks |
| 7 | Platforms and acceptance (Android, then iOS) | — | 1–1.5 weeks |
| 8 | Switch-over; Python kept only as reference | — | — |

## Feature matrix

Acceptance list for Phases 5–7. "Desktop" and "Quick" show where the feature was found in the code
(✓ yes, ~ partly / different form, — no, ? not confirmed). The columns are informational only:
by decision the C++ app implements **every row**, on the phone, tablet and wide (desktop) layout.

### Model and controller

| Feature | Desktop | Quick | Notes |
|---|---|---|---|
| Plant editor: cart on a track or fixed pivot, segments (mass, length, inertia, friction) | ✓ | ✓ | `plant_panel`, `ModelScreen.qml` |
| Actuator limit (force / torque) | ✓ | ✓ | |
| Multi-loop PID controller editor | ✓ | ✓ | `controller_panel`, `ControllerScreen.qml` |
| Reset integrators | ✓ | ✓ | |
| Seed gains from LQR | ✓ | ✓ | |
| Restore original gains | ✓ | ~ | Quick: "Reset to default" |
| Undo / redo of edits | ? | ✓ | `session/history` |
| Scenarios: load built-in / save / load JSON configs | ✓ | ✓ | `configs/`, `SettingsBridge`/`ScenarioBridge` |

### Simulation and view

| Feature | Desktop | Quick | Notes |
|---|---|---|---|
| Real-time simulation, fixed `dt`, start / pause / step / reset | ✓ | ✓ | |
| Simulation settings (dt, speed, view) | ✓ | ✓ | `settings_panel`, `SettingsScreen.qml` |
| Pendulum view: zoom (wheel / pinch), fit view, trails, clear trajectories | ✓ | ✓ | |
| Mouse / touch push | ✓ | ✓ | v2: push at the tapped point, user-set push force |
| Left / right push buttons (0.1 s pulse) | ? | ✓ | |
| Status tiles / readouts under the view, editable tile layout, saved layouts | — | ✓ | `TileEditor.qml` |
| Light and dark theme | ~ | ✓ | |
| Performance report / benchmark | — | ✓ | `bench`, `android_perf` |

### Graphs and analysis

| Feature | Desktop | Quick | Notes |
|---|---|---|---|
| Live telemetry plots (angles, cart, output, per-loop signals) | ✓ | ✓ | `telemetry_panel`, `GraphsScreen.qml` |
| Loop selector, time window | ✓ | ? | |
| Wrap angles to ±180° | ✓ | ? | |
| Calm Y scaling (expand fast, shrink slowly) | ✓ | ? | |
| Display smoothing (10 / 30 / 100 ms moving average) | — | ✓ | |
| Choose graphs, graph cards | ~ | ✓ | |
| Graph analyser (full-screen explorer) | — | ~ | First Android app: ✓. Specified below |

### Experiments

| Feature | Desktop | Quick | Notes |
|---|---|---|---|
| Run experiment, list of runs, before / after comparison | ✓ | ✓ | `experiment_panel`, `ExperimentsScreen.qml` |
| Metrics per run | ✓ | ✓ | `simulation/metrics` |
| Remove selected / clear all runs | ✓ | ✓ | |
| Load selected gains into controller | ✓ | ? | |
| Save / load results, export selected as CSV | ✓ | ? | |

### Auto-tune

| Feature | Desktop | Quick | Notes |
|---|---|---|---|
| Auto-tune for robustness (largest recoverable push) | ✓ | ✓ | `tuning_dialog`, `AutotuneScreen.qml` |
| Foreground task, multi-core, progress with ETA, cancel (keep best so far) | ✓ | ✓ | C++: thread pool instead of processes |
| Live round chart (best push per round, candidates) | ? | ✓ | |
| Choose loops to tune, Ki not forced to zero | — | ✓ | v2 |
| Gain limits: per loop / general / none | — | ✓ | v2 |
| Before / after result and "apply gains" | ✓ | ✓ | |

### Platform and files

| Feature | Desktop | Quick | Notes |
|---|---|---|---|
| System file dialogs (incl. Android `content://` URIs) | ✓ | ✓ | `ui/file_io` |
| Menu, keyboard shortcuts, About | ✓ | — | Wide layout only |
| Exit confirmation, "previous start did not finish" recovery | — | ✓ | |

### Graph analyser (from the first Android app)

Every graph — live telemetry, experiment runs, auto-tune charts — can be opened full-screen and
explored with the fingers (mouse and wheel on the desktop). Reference: `GraphExplorer` and
`PlotView` in `pendulum_sim/mobile/plot_view.py`, fast drawing in `mobile/graphs.py` and
`mobile/plot_math.py`.

| Feature | Notes |
|---|---|
| Open any graph full-screen by tapping it (Graphs, Experiments, Auto-tune) | Thumbnails stay non-interactive so the page scrolls |
| Pinch zoom: sideways = time, vertical = values, diagonal = both; point between the fingers stays put | Desktop: wheel = time, Shift+wheel = values |
| One-finger drag pans | |
| Double tap fits all data | Plus a **Fit** button |
| Tap = read-out cursor with the value of every curve at that time | |
| **Live on / off**: follow the running simulation or freeze the graph to study it | Freezing does not stop the simulation |
| **Now**: jump back to the newest point and resume following | |
| **Scroll on / off**: while live, a sideways drag looks back in time and keeps that offset as the run continues | |
| Zoom / value range kept while following | |
| **Image**: save the graph as PNG; **Data**: save its data as CSV | Through the system file dialog |
| Min/max downsampling to two points per pixel column | Pan and zoom stay smooth however long the recording |
| Graph strip on the Graphs page: choose graphs, time window, PID loop, remembered between starts | Plus Quick's smoothing, desktop's angle wrapping and calm Y scaling |

