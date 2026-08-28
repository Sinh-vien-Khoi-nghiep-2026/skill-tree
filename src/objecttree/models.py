"""Immutable public and persistence models used by ObjectTree."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from itertools import chain
from typing import Any

NodeId = str
CommitId = str


@dataclass(frozen=True, slots=True)
class TreeNode[T]:
    """An immutable public view of a normalized node."""

    id: NodeId
    name: str
    value: T | None
    metadata: Mapping[str, Any]
    parent_id: NodeId | None
    path: str
    created_at: datetime
    updated_at: datetime
    tags: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class StoredNode:
    """A serializer-neutral node record used in commits and persistence.

    ``value`` and ``metadata`` contain JSON-compatible SerializerRegistry
    envelopes. Applications normally interact with :class:`TreeNode` instead.
    """

    id: NodeId
    name: str
    parent_id: NodeId | None
    value: Any
    metadata: Any
    tags: tuple[str, ...]
    created_at: datetime
    updated_at: datetime


class ChangeKind(StrEnum):
    ADD = "add"
    REMOVE = "remove"
    UPDATE = "update"
    MOVE = "move"


@dataclass(frozen=True, slots=True)
class FieldDelta:
    """One semantic field change within a node value or metadata.

    Presence flags distinguish an absent key from a real ``None`` (or any
    string that merely looks like a missing-value marker).
    """

    field: str
    before: Any
    after: Any
    before_exists: bool = True
    after_exists: bool = True


@dataclass(frozen=True, slots=True)
class Change:
    """A replayable semantic node change."""

    kind: ChangeKind
    node_id: NodeId
    old_path: str | None
    new_path: str | None
    before: StoredNode | None = field(default=None, repr=False)
    after: StoredNode | None = field(default=None, repr=False)
    deltas: tuple[FieldDelta, ...] = ()

    @property
    def path(self) -> str | None:
        return self.new_path if self.new_path is not None else self.old_path


@dataclass(frozen=True, slots=True)
class Commit:
    id: CommitId
    parent: CommitId | None
    timestamp: datetime
    message: str
    author: str | None
    changes: tuple[Change, ...]


@dataclass(frozen=True, slots=True)
class TreeDiff:
    """A semantic diff grouped by operation category."""

    added: tuple[Change, ...] = ()
    removed: tuple[Change, ...] = ()
    updated: tuple[Change, ...] = ()
    moved: tuple[Change, ...] = ()

    def __iter__(self) -> Iterator[Change]:
        return iter(chain(self.added, self.removed, self.updated, self.moved))

    def __len__(self) -> int:
        return sum(map(len, (self.added, self.removed, self.updated, self.moved)))

    def __bool__(self) -> bool:
        return bool(len(self))


@dataclass(frozen=True, slots=True)
class TreeSnapshot:
    revision: str
    nodes: tuple[TreeNode[Any], ...]

    def __iter__(self) -> Iterator[TreeNode[Any]]:
        return iter(self.nodes)

    def __len__(self) -> int:
        return len(self.nodes)


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    node_id: NodeId | None = None
    old_path: str | None = None
    new_path: str | None = None
    commit_id: CommitId | None = None


@dataclass(frozen=True, slots=True)
class RemotePack:
    commits: tuple[Commit, ...]
    head: CommitId | None


@dataclass(frozen=True, slots=True)
class FetchResult:
    received_commits: tuple[CommitId, ...]
    remote_head: CommitId | None


@dataclass(frozen=True, slots=True)
class PullResult:
    fetch: FetchResult
    previous_head: CommitId | None
    head: CommitId | None
    fast_forwarded: bool


@dataclass(frozen=True, slots=True)
class PushResult:
    sent_commits: tuple[CommitId, ...]
    remote_head: CommitId | None
