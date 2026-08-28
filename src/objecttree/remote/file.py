"""SQLite-backed fast-forward remote."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..exceptions import CorruptStoreError, NonFastForwardError, RemoteError
from ..models import PushResult, RemotePack
from .base import accept_push, remote_pack_from_payload, remote_pack_to_payload


class FileRemote:
    """Persist remote commits in the SQLite file at *path*."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def fetch(self) -> RemotePack:
        connection = self._connect()
        try:
            row = connection.execute("SELECT payload FROM remote WHERE id = 1").fetchone()
        except sqlite3.Error as exc:
            raise RemoteError(f"cannot read remote {self.path}") from exc
        finally:
            connection.close()
        if row is None:
            return RemotePack((), None)
        return self._decode(row[0])

    def push(self, pack: RemotePack) -> PushResult:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT payload FROM remote WHERE id = 1").fetchone()
            current = self._decode(row[0]) if row is not None else RemotePack((), None)
            existing = {commit.id: commit for commit in current.commits}
            combined, result = accept_push(existing, current.head, pack)
            payload = remote_pack_to_payload(
                RemotePack(tuple(combined.values()), result.remote_head)
            )
            text = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if row is None:
                connection.execute("INSERT INTO remote(id, payload) VALUES (1, ?)", (text,))
            else:
                connection.execute("UPDATE remote SET payload = ? WHERE id = 1", (text,))
            connection.commit()
            return result
        except NonFastForwardError:
            connection.rollback()
            raise
        except (sqlite3.Error, TypeError, ValueError) as exc:
            connection.rollback()
            raise RemoteError(f"cannot write remote {self.path}") from exc
        finally:
            connection.close()

    def _decode(self, text: str) -> RemotePack:
        try:
            payload = json.loads(text, parse_constant=_reject_constant)
            return remote_pack_from_payload(payload)
        except (json.JSONDecodeError, TypeError, ValueError, CorruptStoreError) as exc:
            raise RemoteError(f"remote {self.path} is corrupt") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=30)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS remote (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload TEXT NOT NULL
                )
                """
            )
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise RemoteError(f"cannot open remote {self.path}") from exc


def _reject_constant(value: str) -> object:
    raise ValueError(f"invalid JSON numeric constant: {value}")
