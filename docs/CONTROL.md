# Control strategy

Implementation: [`pendulum_sim/control/`](../pendulum_sim/control/).

## 1. The PID loop

Each loop measures one signal `y` (cart position or a segment's absolute angle) and computes

```
e = σ · (y − r)                 σ = +1 direct action, −1 reverse action; angles wrapped to (−π, π]
u = Kp·e + I + Kd·ė
I ← I + Ki·e·Ts (+ anti-windup correction)
```

- **Sample rate** `Ts = 1/f`: the controller runs at its own frequency and holds its output
  between samples (zero-order hold), independent of the physics step.
- **Integral term** is stored already multiplied by `Ki` → changing `Ki` at run time causes no
  output jump (bumpless retuning). Optional `|I|` limit.
- **Anti-windup**, evaluated against the *total* saturated controller output:
  - *Clamping*: the integrator is frozen while the output is saturated and the error would
    drive it further into saturation.
  - *Back-calculation*: `I ← I + (Ki·e + Kt·(u_sat − u_unsat))·Ts`.
- **Derivative**: either the measured rate (`ė = σ·ẏ`, like an encoder speed or gyro – no
  derivative kick, no differentiation noise) or the filtered difference of the error with a
  first-order low-pass (cut-off in Hz).
- **Components**: P, I and D can be switched off individually.

### Why “direct” and “reverse” action

For a **cart**, a pendulum leaning to +x is caught by driving the cart to +x – the output must
*follow* the deviation (direct action). For a **base motor** the torque must rotate the segment
back – the output *opposes* the deviation (reverse action). The action is chosen automatically
when the base type changes so that a single balancing loop has positive gains in both cases.

## 2. Multi-segment strategy: parallel multi-loop PID

There is only **one actuator**, but 2–4 signals to regulate (cart and every segment). The
controller runs **one PID loop per signal and sums the loop outputs**:

```
u = sat( PID_x(e_x) + PID_θ1(e_θ1) + PID_θ2(e_θ2) + PID_θ3(e_θ3) )
```

### Why this structure

- With `Ki = 0` the sum of PD loops is exactly **linear full-state feedback**
  `u = −K s`, `s = [x, θ1…θn, ẋ, θ̇1…θ̇n]`: each loop's Kp multiplies a position, its Kd the
  matching velocity. Full-state feedback is what is required to stabilise a double or triple
  inverted pendulum with a single input; independent single-angle PIDs or simple cascades of
  them cannot do it in general, because every segment's motion depends on all others.
- It keeps the familiar PID knobs per signal (and adds integral action and anti-windup per
  loop), so the user can still reason about and tune each contribution.

### LQR seeding

Finding a stabilising set of 6–8 coupled gains by hand is impractical. **Seed gains from LQR**
(PID panel) linearises the plant about the upright equilibrium numerically, `ṡ ≈ A s + B u`,
solves the continuous algebraic Riccati equation (SciPy) for weights `Q = diag(q_x, q_θ, q_ẋ, q_ω)`,
`R`, and writes `Kp = −σ·K_pos`, `Kd = −σ·K_vel` into the loops. The integral gains are kept.
Larger angle weights give stiffer angle control; a larger `R` gives gentler actuator use.

Typical resulting signs are *mixed*: e.g. for the triple pendulum on a cart the θ2 loop gain is
negative. This is physical (the inner segments must be “over-corrected” to move the upper ones)
and is why hand-tuning positive gains per segment does not work.

### Single-segment cart-pole

- One angle loop alone balances the pendulum but leaves the cart position uncontrolled – the
  cart drifts (scenario `single_cart_pid`).
- Adding the cart loop holds the cart: its gain sign is “wrong-looking” because the cart is a
  **non-minimum-phase** output – to move right it must first move left so that the pendulum tilts
  right (scenario `single_cart_full`).

## 3. Robust auto-tuning

**★ Auto-tune for robustness…** (PID panel) finds the best gains *for the system as it is
configured* – masses, lengths, inertia, friction, actuator limit, lag and rate limit, controller
rate. Implementation: [`simulation/tuning.py`](../pendulum_sim/simulation/tuning.py).

- **Objective.** Primary: the *recoverable tip push* – the largest horizontal force pulse
  (0.1 s at the tip, starting upright) after which no segment ever exceeds 60° and all are back
  within 3° after 4 s. It is measured by doubling + bisection on the full nonlinear simulation.
  Secondary (tie-break within the bisection resolution): the integral of the squared angles after
  a push of half that strength, i.e. how quickly and calmly the system recovers.
- **Search space.** The five LQR weights (log scale). Every candidate is therefore a
  *stabilising* design mapped onto the loop gains, which keeps the search small and smooth.
  Random exploration around the defaults is followed by refinement with a shrinking step.
  Refining the raw gains further beyond the LQR family did not improve the result in tests.
- **Parallel.** Candidates are evaluated in worker processes (CPU cores − 1, up to 8).
- **Output.** All loops enabled, Kp/Kd from the best design, Ki = 0, clamping anti-windup,
  measured-rate derivative, output limits = actuator limit. It also proposes a test push of
  half the recoverable push.
- **Restore original gains** brings back the gains of the loaded scenario/file.

Results for the built-in systems (the scenarios ship with these tuned gains):

| System | Recoverable push, default LQR weights | After robust tuning | Default *Push* |
|---|---|---|---|
| Single pendulum on cart (40 N) | 4.5 N | 5.1 N | 2.5 N |
| Double pendulum on cart (40 N) | 0.34 N | 0.54 N (+59 %) | 0.25 N |
| Pendubot, double on fixed pivot (2 N·m) | 0.63 N | 0.85 N (+35 %) | 0.4 N |
| Triple pendulum on cart (60 N) | 0.089 N | 0.126 N (+41 %) | 0.06 N |

**Why the double and triple tolerate so little.** These limits are physical, not a tuning
problem: with an *unlimited* actuator, no lag and a 5 kHz controller the double still fails at
≈0.36 N and the triple at ≈0.08 N with the default design. A 0.1 N·s impulse on the light upper
segments throws them into fast relative motion; recovering requires the lower segments to swing
far (a 0.07 N push already swings the triple's lowest segment by ~20°), and beyond that point the
nonlinearity takes over. What *does* raise the limit is the physics: a lighter top segment, a
heavier/longer lower segment, or a longer track. Re-run the tuner after such changes.

## 4. Limitations

- **Local controller.** Gains are designed for the upright equilibrium. Large initial angles,
  strong pushes or tight actuator limits can make the system fall; there is no swing-up
  controller. The 3-segment system on a cart tolerates roughly 10–12° of a straight tilt with the
  default 60 N actuator, but much less if the segments start bent against each other.
- **Unmodelled dynamics in the design.** LQR seeding ignores the actuator lag, rate limit,
  deadband, friction, sensor noise and the sampling rate. Lower controller frequencies or slower
  actuators reduce the stability margin – this is a good thing to explore in experiment mode.
- **Integral action on multi-segment systems** adds states that LQR did not account for; small
  `Ki` values are usually fine, large values destabilise.
- **Saturation** of the single actuator is shared by all loops; anti-windup is applied to every
  loop's integrator against the total output.
- **Underactuation** of the fixed-pivot double/triple pendulum (pendubot): it is controllable but
  very sensitive; only small deviations are recoverable.
- Angle errors are wrapped to ±180°, so a setpoint “across” the hanging position is reached the
  short way round.
