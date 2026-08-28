from __future__ import annotations

import pytest

from objecttree import Event, ObjectTree
from objecttree.store import MemoryStore


def test_node_and_commit_events_run_after_durable_change() -> None:
    store = MemoryStore()
    tree = ObjectTree(store)
    seen: list[tuple[Event, bool]] = []

    tree.on(
        "node_added",
        lambda event: seen.append((event, ObjectTree(store).exists(event.new_path or "/"))),
    )
    tree.on("commit", lambda event: seen.append((event, ObjectTree(store).head == event.commit_id)))

    tree.add("value", 1)
    commit = tree.commit("Add value")

    assert [(event.name, durable) for event, durable in seen] == [
        ("node_added", True),
        ("commit", True),
    ]
    assert seen[-1][0].commit_id == commit.id


def test_transaction_buffers_events_and_discards_them_on_rollback() -> None:
    tree = ObjectTree()
    seen: list[str] = []
    tree.on("node_added", lambda event: seen.append(event.name))
    tree.on("node_updated", lambda event: seen.append(event.name))
    tree.on("commit", lambda event: seen.append(event.name))

    with tree.transaction("Atomic"):
        tree.add("value", 1)
        tree.set("value", 2)
        assert seen == []
    # Intermediate updates are coalesced into durable final-state events.
    assert seen == ["node_added", "commit"]

    seen.clear()
    with pytest.raises(RuntimeError), tree.transaction("Rollback"):
        tree.add("temporary", 1)
        raise RuntimeError
    assert seen == []


def test_unsubscribe_and_handler_failure_do_not_undo_durable_state() -> None:
    store = MemoryStore()
    tree = ObjectTree(store)
    calls: list[str] = []
    unsubscribe = tree.on("node_added", lambda event: calls.append(event.new_path or ""))
    tree.add("first", 1)
    unsubscribe()
    tree.add("second", 2)
    assert calls == ["/first"]

    def fail(_event: Event) -> None:
        raise RuntimeError("observer failed")

    tree.on("node_added", fail)
    with pytest.raises(RuntimeError, match="observer failed"):
        tree.add("durable", 3)
    assert ObjectTree(store).get("durable") == 3
