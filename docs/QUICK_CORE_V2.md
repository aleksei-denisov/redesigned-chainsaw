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

In the app: a **tap** on the pendulum pushes the nearest point away from the finger with
the push force; a **drag** that starts on the pendulum pulls that point towards the finger
with a force that grows with the finger's distance up to the push force
(`rules.point_drag_force`: 2 · push force · offset / total length, at most the push
force), so a pull is never stronger than a tap; *Push Left / Right* push the tip. The
**push force** is set in the dock (presets 0.05–200 N or typed); applying an auto-tune result no longer changes it.

A push near the pivot mostly moves the cart: its lever arms on the angles are small
(`a_1 = r L_1`, nothing above), so with a stiff controller the segments move almost
together – correct physics, not a single rigid body (`tests/test_point_force.py`).

## Auto-tune with options (`simulation/tuning2.py`)

Same robustness metric as before (largest recoverable 0.1 s tip push; ties by recovery
cost), same parallel workers – new search options:

* **All loops (LQR design)** – the LQR-weight search as before, then **integral gains**:
  each loop's `Ki/Kp` (1/s, log scale, "off" included) searched with the best design
  fixed; integral action is kept only where it makes the controller more robust.
  `Ki` is no longer forced to zero.
* **Selected loops** – only the chosen loops, each gain set to *Tune* (searched around its
  current value, sign kept, and kept non-zero), *Keep as is* or *Off (0)*; every other
  loop keeps its settings.
* **Gain limits** – none, the same for every loop, or per loop (a loop's own limits, the
  general ones for the others); signed `[min, max]`, each side optional. In the direct
  search they bound the search; an LQR design breaking them is rejected (clipping a
  design can destabilise it); if nothing within the limits recovers from a push, the
  search says so.
* **Methods** (optional) – the other derivative source and anti-windup strategies,
  tried one change at a time for the tuned loops and kept if they help.
* **Hold position under a steady push** (optional, off by default) – a second test per
  candidate: a steady tip force of 5 % of its recoverable push for 12 s. Held quantity:
  the cart position, or without a cart segment 1's angle (on a cart the pendulum must
  lean against a steady force, so the cart is what can be brought back). Residual `r` =
  mean |offset| over the last second / peak |offset| (0 = back at the setpoint, 1 = a PD
  controller's steady offset, or a fall). Candidates are compared by
  `push · (1 − 0.5 r)`: a controller that holds its position beats one that does not as
  long as it recovers from at least half the push. With the integral search, the held
  loop's integral alone at `Ki/Kp` = 0.1 / 0.2 / 0.3 1/s is tried too (the research
  below found exactly that one useful). The result shows the residual before → after.
  The search takes about 2–3 times as long (*Quick* preset, one core per row below).

  | Scenario | push off / on | residual: start → result | integral found |
  |---|---|---|---|
  | single_cart_pid | 4.48 / 4.48 N | 100 % → 2 % | cart, Ki/Kp 0.27 1/s |
  | double_cart_lqr | 0.46 / 0.46 N | 100 % → 6 % | cart, 0.20 1/s |
  | double_fixed_lqr | 0.78 / 0.78 N | 100 % → 18 % (θ1) | θ1, 0.27 1/s |
  | triple_cart_lqr | 0.12 / 0.12 N | 100 % → 8 % | cart, 0.20 1/s |

Controller frequency and output limits are not tuned: they are hardware limits, and
the search would always push them to the maximum.

Tests: `tests/test_tuning2.py`. Research on integral gains: `scripts/research_pid_vs_pd.py`
and the results below.

## Research: PID or PD for the auto-tuned controller?

Method (`scripts/research_pid_vs_pd.py`, *Quick* preset, same seed): the full auto-tune
with the integral search off (PD) and on (PID) on the four balancing scenarios, then
two measurements – the tuner's robustness metric (largest recoverable 0.1 s tip push)
and a **steady** tip force of 3 % of that push held for 8 s (cart and θ1 offsets at the
end). Then the PD result with integral gains added by hand: the same `Ki/Kp` on every
loop, and on the cart position loop only (steady force held for 15 s).

**1. The integral search (impulse metric)** – integral action never raised the
recoverable push; it was kept once (double cart, θ1 loop, Ki = 35, recovery cost −0.7 %):

| Scenario | PD push | PID push | PID integral kept | Steady force: PD cart / θ1 offset | PID cart / θ1 offset |
|---|---|---|---|---|---|
| single_cart_pid | 4.48 N | 4.48 N | none | 0.91 m / 7.8° | (same) |
| double_cart_lqr | 0.46 N | 0.46 N | θ1 (35.4) | 1.05 m / 0.31° | 0.78 m / 0.02° |
| double_fixed_lqr | 0.78 N | 0.78 N | none | – / 10.2° | (same) |
| triple_cart_lqr | 0.12 N | 0.12 N | none | 0.46 m / 0.06° | (same) |

**2. The same Ki/Kp on every loop** – does not remove the offsets and costs robustness
at higher values (single cart 1/s: push 4.48 → 3.84 N; triple cart 1/s: 0.12 → 0.09 N;
double fixed: θ1 offset 10.2° → 17.1°). Physically, a pendulum on a cart *must* lean
against a steady side force (gravity torque balances it), so integrators on the angle
loops fight physics and wind up.

**3. Integral on the cart position loop only** – removes the cart's steady offset:

| Scenario | cart Ki/Kp | push | cart offset after 15 s |
|---|---|---|---|
| single_cart_pid | 0 / 0.1 / 0.3 / 1.0 1/s | 4.48 / 4.48 / 4.48 / 3.68 N | 0.91 / 0.22 / 0.001 / 0.013 m |
| double_cart_lqr | 0 / 0.1 / 0.3 / 1.0 1/s | 0.46 / 0.46 / 0.46 / 0.46 N | 1.05 / 0.27 / 0.016 / 1.09 m |
| triple_cart_lqr | 0 / 0.1 / 0.3 / 1.0 1/s | 0.12 / 0.12 / 0.086 / 0.043 N | 0.46 / 0.11 / 0.0001 / 0.36 m |

**Conclusions.** PID is *not* better than PD for the robustness to pushes the tuner
measures – integral action adds phase lag and at best leaves the recoverable push
unchanged. PID *is* better where PD cannot be: steady disturbances (a constant force,
a slope, a sensor or setpoint offset) – but only with the integral on the **cart
position** loop, at a moderate rate (`Ki/Kp ≈ 0.1–0.3 /s`); integrals on the angle loops
make things worse. The integral search therefore stays on by default and keeps an
integral only where the metric improves; to make the benefit of a cart integral visible
to the tuner, the optional *Hold position under a steady push* criterion adds it to the
metric (above).
