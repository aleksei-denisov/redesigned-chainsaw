# Android app

The simulator also ships as an Android app, **Inverted Pendulum Simulator**
(application id `com.adenisov.invertedpendulum`). It runs the *same* physics,
controller and simulation code as the desktop version and draws the pendulum with
the *same* QPainter view; graphs, the experiment page and the input handling are
mobile-specific (QPainter, no pyqtgraph).

The app has the **same features as the desktop version**: live simulation, scenarios,
pendulum / controller / simulation settings, LQR seeding, robust auto-tuning, restoring
gains, resetting integrators, the experiment page with comparison plots and the results
table, and opening/saving configurations, experiment results and CSV exports (through
Android's file picker).

## Architecture

```
pendulum_sim/physics, control, simulation   shared, Qt-free
pendulum_sim/simulation/parallel.py          worker pools: processes (desktop), fork+pipes (Android), serial
pendulum_sim/ui                              desktop UI; MainWindow is built from overridable builders
pendulum_sim/ui/file_io.py                   file dialogs + read/write that accept Android content:// URIs
pendulum_sim/mobile/window.py                MobileWindow(MainWindow): screens + navigation, inherits every handler
pendulum_sim/mobile/pages.py                 Simulate, Graphs, Controller, System, Settings screens
pendulum_sim/mobile/autotune_screen.py       auto-tune flow: set-up → progress → result (fixed action bars)
pendulum_sim/mobile/controls.py              touch controls: number rows, switches, chips, sections, kinetic scrolling,
                                             action bar, progress bar
pendulum_sim/mobile/theme.py, icons.py       theme system (colours, sizes, style sheet); bundled SVG icons
pendulum_sim/mobile/graphs.py, plot_math.py  scrolling QPainter strip charts; Qt-free ring/downsampling/axis maths
pendulum_sim/mobile/plot_view.py             explorable plots (pinch/pan/read-out) and the full-window graph explorer
pendulum_sim/mobile/experiments_page.py      Experiments screen: runs, watch (replay), comparison, result cards, files
pendulum_sim/mobile/setup_file.py            full set-up files: configuration + "app" section (Qt-free)
pendulum_sim/mobile/touch_view.py            PendulumView subclass: tap / drag / pinch, render resolution
pendulum_sim/mobile/performance.py, cpu.py   drawing settings (saved per device), frame timings, fast-core pinning
pendulum_sim/mobile/perftest.py              on-device performance test (Settings → Diagnostics)
pendulum_sim/mobile/startup.py, loading.py   start-up timing from process start; in-app loading screen
android/main.py                              APK entry point (packaged as main.py)
android/splash_logo.png                      native splash bitmap (logo + name, xhdpi)
scripts/build_android.py                     staging + pyside6-android-deploy + python-for-android patch + buildozer
scripts/mobile_preview.py                    offscreen screenshots of every screen + benchmark (CI)
.github/workflows/android.yml                CI: debug APK on every push, signed AAB on tags
```

`MobileWindow` subclasses the desktop `MainWindow` and replaces only the *builders*
(layout, control bar, status bar, view class) and four UI ↔ configuration hooks. The
configuration dataclasses are the single source of truth: the touch editors work on
copies and hand complete new objects to the desktop handlers, so parameter edits,
LQR/robust tuning, experiments, file handling and divergence handling are the desktop
code, and a fix or feature there reaches both versions. No simulation, controller or LQR
logic lives in the mobile package.

Packaging uses Qt's official [`pyside6-android-deploy`](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-android-deploy.html),
which drives buildozer/python-for-android with the official PySide6 Android wheels.
NumPy is compiled by its python-for-android recipe; pyqtgraph (pure Python) is still
bundled but no longer imported by the app – it could be dropped to shrink the APK.
**SciPy is not bundled**: LQR gain seeding uses the NumPy Riccati solver in
`pendulum_sim/control/riccati.py` when SciPy is missing (desktop keeps using SciPy;
the two agree to ~1e-9, see `tests/test_riccati.py`).

Real-time pacing (`pendulum_sim/simulation/realtime.py`, also used by the desktop window)
keeps the physics at its fixed `dt`, independent of the display, clamps stalls to 0.1 s and
limits physics work to 10 ms per frame. When the app goes to the background the timer and
physics stop; on return the simulation continues in the state it had (no catch-up burst).

| | |
|---|---|
| Minimum Android | 9.0 (API 28) – the minimum of Qt 6.8+ |
| Target API | 36 (Google Play requirement since 31 Aug 2026) |
| ABI | arm64-v8a (phones, tablets and Apple-Silicon emulators) |
| PySide6 | 6.12.0 (pinned in `android/requirements-build.txt`) |

## Using the app

### Screens and navigation

| Screen | Contents |
|---|---|
| **Simulate** | large pendulum view; Start/Pause, Reset, Restart, Step, push ← / force / →, speed, scenario, fit |
| **Graphs** | up to four graphs (chips: angle, angular velocity, cart position/velocity, control force, control error, PID components, energy) next to (landscape) or below (portrait) the pendulum, with Start/Pause, Reset and pushes; time window 5–30 s; **tap a graph** to open it full-window (explorer, below) |
| **Controller** | auto-tune, seed from LQR, restore gains, reset integrators; LQR weights; controller settings; one section per PID loop (gains, P/I/D terms, derivative source, anti-windup, limits, live output) |
| **System** | cart or fixed base, number of segments, gravity, cart and actuator, one section per segment, time step, telemetry, sensor noise |
| **Auto-tune** | see below |
| **Experiments** | runs, optional watching (replay), comparison plots, result cards, files – see below |
| **Settings** | view options, trajectories, performance (this device), open / save configuration |

Tablets in landscape show a navigation **rail** on the left with all screens; phones and
portrait show a **bottom bar** (Simulate, Graphs, Controller, System, *More* → Auto-tune,
Experiments, Settings). The simulation keeps running on every screen; only what is visible
is drawn. **Android back** returns to the previously visited screen, cancels a running
auto-tune, and on *Simulate* asks before leaving the app. Back is handled just after the key
event (Android's UI thread waits while the key is delivered; a dialog opened inside it froze the
app). Questions and messages (*Leave the simulator?*, errors, the performance report, the push
force) are in-window dialogs (`dialogs.py`) – no `QMessageBox`/`QDialog` windows.

`scripts/build_android.py` patches python-for-android's Qt activity (`PythonActivity.java`),
checked against a copy of the real file in `tests/test_build_android_splash.py`:

- `onKeyDown` swallowed every first key press with a *"Click again to close the app"* toast –
  keys now go straight to Qt;
- `onActivityResult` / `onNewIntent` did not call `super`, so Qt never received the storage
  picker's answer: after *Save… / Open…* a modal file dialog waited forever and no button
  reacted – they now pass the result on to Qt.

Loading a scenario or a file, or adding/removing segments, shows a *Loading …* cover while
the screens are rebuilt. It is put on screen for ~0.1 s before the work starts, so the
scenario menu (a window of its own on Android) is gone first – closing it while the work
blocked the app left the screen black or half-drawn.

Numbers are edited with a slider (logarithmic for gains and wide ranges), large − / +
steppers, or by tapping the value and typing it; a change is applied when the finger is
lifted. Typed values accept "." and "," as decimal separator whatever the device language
(a locale-bound validator rejected "30.408" on a Russian device and the field never closed);
typing ends with *Done*/Enter or a touch anywhere else, text that is not a number keeps the
old value.

**Scrolling** starts anywhere on a page, also on sliders, buttons and texts (`DragScroller`
in `controls.py`, replacing `QScroller`, which only scrolled where the finger landed on empty
background). The press on a widget is held back for the first few pixels: a mostly vertical
movement scrolls the page (with fling) and the widget never sees the touch, a sideways
movement on a slider hands the touch to the slider, a tap or a hold (> 0.25 s) goes to the
widget as usual. Views that handle their own gestures (pendulum, explorable plots) are left alone. On/off settings are switches, choices are chips, long screens scroll with the
finger (no horizontal scrolling). All icons are bundled SVGs, so nothing depends on the
device's fonts.

| Gesture | Effect |
|---|---|
| tap on the pendulum view | tip push (0.1 s, *push force*) towards the tapped side of the tip |
| one-finger drag on the view | continuous tip force pulling towards the finger (rubber band, capped at 4× push force) |
| pinch / two-finger drag | zoom / pan the view |

### Experiments

The Experiments screen (`pendulum_sim/mobile/experiments_page.py`) has the functions of the
desktop experiment page in the style of the other screens; the computation is the shared
`run_experiment`:

* **Experiment** – name, duration, settling band, *Same start as the first run* (plant, initial
  state and noise of run 1, so only the controller differs), *Watch the pendulum*;
  **Scheduled disturbance** – a tip push at a given time. *Run* / *Cancel* and the progress
  bar stay in a bar at the bottom. Runs are computed in a plain Python thread at full speed
  (the UI polls it; nothing Qt crosses the thread).
* **Watch** (optional) – a finished run is played back in a pendulum view at real speed with
  pause, a time slider and 0.25–4×; a time marker moves through the comparison plots. The
  playback rebuilds the pendulum from the recorded states (`theta*`, `x`, …), so watching
  never slows or changes the computation. Every result card has *Watch*, also for loaded
  results. One finger pans the view, pinch zooms, double tap fits.
* **Comparison** – angle, control error (with the ±settling band), actuator output and cart
  position of every shown run, per segment; tap a plot to open it in the graph explorer.
* **Results** – a card per run: colour, status (stable / fell / diverged), settling time, max
  deviation, overshoot, RMSE, u RMS, u peak, ∫u² dt, max |x|, the gains, and *Shown/Hidden*,
  *Watch*, *Use gains*, *CSV*, *Remove*.
* **Files** – kept apart: *Save full set-up (JSON)* (configuration plus the app's graph and view
  settings, see below), *Save / Load results (JSON)* (configuration, metrics and time series of
  every run – the desktop format), *Save comparison graphs as image (PNG)* (the visible plots
  stacked), *Save comparison graph data (CSV)* (every curve; one time column per run).

**Full set-up files.** *Settings → Save / Save as…* and *Experiments → Save full set-up* write
the configuration (plant, controller, simulation, experiment settings, push force – the
desktop format) plus an `"app"` section with the pendulum-view options and the graph
selection, time window and PID loop (`setup_file.py`). *Settings → Open* restores both; the
desktop version opens the same file and ignores the `"app"` section. Recorded data never go
into a set-up file.

### Auto-tune

*Controller → Auto-tune for robustness* (or *More → Auto-tune*) opens the auto-tune screen:

1. **Set-up** – *Quick*, *Balanced* or *Thorough*, each with an estimated duration measured on
   this device (one calibration simulation when the screen opens) and the number of CPU cores used.
2. **Progress** – percentage bar, phase in plain words, elapsed and remaining time, cores in use,
   number of simulations, the best controller so far (recoverable push and gains) and a live plot
   of every candidate's recoverable push. The rest of the app is locked and the simulation paused
   while the search runs. **Cancel** stops the workers at once and offers the best result found so far.
3. **Result** – recoverable push before → after, old and new gains, settling time, overshoot,
   peak force and control effort for the same push, a preview of both responses, and
   **Apply**, **Discard** or **Rerun**.

The search runs in a background thread that drives a pool of worker processes, so the screen
keeps animating and Android never reports the app as not responding. Android has no
`sem_open` and no Python executable for `multiprocessing`'s spawn, so the pool there forks
the app process (`ForkPool`: `os.fork` + pipes, workers never touch Qt); the desktop uses a
`ProcessPoolExecutor` with the same tasks, and both fall back to one process with a note if
workers cannot start. Results are combined in candidate order, so the outcome does not depend
on the number of workers (`tests/test_tuning_parallel.py`). Evaluations are cheaper than before:
a simulation stops early once the pendulum is at rest, and candidates that cannot beat the
current best are pruned. Auto-tune is a foreground operation: **leaving the app cancels it** –
keep the screen on.

### Performance settings

*Settings → Performance* – saved on the device. The drawing method is not a performance
choice any more: the app draws with the GPU; software drawing is kept as a fallback under
*Diagnostics*. Settings saved by older builds (when software was the default) are switched to
GPU once.

| Setting | Effect |
|---|---|
| Preset | one tap sets smooth edges, frame rate and graph scrolling: **Smooth** (default: smooth edges, 60 fps, graphs 60×/s), **Balanced** (no edge smoothing, graphs 30×/s), **Battery saver** (30 fps, graphs 15×/s). Any other combination shows as *Custom* |
| Smooth edges | antialiasing: 4× multisampling on the GPU, antialiased painting in software (default on) |
| Frame rate | 60 or 30 fps |
| Graph scrolling | how often the graphs take in new samples: 60 (default), 30 or 15 times per second, never more than the frame rate; each step draws only the new samples |
| Use performance CPU cores | pins the simulation thread to the big cores (Snapdragon 870: 3.2 + 3 × 2.42 GHz instead of the 1.8 GHz efficiency cores) |
| Drawing method (*Diagnostics*, advanced) | **GPU (OpenGL)** (default): the pendulum view and the graphs are drawn by the GPU (`QOpenGLWidget`, same QPainter code through Qt's OpenGL engine). *Software* is another way of drawing, much slower on most devices: the CPU paints every pixel on the UI thread (≈4 M pixels per frame on a 2560×1600 screen, antialiasing costs extra per pixel) and, because Android composes apps with the GPU anyway, the whole window image (≈16 MB) is copied to the GPU every frame – ≈10 ms of a 16 ms frame on the Pad 6. It is kept because it needs no OpenGL driver and is used automatically if no OpenGL context can be created |
| Software drawing resolution (*Diagnostics*, software only) | *Native* (default) draws the pendulum at the screen's full pixel density; *1.5×*, *1.25×* and *1×* draw fewer pixels and scale up. The GPU always draws at the screen's own resolution |
| Performance overlay (*Diagnostics*) | at the top right of the view: `fps · max <longest frame gap> │ physics · view · graphs <data>+<paint> · screen <whole update>` in ms per frame, and the CPU core in use |
| Log timings (*Diagnostics*) | the same line once per second in `adb logcat` (tag `python`) |
| Run performance test (*Diagnostics*) | about 45 s: with GPU drawing Simulate, Graphs with 2, 3 and 4 graphs and a Python profile, then Simulate and Graphs with software drawing for comparison; shows a report with *Copy report* (also printed to `adb logcat`) |
| Start-up times (*Diagnostics*) | the start-up steps of this launch, in seconds since the process started |

### Graph rendering

Each graph is a `StripChart` (`pendulum_sim/mobile/graphs.py`) drawn with QPainter on Qt's
software rasteriser – the same path as the rest of the widgets, so nothing extra is needed
on Android:

* **Static layer** – title, legend, y labels, frame and time ticks are drawn once into an
  image and rebuilt only when the y range, the size or the time window changes.
* **Scrolling trace** – the curves live in an image of the plot area in device pixels used as
  a *ring of pixel columns*: absolute column `c = t · px_per_s` is stored at image column
  `(c − ring0) mod width`. On every graph step the columns that time has moved past are
  cleared (with the horizontal grid), only the samples that arrived since the last step are
  drawn as short line segments (2–4 per frame at the default 200 Hz telemetry), and the
  paint copies the ring in two parts so that the newest column sits at the right edge. The
  curves scroll by whole device pixels every frame and the cost does not depend on how much
  history is shown (`ring_runs` / `ring_blits` in `plot_math.py`).
* **Full redraw** – only when the y range changes (checked at 10 Hz, or at once when a new
  sample leaves the range; ranges keep a minimum span, so no `×1e-15`), on resize or when the
  time window changes. It decimates each series to a min/max pair per pixel column
  (`downsample_minmax`) and writes the points straight into a `QPolygonF`'s memory.
* **Pens** – 1-pixel cosmetic lines without antialiasing (Qt's fast line rasteriser), drawn
  twice one device pixel apart on dense screens, i.e. ≈ 2 px thick.
* **Scheduling** – the window steps the graphs from its frame timer, at the *Graph animation*
  rate, after the physics; a frame that overran skips the graph step (the next one catches up
  because the charts are time-based), so the pendulum and the physics keep their pace. Hidden
  graphs (another screen shown, or not selected) do no work at all; only the trace areas are
  repainted.

Options considered (see the sources in the commit history): pyqtgraph tuned for speed
(`setDownsampling(auto=True, method='peak')`, `setClipToView`, `skipFiniteCheck`, no
antialiasing, cosmetic pens) still runs its Python scene graph on every refresh – about
200 ms per refresh on the tablet in the first Android build; Qt Charts with OpenGL needs an
extra Qt module, accelerates only line/scatter series and in Qt 6 needs the OpenGL backend
forced; a Qt Quick scene-graph line item would add a second rendering stack to the widget
app (an architecture change). The scrolling trace keeps the existing stack and makes the
per-frame cost a small, constant amount.

If the animation still stutters, switch on the performance overlay or run the performance
test: a large *graphs* value → fewer graphs or a lower graph animation rate; a large *view*
value → lower view resolution or no antialiasing; a large *physics* value with fast-core
pinning off → turn it on; a large *screen* value with small parts → the upload of the frame
to the display (Qt draws widgets in software and uploads the changed area each frame); a
large *max* with small parts → the system (a background app or thermal throttling) delayed
the frame.

### Graph explorer

Tapping a graph on *Graphs* opens it over the whole window (`pendulum_sim/mobile/plot_view.py`,
`GraphExplorer` around a `PlotView`). It shows everything still in the live recording (the
last 30–60 s). With **Live: on** it follows the running simulation: the right edge stays at the
newest sample, and pinching changes the time span and the value range, which are kept while it
follows. **Live: off** (only the button switches) freezes the data for free panning and zooming:

| Gesture | Effect |
|---|---|
| pinch | zoom – sideways zooms time, up/down zooms values, diagonal both; the point between the fingers stays put |
| one-finger drag | pan |
| tap | read-out: cursor line with the value of every curve at that time |
| double tap | default view (live: last *time window* seconds; frozen: all data) |
| *Fit* | the whole graph – while live: everything recorded, still following |
| *Now* | live: back to the present moment; not live: to the end of the recording, same span |
| *Live: on / off* | follow the simulation / freeze the data (green = on, red = off) |
| *Scroll: on / off* (option, while live) | a sideways drag looks back in time: the view keeps moving with the run, that far behind the newest sample; *Now* returns to the present |
| *Image* / *Data* | save the graph as PNG / its curves as CSV (time + one column per curve) |
| *Close* / Android back | back to *Graphs* |

`PlotView` is QPainter only: each curve is reduced to two points per device-pixel column of
the visible range (`downsample_minmax`), so a pan or pinch redraws in a few milliseconds
whatever the length of the recording. While the explorer is open the graphs and the
pendulum view behind it are not drawn (the simulation keeps running). The zoom/pan
arithmetic, nearest-sample read-out and CSV writer are Qt-free (`plot_math.py`) and unit-tested.

### Measurements

Offscreen on a GitHub runner at the Xiaomi Pad 6 geometry (1440×900 logical, pixel ratio 2),
triple pendulum, 200 Hz telemetry, 10 s window (`scripts/mobile_preview.py`, which also runs a
shortened performance test). A tablet is slower in absolute terms; on the device use
*Settings → Diagnostics → Run performance test* (the report is the same format).

| | before (mobile-ux) | now (mobile-polish) |
|---|---|---|
| graph updates | all graphs recomputed and repainted every 100 ms in one frame (curves move in 100 ms steps) | every frame, only new samples; smooth 1-pixel scroll |
| graph cost, 2 / 3 / 4 graphs | ≈ 2.4 ms in one frame out of six (CI) – on the tablet the earlier pyqtgraph build spent ≈ 200 ms per refresh | 0.9 / 0.9 / 1.1 ms per frame (data 0.5–0.7 + paint 0.4–0.5), every frame |
| frame busy time, Graphs with 3 graphs (60 fps) | 5.5 ms mean, 8.8 ms max (different runner) | 5.0 ms mean, 5.6 ms p95 |
| full graph redraw (range/size change only) | – | 3.7 / 5.3 ms for 2 / 4 graphs |
| start-up (fresh process) | blank until everything, incl. pyqtgraph, Experiments and Auto-tune, was built | loading screen after 0.1–0.2 s, main window after 0.6–1.0 s (runner speeds vary); Experiments 0.06 s and Auto-tune 0.03 s on first use; icon rendering cut from 28 to ≤ 6 SVG renders per icon |

### Start-up and loading screen

1. **Native splash** (Android, before Python runs): the app logo and name centred on the app's
   background colour. `scripts/build_android.py` checks out python-for-android at the commit
   `pyside6-android-deploy` pins and patches its Qt bootstrap: the activity gets the
   `android.app.splash_screen_drawable` meta-data (Qt's activity shows that drawable until Qt
   draws its first frame) and its style gets the same drawable as window background (also
   what shows behind Android 12+'s own short icon splash). The bitmap is
   `android/splash_logo.png`; the drawable and colour are written into the bootstrap's
   resources. The patch is checked against copies of the real templates
   (`tests/test_build_android_splash.py`, rendered with Jinja) and, if the templates change,
   the build continues without the splash instead of failing.
2. **In-app loading screen** (`pendulum_sim/mobile/loading.py`), shown as soon as the
   QApplication exists, with the *same* logo and title at the same place, a progress bar and
   the current step: *Loading NumPy…*, *Loading physics engine…*, *Preparing user interface…*,
   *Building screens…*, *Starting simulation…*. The loading screen is a page of `AppShell`, the
   app's **only top-level window**: when the main window is ready it is embedded as the next
   page and shown in one step. (On Android every top-level Qt window is a surface of its own;
   a separate loading window closing while the main window opened was composed in changing
   order and flashed up again over the app, and a fade over the GPU-drawn view flickered.)
   For the same reason no widget is made visible before it has a parent: a parentless widget
   that is shown becomes a window for a moment (the navigation rail's icons flashed over the
   loading screen). `tests/test_mobile_smoke.py` records every widget shown as a window while
   the screens are built and on every screen.
3. **Lazy screens**: the Experiments screen and the Auto-tune screen are
   built the first time they are opened (the app no longer imports pyqtgraph at all); the desktop window imports its pyqtgraph panels inside
   its builders (desktop behaviour unchanged); icons are rendered at two sizes instead of seven.

Every step is timed from the start of the *process* (`/proc/self/stat`), printed to
`adb logcat` (`startup  1.234 s  …`) and listed under *Settings → Diagnostics*;
`python -m pendulum_sim.mobile --startup-test` prints the same report on the desktop.

### Theme system

`pendulum_sim/mobile/theme.py` is the single place for the look of the mobile app:

* colour tokens (`BG`, `SURFACE`, `SURFACE_2`, `OUTLINE`, `TEXT`, `TEXT_2`, `DISABLED`,
  `ACCENT`, `ACCENT_DARK`, `ON_ACCENT`, `DANGER`) chosen for contrast on an LCD in daylight
  (body text 14.8 : 1, secondary text 8 : 1, accent 6.9 : 1 on the background);
* sizes in logical pixels (= Android dp): body text 16, secondary 14, titles 20, touch targets 48;
* `palette()` (Qt palette for every widget, including disabled states) and one `STYLE_SHEET`
  with a few roles selected by widget properties – `primary` and `chip` buttons, `nav` and
  `flat` tool buttons, `role="secondary|title|value"` labels, `card` frames, `navbar` /
  `actionbar` bars;
* `apply(app)` sets palette, pixel-sized font and style sheet once at start-up. There are no
  Python style hooks (a Python `QProxyStyle` was removed: it ran Python on every paint).

Custom-painted widgets (pendulum view, strip charts, auto-tune plots, progress bar, loading
screen) take their colours from the palette or the same tokens, and the native splash uses
`BG` (`SPLASH_BG` in the build script), so the hand-over from splash to loading screen to
the app shows no colour jump.

The mobile interface can be previewed on the desktop (mouse = one finger, wheel = zoom):

```bash
python -m pendulum_sim.mobile                       # phone-sized window
python -m pendulum_sim.mobile --tablet --landscape  # 1280×800 tablet
```

CI renders every screen at a 2× pixel ratio for phone and tablet sizes and times the drawing
(`scripts/mobile_preview.py`); the images and `benchmark.txt` are published as the
[mobile-preview](https://github.com/aleksei-denisov/redesigned-chainsaw/releases/tag/mobile-preview)
pre-release (`mobile-ux-preview` / `mobile-polish-preview` for those branches).

## Building

### Download the latest APK

Pushes to `main` and `android-apk` publish the debug APK as the rolling pre-release
**android-latest** – open this link directly on the phone/tablet:

<https://github.com/aleksei-denisov/redesigned-chainsaw/releases/download/android-latest/InvertedPendulumSimulator-debug.apk>

The `mobile-ux` branch (multi-screen touch interface) publishes to **android-mobile-ux**,
`mobile-polish` (loading screen, scrolling graphs) to **android-mobile-polish**:

<https://github.com/aleksei-denisov/redesigned-chainsaw/releases/download/android-mobile-ux/InvertedPendulumSimulator-debug.apk>
<https://github.com/aleksei-denisov/redesigned-chainsaw/releases/download/android-mobile-polish/InvertedPendulumSimulator-debug.apk>

Tap the downloaded file and allow installing from this source (browser) when asked.

### GitHub Actions (recommended)

Every push runs `.github/workflows/android.yml` and uploads
**`inverted-pendulum-debug-apk`** (Actions → run → Artifacts). The first build takes
~30–60 min (python-for-android compiles CPython and NumPy); later builds reuse the cache.

### Locally on an Apple-Silicon Mac (native)

`pyside6-android-deploy` supports macOS hosts.

```bash
brew install openjdk@17 autoconf automake libtool pkg-config cmake ninja
export JAVA_HOME=$(/usr/libexec/java_home -v 17)
# Android SDK: install Android Studio or the command-line tools, then
sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0" "ndk;28.2.13676358"
export ANDROID_HOME=~/Library/Android/sdk

python3.12 -m venv ~/venvs/pendulum-android     # keep this venv OUTSIDE the repository
~/venvs/pendulum-android/bin/pip install -r android/requirements-build.txt
~/venvs/pendulum-android/bin/python scripts/build_android.py \
    --sdk-path "$ANDROID_HOME" --ndk-path "$ANDROID_HOME/ndk/28.2.13676358"
# → dist/invertedpendulum-<version>-debug.apk
```

### Locally with Docker (any host)

```bash
docker build --platform linux/amd64 -t pendulum-android -f android/Dockerfile .
docker run --rm --platform linux/amd64 -v "$PWD":/src \
    -v pendulum-android-cache:/root/.buildozer pendulum-android
```

The NDK's Linux toolchain is x86_64-only; on Apple Silicon enable *Use Rosetta for
x86_64/amd64 emulation* in Docker Desktop, otherwise the build is very slow.

### Signed release (AAB for Google Play)

1. Create an upload key once (keep it safe – Play needs the same key for every update):

   ```bash
   keytool -genkeypair -v -keystore upload.keystore -alias upload \
       -keyalg RSA -keysize 4096 -validity 10000
   ```

2. Local build:

   ```bash
   export P4A_RELEASE_KEYSTORE=$PWD/upload.keystore P4A_RELEASE_KEYALIAS=upload
   export P4A_RELEASE_KEYSTORE_PASSWD=… P4A_RELEASE_KEYALIAS_PASSWD=…
   python scripts/build_android.py --release --sdk-path … --ndk-path …   # → dist/*.aab
   ```

3. CI: add repository secrets `ANDROID_KEYSTORE_BASE64` (`base64 -i upload.keystore`),
   `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`, then push a
   tag `v*` or run the *Android* workflow manually with *release* ticked. The job uploads
   `inverted-pendulum-release-aab`.

Bump `version` in `pyproject.toml` for every Play upload. An AAB cannot be installed with
`adb install`; to test a release build locally use `bundletool build-apks --connected-device`.

## Installing and testing

### Real device

1. On the phone: *Settings → About phone →* tap *Build number* 7× → *Developer options →
   USB debugging* on.
2. Unzip the CI artifact, connect the phone, accept the debugging prompt, then:

   ```bash
   brew install android-platform-tools          # provides adb
   adb devices                                   # the phone must be listed as "device"
   adb install -r invertedpendulum-*-debug.apk   # -r: replace an earlier build
   ```

   (Alternatively copy the APK to the phone and open it; allow installing unknown apps.)
3. Watch the log while starting the app:

   ```bash
   adb logcat -s python:* Qt:* AndroidRuntime:*
   ```

   If Python fails at start-up the app shows the traceback on screen instead of closing.

### Emulator (Apple Silicon)

Android Studio → *Device Manager* → create a device with an **arm64-v8a** system image,
API 28 or newer (e.g. *Pixel 7, API 35, Google APIs ARM 64*). Start it, then
`adb install -r dist/invertedpendulum-*-debug.apk`. On an Intel/x86_64 host build with
`--arch x86_64` instead.

### Test checklist

- launch: the splash (logo and name) appears at once, then the loading screen with its steps, then
  the app – no black or blank screen, no loading screen flashing up again; *Settings →
  Diagnostics* lists the start-up times;
- app starts, pendulum balances in the default scenario; *Settings → Diagnostics → Performance overlay* shows ~60 fps;
- tap left/right of the tip → push; drag → red rubber band and the pendulum follows; pinch/pan, *Fit*;
- every screen opens from the rail / bottom bar (*More*); the simulation keeps running; back button
  walks back through the screens with **one** press each (no "Click again to close the app" toast)
  and asks before leaving on *Simulate*;
- *Scenario* → another scenario: a steady *Loading …* cover, no black or flickering frames;
- *Settings → Performance*: the presets switch the rows below them, any manual change shows
  *Custom*; *Diagnostics → Drawing method* switches to Software (slower) and shows *Software
  drawing resolution* only then;
- scroll Controller and Settings by dragging on sliders, texts and buttons; sideways on a slider
  still moves it; taps on buttons and values still work;
- *System → Noise seed*: slider (0–9999) and −/+ in steps of 1;
- tap a number, type it with "." or "," and tap anywhere else (slider, −/+, another row) or press
  *Done*: the value is taken, the field closes and the slider works again;
- *Graphs*: tap a graph → full window, it follows the run; pinch sideways/up-down, drag, tap for
  values, double-tap to fit, *Live* to follow again, *Image* / *Data* save PNG / CSV, back closes;
- *Graphs*: select up to four, the fifth replaces the oldest; the choice survives a restart; the
  curves scroll smoothly (no 100 ms steps) and with 2–3 graphs the overlay stays at ~60 fps;
  *Settings → Diagnostics → Run performance test* → *Copy report*;
- *Controller*: drag a gain slider, tap a value and type one, −/+; the pendulum reacts on release;
  *Seed from LQR* on the double/triple pendulum balances it;
- *System*: add a segment → a new loop appears on *Controller*; switch cart ↔ fixed base;
- *Auto-tune*: estimates appear for the three presets; run *Quick* → progress, cancel → best-so-far
  offered; run again → result with Apply / Discard / Rerun in a bar that stays at the bottom, in
  both orientations; Apply returns to *Controller*; pressing *Home* during a run cancels it;
- every screen scrolls with the finger where its content is longer than the screen (also
  *Experiments*), on tablet and phone, portrait and landscape;
- *Experiments*: run twice with different gains → two result cards, curves in the four plots; *Hidden*
  removes a run's curves; *Watch* plays it back (pause, slider, speeds) with a marker in the plots;
  tap a plot → full window; *Use gains* → Controller; *Files*: full set-up, results, PNG, CSV each
  saved separately; *Settings → Open* of the set-up restores the graph selection;
- rotate the device → layout switches (rail ↔ bottom bar, graphs beside ↔ below the view);
- *Home* while running → come back → simulation resumes where it was, no jump.

## Known limitations

- GPU drawing covers the pendulum view and the graphs; the other screens are drawn by Qt's software
  rasteriser (as on the desktop); see *Performance settings*.
- Auto-tune needs the app in the foreground; leaving the app cancels it.
- The fork-based worker pool and the fast-core pinning are verified on Linux CI; on devices whose
  kernel forbids `sched_setaffinity` the pinning is silently skipped, and if forking fails the
  search runs in one process (the progress screen says so).
- Keyboard shortcuts of the desktop version are replaced by touch controls.
- arm64-v8a only (no 32-bit devices; Qt dropped them).
- Android 9+ only (Qt 6.8+ requirement; Android 8.x cannot run current Qt).
- APK size is large (~180 MB) because the PySide6 wheel is bundled whole.
- First launch takes a few seconds longer while Python unpacks (the splash covers it).
- The native splash depends on patching python-for-android's Qt bootstrap templates; if a future
  python-for-android changes them, the build logs a warning and the app starts without the
  native splash (the in-app loading screen still appears).
- Graphs scroll by whole device pixels (at 10 s on a 1400-pixel-wide chart ≈ 140 px/s); the
  curves between two graph steps are straight segments, the full redraws decimate to min/max
  per column – both look the same at this resolution.
