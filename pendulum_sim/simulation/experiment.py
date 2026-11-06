"""Headless experiment runs for controller comparison.

An experiment runs the full simulator (same engine as the live view, so the
results are identical to what the user sees) for a fixed duration from the
configured initial conditions, optionally applies a scheduled tip-force
disturbance, and computes :class:`RunMetrics`. Runs are deterministic: the
same configuration (including the noise seed) always gives the same result,
so experiments that differ only in their PID gains are directly comparable.
"""

from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..control import wrap_angle
from .config import AppConfig, config_from_dict, config_to_dict
from .engine import SimulationDiverged, Simulator
from .metrics import RunMetrics, SignalMetrics, run_metrics
from .telemetry import COLUMNS

ProgressCallback = Callable[[float], bool]  # fraction done → return False to cancel


@dataclass
class ExperimentResult:
    name: str
    config: AppConfig
    data: dict[str, np.ndarray]
    metrics: RunMetrics
    diverged: bool = False
    message: str = ""
    created: float = field(default_factory=time.time)
    visible: bool = True  # shown in comparison plots (UI state)

    @property
    def n_segments(self) -> int:
        return self.config.plant.n_segments

    def angle_deg(self, segment: int) -> np.ndarray:
        return np.degrees(self.data[f"theta{segment + 1}"])

    def error_deg(self, segment: int) -> np.ndarray:
        return np.degrees(angle_error(self.config, self.data, segment))

    def gains_summary(self) -> str:
        parts = []
        for loop in self.config.controller.loops:
            if not loop.enabled:
                continue
            tag = "x" if loop.signal == "cart" else f"θ{loop.signal[5:]}"
            parts.append(f"{tag}: P={loop.kp:g} I={loop.ki:g} D={loop.kd:g}")
        return "; ".join(parts) if self.config.controller.enabled else "controller off"


def angle_error(config: AppConfig, data: dict[str, np.ndarray], segment: int) -> np.ndarray:
    """Angle minus the setpoint of the matching loop (0 if none), wrapped."""
    setpoint = 0.0
    for loop in config.controller.loops:
        if loop.signal == f"theta{segment + 1}" and loop.enabled:
            setpoint = loop.setpoint
    raw = data[f"theta{segment + 1}"] - setpoint
    return np.vectorize(wrap_angle)(raw) if raw.size else raw


def run_experiment(
    config: AppConfig, name: str = "Experiment", progress: ProgressCallback | None = None
) -> ExperimentResult:
    cfg = config.copy()
    cfg.validate()
    sim = Simulator(cfg, telemetry_window_s=None)
    dist = cfg.experiment.disturbance
    if dist.enabled:
        sim.apply_disturbance(dist.force, dist.duration, start=dist.time)
    duration = cfg.experiment.duration
    chunk = max(1, int(round(0.1 / cfg.simulation.dt)))
    total = int(round(duration / cfg.simulation.dt))
    diverged, message = False, ""
    try:
        done = 0
        while done < total:
            n = min(chunk, total - done)
            for _ in range(n):
                sim.step()
            done += n
            if progress is not None and not progress(done / total):
                message = "cancelled"
                break
    except SimulationDiverged as exc:
        diverged, message = True, str(exc)
    data = sim.telemetry.as_dict()
    errors = [np.degrees(angle_error(cfg, data, i)) for i in range(cfg.plant.n_segments)]
    metrics = run_metrics(
        data["t"], errors, data["actuator"], data["x"], cfg.experiment.settle_tolerance_deg, diverged
    )
    return ExperimentResult(name=name, config=cfg, data=data, metrics=metrics,
                            diverged=diverged, message=message)


# ------------------------------------------------------------------ export
def export_csv(result: ExperimentResult, path: str | Path) -> None:
    keys = ["t", "x", "xd"]
    for i in range(result.n_segments):
        keys += [f"theta{i + 1}", f"omega{i + 1}"]
    keys += ["command", "actuator", "tip_force", "energy"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        header = []
        for k in keys:
            header.append(k + ("_deg" if k.startswith("theta") else "_dps" if k.startswith("omega") else ""))
        writer.writerow(header)
        cols = []
        for k in keys:
            v = result.data[k]
            cols.append(np.degrees(v) if k.startswith(("theta", "omega")) else v)
        for row in zip(*cols):
            writer.writerow([f"{v:.6g}" for v in row])


def save_experiments(results: list[ExperimentResult], path: str | Path) -> None:
    """Save experiments (configuration, metrics and time series) as JSON."""
    payload = {
        "version": 1,
        "experiments": [
            {
                "name": r.name,
                "created": r.created,
                "diverged": r.diverged,
                "message": r.message,
                "config": config_to_dict(r.config),
                "metrics": r.metrics.to_dict(),
                "data": {k: [None if math.isnan(x) else x for x in v.tolist()] for k, v in r.data.items()
                         if not np.all(np.isnan(v))},
            }
            for r in results
        ],
    }
    Path(path).write_text(json.dumps(payload), encoding="utf-8")


def load_experiments(path: str | Path) -> list[ExperimentResult]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        out = []
        for e in payload["experiments"]:
            cfg = config_from_dict(e["config"])
            data = {k: np.array([np.nan if x is None else x for x in v], dtype=float)
                    for k, v in e["data"].items()}
            length = len(data["t"])
            for col in COLUMNS:
                data.setdefault(col, np.full(length, np.nan))
            m = dict(e["metrics"])
            m["segments"] = [SignalMetrics(**s) for s in m["segments"]]
            out.append(ExperimentResult(
                name=e["name"], config=cfg, data=data, metrics=RunMetrics(**m),
                diverged=e.get("diverged", False), message=e.get("message", ""),
                created=e.get("created", time.time()),
            ))
        return out
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"not a valid experiment file: {exc}") from exc
