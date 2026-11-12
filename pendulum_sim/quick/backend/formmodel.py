"""A shared parameter form (``pendulum_sim/session/forms.py``) as a QML list model.

Rows are section headers and fields, in screen order; hidden sections (the cart on a
fixed pivot, unused segments) are left out. QML shows each row with a delegate chosen
by ``kind`` and edits with :meth:`FormModel.setValue`; the owner (a bridge) decides what
an edit means and calls :meth:`refresh` / :meth:`update_values` afterwards.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Qt, QTimer, Signal, Slot

from ...session import sliders
from ...session.forms import Form

ROLES = ["rowType", "key", "label", "kind", "unit", "decimals", "minimum", "maximum", "mapping", "step",
         "hint", "options", "value", "editable", "section", "expanded", "note"]
ROLE_IDS = {name: Qt.ItemDataRole.UserRole + 1 + i for i, name in enumerate(ROLES)}


class FormModel(QAbstractListModel):
    edited = Signal(str, "QVariant")          # key, value – emitted by setValue for the owner

    def __init__(self, form: Form, on_edit: Callable[[str, object], None],
                 visible: Callable[[str], bool] | None = None,
                 editable: Callable[[str], bool] | None = None,
                 note: Callable[[str], str] | None = None, expanded: dict[str, bool] | None = None,
                 parent=None) -> None:
        super().__init__(parent)
        self.form = form
        self.on_edit = on_edit
        self._visible = visible or (lambda _section: True)
        self._editable = editable or (lambda _key: True)
        self._note = note
        self._expanded: dict[str, bool] = dict(expanded or {})     # overrides of the sections' defaults
        self._rows: list[tuple[str, object]] = []      # ("section", Section) | ("field", (Field, section key))
        self._index: dict[str, int] = {}
        self.refresh()

    # ---------------------------------------------------------- structure
    def refresh(self) -> None:
        """Rebuild the rows (sections shown, loops rebuilt …)."""
        self.beginResetModel()
        rows: list[tuple[str, object]] = []
        for section in self.form.sections:
            if not self._visible(section.key):
                continue
            expanded = self._expanded.setdefault(section.key, section.expanded)
            rows.append(("section", section))
            if expanded:
                rows.extend(("field", (f, section.key)) for f in section.fields)
        self._rows = rows
        self._index = {row[1][0].key: i for i, row in enumerate(rows) if row[0] == "field"}
        self.endResetModel()

    def update_values(self) -> None:
        """Values changed from outside (configuration loaded, LQR seeding …)."""
        if self._rows:
            self.dataChanged.emit(self.index(0), self.index(len(self._rows) - 1),
                                  [ROLE_IDS["value"], ROLE_IDS["editable"], ROLE_IDS["note"]])

    # ---------------------------------------------------------- Qt model
    def roleNames(self):  # noqa: N802 - Qt API
        return {rid: QByteArray(name.encode()) for name, rid in ROLE_IDS.items()}

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802, B008 - Qt API
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        kind, item = self._rows[index.row()]
        if kind == "section":
            section = item
            return {
                ROLE_IDS["rowType"]: "section", ROLE_IDS["key"]: section.key, ROLE_IDS["label"]: section.title,
                ROLE_IDS["section"]: section.key, ROLE_IDS["expanded"]: self._expanded.get(section.key, True),
                ROLE_IDS["note"]: self._note(section.key) if self._note else section.note,
            }.get(role, None)
        field, section_key = item
        if role == ROLE_IDS["rowType"]:
            return "field"
        if role == ROLE_IDS["value"]:
            return self.form.values.get(field.key)
        if role == ROLE_IDS["editable"]:
            return self._editable(field.key)
        if role == ROLE_IDS["options"]:
            return [{"value": v, "text": t} for v, t in field.options]
        if role == ROLE_IDS["section"]:
            return section_key
        if role == ROLE_IDS["expanded"]:
            return True
        if role == ROLE_IDS["note"]:
            return ""
        if role == ROLE_IDS["step"]:
            return field.step if field.step is not None else 0.0
        name = ROLES[role - Qt.ItemDataRole.UserRole - 1] if role > Qt.ItemDataRole.UserRole else None
        return getattr(field, name, None) if name else None

    # --------------------------------------------------------------- QML
    # Edits from QML take effect once control is back in the event loop: an edit can
    # rebuild the rows (loops, segments), and rebuilding delegates inside the QML handler
    # that asked for it is what crashed the side panel.
    def _later(self, action) -> None:
        QTimer.singleShot(0, self, action)

    @Slot(str, "QVariant")
    def setValue(self, key: str, value) -> None:  # noqa: N802 - QML API
        if key in self.form.fields:
            self._later(lambda: self.apply(key, value))

    def apply(self, key: str, value) -> None:
        """An edit, at once (tests, Python callers)."""
        if key in self.form.fields:
            self.on_edit(key, value)
            self.edited.emit(key, value)

    @Slot(str, bool)
    def setExpanded(self, section_key: str, on: bool) -> None:  # noqa: N802 - QML API
        def change() -> None:
            if self._expanded.get(section_key) != on:
                self._expanded[section_key] = on
                self.refresh()
        self._later(change)

    @Slot(str, result="QVariant")
    def value(self, key: str):
        return self.form.values.get(key)

    # ------------------------------------------------- sliders (shared rules)
    def _field(self, key: str):
        return self.form.fields.get(key)

    @Slot(str, float, result=float)
    def position(self, key: str, value: float) -> float:
        """Slider position (0 … 1) of ``value`` for the field ``key`` (session.sliders)."""
        f = self._field(key)
        if f is None:
            return 0.0
        try:
            return min(max(sliders.to_position(float(value), f.minimum, f.maximum, f.mapping, f.decimals), 0.0), 1.0)
        except (ValueError, ZeroDivisionError):
            return 0.0

    @Slot(str, float, result=float)
    def valueAt(self, key: str, t: float) -> float:  # noqa: N802 - QML API
        """The value at slider position ``t``, rounded to the field's decimals and clamped."""
        f = self._field(key)
        if f is None:
            return 0.0
        v = sliders.from_position(min(max(float(t), 0.0), 1.0), f.minimum, f.maximum, f.mapping, f.decimals)
        return min(max(round(v, f.decimals), f.minimum), f.maximum)

    @Slot(str, int, result=float)
    def nudged(self, key: str, direction: int) -> float:
        """The value after one tap on − / +, rounded and clamped (not yet applied)."""
        f = self._field(key)
        v = self.form.values.get(key)
        if f is None or not isinstance(v, (int, float)):
            return 0.0
        new = sliders.nudge(float(v), int(direction), f.minimum, f.maximum, f.mapping, f.decimals, f.step)
        return min(max(round(new, f.decimals), f.minimum), f.maximum)
