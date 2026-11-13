# Branch `quick-core-v2` – new physics core and new auto-tune (Qt Quick app only)

> **This branch uses a new physics core and a new auto-tune.** For now only the newest
> Android app – the Qt Quick app (`pendulum_sim/quick`, application id
> `com.adenisov.invertedpendulumquick`) – uses them. The desktop app and the Widgets
> Android app keep their behaviour: every addition is opt-in, the original code paths
> are unchanged and tested to give the same numbers.

APK of this branch: `https://github.com/aleksei-denisov/redesigned-chainsaw/releases/download/android-quick-core-v2/PendulumQuick-debug.apk`
(same application id as the `qtquick-ui` build, so it installs over it).

## Physics: a push at any point

`MultiLinkPendulum.push_arms(segment, ratio)` gives the lever arms of a horizontal force
at `ratio` (0 … 1) along `segment`: the point's horizontal position is
`x + Σ_{j<k} L_j sin θ_j + r L_k sin θ_k`, so the generalised force is
`Q_x = F`, `Q_θj = F a_j cos θ_j` with `a_j = L_j` below the segment, `r L_k` on it and 0
above (`Q = Jᵀ F`). `generalized_forces`, `accelerations`, `rk4_step` (reference and the
fast scalar path) take the arm-weighted sum of all active pushes; a push at the tip takes
the old path, bit for bit. `Simulator.apply_disturbance(force, duration, segment=, ratio=)`.
Tests: `tests/test_point_force.py` (tip equivalence, joints, fast vs reference, virtual
power `Q·q̇ = F·ẋ_point`).

In the app: a **tap** on the pendulum pushes the nearest point away from the finger; a
**drag** that starts on the pendulum pulls that point towards the finger (the Widgets
app's force law); *Push Left / Right* push the tip. The **push force** is set in the dock
(presets 0.05–200 N or typed).

## Auto-tune with options (`simulation/tuning2.py`)

Same robustness metric as before (largest recoverable 0.1 s tip push; ties by recovery
cost), same parallel workers – new search options:

* **All loops (LQR design)** – the LQR-weight search as before, then **integral gains**:
  each loop's `Ki/Kp` (1/s, log scale, "off" included) searched with the best design
  fixed; integral action is kept only where it makes the controller more robust.
  `Ki` is no longer forced to zero.
* **Selected loops** – only the chosen loops and gains (Kp, Ki, Kd) by a direct search
  around the current values (sign kept); every other loop keeps its settings.
* **Gain limits** – none, the same for every loop, or per loop (a loop's own limits, the
  general ones for the others); signed `[min, max]`, each side optional. In the direct
  search they bound the search; an LQR design breaking them is rejected (clipping a
  design can destabilise it); if nothing within the limits recovers from a push, the
  search says so.
* **Methods** (optional) – the other derivative source and anti-windup strategies,
  tried one change at a time for the tuned loops and kept if they help.

Controller frequency and output limits are not tuned: they are hardware limits, and
the search would always push them to the maximum.

Tests: `tests/test_tuning2.py`. Research on integral gains: `scripts/research_pid_vs_pd.py`
and the results below.

## Research: PID or PD for the auto-tuned controller?

See the section *Results* (filled in from `scripts/research_pid_vs_pd.py`).
