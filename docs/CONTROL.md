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

## 3. Limitations

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
