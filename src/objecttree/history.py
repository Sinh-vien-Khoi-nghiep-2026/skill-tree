"""Commit hashing, replay, ancestry, semantic diff, and path-specific logs."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from .exceptions import CorruptStoreError, NodeNotFoundError, RevisionNotFoundError
from .models import Change, ChangeKind, Commit, FieldDelta, StoredNode, TreeDiff
from .serialization import SemanticObject, SerializerRegistry
from .state import (
    ROOT_NODE_ID,
    RepositoryData,
    TreeState,
    canonical_json,
    changes_equal,
    commit_to_data,
    encoded_equal,
    stored_nodes_equal,
    utc_now,
)


def compute_commit_id(commit: Commit) -> str:
    canonical = canonical_json(commit_to_data(commit, include_id=False)).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def make_commit(
    *,
    parent: str | None,
    message: str,
    author: str | None,
    changes: tuple[Change, ...],
    timestamp: datetime | None = None,
) -> Commit:
    if not isinstance(message, str) or not message.strip():
        raise ValueError("commit message must be a non-empty string")
    if author is not None and not isinstance(author, str):
        raise TypeError("commit author must be a string or None")
    moment = timestamp or utc_now()
    if not isinstance(moment, datetime) or moment.tzinfo is None:
        raise ValueError("commit timestamp must be a timezone-aware datetime")
    moment = moment.astimezone(UTC)
    unhashed = Commit(
        id="",
        parent=parent,
        timestamp=moment,
        message=message,
        author=author,
        changes=changes,
    )
    return Commit(
        id=compute_commit_id(unhashed),
        parent=unhashed.parent,
        timestamp=unhashed.timestamp,
        message=unhashed.message,
        author=unhashed.author,
        changes=unhashed.changes,
    )


def apply_commit(parent_state: TreeState, commit: Commit) -> TreeState:
    """Apply and validate one canonical semantic change batch.

    Validation is stricter than merely reaching a valid final tree: operation
    kinds, paths, before/after records, deltas, ordering, and the synthetic root
    must all match the canonical diff.
    """
    if not commit.changes:
        raise CorruptStoreError("commits without changes are not supported")

    grouped: dict[str, list[Change]] = {}
    for change in commit.changes:
        grouped.setdefault(change.node_id, []).append(change)

    final_records: dict[str, StoredNode | None] = {}
    for node_id, changes in grouped.items():
        current = parent_state.nodes.get(node_id)
        kinds = [change.kind for change in changes]
        if len(kinds) != len(set(kinds)):
            raise CorruptStoreError(f"duplicate change kind for node {node_id!r}")

        if kinds == [ChangeKind.ADD]:
            change = changes[0]
            if current is not None or change.before is not None or change.after is None:
                raise CorruptStoreError(f"invalid ADD change for {node_id!r}")
            final_records[node_id] = change.after
            continue
        if kinds == [ChangeKind.REMOVE]:
            change = changes[0]
            if (
                current is None
                or not stored_nodes_equal(change.before, current)
                or change.after is not None
            ):
                raise CorruptStoreError(f"invalid REMOVE change for {node_id!r}")
            final_records[node_id] = None
            continue
        if not set(kinds) <= {ChangeKind.UPDATE, ChangeKind.MOVE}:
            raise CorruptStoreError(f"contradictory changes for node {node_id!r}")
        if current is None:
            raise CorruptStoreError(f"cannot update missing node {node_id!r}")
        final_record = changes[0].after
        if final_record is None or any(
            not stored_nodes_equal(change.before, current)
            or not stored_nodes_equal(change.after, final_record)
            for change in changes
        ):
            raise CorruptStoreError(f"conflicting changes for node {node_id!r}")
        if (
            final_record.id != current.id
            or final_record.created_at != current.created_at
            or final_record.updated_at < current.updated_at
        ):
            raise CorruptStoreError(f"invalid identity or timestamps for node {node_id!r}")
        final_records[node_id] = final_record

    nodes = dict(parent_state.nodes)
    for node_id, record in final_records.items():
        if record is None:
            nodes.pop(node_id, None)
        else:
            nodes[node_id] = record
    final_state = TreeState(nodes)
    expected = tuple(diff_states(parent_state, final_state, SerializerRegistry()))
    if not changes_equal(commit.changes, expected):
        raise CorruptStoreError("commit changes are not the canonical semantic diff")
    return final_state


def reconstruct_state(
    commit_id: str | None,
    commits: dict[str, Commit],
    cache: dict[str | None, TreeState] | None = None,
) -> TreeState:
    cache = cache if cache is not None else {}
    cache.setdefault(None, TreeState())
    if commit_id in cache:
        return cache[commit_id]

    trail: list[Commit] = []
    seen: set[str] = set()
    current = commit_id
    while current not in cache:
        if current is None or current in seen:
            raise RevisionNotFoundError(f"invalid commit ancestry at {current!r}")
        seen.add(current)
        commit = commits.get(current)
        if commit is None:
            raise RevisionNotFoundError(f"unknown commit: {current!r}")
        trail.append(commit)
        current = commit.parent
    for commit in reversed(trail):
        cache[commit.id] = apply_commit(cache[commit.parent], commit)
    return cache[commit_id]


def validate_node_id_uniqueness(commits: dict[str, Commit]) -> None:
    """Reject reuse of a node identity after removal or across lineages."""
    added_by: dict[str, str] = {}
    for commit in commits.values():
        for change in commit.changes:
            if change.kind is not ChangeKind.ADD:
                continue
            previous = added_by.get(change.node_id)
            if previous is not None:
                raise CorruptStoreError(
                    f"node ID {change.node_id!r} is added by both {previous!r} and {commit.id!r}"
                )
            added_by[change.node_id] = commit.id


def validate_history(repository: RepositoryData) -> None:
    """Validate hashes, closed parent links, heads, cycles, and replayability."""
    commits = repository.commits
    for commit_id, commit in commits.items():
        if commit.id != commit_id:
            raise CorruptStoreError(f"commit key/id mismatch: {commit_id!r}")
        if compute_commit_id(commit) != commit_id:
            raise CorruptStoreError(f"invalid commit hash: {commit_id!r}")
        if commit.parent is not None and commit.parent not in commits:
            raise CorruptStoreError(f"commit {commit_id!r} has a missing parent")
    validate_node_id_uniqueness(commits)
    for label, head in (("HEAD", repository.head), ("REMOTE_HEAD", repository.remote_head)):
        if head is not None and head not in commits:
            raise CorruptStoreError(f"{label} references an unknown commit")

    for commit_id in commits:
        seen: set[str] = set()
        current: str | None = commit_id
        while current is not None:
            if current in seen:
                raise CorruptStoreError("commit history contains a cycle")
            seen.add(current)
            current = commits[current].parent

    cache: dict[str | None, TreeState] = {None: TreeState()}
    for commit_id in commits:
        reconstruct_state(commit_id, commits, cache)


def resolve_revision_id(revision: str, repository: RepositoryData) -> str | None:
    """Resolve a committed revision. `ROOT` resolves to ``None``."""
    if not isinstance(revision, str):
        raise RevisionNotFoundError("revision must be a string")
    if revision == "ROOT":
        return None
    if revision == "HEAD":
        return repository.head
    if revision == "REMOTE_HEAD":
        return repository.remote_head
    if revision.startswith("HEAD~"):
        suffix = revision[5:]
        if not suffix.isdigit():
            raise RevisionNotFoundError(f"invalid revision: {revision!r}")
        current = repository.head
        for _ in range(int(suffix)):
            if current is None:
                raise RevisionNotFoundError(f"revision is before ROOT: {revision!r}")
            current = repository.commits[current].parent
        return current
    if revision in repository.commits:
        return revision
    raise RevisionNotFoundError(f"unknown revision: {revision!r}")


def state_for_revision(
    revision: str,
    repository: RepositoryData,
    cache: dict[str | None, TreeState] | None = None,
) -> TreeState:
    if revision == "WORKING":
        return repository.working
    return reconstruct_state(resolve_revision_id(revision, repository), repository.commits, cache)


def is_ancestor(
    ancestor: str | None,
    descendant: str | None,
    commits: dict[str, Commit],
) -> bool:
    """Return whether *ancestor* is on *descendant*'s parent chain.

    ``None`` denotes the immutable empty root state and is an ancestor of every
    complete history.
    """
    if ancestor is None:
        return True
    current = descendant
    while current is not None:
        if current == ancestor:
            return True
        commit = commits.get(current)
        if commit is None:
            return False
        current = commit.parent
    return False


def ancestry(head: str | None, commits: dict[str, Commit]) -> tuple[str, ...]:
    """Return commit IDs newest first."""
    result: list[str] = []
    current = head
    while current is not None:
        result.append(current)
        current = commits[current].parent
    return tuple(result)


def ancestry_oldest_first(head: str | None, commits: dict[str, Commit]) -> tuple[str, ...]:
    return tuple(reversed(ancestry(head, commits)))


def diff_states(
    before: TreeState,
    after: TreeState,
    registry: SerializerRegistry,
    *,
    scope_ids: set[str] | None = None,
) -> TreeDiff:
    before_ids = set(before.nodes) - {ROOT_NODE_ID}
    after_ids = set(after.nodes) - {ROOT_NODE_ID}
    if scope_ids is not None:
        before_ids &= scope_ids
        after_ids &= scope_ids

    added: list[Change] = []
    removed: list[Change] = []
    updated: list[Change] = []
    moved: list[Change] = []

    for node_id in after_ids - before_ids:
        record = after.nodes[node_id]
        added.append(
            Change(
                ChangeKind.ADD,
                node_id,
                None,
                after.path_for(node_id),
                after=_clone_record(record),
            )
        )
    for node_id in before_ids - after_ids:
        record = before.nodes[node_id]
        removed.append(
            Change(
                ChangeKind.REMOVE,
                node_id,
                before.path_for(node_id),
                None,
                before=_clone_record(record),
            )
        )
    for node_id in before_ids & after_ids:
        old = before.nodes[node_id]
        new = after.nodes[node_id]
        old_path = before.path_for(node_id)
        new_path = after.path_for(node_id)
        if old.parent_id != new.parent_id or old.name != new.name:
            moved.append(
                Change(
                    ChangeKind.MOVE,
                    node_id,
                    old_path,
                    new_path,
                    before=_clone_record(old),
                    after=_clone_record(new),
                )
            )
        deltas = _node_deltas(old, new, registry)
        if deltas:
            updated.append(
                Change(
                    ChangeKind.UPDATE,
                    node_id,
                    old_path,
                    new_path,
                    before=_clone_record(old),
                    after=_clone_record(new),
                    deltas=deepcopy(deltas),
                )
            )

    def sort_key(change: Change) -> tuple[str, str]:
        return change.path or "", change.node_id

    return TreeDiff(
        added=tuple(sorted(added, key=sort_key)),
        removed=tuple(sorted(removed, key=sort_key)),
        updated=tuple(sorted(updated, key=sort_key)),
        moved=tuple(sorted(moved, key=sort_key)),
    )


def scope_ids_for_path(before: TreeState, after: TreeState, path: str) -> set[str]:
    root_ids: set[str] = set()
    for state in (before, after):
        try:
            root_ids.add(state.resolve(path))
        except NodeNotFoundError:
            continue
    if not root_ids:
        raise NodeNotFoundError(path)

    scoped: set[str] = set()
    for root_id in root_ids:
        for state in (before, after):
            if root_id in state.nodes:
                scoped.update(state.walk_ids(root_id))

    # A direct move of an ancestor changes every descendant's derived path.
    # Include only moved ancestors needed to explain that location change.
    for root_id in tuple(root_ids):
        for state in (before, after):
            current = root_id
            while current in state.nodes and current != ROOT_NODE_ID:
                other_state = after if state is before else before
                node = state.nodes[current]
                other = other_state.nodes.get(current)
                if other is not None and (
                    node.parent_id != other.parent_id or node.name != other.name
                ):
                    scoped.add(current)
                if node.parent_id is None:
                    break
                current = node.parent_id
    return scoped


def path_log(
    repository: RepositoryData,
    path: str,
    *,
    revision: str = "HEAD",
    limit: int | None = None,
) -> list[Commit]:
    anchor_id = resolve_revision_id(revision, repository)
    anchor_state = reconstruct_state(anchor_id, repository.commits)
    try:
        target_id = anchor_state.resolve(path)
    except NodeNotFoundError as original_error:
        # A working-only rename can still be anchored by stable ID at HEAD.
        try:
            working_target = repository.working.resolve(path)
        except NodeNotFoundError:
            working_target = None
        if working_target is not None:
            if working_target not in anchor_state.nodes:
                return []
            target_id = working_target
        else:
            # Find the newest historical identity at a removed path while
            # retaining the anchor so the removal commit remains visible.
            target_id = ""
            for historical_id in ancestry(anchor_id, repository.commits):
                historical = reconstruct_state(historical_id, repository.commits)
                try:
                    target_id = historical.resolve(path)
                    break
                except NodeNotFoundError:
                    continue
            if not target_id:
                raise original_error
    cache: dict[str | None, TreeState] = {None: TreeState()}
    result: list[Commit] = []
    current = anchor_id

    while current is not None:
        commit = repository.commits[current]
        after = reconstruct_state(current, repository.commits, cache)
        before = reconstruct_state(commit.parent, repository.commits, cache)
        before_scope = set(before.walk_ids(target_id)) if target_id in before.nodes else set()
        after_scope = set(after.walk_ids(target_id)) if target_id in after.nodes else set()
        affected = {change.node_id for change in commit.changes}
        target_path_changed = (
            target_id in before.nodes
            and target_id in after.nodes
            and before.path_for(target_id) != after.path_for(target_id)
        )
        if affected & (before_scope | after_scope) or target_path_changed:
            result.append(commit)
            if limit is not None and len(result) >= limit:
                break
        current = commit.parent
    return result


def _node_deltas(
    old: Any,
    new: Any,
    registry: SerializerRegistry,
) -> tuple[FieldDelta, ...]:
    deltas: list[FieldDelta] = []
    old_value = registry.semantic_data(old.value)
    new_value = registry.semantic_data(new.value)
    _collect_deltas(old_value, new_value, "", deltas)
    old_metadata = registry.semantic_data(old.metadata)
    new_metadata = registry.semantic_data(new.metadata)
    _collect_deltas(old_metadata, new_metadata, "metadata", deltas)
    if old.tags != new.tags:
        deltas.append(FieldDelta("tags", list(old.tags), list(new.tags)))
    return tuple(deltas)


def _collect_deltas(before: object, after: object, prefix: str, output: list[FieldDelta]) -> None:
    if isinstance(before, SemanticObject) and isinstance(after, SemanticObject):
        object_prefix = f"{prefix}.$object" if prefix else "$object"
        if not encoded_equal(before.type_id, after.type_id):
            output.append(FieldDelta(f"{object_prefix}.type", before.type_id, after.type_id))
        if not encoded_equal(before.version, after.version):
            output.append(FieldDelta(f"{object_prefix}.version", before.version, after.version))
        _collect_deltas(before.data, after.data, prefix, output)
        return
    if isinstance(before, SemanticObject) or isinstance(after, SemanticObject):
        output.append(
            FieldDelta(prefix or "value", _plain_semantic(before), _plain_semantic(after))
        )
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            field = f"{prefix}.{key}" if prefix else key
            if key not in before:
                output.append(
                    FieldDelta(
                        field,
                        None,
                        _plain_semantic(after[key]),
                        before_exists=False,
                    )
                )
            elif key not in after:
                output.append(
                    FieldDelta(
                        field,
                        _plain_semantic(before[key]),
                        None,
                        after_exists=False,
                    )
                )
            else:
                _collect_deltas(before[key], after[key], field, output)
        return
    if _semantic_equal(before, after):
        return
    output.append(FieldDelta(prefix or "value", _plain_semantic(before), _plain_semantic(after)))


def _semantic_equal(before: object, after: object) -> bool:
    if isinstance(before, SemanticObject) and isinstance(after, SemanticObject):
        return (
            encoded_equal(before.type_id, after.type_id)
            and encoded_equal(before.version, after.version)
            and _semantic_equal(before.data, after.data)
        )
    if isinstance(before, SemanticObject) or isinstance(after, SemanticObject):
        return False
    if isinstance(before, dict) and isinstance(after, dict):
        return before.keys() == after.keys() and all(
            _semantic_equal(before[key], after[key]) for key in before
        )
    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(
            _semantic_equal(left, right) for left, right in zip(before, after, strict=True)
        )
    return encoded_equal(before, after)


def _plain_semantic(value: object) -> object:
    if isinstance(value, SemanticObject):
        return {
            "$object": {
                "type": value.type_id,
                "version": value.version,
                "data": _plain_semantic(value.data),
            }
        }
    if isinstance(value, list):
        return [_plain_semantic(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain_semantic(item) for key, item in value.items()}
    return value


def _clone_record(record: StoredNode) -> StoredNode:
    return replace(
        record,
        value=deepcopy(record.value),
        metadata=deepcopy(record.metadata),
    )
