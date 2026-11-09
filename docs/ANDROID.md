# Android app

The simulator also ships as an Android app, **Inverted Pendulum Simulator**
(application id `com.adenisov.invertedpendulum`). It runs the *same* physics,
controller and simulation code as the desktop version and draws the pendulum with
the *same* QPainter view and pyqtgraph telemetry; only the window layout and the
input handling are mobile-specific.

The app has the **same features as the desktop version**: live simulation, scenarios,
the *Pendulum*, *PID controller* and *Simulation & view* panels, LQR seeding, robust
auto-tuning, restoring gains, resetting integrators, the experiment page with comparison
plots and the results table, and opening/saving configurations, experiment results and
CSV exports (through Android's file picker).

## Architecture

```
pendulum_sim/physics, control, simulation   shared, Qt-free
pendulum_sim/ui                              desktop UI; MainWindow is built from overridable builders
pendulum_sim/ui/file_io.py                   file dialogs + read/write that accept Android content:// URIs
pendulum_sim/mobile/window.py                MobileWindow(MainWindow): touch layout, inherits every handler
pendulum_sim/mobile/touch_view.py            PendulumView subclass: tap / drag / pinch, render resolution
pendulum_sim/mobile/touch_style.py           finger-sized widgets, −/+ spin buttons, kinetic scrolling
pendulum_sim/mobile/performance.py           drawing settings (saved per device) and frame timings
android/main.py                              APK entry point (packaged as main.py)
android/icon.svg, icon.png                   launcher icon (PNG rendered from the SVG)
android/requirements-build.txt               host build tools (PySide6, buildozer, cython)
android/Dockerfile                           Linux build environment
scripts/build_android.py                     staging + pyside6-android-deploy + buildozer
scripts/mobile_preview.py                    offscreen screenshots + drawing benchmark (CI)
.github/workflows/android.yml                CI: debug APK on every push, signed AAB on tags
```

`MobileWindow` subclasses the desktop `MainWindow` and replaces only the *builders*
(layout, control bar, status bar, view class). Parameter edits, LQR/robust tuning,
experiments, file handling and divergence handling are the desktop code, so a fix or
feature there reaches both versions.

Packaging uses Qt's official [`pyside6-android-deploy`](https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-android-deploy.html),
which drives buildozer/python-for-android with the official PySide6 Android wheels.
NumPy is compiled by its python-for-android recipe; pyqtgraph is pure Python.
**SciPy is not bundled**: LQR gain seeding uses the NumPy Riccati solver in
`pendulum_sim/control/riccati.py` when SciPy is missing (desktop keeps using SciPy;
the two agree to ~1e-9, see `tests/test_riccati.py`). The robust auto-tuner runs in a
single process on Android (no `multiprocessing` there), so it takes longer than on a
multi-core desktop.

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

| Gesture / control | Effect |
|---|---|
| tap on the pendulum view | tip push (0.1 s, *push force*) towards the tapped side of the tip |
| one-finger drag on the view | continuous tip force pulling towards the finger (rubber band, capped at 4× push force) |
| pinch / two-finger drag | zoom / pan the view |
| ▶ Start · ⏸ Pause, ⟲ Reset, ⟳ Restart, Step | run control (Step = one controller period) |
| ◀ Push, push force, Push ▶ | tip push left / right with the given force |
| speed, Scenario… | simulation speed, load a demonstration |
| ⚙ | show / hide the configuration panel (right in landscape, below in portrait) |
| ☰ | open / save configuration, new from scenario, fit view, clear trajectories, timing read-out, about |
| *Live* / *Experiments* tabs | live view + graphs / experiment runs and comparison |
| *Graphs ▾* | choose the graphs (angular positions and control signal by default; all desktop graphs available) |

Spin boxes have large − / + buttons; tap the number to type a value. On tablets the
configuration panel is open at start, on phones it is hidden.

### Performance settings

*⚙ → Simulation → Performance (this device)* – saved on the device:

| Setting | Effect |
|---|---|
| View resolution | *Native* draws the pendulum at the screen's full pixel density; *High* (1.5×), *Balanced* (1.25×) and *Fast* (1×) draw fewer pixels and scale up. *Fast* on a 2× screen is a plain pixel doubling and the cheapest. |
| Graph refresh | upper limit (default 10 Hz); lowered automatically when frames get slow |
| Frame rate | 60 or 30 fps |
| Antialiased pendulum view | smooth edges; off is noticeably cheaper |
| Timing read-out | top-right of the view: `fps · max <longest frame gap> │ physics · view · graphs` in ms per frame |

The physics takes about 0.05 ms per step on a desktop CPU (≈1 ms per 60 Hz frame) and is not
the bottleneck; drawing a 2880×1800 screen in software is. If the animation stutters, read the
timing read-out: a large *graphs* value → fewer graphs or a lower graph refresh; a large *view*
value → lower view resolution or no antialiasing.

The mobile interface can be previewed on the desktop (mouse = one finger, wheel = zoom):

```bash
python -m pendulum_sim.mobile                       # phone-sized window
python -m pendulum_sim.mobile --tablet --landscape  # 1280×800 tablet
```

CI renders the interface at a 2× pixel ratio for phone and tablet sizes and times the drawing
(`scripts/mobile_preview.py`); the images and `benchmark.txt` are published as the
[mobile-preview](https://github.com/aleksei-denisov/redesigned-chainsaw/releases/tag/mobile-preview)
pre-release.

## Building

### Download the latest APK

Pushes to `main` and `android-apk` publish the debug APK as the rolling pre-release
**android-latest** – open this link directly on the phone/tablet:

<https://github.com/aleksei-denisov/redesigned-chainsaw/releases/download/android-latest/InvertedPendulumSimulator-debug.apk>

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

- app starts, pendulum balances in the default scenario, ~60 fps shown at the bottom right;
- tap left/right of the tip → push; drag → red rubber band and the pendulum follows;
- pinch/pan, *Fit*; rotate the device → layout switches, simulation keeps running;
- *Home* while running → come back → simulation resumes where it was, no jump;
- every scenario in the top list loads; *LQR gains* on the double/triple pendulum balances it.

## Known limitations

- Rendering is done by Qt's software rasteriser (as on the desktop); see *Performance settings*.
- The robust auto-tuner runs single-process (slower than on a multi-core desktop).
- Keyboard shortcuts of the desktop version are replaced by the touch bar and ☰ menu.
- The desktop *Help → About* and window docking are not applicable; the configuration panel
  is a collapsible side/bottom panel instead.
- arm64-v8a only (no 32-bit devices; Qt dropped them).
- Android 9+ only (Qt 6.8+ requirement; Android 8.x cannot run current Qt).
- APK size is large (~180 MB) because the PySide6 wheel is bundled whole.
- First launch takes a few seconds while Python unpacks.
