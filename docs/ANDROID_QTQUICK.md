# Qt Quick version of the Android app

A second mobile interface, rebuilt in Qt Quick (QML) with the "Instrument" design. It is
developed next to the existing Qt Widgets mobile app (`pendulum_sim/mobile`, see
[ANDROID.md](ANDROID.md)), which stays intact and remains the full-featured app until the
Qt Quick version reaches feature parity. The simulation, controllers, LQR and autotune code
are shared and unchanged.

> Branch `quick-core-v2`: this app uses the **new physics core** (a push at any point of a segment) and the **new auto-tune** (integral gains, gain limits, selected loops, methods) – see [QUICK_CORE_V2.md](QUICK_CORE_V2.md). The desktop and Widgets apps are unchanged.

**Status: Phase 3 – screens.** The backend for every screen exists; the QML has the
Simulate, Graphs, Controller, Model, Auto-tune, Experiments and Settings
screens. Still to come: watching a run, files (set-up, results, CSV) and scenario files.

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
| Bridges to QML | `quick/backend/*.py` | Context objects `sim` (state, read-outs, run control), `model`, `controller` (shared forms as list models), `graphs` (signal catalogue, Graphs-screen cards and graph tiles, smoothing), `panel` (side-panel layout, values, status), `autotune`, `scenarios`, `frames`, `bench`, created after the loading screen; singletons `App` and `Settings`, registered before the engine starts (types registered later broke QML type checks). No UI logic in the core, no physics in QML. |
| Graph data | `quick/backend/signals.py`, `scale.py`, `curves.py`, `linegeom.py`, `graphs.py`, `lineitem.py` | A signal catalogue (angles, rates, cart, force, loop errors, PID terms, energies) for the current structure; each graph is a `GraphSpec` (signals, window, scale, smoothing, angle unit). **Stable scales** (`StableScale`): *auto* grows at once (eased over 0.3 s) and shrinks only after the data has used less than 40 % of the range for 3 s; *symmetric* does the same around zero; *fixed* keeps the user's limits. Up to two units per graph – left and right axis. **Smoothing** (Off/Light/Medium/Strong = centred moving average over 10/30/100 ms) changes only what is drawn. **Dense signals**: each decimation column is drawn through its mean, and its range [min, max] as a translucent band wherever that is taller than the line itself covers – controller chatter or noise faster than a pixel shows as a band around a calm line (as on an oscilloscope) instead of a zigzag that aliases into hatching. **Incremental curves** (`GraphState`): decimation columns are fixed in time (two physical pixels each), so a refresh only decimates and strokes the newest samples and the scene graph scrolls the rest with a transform node; everything is redrawn only while a scale moves, on resize or style change. `GraphLines` draws the antialiased triangles built with NumPy; its render step only copies the buffers. It never calls `window()` – PySide makes a returned object a child of the caller's wrapper, so destroying a graph item invalidated (or, when collected, deleted) the window: the crash of *Move up / Move down*. CI edits the panel 180 times through the tile editor (`--panel-stress`); the smoke tests fail if a graph item could not draw. (The first Phase 2 build drew `Shape` paths: the polygons' conversion through QML cost about half of a 10 ms update, and decimation at logical pixels gave visible steps.) A view is computed while any item shows it (`GraphView.setActive(on, owner)`: a card being replaced must not switch off its successor's view); a window or smoothing change recomputes at once; a computing or drawing error is shown on the Graphs screen (`graphs.problem`). CI: `--graph-stress N` changes window / smoothing while the pendulum flails and checks every card for drawn pixels. |
| Parameter screens | `qml/FormView.qml`, `quick/backend/formmodel.py`, `session/forms.py`, `session/sliders.py` | A shared editor form as a touch list: foldable sections (a loop's live error and P/I/D terms in its header), number rows (label, value – tap to type –, − / slider / +), switches, chips, steppers. A slider moves only with a deliberate sideways drag; a touch that starts moving vertically scrolls the list, a tap changes nothing. Slider mapping (linear, log, symlog) and the − / + steps are the shared rules in `session/sliders.py`, used by the Widgets app too. Edits apply when the finger lifts, one event-loop turn later (an edit may rebuild the rows). A rebuild only inserts / removes the rows that changed, so folding or unfolding a section keeps the scroll position. Both parameter screens have **Undo / Redo / Reset** (`session/history.py`: snapshots before each edit, restored through the session's rules – `restore_controller`, `restore_model`; Reset returns to the values the scenario was loaded with and can be undone; loading a scenario clears the history). **Sub-pages** (`qml/SubPages.qml`, in both orientations): a row of tabs; a page shows some of the form's sections (`FormModel.setSections`, `"loop*"` matches by prefix) and starts at its top; the page survives a rotation. Portrait: edits, tabs, page; landscape: tabs and page, edits and actions on the side. **Controller screen**: *Loops* · *Controller* (global settings, live output, *Reset integrators*) · *LQR seeding* (weights, *Seed gains from LQR*). **Model screen**: *Model* (pendulum, cart, actuator) · *Segments* (initial state included) · *Simulation settings* (time step, telemetry, seeded sensor noise); changing the base or the number of segments rebuilds the loops by the shared rules. Also sub-pages: Auto-tune set-up (*Preset* · *Options*, *Start* below both), Experiments in portrait (*Set-up* · *Runs*, the run bar below both; a finished run opens *Runs*), Settings (*Appearance* · *Side panel* · *Scenario* · *Performance*). |
| Auto-tune screen | `qml/AutotuneScreen.qml`, `MiniChart.qml`, `quick/backend/autotune.py` | The shared robustness search (`session/jobs.TuneWorker`, all cores but one) in three steps: preset with this device's time estimate → running (progress, best result and its gains, best push per round, candidates of the round; *Cancel* keeps the best so far) → result (old → new gains, response metrics before/after, both responses to the same push; *Apply* – undoable on the Controller screen –, *Run again*, *Discard*). The simulation pauses and the dock's screen buttons are locked while it runs. The dock shows *Push* only on Simulate and Graphs. |
| Experiments screen | `qml/ExperimentsScreen.qml`, `quick/backend/experiments.py`, `session/forms.ExperimentForm` | Settings (duration, settling band, *Same start as the first run*, scheduled tip push) through the shared form, a name, *Run* / *Cancel* with progress (`session/jobs.ExperimentWorker`, full speed, deterministic). Comparison of the visible runs – angle, control error with the settling band, actuator output, cart position; segment chips – thinned to 600 points per run (min/max per bucket). A card per run: status, metrics and gains (shared texts), *Show/Hide*, *Use gains* (undoable), *Remove*; colours stay with their run. |
| Trajectory | `quick/backend/trailgeom.py`, `trail.py` | The tip's path (or every joint's), fading from old to new in the segment colour, over the last 1–10 s of simulation time (*Settings → Appearance*). Points are kept in metres and drawn by a `GraphLines` in the pendulum view. |
| Side panel | `quick/backend/panel_model.py`, `panel.py`, `qml/SidePanel.qml`, `TileEditor.qml` | The right column of the Simulate screen is a list of tiles – *value* (one live number: signal, half/full width, colour, degrees/radians), *graph* (1–4 curves, window, scale, smoothing, height) and *status* (scenario, controller type, gains). The default is the previous fixed layout (θ1, ω1, x, F and the θ1/x graph). Long-press a tile to edit: tap a tile to open its editor, drag it by its grip onto another tile to move it, × removes, + Value/Graph/Status adds; Done or Back finishes. Layouts are kept in QSettings; named layouts can be saved, loaded and deleted under *Settings → Side panel*. `panel_model.clean` repairs any stored layout, so an old or broken one never breaks the screen. Values refresh at 15 Hz with one shared-memory read each. |
| Frame loop | `quick/backend/frames.py`, `pacing.py` | At the display rate `FrameAnimation` calls `frames.vsync()` on every vsync. Below it (60/40/30 fps on a 120 Hz display) the FrameAnimation stops – a running animation makes Qt render every vsync even when nothing changed – and each frame is scheduled from the previous frame's swap, so Qt really renders less. *Auto* keeps the highest rate without missed frames (steps down when more than 10 % of the frames in a second were dropped – a gap of more than 1.8 frame intervals; shorter late frames count as jitter – tries the faster rate again after 3 s without a drop, backing off after failed tries). Graph data is refreshed on shown frames at the graph rate. |
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
python -m pendulum_sim.quick --screenshot p.png --panel graph # screenshot of the side panel's graph-tile editor
```

On the device: *Settings → Run performance test* (about 45 s; leave the tablet alone),
then *Copy report*. The report is saved after every phase, so if the app dies during the
test, the next start offers what was measured (*Show report*).

### If the app does not start

Every start writes a journal (`pendulum_sim/quick/diagnostics.py`): its steps, Python
exceptions, Qt warnings and – through `faulthandler` – native crashes. After a start that
did not finish, the next start opens in *safe mode* (physics in a thread, no performance
hints) and shows the journal with a *Copy* button. A copy is also kept where a file
manager or a computer connected over USB can read it, without adb:

`Android/media/com.adenisov.invertedpendulumquick/logs/` – `start.log` (the current start),
`previous-start.log` and `previous-fault.log` (the start before).

The journal is opened by `main.py` before anything else is imported, so even a failure
while importing the app is recorded. With adb: `adb logcat -d | grep -iE "python|qt|DEBUG"`.

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
- `tests/test_quick_linegeom.py`, `test_quick_curves.py` – decimation, stroking, smoothing, stable scales; incremental curves equal a full redraw.
- `tests/test_quick_panel_model.py` – side-panel layouts: validation and repair, rows, editing, saved layouts, status rows.
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
