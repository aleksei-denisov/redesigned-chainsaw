# Reference data (Phase 1)

`generate.py` runs the original `pendulum_sim` package and writes golden JSON files to
`data/`. The C++ tests (`cpp/tests`, helper `reference.hpp`) compare against them, so the port
is proven to give the same results as the Python implementation.

```bash
python cpp/reference/generate.py             # all groups (~30 s)
python cpp/reference/generate.py physics     # selected groups
```

| File | Content | Tolerance (rel / abs) |
|---|---|---|
| `physics.json` | Per scenario plant, 6 seeded states (first = upright): mass matrix, bias and generalised forces, joint friction, accelerations (free, actuator + tip force, point push), push arms, energies, momentum, one RK4 step | 1e-12 / 1e-12 |
| `control.json` | Linearisation A, B; CARE solution (NumPy and SciPy backends); LQR gain; 200 scripted steps of every PID loop | 1e-8 / 1e-10 |
| `simulation.json` | Every scenario for 4 s: state every 50 ms and telemetry at 20 Hz, plain and with a 1 N point push at mid-segment (v2 path) | 1e-7 / 1e-9 |
| `experiment.json` | `run_experiment` metrics per scenario | 1e-6 / 1e-9 |
| `tuning.json` | `max_recoverable_push`, `tuning2.evaluate_controller` for fixed controllers, and every evaluation of one seeded `tuning2.tune` run (quick preset, 1 worker) | 1e-9 / 1e-12 |

Not reproduced on purpose:

* **Sensor noise** — off in every scenario; NumPy's random generator is not re-implemented in C++.
* **The random sampling inside auto-tune** — the C++ auto-tune uses its own generator. Every
  deterministic part (each candidate's evaluation, the push bisection) must match exactly; the
  search as a whole is judged by result quality on the same scenarios.

`tests/test_cpp_reference.py` regenerates the fast groups and fails if the Python code no
longer produces the stored numbers. When physics or control change on purpose, regenerate the
data and commit it with the change.
