"""Android performance hints and thermal status through the NDK (Qt-free, ctypes).

PySide6 has no JNI bindings, so the app talks to the same system services through
their native C API in ``libandroid.so``:

* ``APerformanceHint`` (API 33+, the native side of ``PerformanceHintManager``): a hint
  session for the GUI thread and Qt's render thread with the frame interval as target
  work duration; each frame reports how long its work took, so the system raises or
  lowers CPU clocks to just meet the frame rate. No CPU affinity is set.
* ``AThermal_getCurrentThermalStatus`` (API 30+): the device's thermal state for the
  performance report.

The physics runs in a forked child process. A child of an Android app must not use
binder (it shares the parent's binder connection), so the physics process gets no hint
session; it is light (about half a core) and the scheduler's own boosting covers it.

Everything degrades to a no-op where the library or a function is missing (desktop,
older Android).
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading

IS_ANDROID = hasattr(sys, "getandroidapilevel") or "ANDROID_ARGUMENT" in os.environ

THERMAL_STATUS = {
    -1: "error", 0: "none", 1: "light", 2: "moderate", 3: "severe", 4: "critical", 5: "emergency", 6: "shutdown",
}


def api_level() -> int:
    get = getattr(sys, "getandroidapilevel", None)
    return int(get()) if get else 0


def _library():
    if not IS_ANDROID:
        return None
    try:
        return ctypes.CDLL("libandroid.so")
    except OSError:
        return None


def _function(lib, name: str, restype, argtypes):
    if lib is None:
        return None
    try:
        fn = getattr(lib, name)
    except AttributeError:
        return None
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


class PerformanceHints:
    """One hint session for the given threads (``available`` is ``False`` where unsupported)."""

    def __init__(self, tids: list[int], target_ms: float, lib=None) -> None:
        self.reason = ""
        self.session = None
        self.tids = [int(t) for t in tids if t]
        self.target_ms = float(target_ms)
        self.reports = 0
        lib = lib if lib is not None else _library()
        if lib is None:
            self.reason = "not Android" if not IS_ANDROID else "libandroid.so not loadable"
            return
        get_manager = _function(lib, "APerformanceHint_getManager", ctypes.c_void_p, [])
        create = _function(lib, "APerformanceHint_createSession", ctypes.c_void_p,
                           [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32), ctypes.c_size_t, ctypes.c_int64])
        self._report = _function(lib, "APerformanceHint_reportActualWorkDuration", ctypes.c_int,
                                 [ctypes.c_void_p, ctypes.c_int64])
        self._update = _function(lib, "APerformanceHint_updateTargetWorkDuration", ctypes.c_int,
                                 [ctypes.c_void_p, ctypes.c_int64])
        self._close = _function(lib, "APerformanceHint_closeSession", None, [ctypes.c_void_p])
        if None in (get_manager, create, self._report, self._update, self._close):
            self.reason = f"API {api_level()}: performance hints need Android 13 (API 33)"
            return
        manager = get_manager()
        if not manager or not self.tids:
            self.reason = "no hint manager on this device" if not manager else "no threads"
            return
        array = (ctypes.c_int32 * len(self.tids))(*self.tids)
        session = create(manager, array, len(self.tids), int(self.target_ms * 1e6))
        if not session:
            self.reason = "the system refused a hint session (hints disabled on this device?)"
            return
        self.session = session

    @property
    def available(self) -> bool:
        return self.session is not None

    def describe(self) -> str:
        if self.available:
            return (f"active for {len(self.tids)} thread{'s' if len(self.tids) != 1 else ''}, "
                    f"target {self.target_ms:.2f} ms, {self.reports} reports")
        return f"off ({self.reason})"

    def report(self, work_ms: float) -> None:
        """Actual work of the last frame."""
        if self.session is not None and work_ms > 0:
            self._report(self.session, int(work_ms * 1e6))
            self.reports += 1

    def set_target(self, target_ms: float) -> None:
        if self.session is not None and abs(target_ms - self.target_ms) > 0.01:
            self.target_ms = float(target_ms)
            self._update(self.session, int(target_ms * 1e6))

    def close(self) -> None:
        if self.session is not None:
            self._close(self.session)
            self.session = None
            self.reason = "closed"


def thermal_status(lib=None) -> str:
    """``none`` … ``shutdown`` (Android 11+), or ``unknown``."""
    lib = lib if lib is not None else _library()
    acquire = _function(lib, "AThermal_acquireManager", ctypes.c_void_p, [])
    status = _function(lib, "AThermal_getCurrentThermalStatus", ctypes.c_int, [ctypes.c_void_p])
    release = _function(lib, "AThermal_releaseManager", None, [ctypes.c_void_p])
    if None in (acquire, status, release):
        return "unknown"
    manager = acquire()
    if not manager:
        return "unknown"
    try:
        return THERMAL_STATUS.get(int(status(manager)), "unknown")
    finally:
        release(manager)


def gui_thread_id() -> int:
    """Kernel thread id of Python's main thread (Qt's GUI thread in this app)."""
    return int(threading.main_thread().native_id or 0)
