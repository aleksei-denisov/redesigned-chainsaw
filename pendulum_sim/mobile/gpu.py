"""Optional GPU drawing for the pendulum view and the graphs (*Settings → Drawing*).

Qt draws widgets in software into one window-sized image; on Android every frame
that changes any software-drawn widget is handed to the display by copying that whole
image (≈ 15 MB on the Pad 6, ~10 ms per frame in the performance test). A
``QOpenGLWidget`` is drawn by the GPU into its own texture and composed by Qt; when
only such widgets change, Qt flushes the window with an *empty* software region and the
copy is skipped (``QWidgetRepaintManager::flush`` → ``flush(tlw, QRegion(), textures)``).

:class:`GLSurface` is a ``QOpenGLWidget`` that covers its host widget (the pendulum view
or a strip chart) and calls the host's own ``QPainter`` code in ``paintGL`` – the same
drawing, executed by Qt's OpenGL paint engine. Touches pass through to the host. If no
OpenGL context can be created the host is told and falls back to software drawing.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QSurfaceFormat
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
except ImportError:  # pragma: no cover - module missing in a stripped Qt build
    QOpenGLWidget = None

DRAWING_MODES = [("software", "Software"), ("gpu", "GPU (OpenGL)")]


ERRORS = {"count": 0}           # exceptions raised while drawing on the GPU (shown in the report)


def available() -> bool:
    """OpenGL widgets present and a platform that can create a GL context.

    The ``offscreen``/``minimal`` platforms (tests, CI screenshots) get software drawing;
    ``PENDULUM_FORCE_GPU=1`` overrides that.
    """
    if QOpenGLWidget is None:
        return False
    import os

    from PySide6.QtGui import QGuiApplication

    platform = QGuiApplication.platformName() if QGuiApplication.instance() is not None else ""
    return platform not in ("offscreen", "minimal") or os.environ.get("PENDULUM_FORCE_GPU") == "1"


if QOpenGLWidget is not None:

    class GLSurface(QOpenGLWidget):
        """GPU canvas covering ``host``; ``paint(p)`` draws the host's content."""

        def __init__(self, host: QWidget, paint: Callable[[QPainter], None], samples: int = 0,
                     on_failure: Callable[[], None] | None = None) -> None:
            super().__init__(host)
            fmt = QSurfaceFormat()
            fmt.setSamples(max(0, samples))          # multisampling = antialiasing on the GPU
            fmt.setDepthBufferSize(24)               # depth/stencil: what Qt's GL paint engine
            fmt.setStencilBufferSize(8)              # uses for complex fills and clipping
            self.setFormat(fmt)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._paint = paint
            self._on_failure = on_failure
            self.failed = False
            self.samples = samples
            self.setGeometry(host.rect())
            self.show()

        def initializeGL(self) -> None:  # noqa: N802 - Qt API
            ctx = self.context()
            if ctx is None or not ctx.isValid():
                self._fail()

        def paintGL(self) -> None:  # noqa: N802 - Qt API
            if self.failed:
                return
            p = QPainter(self)
            if not p.isActive():
                self._fail()
                return
            try:
                self._paint(p)
            except Exception:  # noqa: BLE001 - report once, keep the app running
                ERRORS["count"] += 1
                if ERRORS["count"] <= 3:
                    import traceback

                    print("GPU drawing error:\n" + traceback.format_exc(), flush=True)
            finally:
                p.end()

        def _fail(self) -> None:
            if not self.failed:
                self.failed = True
                if self._on_failure is not None:
                    from PySide6.QtCore import QTimer

                    QTimer.singleShot(0, self._on_failure)

else:  # pragma: no cover
    GLSurface = None


class GPUHost:
    """Mixin for a widget that can draw through a :class:`GLSurface` child.

    The host keeps its own ``paintEvent`` for software drawing. With a surface, its
    ``update()`` repaints the surface only, so no software region becomes dirty.
    Subclasses implement ``paint_gpu(p)`` and call ``_resize_surface()`` on resize.
    """

    gpu_surface = None
    gpu_failed = False
    gpu_fallback = None          # optional callback after a failed start

    def set_gpu(self, on: bool, samples: int = 0) -> bool:
        """Switch GPU drawing on/off; returns whether it is on."""
        want = bool(on) and available() and not self.gpu_failed
        cur = self.gpu_surface
        if cur is not None and (not want or cur.samples != samples):
            cur.hide()
            cur.deleteLater()
            self.gpu_surface = None
        if want and self.gpu_surface is None:
            self.gpu_surface = GLSurface(self, self.paint_gpu, samples, on_failure=self._gpu_failed)
        QWidget.update(self)
        return self.gpu_surface is not None

    @property
    def gpu(self) -> bool:
        return self.gpu_surface is not None and not self.gpu_surface.failed

    def _gpu_failed(self) -> None:
        self.gpu_failed = True
        self.set_gpu(False)
        if self.gpu_fallback is not None:
            self.gpu_fallback()

    def _resize_surface(self) -> None:
        if self.gpu_surface is not None:
            self.gpu_surface.setGeometry(self.rect())

    def update(self, *args) -> None:
        """Repaint: the GPU surface when present (keeps the software region clean)."""
        if self.gpu_surface is not None and not self.gpu_surface.failed:
            self.gpu_surface.update()
        else:
            QWidget.update(self, *args)

    def paint_gpu(self, p: QPainter) -> None:  # pragma: no cover - implemented by hosts
        raise NotImplementedError
