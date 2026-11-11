"""Start-up journal and crash capture for the Qt Quick app (works without adb).

Every start writes its steps, Python exceptions and Qt warnings to ``start.log`` (one
line at a time, flushed), and native crashes (segfault, abort) to ``fault.log``
through :mod:`faulthandler`. A start that reaches the Simulate screen and keeps running
for a few seconds is marked complete. If the previous start was not, the app starts in
*safe mode* (physics in a thread, no Android performance hints) and shows what was
recorded, with a button to copy it.

Files live in the app's private storage on Android (``ANDROID_PRIVATE``) and in
``~/.cache/pendulum-quick`` elsewhere.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import threading
import time
import traceback
from pathlib import Path

COMPLETE = "== start complete =="


def folder() -> Path:
    base = os.environ.get("ANDROID_PRIVATE") or os.environ.get("PENDULUM_QUICK_DIAG")
    path = Path(base) if base else Path.home() / ".cache" / "pendulum-quick"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


class Journal:
    """The current start's log, and what the previous start left behind."""

    def __init__(self, path: Path | None = None) -> None:
        self.dir = path or folder()
        self.log_path = self.dir / "start.log"
        self.fault_path = self.dir / "fault.log"
        self.previous = self._read(self.log_path)
        self.previous_fault = self._read(self.fault_path)
        self.previous_failed = bool(self.previous.strip()) and COMPLETE not in self.previous
        self._t0 = time.monotonic()
        self._lock = threading.Lock()
        self._log = self._open(self.log_path)
        self._fault = self._open(self.fault_path)
        if self._fault is not None:
            try:
                faulthandler.enable(file=self._fault, all_threads=True)
            except (OSError, ValueError, RuntimeError):
                pass
        self.write(f"start {time.strftime('%Y-%m-%d %H:%M:%S')}, Python {sys.version.split()[0]} on {sys.platform}")

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @staticmethod
    def _open(path: Path):
        try:
            return open(path, "w", encoding="utf-8", buffering=1)   # line buffered
        except OSError:
            return None

    def write(self, text: str) -> None:
        line = f"{time.monotonic() - self._t0:7.3f}  {text}"
        with self._lock:
            if self._log is not None:
                try:
                    self._log.write(line + "\n")
                    self._log.flush()
                except (OSError, ValueError):
                    pass

    def exception(self, where: str, exc_info=None) -> None:
        text = "".join(traceback.format_exception(*(exc_info or sys.exc_info())))
        self.write(f"EXCEPTION in {where}:\n{text}")

    def complete(self) -> None:
        self.write(COMPLETE)

    def report(self) -> str:
        """What the previous (failed) start recorded."""
        parts = ["The previous start did not finish. What it recorded:", "", self.previous.strip() or "(nothing)"]
        if self.previous_fault.strip():
            parts += ["", "Native crash (faulthandler):", self.previous_fault.strip()]
        return "\n".join(parts)

    # ----------------------------------------------------------- hooks
    def install_hooks(self) -> None:
        """Python exceptions (main and other threads) go to the journal too."""
        previous_hook = sys.excepthook

        def hook(*exc_info) -> None:
            self.exception("Python (uncaught)", exc_info)
            previous_hook(*exc_info)

        sys.excepthook = hook

        def thread_hook(args) -> None:
            self.exception(f"thread {getattr(args.thread, 'name', '?')}",
                           (args.exc_type, args.exc_value, args.exc_traceback))

        threading.excepthook = thread_hook

    def install_qt_handler(self) -> None:
        """Qt's warnings, criticals and fatal messages (written before Qt aborts)."""
        try:
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler
        except ImportError:
            return
        names = {QtMsgType.QtDebugMsg: "debug", QtMsgType.QtInfoMsg: "info", QtMsgType.QtWarningMsg: "warning",
                 QtMsgType.QtCriticalMsg: "critical", QtMsgType.QtFatalMsg: "FATAL"}

        def handler(kind, context, message) -> None:
            if kind != QtMsgType.QtDebugMsg:
                self.write(f"Qt {names.get(kind, kind)}: {message}")
            print(message, file=sys.stderr, flush=True)

        self._qt_handler = handler                       # keep a reference
        qInstallMessageHandler(handler)
