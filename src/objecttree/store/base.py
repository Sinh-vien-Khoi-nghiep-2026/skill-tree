"""Local persistence protocol."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredDocument:
    payload: dict[str, Any] | None
    generation: int


@runtime_checkable
class ObjectStore(Protocol):
    """Optimistic-concurrency store for one repository document."""

    def load(self) -> StoredDocument: ...

    def save(self, payload: dict[str, Any], *, expected_generation: int) -> int: ...
