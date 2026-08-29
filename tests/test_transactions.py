from __future__ import annotations

import pytest

from objecttree import (
    DirtyWorkingTreeError,
    ObjectTree,
    StoreError,
    TransactionError,
)
from objecttree.store import MemoryStore

from .conftest import Skill, skill_registry


class FailingMemoryStore(MemoryStore):
    fail_next = False

    def save(self, payload: dict, *, expected_generation: int) -> int:
        if self.fail_next:
            self.fail_next = False
            raise StoreError("injected save failure")
        return super().save(payload, expected_generation=expected_generation)


def test_named_transaction_creates_one_atomic_commit() -> None:
    store = MemoryStore()
    tree = ObjectTree(store, registry=skill_registry())

    with tree.transaction("Initial assessment"):
        tree.add("skills/python", Skill("Python", 0.6))
        tree.add("skills/statistics", Skill("Statistics", 0.4))
        assert tree.log() == []

    assert [commit.message for commit in tree.log()] == ["Initial assessment"]
    assert store.load().generation == 1
    assert tree.is_clean()


def test_unnamed_transaction_persists_working_state_without_commit() -> None:
    store = MemoryStore()
    tree = ObjectTree(store)

    with tree.transaction():
        tree.add("a", 1)
        tree.add("b", 2)

    assert tree.head is None
    assert len(tree.diff().added) == 2
    assert store.load().generation == 1
    reopened = ObjectTree(store)
    assert reopened.get("a") == 1


def test_transaction_rolls_back_on_user_exception(tree: ObjectTree) -> None:
    tree.add("existing", 1)
    tree.commit("Base")

    with pytest.raises(RuntimeError, match="stop"), tree.transaction("Should not exist"):
        tree.set("existing", 2)
        tree.add("temporary", 3)
        raise RuntimeError("stop")

    assert tree.get("existing") == 1
    assert not tree.exists("temporary")
    assert [commit.message for commit in tree.log()] == ["Base"]


def test_transaction_rolls_back_when_atomic_store_save_fails() -> None:
    store = FailingMemoryStore()
    tree = ObjectTree(store)
    store.fail_next = True

    with pytest.raises(StoreError, match="injected"), tree.transaction("Atomic"):
        tree.add("a/b", 1)

    assert not tree.exists("a")
    assert tree.head is None
    assert store.load().payload is None


def test_commit_save_failure_restores_history_but_keeps_dirty_working_tree() -> None:
    store = FailingMemoryStore()
    tree = ObjectTree(store)
    tree.add("assessment", 1)
    store.fail_next = True

    with pytest.raises(StoreError, match="injected"):
        tree.commit("Should fail")
    assert tree.head is None
    assert tree.log() == []
    assert tree.get("assessment") == 1
    assert tree.diff().added


def test_failed_non_transactional_mutation_restores_working_state() -> None:
    store = FailingMemoryStore()
    tree = ObjectTree(store)
    store.fail_next = True

    with pytest.raises(StoreError):
        tree.add("not-durable", 1)
    assert not tree.exists("not-durable")


def test_named_transaction_rejects_preexisting_dirty_state(tree: ObjectTree) -> None:
    tree.add("dirty", 1)
    with pytest.raises(DirtyWorkingTreeError), tree.transaction("Would include unrelated data"):
        pass


def test_net_zero_unnamed_transaction_does_not_write_or_emit() -> None:
    store = MemoryStore()
    tree = ObjectTree(store)
    seen: list[str] = []
    tree.on("node_added", lambda event: seen.append(event.name))
    tree.on("node_removed", lambda event: seen.append(event.name))

    with tree.transaction():
        tree.add("temporary", 1)
        tree.remove("temporary")

    assert store.load().generation == 0
    assert seen == []
    assert not tree.exists("temporary")


def test_nested_transaction_and_explicit_commit_are_rejected(tree: ObjectTree) -> None:
    with tree.transaction():
        with pytest.raises(TransactionError, match="nested"), tree.transaction():
            pass
        tree.add("a", 1)
        with pytest.raises(TransactionError, match="explicit commit"):
            tree.commit("not allowed")

    assert tree.get("a") == 1
