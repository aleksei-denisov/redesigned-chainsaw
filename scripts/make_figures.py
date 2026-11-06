"""Generate the documentation figures in ``docs/images`` from real simulation runs.

Optional helper – requires matplotlib (not an application dependency)::

    python -m pip install matplotlib
    python scripts/make_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from pendulum_sim.control import AntiWindup  # noqa: E402
from pendulum_sim.physics import MultiLinkPendulum  # noqa: E402
from pendulum_sim.simulation import run_experiment, scenario  # noqa: E402

OUT = ROOT / "docs" / "images"
COLORS = ["#2E86DE", "#E67E22", "#27AE60"]


def windup_comparison() -> None:
    cfg = scenario("windup_demo")
    runs = []
    for mode, label in ((AntiWindup.NONE, "no anti-windup"), (AntiWindup.CLAMP, "clamping"),
                        (AntiWindup.BACK_CALCULATION, "back-calculation")):
        cfg.controller.loops[0].anti_windup = mode
        runs.append(run_experiment(cfg, label))
    fig, axes = plt.subplots(2, 1, figsize=(8, 5.2), sharex=True)
    for r, c in zip(runs, ["#E74C3C", "#2E86DE", "#27AE60"]):
        m = r.metrics
        axes[0].plot(r.data["t"], r.angle_deg(0), color=c,
                     label=f"{r.name}: overshoot {m.overshoot_pct:.0f} %, RMSE {m.rmse:.2f}°")
        axes[1].plot(r.data["t"], r.data["actuator"], color=c)
    axes[0].axhline(0, color="#7F8C8D", lw=0.8, ls="--")
    axes[0].set_ylabel("θ1 [deg]")
    axes[0].legend(fontsize=8)
    axes[0].set_title("Experiment comparison: integral windup (identical initial conditions)")
    axes[1].set_ylabel("motor torque [N·m]")
    axes[1].set_xlabel("time [s]")
    for ax in axes:
        ax.grid(alpha=0.3)
    axes[0].set_xlim(0, 3)
    fig.tight_layout()
    fig.savefig(OUT / "windup_comparison.png", dpi=120)
    plt.close(fig)


def triple_strobe() -> None:
    cfg = scenario("triple_cart_lqr")
    for seg, ang in zip(cfg.plant.segments, np.radians([10.0, 0.0, 0.0])):
        seg.initial_angle = float(ang)
    r = run_experiment(cfg, "triple")
    model = MultiLinkPendulum(cfg.plant)
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10, 4.4), gridspec_kw={"width_ratios": [1.1, 1]})
    times = [0.0, 0.08, 0.16, 0.25, 0.4, 1.0]
    n1 = 4
    for k, tt in enumerate(times):
        i = int(np.searchsorted(r.data["t"], tt))
        state = np.zeros(2 * n1)
        state[0] = r.data["x"][i]
        state[1:n1] = [r.data[f"theta{j + 1}"][i] for j in range(3)]
        kin = model.kinematics(state)
        alpha = 0.25 + 0.75 * k / (len(times) - 1)
        for j in range(3):
            a, b = kin.joints[j], kin.joints[j + 1]
            ax.plot([a[0], b[0]], [a[1], b[1]], color=COLORS[j], lw=4, alpha=alpha, solid_capstyle="round")
        ax.plot(kin.coms[:, 0], kin.coms[:, 1], "o", ms=4, color="black", alpha=alpha)
        ax.add_patch(plt.Rectangle((state[0] - 0.15, -0.14), 0.3, 0.12, color="#34495E", alpha=alpha))
    ax.axhline(-0.17, color="#7F8C8D", lw=2)
    ax.set_aspect("equal")
    ax.set_xlim(-0.6, 0.8)
    ax.set_title("Triple pendulum on a cart recovering from a 10° tilt (strobe, 0–1 s)")
    ax.set_xlabel("x [m]")
    ax.grid(alpha=0.3)
    for j in range(3):
        ax2.plot(r.data["t"], r.angle_deg(j), color=COLORS[j], label=f"θ{j + 1}")
    ax2.set_xlim(0, 2)
    ax2.set_xlabel("time [s]")
    ax2.set_ylabel("angle [deg]")
    ax2.set_title("Multi-loop PID (LQR-seeded gains)")
    ax2.legend()
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "triple_pendulum.png", dpi=120)
    plt.close(fig)


def energy_check() -> None:
    r = run_experiment(scenario("double_free_swing"), "free swing")
    fig, ax = plt.subplots(figsize=(8, 3.2))
    e = r.data["energy"]
    ax.plot(r.data["t"], r.data["ke"], label="kinetic", color="#2E86DE")
    ax.plot(r.data["t"], r.data["pe"], label="potential", color="#27AE60")
    ax.plot(r.data["t"], e, label=f"total (drift {np.ptp(e):.1e} J)", color="#C0392B", lw=2)
    ax.set_xlabel("time [s]")
    ax.set_ylabel("energy [J]")
    ax.set_title("Chaotic frictionless double pendulum: total energy is conserved")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUT / "energy_conservation.png", dpi=120)
    plt.close(fig)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    windup_comparison()
    triple_strobe()
    energy_check()
    print(f"figures written to {OUT.relative_to(ROOT)}")
