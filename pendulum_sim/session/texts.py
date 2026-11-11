"""Texts every front end shows the same way (Qt-free; moved from the Widgets screens)."""

from __future__ import annotations

import math

from ..control import ControllerConfig
from ..simulation import ExperimentResult

TUNE_PRESETS = [("quick", "Quick", "Fewest candidates · fastest"),
                ("normal", "Balanced", "Recommended"),
                ("thorough", "Thorough", "Most candidates · best result")]

STRUCTURE_CHANGED = ("Structure changed – simulation reset. Use 'Seed gains from LQR' in the "
                     "PID panel to obtain stabilising gains.")
LQR_SEEDED = "Gains seeded from LQR. Reset/Restart to try them from the initial conditions."
GAINS_RESTORED = "Original gains restored"


def tuning_applied(result) -> str:
    return (f"Robust gains applied: recoverable push {result.baseline_max_push:.3g} N → "
            f"{result.max_push:.3g} N. Restart to try them; Push uses {result.recommended_push:g} N.")


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "–"
    seconds = max(0, int(round(seconds)))
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min"
    if seconds >= 60:
        return f"{seconds // 60} min {seconds % 60:02d} s"
    return f"{seconds} s"


def loop_name(signal: str) -> str:
    return "Cart x" if signal == "cart" else f"θ{signal[5:]}"


def gains_text(ctrl: ControllerConfig | None) -> str:
    if ctrl is None:
        return "–"
    return "\n".join(f"{loop_name(lp.signal)}:  Kp {lp.kp:.4g} · Ki {lp.ki:.4g} · Kd {lp.kd:.4g}"
                     for lp in ctrl.loops if lp.enabled)


def status_text(r: ExperimentResult) -> str:
    return "diverged" if r.diverged else "fell" if r.metrics.fell else "stable"


def metric_pairs(r: ExperimentResult) -> list[tuple[str, str]]:
    """The desktop table's columns as (name, value) pairs."""
    m = r.metrics
    settle = "–" if m.settling_time is None else f"{m.settling_time:.3f} s"
    pairs = [("Settling", settle), ("Max dev", f"{m.max_deviation:.2f}°"), ("Overshoot", f"{m.overshoot_pct:.1f} %"),
             ("RMSE", f"{m.rmse:.3f}°"), ("u RMS", f"{m.control_rms:.3f}"), ("u peak", f"{m.control_peak:.3f}"),
             ("∫u² dt", f"{m.control_energy:.3f}")]
    if r.config.plant.has_cart:
        pairs.append(("Max |x|", f"{m.max_cart_displacement:.3f} m"))
    return pairs
