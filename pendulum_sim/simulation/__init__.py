"""Simulation layer: configuration, fixed-step engine, telemetry, experiments (no GUI code)."""

from .config import (
    AppConfig,
    DisturbanceSettings,
    ExperimentSettings,
    SimulationSettings,
    config_from_dict,
    config_to_dict,
    load_config,
    save_config,
)
from .engine import SimulationDiverged, Simulator
from .experiment import (
    ExperimentResult,
    export_csv,
    load_experiments,
    run_experiment,
    save_experiments,
)
from .metrics import RunMetrics, SignalMetrics, run_metrics
from .scenarios import DEFAULT_SCENARIO, SCENARIOS, scenario
from .telemetry import COLUMNS, TelemetryRecorder

__all__ = [
    "AppConfig",
    "COLUMNS",
    "DEFAULT_SCENARIO",
    "DisturbanceSettings",
    "ExperimentResult",
    "ExperimentSettings",
    "RunMetrics",
    "SCENARIOS",
    "SignalMetrics",
    "SimulationDiverged",
    "SimulationSettings",
    "Simulator",
    "TelemetryRecorder",
    "config_from_dict",
    "config_to_dict",
    "export_csv",
    "load_config",
    "load_experiments",
    "run_experiment",
    "run_metrics",
    "save_config",
    "save_experiments",
    "scenario",
]
