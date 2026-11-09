"""Touch-first interface for phones and tablets (used by the Android build).

Reuses the shared physics/control/simulation layers and the desktop QPainter
pendulum view unchanged; window layout, input handling, graphs and the
experiment page are mobile-specific (QPainter, no pyqtgraph).
Desktop preview: ``python -m pendulum_sim.mobile``.
"""
