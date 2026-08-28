from __future__ import annotations

from dataclasses import replace

import pytest

from objecttree import (
    DirtyWorkingTreeError,
    DivergedHistoryError,
    NonFastForwardError,
    ObjectTree,
    RemoteError,
    RemoteNotConfiguredError,
    RemotePack,
    TransactionError,
)
from objecttree.history import compute_commit_id
from objecttree.remote import FileRemote, MemoryRemote


def committed_origin(remote) -> ObjectTree:
    origin = ObjectTree(remote=remote, author="origin")
    origin.add("shared/value", 1)
    origin.commit("Base")
    origin.push()
    return origin


def clone(remote) -> ObjectTree:
    replica = ObjectTree(remote=remote)
    replica.pull()
    return replica


def test_fetch_updates_tracking_only_and_pull_fast_forwards() -> None:
    remote = MemoryRemote()
    origin = committed_origin(remote)
    replica = ObjectTree(remote=remote)

    fetched = replica.fetch()
    assert fetched.received_commits == (origin.head,)
    assert fetched.remote_head == origin.head
    assert replica.head is None
    assert not replica.exists("shared")

    pulled = replica.pull()
    assert pulled.fast_forwarded
    assert replica.head == origin.head
    assert replica.get("shared/value") == 1


def test_push_sends_commits_and_remote_rejects_non_fast_forward() -> None:
    remote = MemoryRemote()
    committed_origin(remote)
    first = clone(remote)
    stale = clone(remote)

    first.set("shared/value", 2)
    first.commit("First advances")
    pushed = first.push()
    assert pushed.remote_head == first.head
    assert len(pushed.sent_commits) == 1

    stale.set("shared/value", 3)
    stale.commit("Stale advances differently")
    with pytest.raises(NonFastForwardError):
        stale.push()


def test_pull_detects_divergence_after_fetch() -> None:
    remote = MemoryRemote()
    committed_origin(remote)
    first = clone(remote)
    second = clone(remote)

    first.set("shared/value", 2)
    first.commit("Remote side")
    first.push()

    second.set("shared/value", 3)
    second.commit("Local side")
    with pytest.raises(DivergedHistoryError):
        second.pull()
    assert second.remote_head == first.head
    assert second.get("shared/value") == 3


def test_pull_refuses_to_overwrite_dirty_working_tree() -> None:
    remote = MemoryRemote()
    origin = committed_origin(remote)
    replica = clone(remote)

    origin.set("shared/value", 2)
    origin.commit("Advance origin")
    origin.push()
    replica.set("shared/value", 99)

    with pytest.raises(DirtyWorkingTreeError):
        replica.pull()
    assert replica.get("shared/value") == 99
    assert replica.remote_head == origin.head


def test_pull_is_noop_when_local_is_ahead_of_remote() -> None:
    remote = MemoryRemote()
    committed_origin(remote)
    local = clone(remote)
    local.add("local-only", True)
    local.commit("Local ahead")

    result = local.pull()
    assert not result.fast_forwarded
    assert local.get("local-only") is True


def test_file_remote_survives_reopen(tmp_path) -> None:
    path = tmp_path / "origin.remote"
    first_remote = FileRemote(path)
    origin = committed_origin(first_remote)

    reopened_remote = FileRemote(path)
    replica = ObjectTree(remote=reopened_remote)
    fetched = replica.fetch()
    assert fetched.remote_head == origin.head
    assert not replica.exists("shared")
    replica.pull()
    assert replica.get("shared/value") == 1


def test_remote_rejects_disconnected_commits_and_detaches_fetch_packs() -> None:
    first = ObjectTree()
    first.add("first", 1)
    first_commit = first.commit("First root")
    second = ObjectTree()
    second.add("second", 2)
    second_commit = second.commit("Second root")
    remote = MemoryRemote()

    with pytest.raises(RemoteError, match="outside"):
        remote.push(RemotePack((first_commit, second_commit), first_commit.id))

    remote.push(RemotePack((first_commit,), first_commit.id))
    fetched = remote.fetch()
    add = next(change for change in fetched.commits[0].changes if change.after is not None)
    add.after.metadata["items"].append(["tampered", True])
    fresh = remote.fetch()
    fresh_add = next(change for change in fresh.commits[0].changes if change.after is not None)
    assert fresh_add.after.metadata["items"] == []


def test_remote_rejects_hashed_but_false_semantic_audit_data() -> None:
    source = ObjectTree()
    source.add("value", 1)
    valid = source.commit("Valid")
    false_change = replace(valid.changes[0], old_path="/a-false-old-path")
    unhashed = replace(valid, id="", changes=(false_change,))
    malicious = replace(unhashed, id=compute_commit_id(unhashed))

    with pytest.raises(RemoteError, match="replayable"):
        MemoryRemote().push(RemotePack((malicious,), malicious.id))


def test_remote_sync_is_rejected_from_its_own_event_handler() -> None:
    remote = MemoryRemote()
    committed_origin(remote)
    replica = ObjectTree(remote=remote)
    replica.on("fetch", lambda event: replica.pull())

    with pytest.raises(TransactionError, match="event handlers"):
        replica.fetch()
    assert replica.remote_head is not None
    assert replica.head is None


def test_remote_operations_require_configuration(tree: ObjectTree) -> None:
    with pytest.raises(RemoteNotConfiguredError):
        tree.fetch()
    with pytest.raises(RemoteNotConfiguredError):
        tree.pull()
    with pytest.raises(RemoteNotConfiguredError):
        tree.push()
