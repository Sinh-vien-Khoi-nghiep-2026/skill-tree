"""Normalized tree state and repository document encoding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .exceptions import CorruptStoreError, NodeNotFoundError
from .models import Change, ChangeKind, Commit, FieldDelta, StoredNode
from .paths import ROOT_PATH, split_path

ROOT_NODE_ID = "00000000-0000-0000-0000-000000000000"
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_EMPTY_MAPPING = {"$objecttree": "mapping", "items": []}
REPOSITORY_SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(UTC)


def root_node() -> StoredNode:
    return StoredNode(
        id=ROOT_NODE_ID,
        name="",
        parent_id=None,
        value=None,
        metadata={"$objecttree": "mapping", "items": []},
        tags=(),
        created_at=_EPOCH,
        updated_at=_EPOCH,
    )


class TreeState:
    """A normalized mapping plus a derived child-name index."""

    def __init__(self, nodes: dict[str, StoredNode] | None = None) -> None:
        self.nodes: dict[str, StoredNode] = (
            dict(nodes) if nodes is not None else {ROOT_NODE_ID: root_node()}
        )
        self._children: dict[str, dict[str, str]] = {}
        self._rebuild_and_validate()

    def clone(self) -> TreeState:
        # StoredNode instances are immutable and encoded payloads are treated as immutable.
        return TreeState(self.nodes)

    def _rebuild_and_validate(self) -> None:
        root = self.nodes.get(ROOT_NODE_ID)
        if root is None:
            raise CorruptStoreError("tree state has no root node")
        if root.parent_id is not None or root.name != "":
            raise CorruptStoreError("invalid root node")

        children: dict[str, dict[str, str]] = {node_id: {} for node_id in self.nodes}
        for node_id, node in self.nodes.items():
            if node.id != node_id:
                raise CorruptStoreError(f"node key/id mismatch for {node_id!r}")
            if node_id == ROOT_NODE_ID:
                continue
            if not node.name or "/" in node.name or "\x00" in node.name or node.name in {".", ".."}:
                raise CorruptStoreError(f"invalid persisted node name: {node.name!r}")
            parent_id = node.parent_id
            if parent_id is None or parent_id not in self.nodes:
                raise CorruptStoreError(f"missing parent for node {node_id!r}")
            siblings = children[parent_id]
            if node.name in siblings:
                raise CorruptStoreError(f"duplicate sibling name {node.name!r} below {parent_id!r}")
            siblings[node.name] = node_id
            if (
                not all(isinstance(tag, str) for tag in node.tags)
                or tuple(sorted(set(node.tags))) != node.tags
            ):
                raise CorruptStoreError(f"invalid tags for node {node_id!r}")
            if node.created_at.tzinfo is None or node.updated_at.tzinfo is None:
                raise CorruptStoreError(f"timestamps must be timezone-aware for node {node_id!r}")

        # Every parent chain must terminate at the fixed root.
        for node_id in self.nodes:
            seen: set[str] = set()
            current = node_id
            while current != ROOT_NODE_ID:
                if current in seen:
                    raise CorruptStoreError("tree contains a parent cycle")
                seen.add(current)
                parent = self.nodes[current].parent_id
                if parent is None or parent not in self.nodes:
                    raise CorruptStoreError(f"broken parent chain at {current!r}")
                current = parent
        self._children = children

    def replace_nodes(self, nodes: dict[str, StoredNode]) -> None:
        previous = self.nodes
        self.nodes = dict(nodes)
        try:
            self._rebuild_and_validate()
        except Exception:
            self.nodes = previous
            self._rebuild_and_validate()
            raise

    def resolve(self, path: str) -> str:
        current = ROOT_NODE_ID
        canonical_parts = split_path(path)
        for part in canonical_parts:
            child_id = self._children.get(current, {}).get(part)
            if child_id is None:
                canonical = ROOT_PATH if not canonical_parts else "/" + "/".join(canonical_parts)
                raise NodeNotFoundError(canonical)
            current = child_id
        return current

    def path_for(self, node_id: str) -> str:
        if node_id not in self.nodes:
            raise NodeNotFoundError(node_id)
        if node_id == ROOT_NODE_ID:
            return ROOT_PATH
        names: list[str] = []
        current = node_id
        while current != ROOT_NODE_ID:
            node = self.nodes[current]
            names.append(node.name)
            if node.parent_id is None:
                raise CorruptStoreError(f"broken parent chain at {current!r}")
            current = node.parent_id
        return "/" + "/".join(reversed(names))

    def child_ids(self, node_id: str) -> tuple[str, ...]:
        if node_id not in self.nodes:
            raise NodeNotFoundError(node_id)
        children = self._children.get(node_id, {})
        return tuple(children[name] for name in sorted(children))

    def walk_ids(
        self,
        node_id: str = ROOT_NODE_ID,
        *,
        include_self: bool = True,
    ) -> tuple[str, ...]:
        if node_id not in self.nodes:
            raise NodeNotFoundError(node_id)
        result: list[str] = []
        stack = [node_id]
        while stack:
            current = stack.pop()
            result.append(current)
            stack.extend(reversed(self.child_ids(current)))
        return tuple(result if include_self else result[1:])

    def contains_in_subtree(self, root_id: str, node_id: str) -> bool:
        if root_id not in self.nodes or node_id not in self.nodes:
            return False
        current = node_id
        while True:
            if current == root_id:
                return True
            parent = self.nodes[current].parent_id
            if parent is None:
                return False
            current = parent


@dataclass(slots=True)
class RepositoryData:
    working: TreeState
    commits: dict[str, Commit]
    head: str | None
    remote_head: str | None

    @classmethod
    def empty(cls) -> RepositoryData:
        return cls(working=TreeState(), commits={}, head=None, remote_head=None)

    def clone(self) -> RepositoryData:
        return RepositoryData(
            working=self.working.clone(),
            commits=dict(self.commits),
            head=self.head,
            remote_head=self.remote_head,
        )


def stored_node_to_data(node: StoredNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "name": node.name,
        "parent_id": node.parent_id,
        "value": node.value,
        "metadata": node.metadata,
        "tags": list(node.tags),
        "created_at": _format_datetime(node.created_at),
        "updated_at": _format_datetime(node.updated_at),
    }


def stored_node_from_data(data: object) -> StoredNode:
    if not isinstance(data, dict):
        raise CorruptStoreError("node record must be an object")
    try:
        node_id = data["id"]
        name = data["name"]
        parent_id = data["parent_id"]
        tags = data["tags"]
        created_at = _parse_datetime(data["created_at"])
        updated_at = _parse_datetime(data["updated_at"])
        value = data["value"]
        metadata = data["metadata"]
    except KeyError as exc:
        raise CorruptStoreError(f"node record is missing {exc.args[0]!r}") from exc
    if not isinstance(node_id, str) or not isinstance(name, str):
        raise CorruptStoreError("node id and name must be strings")
    if parent_id is not None and not isinstance(parent_id, str):
        raise CorruptStoreError("parent id must be a string or null")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise CorruptStoreError("node tags must be a string list")
    return StoredNode(
        id=node_id,
        name=name,
        parent_id=parent_id,
        value=value,
        metadata=metadata,
        tags=tuple(tags),
        created_at=created_at,
        updated_at=updated_at,
    )


def change_to_data(change: Change) -> dict[str, Any]:
    return {
        "kind": change.kind.value,
        "node_id": change.node_id,
        "old_path": change.old_path,
        "new_path": change.new_path,
        "before": stored_node_to_data(change.before) if change.before else None,
        "after": stored_node_to_data(change.after) if change.after else None,
        "deltas": [
            {
                "field": delta.field,
                "before": delta.before,
                "after": delta.after,
                "before_exists": delta.before_exists,
                "after_exists": delta.after_exists,
            }
            for delta in change.deltas
        ],
    }


def change_from_data(data: object) -> Change:
    if not isinstance(data, dict):
        raise CorruptStoreError("change must be an object")
    try:
        kind = ChangeKind(data["kind"])
        node_id = data["node_id"]
        old_path = data["old_path"]
        new_path = data["new_path"]
        before_data = data["before"]
        after_data = data["after"]
        deltas_data = data["deltas"]
    except (KeyError, ValueError) as exc:
        raise CorruptStoreError("malformed change") from exc
    if not isinstance(node_id, str):
        raise CorruptStoreError("change node_id must be a string")
    if old_path is not None and not isinstance(old_path, str):
        raise CorruptStoreError("change old_path must be a string or null")
    if new_path is not None and not isinstance(new_path, str):
        raise CorruptStoreError("change new_path must be a string or null")
    if not isinstance(deltas_data, list):
        raise CorruptStoreError("change deltas must be a list")
    deltas: list[FieldDelta] = []
    for item in deltas_data:
        if not isinstance(item, dict) or not isinstance(item.get("field"), str):
            raise CorruptStoreError("malformed field delta")
        if "before" not in item or "after" not in item:
            raise CorruptStoreError("malformed field delta")
        before_exists = item.get("before_exists", True)
        after_exists = item.get("after_exists", True)
        if not isinstance(before_exists, bool) or not isinstance(after_exists, bool):
            raise CorruptStoreError("field delta presence flags must be booleans")
        deltas.append(
            FieldDelta(
                item["field"],
                item["before"],
                item["after"],
                before_exists,
                after_exists,
            )
        )
    return Change(
        kind=kind,
        node_id=node_id,
        old_path=old_path,
        new_path=new_path,
        before=stored_node_from_data(before_data) if before_data is not None else None,
        after=stored_node_from_data(after_data) if after_data is not None else None,
        deltas=tuple(deltas),
    )


def commit_to_data(commit: Commit, *, include_id: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "parent": commit.parent,
        "timestamp": _format_datetime(commit.timestamp),
        "message": commit.message,
        "author": commit.author,
        "changes": [change_to_data(change) for change in commit.changes],
    }
    if include_id:
        data["id"] = commit.id
    return data


def clone_commit(commit: Commit) -> Commit:
    """Return a canonical, deeply detached copy of a commit."""
    if not isinstance(commit, Commit):
        raise CorruptStoreError("expected a Commit instance")
    try:
        payload = json.loads(
            json.dumps(
                commit_to_data(commit),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise CorruptStoreError("commit contains malformed or non-JSON data") from exc
    return commit_from_data(payload)


def commit_from_data(data: object) -> Commit:
    if not isinstance(data, dict):
        raise CorruptStoreError("commit must be an object")
    try:
        commit_id = data["id"]
        parent = data["parent"]
        timestamp = _parse_datetime(data["timestamp"])
        message = data["message"]
        author = data["author"]
        changes_data = data["changes"]
    except KeyError as exc:
        raise CorruptStoreError(f"commit is missing {exc.args[0]!r}") from exc
    if not isinstance(commit_id, str) or not commit_id:
        raise CorruptStoreError("commit id must be a non-empty string")
    if parent is not None and not isinstance(parent, str):
        raise CorruptStoreError("commit parent must be a string or null")
    if not isinstance(message, str) or not message:
        raise CorruptStoreError("commit message must be non-empty")
    if author is not None and not isinstance(author, str):
        raise CorruptStoreError("commit author must be a string or null")
    if not isinstance(changes_data, list):
        raise CorruptStoreError("commit changes must be a list")
    return Commit(
        id=commit_id,
        parent=parent,
        timestamp=timestamp,
        message=message,
        author=author,
        changes=tuple(change_from_data(change) for change in changes_data),
    )


def repository_to_payload(repository: RepositoryData) -> dict[str, Any]:
    return {
        "schema_version": REPOSITORY_SCHEMA_VERSION,
        "working": [
            stored_node_to_data(repository.working.nodes[node_id])
            for node_id in sorted(repository.working.nodes)
        ],
        "commits": [
            commit_to_data(repository.commits[commit_id])
            for commit_id in sorted(repository.commits)
        ],
        "head": repository.head,
        "remote_head": repository.remote_head,
    }


def repository_from_payload(payload: object) -> RepositoryData:
    if not isinstance(payload, dict):
        raise CorruptStoreError("repository document must be an object")
    if payload.get("schema_version") != REPOSITORY_SCHEMA_VERSION:
        raise CorruptStoreError(f"unsupported repository schema: {payload.get('schema_version')!r}")
    working_data = payload.get("working")
    commits_data = payload.get("commits")
    head = payload.get("head")
    remote_head = payload.get("remote_head")
    if not isinstance(working_data, list) or not isinstance(commits_data, list):
        raise CorruptStoreError("repository working state and commits must be lists")
    if head is not None and not isinstance(head, str):
        raise CorruptStoreError("HEAD must be a string or null")
    if remote_head is not None and not isinstance(remote_head, str):
        raise CorruptStoreError("REMOTE_HEAD must be a string or null")

    nodes: dict[str, StoredNode] = {}
    for item in working_data:
        node = stored_node_from_data(item)
        if node.id in nodes:
            raise CorruptStoreError(f"duplicate node id {node.id!r}")
        nodes[node.id] = node
    commits: dict[str, Commit] = {}
    for item in commits_data:
        commit = commit_from_data(item)
        if commit.id in commits:
            raise CorruptStoreError(f"duplicate commit id {commit.id!r}")
        commits[commit.id] = commit
    return RepositoryData(TreeState(nodes), commits, head, remote_head)


def _format_datetime(value: object) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise CorruptStoreError("timestamps must be timezone-aware datetime values")
    return value.astimezone(UTC).isoformat()


def _parse_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise CorruptStoreError("timestamp must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CorruptStoreError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise CorruptStoreError("timestamp must include a timezone")
    return parsed.astimezone(UTC)
