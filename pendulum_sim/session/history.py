"""Undo / redo of parameter edits (Qt-free, shared by every interface).

An :class:`EditHistory` keeps snapshots – deep copies of whatever a screen edits (the
controller, or plant + simulation settings + controller) – taken *before* each edit.
:meth:`EditHistory.undo` hands back the previous snapshot and remembers the current
state for :meth:`EditHistory.redo`; a new edit clears the redo list. The caller applies
a snapshot through the :class:`~.session.Session` like any other edit, so the edit rules
(e.g. loops completed for the current structure) still hold.
"""

from __future__ import annotations

import copy
from typing import Any

LIMIT = 100


class EditHistory:
    def __init__(self, limit: int = LIMIT) -> None:
        self.limit = limit
        self._undo: list[Any] = []
        self._redo: list[Any] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def record(self, before: Any) -> None:
        """The state before an edit (copied); a new edit makes redo impossible."""
        self._undo.append(copy.deepcopy(before))
        del self._undo[:-self.limit]
        self._redo.clear()

    def undo(self, current: Any) -> Any | None:
        if not self._undo:
            return None
        self._redo.append(copy.deepcopy(current))
        return self._undo.pop()

    def redo(self, current: Any) -> Any | None:
        if not self._redo:
            return None
        self._undo.append(copy.deepcopy(current))
        return self._redo.pop()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
