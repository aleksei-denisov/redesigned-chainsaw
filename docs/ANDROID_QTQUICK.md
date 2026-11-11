# Qt Quick version of the Android app

A second mobile interface, rebuilt in Qt Quick (QML) with the "Instrument" design. It is
developed next to the existing Qt Widgets mobile app (`pendulum_sim/mobile`, see
[ANDROID.md](ANDROID.md)), which stays intact and remains the full-featured app until the
Qt Quick version reaches feature parity. The simulation, controllers, LQR and autotune code
are shared and unchanged.

**Status: Phase 2 – architecture.** The backend for every screen exists; the QML has the
Simulate, Graphs and Settings screens. The remaining screens (Controller, Model,
Autotune, Experiments/Scenarios) and the full design come in Phase 3.

## Why

In the Widgets app all painting runs in Python on the UI thread, together with the physics
(19–38 fps on the Pad 6, 12–20 fps in the last report). In Qt Quick the scene is drawn by
the GPU scene graph; Python only supplies state, and the physics run in a separate process.
The Phase 1 spike measured 120 fps on the Pad 6 (frame gap p95 11.6 ms, no gaps over 33 ms).

## Architecture

```
 physics process (fork)              GUI process
 ┌────────────────────────┐          ┌──────────────────────────────────────────────┐
 │ Simulator +            │  shared  │ Sim bridge ── one read per shown frame       │
 │ RealtimeStepper        │  memory  │   (interpolated state into a preallocated    │
 │ (unchanged core)       │ ───────▶ │    row) ─▶ QML positions the pendulum        │
 │ fixed dt, real time    │          │ Graphs bridge ── telemetry window, 20–30 Hz, │
 │ state history ring     │          │   only visible graphs, decimated to pixels   │
 │ telemetry ring         │ ◀─────── │ Model / Controller ── shared editor forms    │
 └────────────────────────┘ commands │ Session (shared rules) ─▶ edit operations    │
                             (pipe)  │ Frames ── vsync, pacing, hints, idle GC      │
                                     └──────────────────────────────────────────────┘
```

| Part | File | Notes |
|---|---|---|
| Shared application logic | `pendulum_sim/session/` | Qt-free rules that used to live in the desktop window and the Widgets screens: structure changes, restoring gains, LQR seeding, gains from a run, single step, drag force, graph labels, experiment configuration; editor forms; auto-tune and experiment workers; texts. Desktop and Widgets mobile use it too. `tests/test_session_equivalence.py` pins it to the previous code (identical configurations, bit-identical trajectories). |
| Physics host | `quick/backend/sim_source.py` | The unchanged `Simulator` + `RealtimeStepper` in a forked process (default) or a thread (fallback), one loop and one shared-memory layout for both. Every edit is a session operation applied with the same simulator calls as the desktop. Frame reads interpolate into a preallocated row, so dropped frames never slow the simulation. Divergence and refused edits come back as messages. |
| Bridges to QML | `quick/backend/*.py` | Context objects `sim` (state, read-outs, run control), `model`, `controller` (shared forms as list models), `graphs` (selection, views, decimation), `autotune`, `scenarios`, `frames`, `bench`, created after the loading screen; singletons `App` and `Settings`, registered before the engine starts (types registered later broke QML type checks). No UI logic in the core, no physics in QML. |
| Graph data | `quick/backend/graphdata.py`, `linegeom.py`, `lineitem.py` | Telemetry window → per series a list of pixel polylines (auto range with minimum span and nice ticks from the Widgets app's chart maths). Decimation keeps real samples – per physical pixel column the minimum and maximum at their own times – so curves stay smooth on a 2.25× screen. `GraphLines` (a scene-graph item) draws them as antialiased triangles built with NumPy when the data changes; its render step only copies the buffers. (The first Phase 2 build drew `Shape` paths: the polygons' conversion through QML cost about half of a 10 ms update, and decimation at logical pixels gave visible steps.) |
| Frame loop | `quick/backend/frames.py`, `pacing.py` | At the display rate `FrameAnimation` calls `frames.vsync()` on every vsync. Below it (60/40/30 fps on a 120 Hz display) the FrameAnimation stops – a running animation makes Qt render every vsync even when nothing changed – and each frame is scheduled from the previous frame's swap, so Qt really renders less. *Auto* keeps the highest rate without missed frames (steps down when over 5 % of the frames in a second are late, tries the faster rate again after 3 s, backing off after failed tries). Graph data is refreshed on shown frames at the graph rate. |
| Android hints | `quick/backend/android_perf.py` | `APerformanceHint` session (the NDK side of `PerformanceHintManager`, Android 13+) for the GUI and render threads, target = frame interval, each frame's work reported; thermal status for the report. No-op on older Android and on the desktop. PySide6 has no JNI bindings, hence the NDK C API through `ctypes`. The forked physics process gets no session (a forked child must not use binder). No CPU affinity is set. |
| Garbage collection | `pacing.IdleCollector` | After start-up all objects are frozen out of the collector's view and automatic collection is off; the young generation is collected in frames with slack, a full collection runs when the app is paused or goes to the background. |
| Start-up | `quick/app.py`, `qml/Main.qml` | The loading screen is the first frame (only `Settings` and `App` exist then). After it: NumPy and the core, the physics process, the backend; then the shell and the Simulate screen are built and fade in. Other screens are built when first opened. |
| Theme | `qml/Theme.qml` | Singleton with the "Instrument" tokens for light and dark; follows *Settings → Theme* (System/Light/Dark, remembered) and switches instantly. No colour is written anywhere else. |
| Fonts | `quick/fonts/` | Space Grotesk and JetBrains Mono (SIL Open Font License, licence texts included). |

Per shown frame the GUI thread makes one Python call (`sim.advance`, about 1 ms on the
Pad 6) and QML moves the cart, links and joints. The render thread never calls Python.

## Running it

```bash
python -m pendulum_sim.quick                                  # desktop preview
python -m pendulum_sim.quick --benchmark                      # performance test, report printed at the end
python -m pendulum_sim.quick --theme dark --screen graphs     # theme and first screen for this run
python -m pendulum_sim.quick --physics thread                 # physics in a thread (default: process)
python -m pendulum_sim.quick --smoke-test 3                   # visit every screen, run briefly, exit (CI)
```

On the device: *Settings → Run performance test* (about 45 s; leave the tablet alone),
then *Copy report*. The report is saved after every phase, so if the app dies during the
test, the next start offers what was measured (*Show report*).

## Building the APK

The Qt Quick app is a separate build target with its own application id
(`com.adenisov.invertedpendulumquick`, launcher name "Pendulum Quick"), so it installs next
to the Widgets app and both can be compared on the same device.

```bash
python scripts/build_android.py --ui quick --sdk-path $ANDROID_HOME --ndk-path $ANDROID_HOME/ndk/28.2.13676358
```

Everything else (host requirements, Docker image, python-for-android patches) is the same
as for the Widgets app and described in [ANDROID.md](ANDROID.md). Differences of the target:

- entry point `android/main_quick.py` instead of `android/main.py`;
- staging folder `build/android/app-quick`, containing only the shared core
  (`physics`, `control`, `simulation`, `session`), `pendulum_sim/quick` and the Qt-free
  helper modules it reuses from `pendulum_sim/mobile` – no Widgets UI, no pyqtgraph;
- `.qml`, `.ttf` and the font licence texts are packaged in addition to the usual files;
- Qt Graphs and Qt Quick 3D are left out of the libraries the Qt bootstrap loads before
  Python starts (`lazy_qt_libs`).

GitHub Actions builds it in the `Android` workflow (matrix entry `quick`) and publishes the
APK of the `qtquick-ui` branch as the pre-release `android-qtquick-ui`
(`PendulumQuick-debug.apk`). The `CI` workflow's `quick-preview` job runs the smoke test
(every screen, both themes, any QML warning fails), screenshots and the performance test
under Xvfb with software-rasterised OpenGL, and publishes them as `qtquick-ui-preview`.

## Tests

- `tests/test_session_equivalence.py`, `test_session_forms.py`, `test_session_texts.py` – the
  shared logic gives the same configurations and bit-identical trajectories as the previous
  code; the GUI tests repeat this inside the real desktop and Widgets windows.
- `tests/test_quick_sim_source.py` – both physics hosts give exactly the core's results for
  the same edit sequences; telemetry, reset, push, drag, divergence, real-time pacing,
  interpolation without per-frame allocations.
- `tests/test_quick_graphdata.py` – decimation, axes, runs of the graph data.
- `tests/test_quick_pacing.py` – frame pacing, idle collection, the Android hint wrappers.
- `tests/test_quick_threads.py` – per-thread CPU times and CPU clocks for the report.
- `tests/test_build_android_targets.py` – the two build targets are staged separately and the
  Qt Quick staging contains everything the app imports.

## Phase 1 results (Pad 6, triple pendulum)

| | Widgets app | Qt Quick spike |
|---|---|---|
| Frame rate | 12–20 fps | 120 fps |
| Frame gap p50 / p95 / max | 21–50 ms busy per frame | 8.3 / 11.6 / 16.4 ms |
| Gaps over 33 ms | many | 0 |
| Python on the GUI thread per frame | 21–50 ms | 0.9 ms mean |
| Start-up to first frame | 2.95 s | 3.1 s (QML load 2.5 s; addressed in Phase 2) |
| APK size | 172 MB | 179 MB |

Chosen in Phase 1: pendulum as plain QML items, graphs as `Shape` paths (curve renderer; replaced by `GraphLines` in Phase 2), physics in a
separate process. The Python scene-graph items, Qt Graphs and `QQuickPaintedItem` variants
were measured and removed (see the git history of `pendulum_sim/quick`).
