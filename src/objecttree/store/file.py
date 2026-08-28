"""SQLite-backed local ObjectStore."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..exceptions import ConcurrentWriteError, CorruptStoreError, StoreError
from .base import StoredDocument


class FileStore:
    """Store one repository document in ``<path>/objecttree.sqlite3``.

    SQLite transactions prevent partial writes. A generation check detects a
    second ObjectTree instance that loaded an older document.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.database_path = self.path / "objecttree.sqlite3"

    def load(self) -> StoredDocument:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT generation, payload FROM repository WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise StoreError(f"cannot read {self.database_path}") from exc
        finally:
            connection.close()
        if row is None:
            return StoredDocument(None, 0)
        generation, text = row
        try:
            payload = json.loads(text, parse_constant=_reject_constant)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise CorruptStoreError(f"invalid repository JSON in {self.database_path}") from exc
        if not isinstance(payload, dict):
            raise CorruptStoreError("repository JSON must contain an object")
        return StoredDocument(payload, int(generation))

    def save(self, payload: dict[str, Any], *, expected_generation: int) -> int:
        try:
            text = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise StoreError("repository payload is not valid JSON data") from exc

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT generation FROM repository WHERE id = 1").fetchone()
            current = int(row[0]) if row is not None else 0
            if current != expected_generation:
                raise ConcurrentWriteError(
                    f"store generation is {current}, expected {expected_generation}"
                )
            next_generation = current + 1
            if row is None:
                connection.execute(
                    "INSERT INTO repository(id, generation, payload) VALUES (1, ?, ?)",
                    (next_generation, text),
                )
            else:
                connection.execute(
                    "UPDATE repository SET generation = ?, payload = ? WHERE id = 1",
                    (next_generation, text),
                )
            connection.commit()
            return next_generation
        except ConcurrentWriteError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StoreError(f"cannot write {self.database_path}") from exc
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path, timeout=30)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS repository (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    generation INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise StoreError(f"cannot open {self.database_path}") from exc


def _reject_constant(value: str) -> object:
    raise ValueError(f"invalid JSON numeric constant: {value}")
