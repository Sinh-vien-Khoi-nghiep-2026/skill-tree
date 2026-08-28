"""Transport-neutral remote protocol and pack validation."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..exceptions import (
    CorruptStoreError,
    NonFastForwardError,
    RemoteError,
    RevisionNotFoundError,
)
from ..history import ancestry, compute_commit_id, is_ancestor, reconstruct_state
from ..models import Commit, PushResult, RemotePack
from ..state import TreeState, clone_commit, commit_from_data, commit_to_data

REMOTE_SCHEMA_VERSION = 1


@runtime_checkable
class RemoteStore(Protocol):
    """Synchronous remote interface.

    A future async implementation can expose the same immutable RemotePack wire
    values through a separate adapter without changing ObjectTree's core API.
    """

    def fetch(self) -> RemotePack: ...

    def push(self, pack: RemotePack) -> PushResult: ...


def accept_push(
    existing: dict[str, Commit],
    current_head: str | None,
    pack: RemotePack,
) -> tuple[dict[str, Commit], PushResult]:
    if not isinstance(pack, RemotePack):
        raise RemoteError("push payload is not a RemotePack")
    if pack.head is not None and not isinstance(pack.head, str):
        raise RemoteError("proposed remote head must be a string or null")
    combined = {commit_id: clone_commit(commit) for commit_id, commit in existing.items()}
    received: list[str] = []
    supplied: set[str] = set()
    try:
        for untrusted in pack.commits:
            commit = clone_commit(untrusted)
            if commit.id in supplied:
                raise RemoteError(f"push repeats commit {commit.id!r}")
            supplied.add(commit.id)
            if compute_commit_id(commit) != commit.id:
                raise RemoteError(f"push contains invalid commit hash {commit.id!r}")
            previous = combined.get(commit.id)
            if previous is not None and previous != commit:
                raise RemoteError(f"push redefines commit {commit.id!r}")
            if previous is None:
                combined[commit.id] = commit
                received.append(commit.id)
    except CorruptStoreError as exc:
        raise RemoteError("push contains malformed commit data") from exc

    for commit in combined.values():
        if commit.parent is not None and commit.parent not in combined:
            raise RemoteError(f"commit {commit.id!r} has a missing parent")
    if pack.head is not None and pack.head not in combined:
        raise RemoteError("proposed remote head is not in the supplied history")
    if not is_ancestor(current_head, pack.head, combined):
        raise NonFastForwardError("remote HEAD is not an ancestor of the proposed local HEAD")

    reachable = set(ancestry(pack.head, combined))
    if supplied - reachable or set(combined) != reachable:
        raise RemoteError("push contains commits outside the proposed HEAD history")
    try:
        cache: dict[str | None, TreeState] = {None: reconstruct_state(None, combined)}
        for commit_id in combined:
            reconstruct_state(commit_id, combined, cache)
    except (CorruptStoreError, KeyError, RevisionNotFoundError) as exc:
        raise RemoteError("pushed history is not replayable") from exc
    return combined, PushResult(tuple(received), pack.head)


def remote_pack_to_payload(pack: RemotePack) -> dict[str, Any]:
    return {
        "schema_version": REMOTE_SCHEMA_VERSION,
        "head": pack.head,
        "commits": [commit_to_data(commit) for commit in sorted(pack.commits, key=lambda c: c.id)],
    }


def remote_pack_from_payload(payload: object) -> RemotePack:
    if not isinstance(payload, dict) or payload.get("schema_version") != REMOTE_SCHEMA_VERSION:
        raise CorruptStoreError("unsupported or malformed remote document")
    head = payload.get("head")
    commits_data = payload.get("commits")
    if head is not None and not isinstance(head, str):
        raise CorruptStoreError("remote head must be a string or null")
    if not isinstance(commits_data, list):
        raise CorruptStoreError("remote commits must be a list")
    commits: dict[str, Commit] = {}
    for item in commits_data:
        commit = commit_from_data(item)
        if commit.id in commits:
            raise CorruptStoreError(f"duplicate remote commit {commit.id!r}")
        if compute_commit_id(commit) != commit.id:
            raise CorruptStoreError(f"invalid remote commit hash {commit.id!r}")
        commits[commit.id] = commit
    for commit in commits.values():
        if commit.parent is not None and commit.parent not in commits:
            raise CorruptStoreError(f"remote commit {commit.id!r} has a missing parent")
    if head is not None and head not in commits:
        raise CorruptStoreError("remote head references an unknown commit")
    if set(commits) != set(ancestry(head, commits)):
        raise CorruptStoreError("remote contains commits outside its HEAD history")
    try:
        cache: dict[str | None, TreeState] = {None: reconstruct_state(None, commits)}
        for commit_id in commits:
            reconstruct_state(commit_id, commits, cache)
    except (CorruptStoreError, KeyError, RevisionNotFoundError) as exc:
        raise CorruptStoreError("remote history is not replayable") from exc
    return RemotePack(tuple(commits.values()), head)
