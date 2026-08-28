"""ObjectTree facade: CRUD, history, transactions, events, and synchronization."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import Any, TypeVar
from uuid import uuid4

from .exceptions import (
    DirtyWorkingTreeError,
    DivergedHistoryError,
    InvalidPathError,
    NodeAlreadyExistsError,
    NodeNotFoundError,
    NothingToCommitError,
    RemoteError,
    RemoteNotConfiguredError,
    RevisionNotFoundError,
    SerializationError,
    TransactionError,
)
from .history import (
    ancestry,
    ancestry_oldest_first,
    diff_states,
    is_ancestor,
    make_commit,
    path_log,
    reconstruct_state,
    resolve_revision_id,
    scope_ids_for_path,
    state_for_revision,
    validate_history,
)
from .models import (
    Commit,
    Event,
    FetchResult,
    PullResult,
    PushResult,
    RemotePack,
    StoredNode,
    TreeDiff,
    TreeNode,
    TreeSnapshot,
)
from .paths import ROOT_PATH, child_path, normalize_path, parent_and_name, split_path, validate_name
from .remote.base import RemoteStore
from .serialization import Migration, SerializerRegistry
from .state import (
    ROOT_NODE_ID,
    RepositoryData,
    TreeState,
    clone_commit,
    repository_from_payload,
    repository_to_payload,
    utc_now,
)
from .store.base import ObjectStore
from .store.file import FileStore
from .store.memory import MemoryStore

T = TypeVar("T")
EventHandler = Callable[[Event], None]
_EVENT_NAMES = {
    "node_added",
    "node_updated",
    "node_removed",
    "node_moved",
    "commit",
    "fetch",
    "pull",
    "push",
}


@dataclass(slots=True)
class _ActiveTransaction:
    events: list[Event]


class ObjectTree:
    """A normalized, safely serialized, versioned hierarchy of Python objects."""

    def __init__(
        self,
        storage: ObjectStore | str | Path | None = None,
        *,
        store: ObjectStore | None = None,
        registry: SerializerRegistry | None = None,
        remote: RemoteStore | None = None,
        author: str | None = None,
    ) -> None:
        if storage is not None and store is not None:
            raise TypeError("pass either storage or store, not both")
        selected = store if store is not None else storage
        if selected is None:
            object_store: ObjectStore = MemoryStore()
        elif isinstance(selected, (str, Path)):
            object_store = FileStore(selected)
        else:
            object_store = selected
        document = object_store.load()
        repository = (
            RepositoryData.empty()
            if document.payload is None
            else repository_from_payload(document.payload)
        )
        validate_history(repository)

        self._store = object_store
        self._generation = document.generation
        self._repository = repository
        self.registry = registry or SerializerRegistry()
        self.remote = remote
        self.author = author
        self._lock = RLock()
        self._transaction: _ActiveTransaction | None = None
        self._handlers: dict[str, list[EventHandler]] = {name: [] for name in _EVENT_NAMES}
        self._event_dispatch_depth = 0

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        registry: SerializerRegistry | None = None,
        remote: RemoteStore | None = None,
        author: str | None = None,
    ) -> ObjectTree:
        return cls(FileStore(path), registry=registry, remote=remote, author=author)

    @property
    def head(self) -> str | None:
        with self._lock:
            return self._repository.head

    @property
    def remote_head(self) -> str | None:
        with self._lock:
            return self._repository.remote_head

    @property
    def store(self) -> ObjectStore:
        return self._store

    def __getitem__(self, path: str) -> object | None:
        return self.get(path)

    # ------------------------------------------------------------------
    # Serialization registration

    def register(
        self,
        cls: type[T],
        dump: Callable[[T], object],
        load: Callable[[object], T],
        *,
        type_id: str | None = None,
        version: int = 1,
        migrations: Mapping[int, Migration] | None = None,
    ) -> None:
        with self._lock:
            self.registry.register(
                cls,
                dump,
                load,
                type_id=type_id,
                version=version,
                migrations=migrations,
            )

    def register_dataclass(
        self,
        cls: type[T],
        *,
        type_id: str | None = None,
        version: int = 1,
        migrations: Mapping[int, Migration] | None = None,
    ) -> None:
        with self._lock:
            self.registry.register_dataclass(
                cls,
                type_id=type_id,
                version=version,
                migrations=migrations,
            )

    # ------------------------------------------------------------------
    # Reads and traversal

    def exists(self, path: str) -> bool:
        with self._lock:
            try:
                self._repository.working.resolve(path)
            except NodeNotFoundError:
                return False
            return True

    def get(self, path: str) -> object | None:
        return self.node(path).value

    def node(self, path: str) -> TreeNode[Any]:
        with self._lock:
            state = self._repository.working
            return self._view(state, state.resolve(path))

    def get_node_by_id(self, node_id: str) -> TreeNode[Any]:
        with self._lock:
            return self._view(self._repository.working, node_id)

    def path_for(self, node_id: str) -> str:
        with self._lock:
            return self._repository.working.path_for(node_id)

    def children(self, path: str = ROOT_PATH) -> list[TreeNode[Any]]:
        with self._lock:
            state = self._repository.working
            node_id = state.resolve(path)
            return [self._view(state, child_id) for child_id in state.child_ids(node_id)]

    def parent(self, path: str) -> TreeNode[Any] | None:
        with self._lock:
            state = self._repository.working
            node = state.nodes[state.resolve(path)]
            return None if node.parent_id is None else self._view(state, node.parent_id)

    def walk(self, path: str = ROOT_PATH) -> list[TreeNode[Any]]:
        with self._lock:
            state = self._repository.working
            root_id = state.resolve(path)
            return [self._view(state, node_id) for node_id in state.walk_ids(root_id)]

    def find(
        self,
        predicate: Callable[[TreeNode[Any]], bool] | None = None,
        *,
        type: type[T] | None = None,
        path: str = ROOT_PATH,
    ) -> list[TreeNode[Any]]:
        with self._lock:
            nodes = self.walk(path)
            result: list[TreeNode[Any]] = []
            for node in nodes:
                if type is not None and not isinstance(node.value, type):
                    continue
                if predicate is not None and not predicate(node):
                    continue
                result.append(node)
            return result

    def filter(
        self,
        predicate: Callable[[TreeNode[Any]], bool],
        *,
        path: str = ROOT_PATH,
    ) -> list[TreeNode[Any]]:
        return self.find(predicate, path=path)

    def count(self, path: str = ROOT_PATH, *, include_self: bool = False) -> int:
        with self._lock:
            state = self._repository.working
            root_id = state.resolve(path)
            return len(state.walk_ids(root_id, include_self=include_self))

    def group_by_type(self, path: str = ROOT_PATH) -> dict[type[Any], list[TreeNode[Any]]]:
        groups: dict[type[Any], list[TreeNode[Any]]] = {}
        for node in self.walk(path):
            if node.value is None:
                continue
            groups.setdefault(type(node.value), []).append(node)
        return groups

    # ------------------------------------------------------------------
    # CRUD

    def add(
        self,
        path: str,
        value: object | None = None,
        *,
        metadata: Mapping[str, Any] | None = None,
        tags: Iterable[str] = (),
        create_parents: bool = True,
    ) -> TreeNode[Any]:
        canonical = normalize_path(path)
        if canonical == ROOT_PATH:
            raise NodeAlreadyExistsError(ROOT_PATH)
        normalized_tags = self._normalize_tags(tags)

        with self._lock:
            encoded_value = self.registry.encode(value)
            encoded_metadata = self.registry.encode(dict(metadata or {}))
            empty_metadata = self.registry.encode({})
            state = self._repository.working
            nodes = dict(state.nodes)
            lookup = {
                (node.parent_id, node.name): node_id
                for node_id, node in nodes.items()
                if node.parent_id is not None
            }
            parent_id = ROOT_NODE_ID
            created_ids: list[str] = []
            parts = split_path(canonical)
            timestamp = utc_now()
            for index, name in enumerate(parts):
                existing = lookup.get((parent_id, name))
                final = index == len(parts) - 1
                if existing is not None:
                    if final:
                        raise NodeAlreadyExistsError(canonical)
                    parent_id = existing
                    continue
                if not final and not create_parents:
                    missing = "/" + "/".join(parts[: index + 1])
                    raise NodeNotFoundError(missing)
                node_id = str(uuid4())
                record = StoredNode(
                    id=node_id,
                    name=name,
                    parent_id=parent_id,
                    value=encoded_value if final else None,
                    metadata=encoded_metadata if final else empty_metadata,
                    tags=normalized_tags if final else (),
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                nodes[node_id] = record
                lookup[(parent_id, name)] = node_id
                parent_id = node_id
                created_ids.append(node_id)

            new_state = TreeState(nodes)
            result = self._view(new_state, parent_id)
            events = [
                Event("node_added", node_id=node_id, new_path=new_state.path_for(node_id))
                for node_id in created_ids
            ]
            events_to_emit = self._install_working(new_state, events)
        self._emit_many(events_to_emit)
        return result

    def set(self, path: str, value: object | None) -> TreeNode[Any]:
        canonical = normalize_path(path)
        if canonical == ROOT_PATH:
            raise InvalidPathError("the synthetic root cannot store a value")
        with self._lock:
            encoded = self.registry.encode(value)
            state = self._repository.working
            node_id = state.resolve(canonical)
            old = state.nodes[node_id]
            if old.value == encoded:
                return self._view(state, node_id)
            nodes = dict(state.nodes)
            nodes[node_id] = replace(old, value=encoded, updated_at=utc_now())
            new_state = TreeState(nodes)
            result = self._view(new_state, node_id)
            events_to_emit = self._install_working(
                new_state,
                [Event("node_updated", node_id=node_id, old_path=canonical, new_path=canonical)],
            )
        self._emit_many(events_to_emit)
        return result

    def update_metadata(
        self,
        path: str,
        updates: Mapping[str, Any],
        *,
        replace: bool = False,
    ) -> TreeNode[Any]:
        if not isinstance(updates, Mapping):
            raise TypeError("metadata updates must be a mapping")
        canonical = normalize_path(path)
        if canonical == ROOT_PATH:
            raise InvalidPathError("the synthetic root metadata cannot be changed")
        with self._lock:
            state = self._repository.working
            node_id = state.resolve(canonical)
            old = state.nodes[node_id]
            if replace:
                metadata = dict(updates)
            else:
                decoded = self.registry.decode(old.metadata)
                if not isinstance(decoded, Mapping):
                    raise SerializationError("persisted node metadata is not a mapping")
                metadata = {**decoded, **dict(updates)}
            encoded = self.registry.encode(metadata)
            if encoded == old.metadata:
                return self._view(state, node_id)
            nodes = dict(state.nodes)
            nodes[node_id] = replace_dataclass(old, metadata=encoded, updated_at=utc_now())
            new_state = TreeState(nodes)
            result = self._view(new_state, node_id)
            events_to_emit = self._install_working(
                new_state,
                [Event("node_updated", node_id=node_id, old_path=canonical, new_path=canonical)],
            )
        self._emit_many(events_to_emit)
        return result

    def set_tags(self, path: str, tags: Iterable[str]) -> TreeNode[Any]:
        canonical = normalize_path(path)
        if canonical == ROOT_PATH:
            raise InvalidPathError("the synthetic root tags cannot be changed")
        normalized = self._normalize_tags(tags)
        with self._lock:
            state = self._repository.working
            node_id = state.resolve(canonical)
            old = state.nodes[node_id]
            if old.tags == normalized:
                return self._view(state, node_id)
            nodes = dict(state.nodes)
            nodes[node_id] = replace(old, tags=normalized, updated_at=utc_now())
            new_state = TreeState(nodes)
            result = self._view(new_state, node_id)
            events_to_emit = self._install_working(
                new_state,
                [Event("node_updated", node_id=node_id, old_path=canonical, new_path=canonical)],
            )
        self._emit_many(events_to_emit)
        return result

    def remove(self, path: str, *, recursive: bool = True) -> None:
        canonical = normalize_path(path)
        if canonical == ROOT_PATH:
            raise InvalidPathError("the synthetic root cannot be removed")
        with self._lock:
            state = self._repository.working
            node_id = state.resolve(canonical)
            descendants = state.walk_ids(node_id)
            if not recursive and len(descendants) > 1:
                raise InvalidPathError("cannot remove a non-empty node without recursive=True")
            old_paths = {child_id: state.path_for(child_id) for child_id in descendants}
            nodes = dict(state.nodes)
            for child_id in descendants:
                del nodes[child_id]
            new_state = TreeState(nodes)
            events = [
                Event("node_removed", node_id=child_id, old_path=old_paths[child_id])
                for child_id in reversed(descendants)
            ]
            events_to_emit = self._install_working(new_state, events)
        self._emit_many(events_to_emit)

    def move(self, source: str, target: str) -> TreeNode[Any]:
        source_path = normalize_path(source)
        target_path = normalize_path(target)
        if source_path == ROOT_PATH or target_path == ROOT_PATH:
            raise InvalidPathError("the synthetic root cannot be moved or replaced")
        target_parent_path, target_name = parent_and_name(target_path)
        with self._lock:
            state = self._repository.working
            source_id = state.resolve(source_path)
            try:
                state.resolve(target_path)
            except NodeNotFoundError:
                pass
            else:
                raise NodeAlreadyExistsError(target_path)
            target_parent_id = state.resolve(target_parent_path)
            if state.contains_in_subtree(source_id, target_parent_id):
                raise InvalidPathError("cannot move a node inside its own subtree")
            old = state.nodes[source_id]
            nodes = dict(state.nodes)
            nodes[source_id] = replace(
                old,
                parent_id=target_parent_id,
                name=target_name,
                updated_at=utc_now(),
            )
            new_state = TreeState(nodes)
            result = self._view(new_state, source_id)
            events_to_emit = self._install_working(
                new_state,
                [
                    Event(
                        "node_moved",
                        node_id=source_id,
                        old_path=source_path,
                        new_path=target_path,
                    )
                ],
            )
        self._emit_many(events_to_emit)
        return result

    def rename(self, path: str, new_name: str) -> TreeNode[Any]:
        checked_name = validate_name(new_name)
        canonical = normalize_path(path)
        if canonical == ROOT_PATH:
            raise InvalidPathError("the synthetic root cannot be renamed")
        parent_path, _ = parent_and_name(canonical)
        return self.move(canonical, child_path(parent_path, checked_name))

    def copy(self, source: str, target: str) -> TreeNode[Any]:
        source_path = normalize_path(source)
        target_path = normalize_path(target)
        if source_path == ROOT_PATH or target_path == ROOT_PATH:
            raise InvalidPathError("copying or replacing the synthetic root is not supported")
        target_parent_path, target_name = parent_and_name(target_path)
        with self._lock:
            state = self._repository.working
            source_id = state.resolve(source_path)
            try:
                state.resolve(target_path)
            except NodeNotFoundError:
                pass
            else:
                raise NodeAlreadyExistsError(target_path)
            target_parent_id = state.resolve(target_parent_path)
            source_ids = state.walk_ids(source_id)
            id_map = {old_id: str(uuid4()) for old_id in source_ids}
            timestamp = utc_now()
            nodes = dict(state.nodes)
            for old_id in source_ids:
                old = state.nodes[old_id]
                new_id = id_map[old_id]
                is_root = old_id == source_id
                new_parent = target_parent_id if is_root else id_map[old.parent_id]  # type: ignore[index]
                nodes[new_id] = StoredNode(
                    id=new_id,
                    name=target_name if is_root else old.name,
                    parent_id=new_parent,
                    value=old.value,
                    metadata=old.metadata,
                    tags=old.tags,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            new_state = TreeState(nodes)
            copied_root = id_map[source_id]
            result = self._view(new_state, copied_root)
            events = [
                Event(
                    "node_added",
                    node_id=id_map[old_id],
                    new_path=new_state.path_for(id_map[old_id]),
                )
                for old_id in source_ids
            ]
            events_to_emit = self._install_working(new_state, events)
        self._emit_many(events_to_emit)
        return result

    # ------------------------------------------------------------------
    # History

    def is_clean(self) -> bool:
        with self._lock:
            return self._is_clean()

    def commit(self, message: str, *, author: str | None = None) -> Commit:
        with self._lock:
            if self._transaction is not None:
                raise TransactionError("explicit commit is not allowed inside a transaction")
            previous_head = self._repository.head
            previous_commits = dict(self._repository.commits)
            commit = self._create_commit(message, author=author)
            try:
                self._persist()
            except Exception:
                self._repository.head = previous_head
                self._repository.commits = previous_commits
                raise
            public_commit = clone_commit(commit)
        self._emit(Event("commit", commit_id=commit.id))
        return public_commit

    def show(self, revision: str = "HEAD") -> Commit:
        with self._lock:
            commit_id = resolve_revision_id(revision, self._repository)
            if commit_id is None:
                raise RevisionNotFoundError(f"revision {revision!r} is the root, not a commit")
            return clone_commit(self._repository.commits[commit_id])

    def log(
        self,
        path: str | None = None,
        *,
        limit: int | None = None,
        revision: str = "HEAD",
    ) -> list[Commit]:
        if limit is not None and limit < 0:
            raise ValueError("log limit cannot be negative")
        if limit == 0:
            return []
        with self._lock:
            if path is not None:
                commits = path_log(
                    self._repository,
                    normalize_path(path),
                    revision=revision,
                    limit=limit,
                )
            else:
                commit_id = resolve_revision_id(revision, self._repository)
                commits = [
                    self._repository.commits[item]
                    for item in ancestry(commit_id, self._repository.commits)
                ]
                if limit is not None:
                    commits = commits[:limit]
            return [clone_commit(commit) for commit in commits]

    def diff(
        self,
        old: str = "HEAD",
        new: str = "WORKING",
        *,
        path: str = ROOT_PATH,
    ) -> TreeDiff:
        canonical = normalize_path(path)
        with self._lock:
            cache: dict[str | None, TreeState] = {}
            before = state_for_revision(old, self._repository, cache)
            after = state_for_revision(new, self._repository, cache)
            scope = None if canonical == ROOT_PATH else scope_ids_for_path(before, after, canonical)
            return diff_states(before, after, self.registry, scope_ids=scope)

    def snapshot(self, revision: str = "HEAD") -> TreeSnapshot:
        with self._lock:
            state = state_for_revision(revision, self._repository)
            nodes = tuple(self._view(state, node_id) for node_id in state.walk_ids())
            return TreeSnapshot(revision, nodes)

    # ------------------------------------------------------------------
    # Transactions and events

    @contextmanager
    def transaction(
        self,
        message: str | None = None,
        *,
        author: str | None = None,
    ) -> Iterator[ObjectTree]:
        events_to_emit: tuple[Event, ...] = ()
        self._lock.acquire()
        try:
            if self._transaction is not None:
                raise TransactionError("nested transactions are not supported")
            if message is not None and not self._is_clean():
                raise DirtyWorkingTreeError(
                    "a named transaction requires a clean working tree at entry"
                )
            backup = self._repository.clone()
            self._transaction = _ActiveTransaction([])
            try:
                yield self
                durable_diff = diff_states(
                    backup.working,
                    self._repository.working,
                    self.registry,
                )
                node_events = self._events_for_diff(durable_diff)
                commit_event: Event | None = None
                if message is not None:
                    commit = self._create_commit(message, author=author)
                    commit_event = Event("commit", commit_id=commit.id)
                elif not durable_diff:
                    # Discard timestamp-only intermediate mutations in a net-zero
                    # unnamed transaction and avoid an unnecessary store write.
                    self._repository.working = backup.working
                if durable_diff or commit_event is not None:
                    self._persist()
                events_to_emit = node_events + ((commit_event,) if commit_event is not None else ())
            except BaseException:
                self._repository = backup
                raise
            finally:
                self._transaction = None
        finally:
            self._lock.release()
        self._emit_many(events_to_emit)

    def on(self, name: str, handler: EventHandler) -> Callable[[], None]:
        if name not in _EVENT_NAMES:
            raise ValueError(f"unknown event name: {name!r}")
        if not callable(handler):
            raise TypeError("event handler must be callable")
        with self._lock:
            self._handlers[name].append(handler)

        def unsubscribe() -> None:
            self.off(name, handler)

        return unsubscribe

    def off(self, name: str, handler: EventHandler) -> None:
        if name not in _EVENT_NAMES:
            raise ValueError(f"unknown event name: {name!r}")
        with self._lock, suppress(ValueError):
            self._handlers[name].remove(handler)

    # ------------------------------------------------------------------
    # Remote synchronization

    def fetch(self) -> FetchResult:
        with self._lock:
            self._ensure_remote_operation_allowed()
            result = self._fetch_locked()
        self._emit(Event("fetch", commit_id=result.remote_head))
        return result

    def pull(self, *, strategy: str = "fast-forward") -> PullResult:
        if strategy != "fast-forward":
            raise RemoteError(f"unsupported pull strategy: {strategy!r}")
        fetched: FetchResult | None = None
        events: tuple[Event, ...] = ()
        try:
            with self._lock:
                self._ensure_remote_operation_allowed()
                fetched = self._fetch_locked()
                local_head = self._repository.head
                remote_head = self._repository.remote_head
                fetch_event = Event("fetch", commit_id=fetched.remote_head)

                if local_head == remote_head or is_ancestor(
                    remote_head, local_head, self._repository.commits
                ):
                    result = PullResult(fetched, local_head, local_head, False)
                    events = (fetch_event, Event("pull", commit_id=local_head))
                else:
                    if not is_ancestor(local_head, remote_head, self._repository.commits):
                        raise DivergedHistoryError("local and remote histories have diverged")
                    if not self._is_clean():
                        raise DirtyWorkingTreeError(
                            "cannot fast-forward pull with uncommitted working changes"
                        )

                    old_working = self._repository.working
                    old_head = local_head
                    integrated = reconstruct_state(remote_head, self._repository.commits).clone()
                    integration_diff = diff_states(
                        old_working,
                        integrated,
                        self.registry,
                    )
                    try:
                        self._repository.working = integrated
                        self._repository.head = remote_head
                        self._persist()
                    except Exception:
                        self._repository.working = old_working
                        self._repository.head = old_head
                        raise
                    result = PullResult(fetched, local_head, remote_head, True)
                    events = (
                        (fetch_event,)
                        + self._events_for_diff(integration_diff)
                        + (Event("pull", commit_id=remote_head),)
                    )
        except Exception:
            if fetched is not None:
                self._emit(Event("fetch", commit_id=fetched.remote_head))
            raise
        self._emit_many(events)
        return result

    def push(self) -> PushResult:
        with self._lock:
            self._ensure_remote_operation_allowed()
            assert self.remote is not None
            head = self._repository.head
            commit_ids = ancestry_oldest_first(head, self._repository.commits)
            pack = RemotePack(
                tuple(
                    clone_commit(self._repository.commits[commit_id]) for commit_id in commit_ids
                ),
                head,
            )
            result = self.remote.push(pack)
            if not isinstance(result, PushResult) or result.remote_head != head:
                raise RemoteError("remote did not accept the proposed local HEAD")
            previous_remote_head = self._repository.remote_head
            try:
                self._repository.remote_head = result.remote_head
                if previous_remote_head != result.remote_head:
                    self._persist()
            except Exception:
                self._repository.remote_head = previous_remote_head
                raise
        self._emit(Event("push", commit_id=result.remote_head))
        return result

    # ------------------------------------------------------------------
    # Internals

    def _view(self, state: TreeState, node_id: str) -> TreeNode[Any]:
        record = state.nodes.get(node_id)
        if record is None:
            raise NodeNotFoundError(node_id)
        value = self.registry.decode(record.value)
        metadata = self.registry.decode(record.metadata)
        if not isinstance(metadata, Mapping):
            raise SerializationError(f"metadata for node {node_id!r} is not a mapping")
        return TreeNode(
            id=record.id,
            name=record.name,
            value=value,
            metadata=dict(metadata),
            parent_id=record.parent_id,
            path=state.path_for(node_id),
            created_at=record.created_at,
            updated_at=record.updated_at,
            tags=frozenset(record.tags),
        )

    def _install_working(
        self,
        state: TreeState,
        events: list[Event],
    ) -> tuple[Event, ...]:
        previous = self._repository.working
        self._repository.working = state
        if self._transaction is not None:
            return ()
        try:
            self._persist()
        except Exception:
            self._repository.working = previous
            raise
        return tuple(events)

    def _create_commit(self, message: str, *, author: str | None) -> Commit:
        parent_state = reconstruct_state(self._repository.head, self._repository.commits)
        tree_diff = diff_states(parent_state, self._repository.working, self.registry)
        changes = tuple(tree_diff)
        if not changes:
            raise NothingToCommitError("working tree is identical to HEAD")
        commit = make_commit(
            parent=self._repository.head,
            message=message,
            author=self.author if author is None else author,
            changes=changes,
        )
        self._repository.commits[commit.id] = commit
        self._repository.head = commit.id
        return commit

    def _is_clean(self) -> bool:
        committed = reconstruct_state(self._repository.head, self._repository.commits)
        return not diff_states(committed, self._repository.working, self.registry)

    def _persist(self) -> None:
        payload = repository_to_payload(self._repository)
        self._generation = self._store.save(
            payload,
            expected_generation=self._generation,
        )

    def _fetch_locked(self) -> FetchResult:
        assert self.remote is not None
        pack = self.remote.fetch()
        if not isinstance(pack, RemotePack):
            raise RemoteError("remote.fetch() returned an invalid pack")
        previous_commits = dict(self._repository.commits)
        previous_remote_head = self._repository.remote_head
        received: list[str] = []
        try:
            for untrusted in pack.commits:
                commit = clone_commit(untrusted)
                existing = self._repository.commits.get(commit.id)
                if existing is not None and existing != commit:
                    raise RemoteError(f"remote redefined commit {commit.id!r}")
                if existing is None:
                    self._repository.commits[commit.id] = commit
                    received.append(commit.id)
            self._repository.remote_head = pack.head
            validate_history(self._repository)
            if received or previous_remote_head != pack.head:
                self._persist()
        except RemoteError:
            self._repository.commits = previous_commits
            self._repository.remote_head = previous_remote_head
            raise
        except Exception as exc:
            self._repository.commits = previous_commits
            self._repository.remote_head = previous_remote_head
            raise RemoteError("remote returned invalid or unpersistable history") from exc
        return FetchResult(tuple(received), pack.head)

    @staticmethod
    def _events_for_diff(tree_diff: TreeDiff) -> tuple[Event, ...]:
        events: list[Event] = []
        events.extend(
            Event("node_added", node_id=change.node_id, new_path=change.new_path)
            for change in tree_diff.added
        )
        events.extend(
            Event("node_removed", node_id=change.node_id, old_path=change.old_path)
            for change in reversed(tree_diff.removed)
        )
        events.extend(
            Event(
                "node_updated",
                node_id=change.node_id,
                old_path=change.old_path,
                new_path=change.new_path,
            )
            for change in tree_diff.updated
        )
        events.extend(
            Event(
                "node_moved",
                node_id=change.node_id,
                old_path=change.old_path,
                new_path=change.new_path,
            )
            for change in tree_diff.moved
        )
        return tuple(events)

    def _emit_many(self, events: Iterable[Event]) -> None:
        for event in events:
            self._emit(event)

    def _emit(self, event: Event) -> None:
        # State is already durable and its operation lock has been released.
        # Handler errors propagate but cannot roll back durable state.
        with self._lock:
            handlers = tuple(self._handlers[event.name])
            self._event_dispatch_depth += 1
        try:
            for handler in handlers:
                handler(event)
        finally:
            with self._lock:
                self._event_dispatch_depth -= 1

    def _ensure_remote_operation_allowed(self) -> None:
        if self._transaction is not None:
            raise TransactionError("remote operations are not allowed inside a transaction")
        if self._event_dispatch_depth:
            raise TransactionError("remote operations are not allowed from event handlers")
        if self.remote is None:
            raise RemoteNotConfiguredError("no remote is configured")

    @staticmethod
    def _normalize_tags(tags: Iterable[str]) -> tuple[str, ...]:
        materialized = tuple(tags)
        if not all(isinstance(tag, str) for tag in materialized):
            raise TypeError("tags must be strings")
        return tuple(sorted(set(materialized)))


# Avoid shadowing the imported dataclasses.replace in update_metadata's API.
replace_dataclass = replace
