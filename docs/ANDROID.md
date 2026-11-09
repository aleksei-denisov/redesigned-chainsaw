# Android app

The simulator also ships as an Android app, **Inverted Pendulum Simulator**
(application id `com.adenisov.invertedpendulum`). It runs the *same* physics,
controller and simulation code as the desktop version and draws the pendulum with
the *same* QPainter view and pyqtgraph telemetry; only the window layout and the
input handling are mobile-specific.

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
pendulum_sim/mobile/autotune_screen.py       auto-tune flow: set-up → progress → result
pendulum_sim/mobile/controls.py              touch controls: number rows, switches, chips, sections, kinetic pages
pendulum_sim/mobile/theme.py, icons.py       dark theme; bundled SVG icons (icons/*.svg, rendered with QtSvg)
pendulum_sim/mobile/graphs.py, plot_math.py  fast QPainter strip charts; Qt-free downsampling and axis maths
pendulum_sim/mobile/touch_view.py            PendulumView subclass: tap / drag / pinch, render resolution
pendulum_sim/mobile/performance.py, cpu.py   drawing settings (saved per device), frame timings, fast-core pinning
android/main.py                              APK entry point (packaged as main.py)
scripts/build_android.py                     staging + pyside6-android-deploy + buildozer
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
NumPy is compiled by its python-for-android recipe; pyqtgraph is pure Python.
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
| **Graphs** | up to four graphs (chips: angle, angular velocity, cart position/velocity, control force, control error, PID components, energy) next to (landscape) or below (portrait) the pendulum, with Start/Pause, Reset and pushes; time window 5–30 s |
| **Controller** | auto-tune, seed from LQR, restore gains, reset integrators; LQR weights; controller settings; one section per PID loop (gains, P/I/D terms, derivative source, anti-windup, limits, live output) |
| **System** | cart or fixed base, number of segments, gravity, cart and actuator, one section per segment, time step, telemetry, sensor noise |
| **Auto-tune** | see below |
| **Experiments** | the desktop experiment page (runs, comparison plots, results table, CSV) |
| **Settings** | view options, trajectories, performance (this device), open / save configuration |

Tablets in landscape show a navigation **rail** on the left with all screens; phones and
portrait show a **bottom bar** (Simulate, Graphs, Controller, System, *More* → Auto-tune,
Experiments, Settings). The simulation keeps running on every screen; only what is visible
is drawn. **Android back** returns to the previously visited screen, cancels a running
auto-tune, and on *Simulate* asks before leaving the app.

Numbers are edited with a slider (logarithmic for gains and wide ranges), large − / +
steppers, or by tapping the value and typing it; a change is applied when the finger is
lifted. On/off settings are switches, choices are chips, long screens scroll with the
finger (no horizontal scrolling). All icons are bundled SVGs, so nothing depends on the
device's fonts.

| Gesture | Effect |
|---|---|
| tap on the pendulum view | tip push (0.1 s, *push force*) towards the tapped side of the tip |
| one-finger drag on the view | continuous tip force pulling towards the finger (rubber band, capped at 4× push force) |
| pinch / two-finger drag | zoom / pan the view |

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

*Settings → Performance (this device)* – saved on the device:

| Setting | Effect |
|---|---|
| Pendulum drawing resolution | *Native* (default) draws the pendulum at the screen's full pixel density; *High* (1.5×), *Balanced* (1.25×) and *Fast* (1×) draw fewer pixels and scale up |
| Graph refresh (maximum) | upper limit (default 10 Hz); lowered automatically when frames get slow |
| Frame rate | 60 or 30 fps |
| Antialiased pendulum view | smooth edges (default on) |
| Use performance CPU cores | pins the simulation thread to the big cores (Snapdragon 870: 3.2 + 3 × 2.42 GHz instead of the 1.8 GHz efficiency cores) |
| Performance overlay (*Debug*) | at the top right of the view: `fps · max <longest frame gap> │ physics · view · graphs` in ms per frame, and the CPU core in use |
| Log timings (*Debug*) | the same line once per second in `adb logcat` (tag `python`) |

Graphs are QPainter strip charts: each refresh downsamples the visible window to two points
(min/max) per pixel column straight from the telemetry ring buffer into a reused `QPolygonF`,
axes and labels are cached in an image and rebuilt only when the range or size changes, and
hidden graphs (another screen shown) are not updated at all. Axes keep a minimum span per
quantity, so a signal at rest never shows `×1e-15` noise.

On the Xiaomi Pad 6 the phase-2 build showed 11 fps with graphs (35 ms per frame in the graphs,
217 ms worst frame) and 30 fps without. Graphs already refreshed at 10 Hz then, so one pyqtgraph refresh took
about 200 ms on the tablet (matching the 217 ms worst frame). On the CI runner pyqtgraph and the
strip charts cost about the same per refresh; on the device the strip charts avoid pyqtgraph's
per-item scene graph and antialiased path stroking (1-pixel cosmetic lines, the Qt rasteriser's
fast path), hidden graphs are not touched, the pendulum grid is cached and the simulation thread
runs on the fast cores. Check the *Performance overlay*
on the device for the real numbers.

Measured offscreen at the Xiaomi Pad 6 geometry (1440×900 logical, pixel ratio 2) on a CI runner
(`scripts/mobile_preview.py`; a tablet is slower, the ratios hold):

| Per frame | phase 2 | now |
|---|---|---|
| pendulum view, native + antialiasing | 25.5 ms | **3.8 ms** – background grid cached as an image |
| graphs, one refresh incl. window flush, 2 / 4 graphs | 2.3 / 2.9 ms (pyqtgraph) | 2.5 / 3.1 ms, of which the charts' own work (data + paint) **1.1 / 1.7 ms** |
| graphs while another screen is shown | refreshed | not touched |
| whole frame at 60 fps, Simulate / Graphs with 3 graphs | – | 7.9 / 6.4 ms mean, ≤ 10.7 ms max |
| auto-tune *Quick*, single cart, 1 → 2 workers | 22.1 s | 11.7 s → **6.2 s** |

If the animation still stutters, switch on the performance overlay: a large *graphs* value → fewer
graphs or a lower graph refresh; a large *view* value → lower view resolution or no antialiasing;
a large *physics* value with fast-core pinning off → turn it on; a large *max* with small parts →
the system (a background app or thermal throttling) delayed the frame.

The mobile interface can be previewed on the desktop (mouse = one finger, wheel = zoom):

```bash
python -m pendulum_sim.mobile                       # phone-sized window
python -m pendulum_sim.mobile --tablet --landscape  # 1280×800 tablet
```

CI renders every screen at a 2× pixel ratio for phone and tablet sizes and times the drawing
(`scripts/mobile_preview.py`); the images and `benchmark.txt` are published as the
[mobile-preview](https://github.com/aleksei-denisov/redesigned-chainsaw/releases/tag/mobile-preview)
pre-release (`mobile-ux-preview` for the `mobile-ux` branch).

## Building

### Download the latest APK

Pushes to `main` and `android-apk` publish the debug APK as the rolling pre-release
**android-latest** – open this link directly on the phone/tablet:

<https://github.com/aleksei-denisov/redesigned-chainsaw/releases/download/android-latest/InvertedPendulumSimulator-debug.apk>

The `mobile-ux` branch (multi-screen touch interface) publishes to **android-mobile-ux**:

<https://github.com/aleksei-denisov/redesigned-chainsaw/releases/download/android-mobile-ux/InvertedPendulumSimulator-debug.apk>

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

- app starts, pendulum balances in the default scenario; *Settings → Debug → Performance overlay* shows ~60 fps;
- tap left/right of the tip → push; drag → red rubber band and the pendulum follows; pinch/pan, *Fit*;
- every screen opens from the rail / bottom bar (*More*); the simulation keeps running; back button
  walks back through the screens and asks before leaving on *Simulate*;
- *Graphs*: select up to four, the fifth replaces the oldest; the choice survives a restart; with
  2–3 graphs the overlay stays ≥ 30 fps;
- *Controller*: drag a gain slider, tap a value and type one, −/+; the pendulum reacts on release;
  *Seed from LQR* on the double/triple pendulum balances it;
- *System*: add a segment → a new loop appears on *Controller*; switch cart ↔ fixed base;
- *Auto-tune*: estimates appear for the three presets; run *Quick* → progress, cancel → best-so-far
  offered; run again → result with Apply / Discard / Rerun; Apply returns to *Controller*;
  pressing *Home* during a run cancels it;
- rotate the device → layout switches (rail ↔ bottom bar, graphs beside ↔ below the view);
- *Home* while running → come back → simulation resumes where it was, no jump.

## Known limitations

- Rendering is done by Qt's software rasteriser (as on the desktop); see *Performance settings*.
- Auto-tune needs the app in the foreground; leaving the app cancels it.
- The fork-based worker pool and the fast-core pinning are verified on Linux CI; on devices whose
  kernel forbids `sched_setaffinity` the pinning is silently skipped, and if forking fails the
  search runs in one process (the progress screen says so).
- Keyboard shortcuts of the desktop version are replaced by touch controls.
- arm64-v8a only (no 32-bit devices; Qt dropped them).
- Android 9+ only (Qt 6.8+ requirement; Android 8.x cannot run current Qt).
- APK size is large (~180 MB) because the PySide6 wheel is bundled whole.
- First launch takes a few seconds while Python unpacks.
