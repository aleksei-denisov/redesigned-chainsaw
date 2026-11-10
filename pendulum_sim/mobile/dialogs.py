"""In-window dialogs: questions, messages and reports drawn inside the app's window.

On Android a ``QMessageBox`` or ``QDialog`` is a window of its own (or a native Android
dialog) with a nested event loop. Asking "Leave the simulator?" from the Back key froze
the app without showing anything – the Back key is delivered while Android's UI thread
waits for the app, so a dialog that needs that thread can never appear. These dialogs
are ordinary child widgets over the window (dimmed backdrop, centred card), never block,
and report the chosen button through a callback / the :attr:`OverlayDialog.finished`
signal. Android Back closes the topmost one (``host.go_back``).
"""

from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from . import theme

Button = tuple[str, str, bool]          # (key, text, primary)
OK: Sequence[Button] = (("ok", "OK", True),)


def dialog_host(widget: QWidget) -> QWidget:
    """The widget dialogs cover: the main window (``_dialog_host``) or the top level."""
    w: QWidget | None = widget
    while w is not None:
        if getattr(w, "_dialog_host", False):
            return w
        w = w.parentWidget()
    return widget.window()


class OverlayDialog(QWidget):
    """Card with title, text, an optional body widget and buttons over a dimmed window."""

    finished = Signal(str)              # key of the pressed button; "" = dismissed (Back, tap outside)

    def __init__(self, host: QWidget, title: str, text: str = "", buttons: Sequence[Button] = OK,
                 body: QWidget | None = None, large: bool = False, dismissible: bool = True) -> None:
        super().__init__(host)
        from .controls import push_button

        self.host = host
        self.large = large
        self.dismissible = dismissible
        self.result_key: str | None = None
        self.buttons: dict[str, QWidget] = {}
        self.card = QFrame(self)
        self.card.setObjectName("card")
        self.card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.card.setStyleSheet(f"QFrame#card {{ background: {theme.SURFACE}; border: 1px solid {theme.OUTLINE};"
                                f" border-radius: 16px; }}")
        v = QVBoxLayout(self.card)
        v.setContentsMargins(20, 18, 20, 16)
        v.setSpacing(12)
        self.title_label = QLabel(title)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(f"QLabel {{ font-weight: 600; font-size: {theme.BODY_PX + 3}px; }}")
        v.addWidget(self.title_label)
        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.text_label)
        if not text:
            self.text_label.hide()
        self.body = body
        if body is not None:
            v.addWidget(body, 1)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        for key, label_text, primary in buttons:
            b = push_button(label_text, primary=primary)
            b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            b.setMinimumWidth(110)
            b.clicked.connect(lambda _=False, k=key: self.done(k))
            row.addWidget(b)
            self.buttons[key] = b
        v.addLayout(row)
        self.follow_host()
        self.show()
        self.raise_()

    # ----------------------------------------------------------------- result
    def done(self, key: str) -> None:
        if self.result_key is not None:
            return
        self.result_key = key
        self.hide()
        self.finished.emit(key)
        self.deleteLater()

    def accept(self) -> None:
        primary = next((k for k, b in self.buttons.items() if b.property("primary")), None)
        self.done(primary or next(iter(self.buttons), "ok"))

    def reject(self) -> None:
        self.done("")

    # ----------------------------------------------------------------- layout
    def follow_host(self) -> None:
        r = self.host.rect()
        self.setGeometry(r)
        w = r.width() - 32 if self.large else min(r.width() - 32, 560)
        self.card.setFixedWidth(max(240, w))
        if self.large:
            h = max(240, int(r.height() * 0.88))
        else:
            self.card.adjustSize()
            h = min(self.card.sizeHint().height(), r.height() - 32)
        self.card.setFixedHeight(h)
        self.card.move((r.width() - self.card.width()) // 2, (r.height() - h) // 2)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - tap outside the card
        event.accept()
        if self.dismissible and not self.card.geometry().contains(event.position().toPoint()):
            self.reject()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(QRect(0, 0, self.width(), self.height()), QColor(0, 0, 0, 150))
        p.end()


def top_dialog(host: QWidget) -> OverlayDialog | None:
    shown = [d for d in host.findChildren(OverlayDialog, options=Qt.FindChildOption.FindDirectChildrenOnly)
             if d.isVisible() and d.result_key is None]
    return shown[-1] if shown else None


def ask(widget: QWidget, title: str, text: str, buttons: Sequence[Button],
        then: Callable[[str], None] | None = None, **kwargs) -> OverlayDialog:
    """Show a question; ``then(key)`` gets the pressed button ("" when dismissed)."""
    dlg = OverlayDialog(dialog_host(widget), title, text, buttons, **kwargs)
    if then is not None:
        dlg.finished.connect(then)
    return dlg


def alert(widget: QWidget, title: str, text: str) -> OverlayDialog:
    """A message with OK (replaces ``QMessageBox.warning`` / ``critical`` in the app)."""
    return ask(widget, title, text, OK)
