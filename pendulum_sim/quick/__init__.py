"""Qt Quick (QML) version of the mobile app – "Instrument" design.

The UI is drawn by Qt's GPU scene graph on its own render thread; Python only
supplies state. Physics and control run off the GUI thread (``backend/sim_source.py``:
a thread, or a forked process with shared memory) on the unchanged simulation core.
Entry point: ``python -m pendulum_sim.quick``. See docs/ANDROID_QTQUICK.md.
"""
