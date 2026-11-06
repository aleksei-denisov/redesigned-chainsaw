# Running and using the simulator

All commands are run from the repository folder with the virtual environment active
(see [INSTALLATION.md](INSTALLATION.md)).

## 1. Launch commands

| Command | What it does |
|---|---|
| `python main.py` | Start with the default scenario (single pendulum on a cart) |
| `python main.py --scenario triple_cart_lqr` | Start with a built-in scenario |
| `python main.py --config configs/windup_demo.json` | Start with a configuration file |
| `python main.py --list-scenarios` | List the built-in scenarios |
| `python main.py --help` | Show all options |
| `python -m pendulum_sim` | Same as `python main.py` |
| `pendulum-sim` | Same, after `python -m pip install .` |
| `./start.sh` / `start.bat` | Create the venv if needed, install, then launch |

Built-in scenarios (also available as files in `configs/` and from the toolbar *Scenario* box):

| Key | Description |
|---|---|
| `single_cart_pid` | Cart-pole balanced by a single angle PID (cart drifts) |
| `single_cart_full` | Angle + cart-position loops (LQR-seeded), scheduled push |
| `single_fixed_pid` | Fixed pivot with a torque motor, reverse-acting PID |
| `windup_demo` | Integral windup with a weak motor – compare anti-windup modes |
| `double_cart_lqr` | Double pendulum on a cart, three loops |
| `double_fixed_lqr` | Double pendulum driven only at the base (pendubot) |
| `triple_cart_lqr` | Triple pendulum on a cart, four loops |
| `double_free_swing` | Uncontrolled, frictionless chaotic double pendulum (energy check) |
| `hanging_damped` | Uncontrolled pendulum falling to the stable hanging position |

## 2. The window

- **Centre, tab “Live simulation”** – the pendulum view (top) and live telemetry graphs (bottom);
  drag the splitter to resize.
- **Centre, tab “Experiments & comparison”** – experiment mode (section 6).
- **Right, “Configuration” dock** – tabs *Pendulum*, *PID controller*, *Simulation & view*.
- **Toolbar** – run controls, speed, tip pushes, scenario selection.
- **Status bar** – run state, simulated time, achieved speed, total energy, display frame rate
  and physics time per frame, messages.

The window sizes itself to the available area of your screen at start-up (it maximises on
small screens). All panels can be resized with their splitters, and the configuration dock can
be detached.

## 3. Starting and controlling a simulation

| Control | Shortcut | Effect |
|---|---|---|
| ▶ Start / ▶ Resume | F5 | Run from the current state |
| ⏸ Pause | F6 | Freeze the simulation (state kept) |
| Start/Pause | Space | Toggle |
| ⟲ Reset | Ctrl+R | Return to the initial conditions, stopped, telemetry cleared |
| ⟳ Restart | Ctrl+Shift+R | Reset and immediately run |
| Step | F7 | Advance by one controller period |
| Speed | – | 0.05× … 8× real time |
| ◀ Push / Push ▶ | Ctrl+← / Ctrl+→ | 0.1 s horizontal force at the tip (magnitude in the box between the buttons; each scenario sets a sensible default, saved with the configuration) |

The physics always advances in fixed steps (default 1 ms) independent of the 60 fps display.
If your computer cannot keep up, the simulation runs slower than real time (shown in the status
bar) instead of losing accuracy. When frames get slow, the graphs automatically refresh less
often so the pendulum animation stays smooth.

### Live graphs

Click **Graphs ▾** above the graphs to choose which ones are shown (angles, angular velocities,
control error, control signal, PID components, energy, cart position) and whether angles are
wrapped to ±180° (default; a spinning pendulum then stays readable). Hidden graphs cost nothing.
*Window* sets the visible time span; *Loop* selects the loop for the PID-components graph.

The Y axes use **calm scaling** (Graphs ▾ → *Calm Y scaling*, on by default): a range expands
immediately when the signal grows (nothing is ever cut off), but shrinks only slowly once the
signal has stayed small, so the graphs do not jump. Error, control and velocity graphs stay
centred on zero. The control graph is limited to about the actuator limit – the dashed
*unsaturated* command can spike far beyond what the actuator can deliver and is clipped there.
Turn calm scaling off to get plain autoscaling.

### Pendulum view

- Mouse wheel: zoom around the cursor · left-drag: pan · double-click or Ctrl+0: fit.
- Drawn elements: track with end stops or fixed mount, segments (blue/orange/green), joints and
  pivot, centre-of-mass symbols, angle arcs with values, curved angular-velocity arrows,
  dashed upright-equilibrium ghost, tip trajectory, actuator force/torque arrow (red when
  saturated), red disturbance arrow, scale bar and a numeric read-out (HUD).
- All elements can be switched on/off in **Simulation & view → Visualisation**, together with
  trajectory length, segment width, zoom and “camera follows the cart”.

## 4. Configuring the pendulum (*Pendulum* tab)

- **Base** – *Cart on a track* (actuator = horizontal force on the cart) or *Fixed pivot*
  (actuator = torque motor at the first joint).
- **Segments** – 1, 2 or 3. Changing this (or the base) resets the simulation and adds the
  missing controller loops with zero gains.
- **Gravity**; **Cart** mass, viscous friction and track length (soft end stops beyond).
- **Actuator** – max output (saturation), rate limit, time constant (first-order lag), deadband.
- **Per segment** – length, mass, centre-of-mass position (fraction of the length from the lower
  joint), moment of inertia about the centre of mass (or tick *Uniform rod* to compute
  m·L²/12), joint viscous and Coulomb friction of the joint below the segment.
- **Initial conditions** – initial angle (absolute, from the upward vertical; positive leans
  towards +x / clockwise) and angular velocity. They apply on Reset/Restart.

Physical parameters apply **immediately while running**.

## 5. Tuning the PID controller (*PID controller* tab)

Global settings:

- **Controller enabled** – off = open loop (watch the pendulum fall).
- **Action** – *Direct* (e = y − r, used for a cart: drive towards the lean) or *Reverse*
  (e = r − y, used for a base motor). With the right action, a single balancing loop needs
  positive gains.
- **Update frequency** – controller sample rate; output is held between samples.
- **Output min / max** – controller output limits (anti-windup reacts to these).
- **★ Auto-tune for robustness…** – one click finds the best gains for the current physical
  system (section 5.1).
- **↺ Restore original gains** – returns to the gains of the loaded scenario or configuration
  file, e.g. after the system became unstable while experimenting.
- **Seed gains from LQR…** – computes stabilising P and D gains for *all* loops (needed for 2 and
  3 segments) from LQR weights you choose. Integral gains are kept.
- **Reset integrators** – clears the stored integral terms.

Per-loop tabs (*Cart x*, *θ1*, *θ2*, *θ3*):

- **Kp, Ki, Kd** (SI units: per rad, per rad·s, per rad/s, or per m for the cart loop) and
  switches to enable/disable **P**, **I** and **D** individually.
- **Setpoint** (degrees for angles, metres for the cart).
- **Anti-windup** – none, clamping (conditional integration) or back-calculation with gain Kt;
  **integral limit**.
- **Derivative source** – measured rate (gyro/encoder speed) or filtered difference of the error
  with an adjustable low-pass cut-off.
- **Live components** – current error, P, I, D terms and their sum.

Everything applies while the simulation runs; the integral state is kept when gains change
(bumpless). A typical manual session:

1. Load `single_fixed_pid`, press **Restart**.
2. Untick **D** → the pendulum oscillates; tick it again.
3. Set Kp to 2 → too weak, the pendulum falls; set it back to 6.
4. Add sensor noise (Simulation & view) and compare *measured rate* with *filtered difference*.

More background: [docs/CONTROL.md](docs/CONTROL.md).

### 5.1 Robust auto-tune

1. Set up the pendulum (any number of segments, base, actuator, controller rate).
2. In the *PID controller* tab click **★ Auto-tune for robustness…**, pick the search effort
   (*Normal* takes roughly 10–60 s depending on the system and the number of CPU cores) and press
   **Start**. The live simulation pauses meanwhile; *Cancel* keeps the best result found so far
   available.
3. The dialog reports the **recoverable tip push** (largest 0.1 s force pulse at the tip the
   system survives) before and after. **Apply gains** loads the result and sets the toolbar
   *Push* force to a comfortable test value (half the recoverable push).
4. Press **Restart** and try **◀ Push / Push ▶**.

The tuner keeps the hardware as it is – stronger motors, faster controllers, heavier lower or
lighter upper segments change what is achievable; re-run the tuner after such changes. Typical
results for the built-in systems are listed in [docs/CONTROL.md](docs/CONTROL.md#3-robust-auto-tuning).

## 6. Running and comparing experiments (*Experiments & comparison* tab)

1. Configure the pendulum and controller as usual.
2. In the experiment panel set a **duration**, the **settling band** (±degrees) and optionally a
   **scheduled disturbance** (tip force, start time, duration).
3. Click **▶ Run experiment**. The run is computed in the background (the GUI stays responsive)
   and appears in the results table and the plots.
4. Change PID coefficients in the *PID controller* tab.
5. Click **▶ Run experiment** again. With **“Reuse plant, initial conditions and noise of the
   first (baseline) experiment”** ticked (default) the new run starts from exactly the same
   state as the first one – only the controller differs.
6. Compare the overlaid curves (angular displacement, control error with the settling band,
   control effort, cart position; choose the segment at the top) and the metrics table:

| Metric | Definition |
|---|---|
| Settling [s] | First time after which every segment stays within the band (— = never) |
| Max dev [°] | Largest absolute angle error of any segment |
| Overshoot [%] | Largest excursion past the setpoint after the peak, relative to the peak |
| RMSE [°] | Root-mean-square angle error over the run (all segments) |
| u RMS / u peak / ∫u² dt | Control effort of the applied actuator output |
| Max \|x\| [m] | Largest cart displacement |
| Status | stable / FELL (> 90°) / diverged |

Other buttons: show/hide runs with the checkbox in the first column, **Save results…** /
**Load results…** (JSON with configurations, metrics and time series), **Export selected as
CSV…**, **Load selected gains into controller**, **Remove selected**, **Clear all**.

Try it with `windup_demo`: run once (anti-windup *None*), set anti-windup to *Clamping*, run,
set *Back-calculation*, run – the overshoot drops from about 49 % to about 21 % and 17 %.

## 7. Saving and loading configurations

- **File → Save configuration** (Ctrl+S) / **Save configuration as…** (Ctrl+Shift+S) writes the
  complete setup (pendulum, actuator, controller, numerics, experiment settings) to JSON.
- **File → Open configuration…** (Ctrl+O) loads one; invalid files are rejected with a message.
- **File → New from scenario** or the toolbar *Scenario* box loads a built-in scenario.
- From the command line: `python main.py --config path/to/file.json`.

The files are human-readable; angles are in degrees, everything else in SI units.
`configs/*.json` contains one example per scenario; regenerate them with
`python scripts/export_examples.py`.

## 8. Running the tests

```bash
python -m pytest            # everything
python -m pytest tests/test_physics.py -v
python -m pytest -k "pid or windup"
```

The GUI tests (`tests/test_gui_smoke.py`) run offscreen and are skipped automatically if
PySide6/pyqtgraph are not installed. On Linux without a display set `QT_QPA_PLATFORM=offscreen`.

To check that the installed application starts (opens the window, runs 2 s and one experiment,
exits with code 0):

```bash
python main.py --smoke-test 2
```
