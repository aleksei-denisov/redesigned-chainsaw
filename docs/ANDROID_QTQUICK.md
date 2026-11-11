# Qt Quick version of the Android app

A second mobile interface, rebuilt in Qt Quick (QML) with the "Instrument" design. It is
developed next to the existing Qt Widgets mobile app (`pendulum_sim/mobile`, see
[ANDROID.md](ANDROID.md)), which stays intact and remains the full-featured app until the
Qt Quick version reaches feature parity. The simulation, controllers, LQR and autotune code
are shared and unchanged.

**Status: Phase 1 – spike.** One *Simulate* screen that proves (or disproves) the approach
on the device before anything is ported. The remaining screens come in later phases.

## Why

In the Widgets app all painting runs in Python on the UI thread, together with the physics.
In Qt Quick the scene is drawn by the GPU scene graph on its own render thread; Python only
supplies state (a few numbers per frame) and the physics run off the GUI thread.

## What the spike contains

| Part | File | Notes |
|---|---|---|
| Physics off the GUI thread | `pendulum_sim/quick/backend/sim_source.py` | The unchanged `Simulator` + `RealtimeStepper` in a worker thread (`ThreadSource`) or a forked process with shared memory (`ForkSource`). Publishes time-stamped state samples; the GUI interpolates between them, so dropped frames never slow the simulation. Qt-free, tested: results are bit-identical to the core. |
| Bridges to QML | `backend/bridge.py` | `SimBridge` (state per frame, read-outs at 15 Hz, Play/Pause, push, reset), `GraphBridge` (two traces from the telemetry ring, decimated to the pixel width, 30 Hz, nothing while hidden). |
| Benchmark | `backend/bench.py`, `backend/threads.py` | Runs every rendering variant for a few seconds and writes the report (fps, frame gaps, CPU per frame on the GUI and render thread, start-up timeline). |
| Comparison variants | `backend/scene_items.py` | Custom scene-graph items written in Python (`QQuickItem` + `QSGGeometry`, `QQuickPaintedItem`). They run Python on the render thread and exist to be measured. |
| Scene | `qml/Main.qml`, `qml/PendulumItems.qml`, `qml/Graph*.qml` | Plain QML items placed once per frame; graph as `Shape` paths, Qt Graphs, scene-graph lines or QPainter. |
| Theme | `qml/Theme.qml` | Singleton with the "Instrument" tokens for light and dark (the spike shows light). |
| Fonts | `fonts/` | Space Grotesk and JetBrains Mono (SIL Open Font License, licence texts included). |

Per displayed frame the GUI thread runs one Python call (`SimBridge.advance`, well under a
millisecond): it reads the interpolated state and emits one signal; a QML function then
positions the cart, links and joints. The render thread never calls Python in the default
variants.

## Running it

```bash
python -m pendulum_sim.quick                        # desktop preview of the spike
python -m pendulum_sim.quick --benchmark            # all variants, report printed at the end
python -m pendulum_sim.quick --physics process      # physics in a forked process (not on Windows)
python -m pendulum_sim.quick --smoke-test 3         # run briefly and exit (CI)
```

On the device: **Run benchmark** (about one minute; leave the tablet alone meanwhile), then
**Copy report**. The report is also saved after every variant, so if a variant crashes the
app, the next start shows what was measured up to that point (**Report** button).

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
  (`physics`, `control`, `simulation`), `pendulum_sim/quick` and the three Qt-free helper
  modules it reuses from `pendulum_sim/mobile` – no Widgets UI, no pyqtgraph, so only the
  Qt Quick libraries are loaded at start-up;
- `.qml`, `.ttf` and the font licence texts are packaged in addition to the usual files.
- Qt Graphs and Qt Quick 3D are packaged but left out of the list of libraries the Qt
  bootstrap loads before Python starts (`lazy_qt_libs`); they load when the Qt Graphs variant
  is selected.

GitHub Actions builds it in the `Android` workflow (matrix entry `quick`) and publishes the
APK of the `qtquick-ui` branch as the pre-release `android-qtquick-ui`
(`PendulumQuick-debug.apk`). The `CI` workflow's `quick-preview` job runs the smoke test,
screenshots and the benchmark under Xvfb with software-rasterised OpenGL.

## Tests

- `tests/test_quick_sim_source.py` – both physics sources give exactly the core's results;
  telemetry, reset, push, real-time pacing, interpolation.
- `tests/test_quick_threads.py` – per-thread CPU times used by the benchmark.
- `tests/test_build_android_targets.py` – the two build targets are staged separately and the
  Qt Quick staging contains everything the app imports.
