"""In-memory fast-forward remote."""

from __future__ import annotations

from threading import RLock

from ..models import Commit, PushResult, RemotePack
from ..state import clone_commit
from .base import accept_push


class MemoryRemote:
    def __init__(self) -> None:
        self._commits: dict[str, Commit] = {}
        self._head: str | None = None
        self._lock = RLock()

    def fetch(self) -> RemotePack:
        with self._lock:
            return RemotePack(
                tuple(clone_commit(self._commits[key]) for key in sorted(self._commits)),
                self._head,
            )

    def push(self, pack: RemotePack) -> PushResult:
        with self._lock:
            commits, result = accept_push(self._commits, self._head, pack)
            self._commits = commits
            self._head = result.remote_head
            return result
