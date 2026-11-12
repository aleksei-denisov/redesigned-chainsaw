"""Catalogue of the signals a value or graph can show (Qt-free).

Each signal is one telemetry column (``sim_source.INDEX``) with a label, a display unit
and a conversion, or one of the run-time values (simulation time, real-time factor,
frame rate). The catalogue depends on the structure: segments, cart, controller loops
(``catalogue``). Angles and angular velocities can be shown in degrees or radians.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEG = 180.0 / math.pi
SPECIAL = ("time", "rtf", "fps")          # not telemetry columns (value tiles only)


@dataclass(frozen=True)
class Signal:
    key: str
    label: str                 # short (legends, axis)
    group: str
    unit: str                  # unit at scale 1 (radians for angular signals)
    name: str = ""             # long (value tiles, pickers)
    angular: bool = False      # rad → deg conversion available
    wrap: bool = False         # an angle: wrap to (−180°, 180°]
    min_span: float = 1.0      # smallest axis span, in the display unit (degrees for angles)
    dashed: bool = False
    graphable: bool = True
    decimals: int = 2

    def display(self, angle_unit: str = "deg") -> tuple[float, str, float, int]:
        """(scale from telemetry, unit text, minimum span, decimals) in ``angle_unit``."""
        if self.angular and angle_unit == "deg":
            unit = "°" if self.unit == "rad" else "°/s"
            return DEG, unit, self.min_span, self.decimals
        if self.angular:
            return 1.0, self.unit, self.min_span / DEG, self.decimals + 2
        return 1.0, self.unit, self.min_span, self.decimals

    def to_dict(self, angle_unit: str = "deg") -> dict:
        _scale, unit, _span, _dec = self.display(angle_unit)
        return {"key": self.key, "label": self.label, "name": self.name or self.label, "group": self.group,
                "unit": unit, "graphable": self.graphable, "angular": self.angular}


def catalogue(n_segments: int, has_cart: bool, loop_labels: list[str]) -> list[Signal]:
    """The signals of the current structure, grouped, in display order."""
    out: list[Signal] = []
    for i in range(n_segments):
        out.append(Signal(f"theta{i + 1}", f"θ{i + 1}", "Angles", "rad", f"Angle θ{i + 1}", angular=True,
                          wrap=True, min_span=2.0, decimals=2))
    for i in range(n_segments):
        out.append(Signal(f"omega{i + 1}", f"ω{i + 1}", "Angular velocities", "rad/s", f"Rate ω{i + 1}",
                          angular=True, min_span=20.0, decimals=1))
    if has_cart:
        out.append(Signal("x", "x", "Cart", "m", "Cart x", min_span=0.05, decimals=3))
        out.append(Signal("xd", "ẋ", "Cart", "m/s", "Cart velocity ẋ", min_span=0.1, decimals=3))
    out += [Signal("actuator", "F", "Control", "N", "Force F", min_span=1.0, decimals=2),
            Signal("command", "F cmd", "Control", "N", "Force command", min_span=1.0, decimals=2),
            Signal("unsaturated", "F unsat", "Control", "N", "Force unsaturated", min_span=1.0, decimals=2),
            Signal("tip_force", "push", "Control", "N", "Push force", min_span=1.0, decimals=2, dashed=True)]
    for k, label in enumerate(loop_labels):
        name = label or f"loop {k + 1}"
        cart = name == "cart"
        out.append(Signal(f"error{k}", f"e {name}", "Control error", "m" if cart else "rad", f"Error {name}",
                          angular=not cart, min_span=0.05 if cart else 2.0, decimals=3 if cart else 2))
    for k, label in enumerate(loop_labels):
        name = label or f"loop {k + 1}"
        for term in ("p", "i", "d"):
            out.append(Signal(f"{term}{k}", f"{term.upper()} {name}", "PID terms", "N",
                              f"{term.upper()} term {name}", min_span=0.5, decimals=2))
    out += [Signal("ke", "E kin", "Energy", "J", "Kinetic energy", min_span=0.02, decimals=3),
            Signal("pe", "E pot", "Energy", "J", "Potential energy", min_span=0.02, decimals=3),
            Signal("energy", "E total", "Energy", "J", "Total energy", min_span=0.02, decimals=3)]
    out += [Signal("time", "T", "Simulation", "s", "Time T", graphable=False, decimals=2),
            Signal("rtf", "speed", "Simulation", "×", "Speed (real time)", graphable=False, decimals=2),
            Signal("fps", "fps", "Simulation", "fps", "Frame rate", graphable=False, decimals=0)]
    return out


def by_key(signals: list[Signal]) -> dict[str, Signal]:
    return {s.key: s for s in signals}


def format_value(value: float, decimals: int, signed: bool = True) -> str:
    if not math.isfinite(value):
        return "—"
    return f"{value:+.{decimals}f}" if signed else f"{value:.{decimals}f}"
