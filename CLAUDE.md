# CLAUDE.md — working rules for this repository

This repository hosts the **Interactive Inverted Pendulum Simulator** (Python, PySide6).
Repository: https://github.com/aleksei-denisov/redesigned-chainsaw

## Mandatory working rules

- Work within the existing repository.
- First inspect the repository structure, existing code, dependencies, and documentation.
- Understand the current implementation before making changes.
- Preserve existing functionality wherever possible.
- Do not unnecessarily replace or rewrite existing components.
- Organize new code into a clean, modular architecture.
- Keep the project easy to understand, maintain, and extend.
- Update the repository documentation (README.md, INSTALLATION.md, RUNNING.md,
  docs/) whenever functionality or dependencies change.
- Do not assume the repository is empty. Adapt the implementation to its existing structure.

## Architecture (keep these layers separate)

| Package | Responsibility | May import |
|---|---|---|
| `pendulum_sim/physics` | Multibody dynamics, actuator, integrator | numpy |
| `pendulum_sim/control` | PID, multi-loop controller, LQR gain seeding | numpy, scipy, physics |
| `pendulum_sim/simulation` | Config, fixed-step engine, telemetry, experiments, metrics, scenarios | physics, control |
| `pendulum_sim/ui` | PySide6 GUI, QPainter view, pyqtgraph plots | everything above |

- The physics/control/simulation layers must **never** import Qt. They are tested headless.
- Physics runs with a fixed timestep (`SimulationConfig.dt`), decoupled from the GUI refresh rate.
- Angles: absolute, measured from the upward vertical, radians internally, degrees in the UI/config.

## Commands

```bash
python main.py                    # launch the GUI
python -m pytest                  # run the test suite (GUI smoke test skips without PySide6)
python scripts/export_examples.py # regenerate configs/*.json from built-in scenarios
```

## Code quality

- Type hints, meaningful names, docstrings for the mathematics.
- Add/extend tests in `tests/` for any physics or controller change.
- Avoid new dependencies unless clearly justified; update `requirements.txt` and `pyproject.toml` together.
