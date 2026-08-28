"""Thread-safe in-memory ObjectStore."""

from __future__ import annotations

from copy import deepcopy
from threading import RLock
from typing import Any

from ..exceptions import ConcurrentWriteError
from .base import StoredDocument


class MemoryStore:
    def __init__(self) -> None:
        self._payload: dict[str, Any] | None = None
        self._generation = 0
        self._lock = RLock()

    def load(self) -> StoredDocument:
        with self._lock:
            return StoredDocument(deepcopy(self._payload), self._generation)

    def save(self, payload: dict[str, Any], *, expected_generation: int) -> int:
        with self._lock:
            if expected_generation != self._generation:
                raise ConcurrentWriteError(
                    f"store generation is {self._generation}, expected {expected_generation}"
                )
            self._payload = deepcopy(payload)
            self._generation += 1
            return self._generation
