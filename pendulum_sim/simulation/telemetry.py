"""Column-oriented telemetry recorder backed by NumPy arrays."""

from __future__ import annotations

import numpy as np

MAX_LOOPS = 4  # cart + three segments

COLUMNS: list[str] = (
    ["t", "x", "xd"]
    + [f"theta{i}" for i in (1, 2, 3)]
    + [f"omega{i}" for i in (1, 2, 3)]
    + ["command", "unsaturated", "actuator", "tip_force", "ke", "pe", "energy"]
    + [f"{kind}{k}" for k in range(MAX_LOOPS) for kind in ("error", "p", "i", "d")]
)
INDEX = {name: i for i, name in enumerate(COLUMNS)}


class TelemetryRecorder:
    """Append-only sample store.

    ``max_samples`` bounds memory for live display: when the buffer grows past
    twice that size the oldest samples are discarded (amortised O(1)).
    ``None`` keeps everything (experiments).
    """

    def __init__(self, max_samples: int | None = None) -> None:
        self.max_samples = max_samples
        self.loop_names: list[str] = []
        self.clear()

    def clear(self) -> None:
        self._data = np.full((1024, len(COLUMNS)), np.nan)
        self._size = 0
        self.version = 0  # incremented on every append (lets the UI skip redraws)

    def __len__(self) -> int:
        return self._size

    def append(self, row: np.ndarray) -> None:
        if self.max_samples is not None and self._size >= 2 * self.max_samples:
            keep = self.max_samples
            self._data[:keep] = self._data[self._size - keep : self._size]
            self._size = keep
        if self._size == self._data.shape[0]:
            grown = np.full((self._data.shape[0] * 2, len(COLUMNS)), np.nan)
            grown[: self._size] = self._data[: self._size]
            self._data = grown
        self._data[self._size] = row
        self._size += 1
        self.version += 1

    def column(self, name: str) -> np.ndarray:
        return self._data[: self._size, INDEX[name]]

    def array(self) -> np.ndarray:
        return self._data[: self._size].copy()

    def as_dict(self) -> dict[str, np.ndarray]:
        return {name: self._data[: self._size, i].copy() for i, name in enumerate(COLUMNS)}
