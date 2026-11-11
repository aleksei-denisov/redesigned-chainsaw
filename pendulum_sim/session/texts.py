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


# ------------------------------------------------------------------ auto-tune
def tune_estimate(preset: str, push_test_s: float | None, workers: int) -> str:
    """Expected duration of a preset on this device (after the calibration run)."""
    from ..simulation.tuning import TuningSettings, estimate_seconds

    if push_test_s is None:
        return "measuring this device…"
    return f"about {fmt_duration(estimate_seconds(TuningSettings.preset(preset), push_test_s, workers))}"


def tune_cores(workers: int, available: int) -> str:
    return (f"Uses {workers} CPU core{'s' if workers != 1 else ''} in parallel "
            f"({available} available; one stays free for the display).")


def tune_activity(u) -> str:
    note = f" – {u.pool_note}" if u.pool_note else ""
    return (f"Working on {u.workers} CPU core{'s' if u.workers != 1 else ''}{note} · "
            f"{u.simulations} simulations so far")


def tune_best(u) -> tuple[str, str]:
    """(best result line – may contain <b>, its gains) of a progress snapshot."""
    if u.best_push > 0:
        before = f" (current controller: {u.baseline_push:.3g} N)" if u.baseline_push is not None else ""
        return f"Recovers from a tip push of <b>{u.best_push:.3g} N</b>{before}", gains_text(u.best_controller)
    if u.baseline_push is not None:
        return f"Current controller recovers from {u.baseline_push:.3g} N", ""
    return "–", ""


def tune_fraction(u, since_update: float) -> float:
    """Progress bar position: extrapolated within the current unit, never past the next one."""
    frac = u.fraction
    if u.eta and u.eta > 0:
        est = (u.elapsed + since_update) / (u.elapsed + u.eta)
        frac = max(frac, min(est, (u.done + 1) / u.total * 0.999))
    return frac


def tune_times(elapsed: float, u, since_update: float) -> str:
    if u is None or u.total == 0:
        return f"Elapsed {fmt_duration(elapsed)}"
    remaining = None if u.eta is None else max(0.0, u.eta - since_update)
    rest = f" · about {fmt_duration(remaining)} left" if remaining is not None else " · estimating time left…"
    return f"Elapsed {fmt_duration(elapsed)}{rest}"


def tune_headline(result) -> str:
    before, after = result.baseline_max_push, result.max_push
    gain = f" (+{(after / before - 1) * 100:.0f} %)" if before > 0 and after > before else ""
    return f"Recoverable tip push: {before:.3g} N → {after:.3g} N{gain}"


def tune_notes(result) -> str:
    before, after = result.baseline_max_push, result.max_push
    notes = [f"{result.evaluations} candidates in {fmt_duration(result.elapsed)}."]
    if result.cancelled:
        notes.insert(0, "Cancelled – this is the best result found so far.")
    if after <= before * 1.02:
        notes.append("The current gains are already about as robust as this search can find; "
                     "applying gives the normalised recommended settings.")
    notes.append(f"Applying also sets the push force to {result.recommended_push:g} N for testing.")
    return " ".join(notes)


def tune_metric_rows(comparison) -> list[tuple[str, str, str]]:
    """(metric, before, after) of the response comparison of an auto-tune result."""
    old, new = comparison

    def st(m):
        return "did not settle" if m.settling_time is None else f"{m.settling_time:.2f} s"

    return [("Settling time", st(old.metrics), st(new.metrics)),
            ("Overshoot", f"{old.metrics.overshoot_pct:.0f} %", f"{new.metrics.overshoot_pct:.0f} %"),
            ("Max angle", f"{old.metrics.max_deviation:.2f}°", f"{new.metrics.max_deviation:.2f}°"),
            ("Peak force", f"{old.metrics.control_peak:.3g}", f"{new.metrics.control_peak:.3g}"),
            ("Control energy ∫u²", f"{old.metrics.control_energy:.3g}", f"{new.metrics.control_energy:.3g}"),
            ("Fell over", "yes" if old.metrics.fell else "no", "yes" if new.metrics.fell else "no")]
