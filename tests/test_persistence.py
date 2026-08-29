from __future__ import annotations

import sqlite3

import pytest

from objecttree import ConcurrentWriteError, CorruptStoreError, ObjectTree
from objecttree.remote import FileRemote
from objecttree.store import MemoryStore

from .conftest import Skill, skill_registry


def test_file_store_reopens_nodes_objects_history_and_head(tmp_path) -> None:
    repository_path = tmp_path / "student-data"
    tree = ObjectTree.open(repository_path, registry=skill_registry(), author="alice")
    with tree.transaction("Initial assessment"):
        node = tree.add("students/alice/skills/python", Skill("Python", 0.65))
    first_head = tree.head

    reopened = ObjectTree.open(repository_path, registry=skill_registry())
    assert reopened.head == first_head
    assert reopened.node("students/alice/skills/python").id == node.id
    assert reopened.get("students/alice/skills/python") == Skill("Python", 0.65)
    assert [commit.message for commit in reopened.log()] == ["Initial assessment"]
    assert not reopened.diff()


def test_file_store_preserves_dirty_working_tree_separately_from_head(tmp_path) -> None:
    path = tmp_path / "repo"
    tree = ObjectTree.open(path, registry=skill_registry())
    tree.add("skills/python", Skill("Python", 0.6))
    tree.commit("Base")
    tree.set("skills/python", Skill("Python", 0.85))

    reopened = ObjectTree.open(path, registry=skill_registry())
    assert reopened.show().message == "Base"
    assert reopened.get("skills/python") == Skill("Python", 0.85)
    delta = reopened.diff().updated[0].deltas
    assert next(item for item in delta if item.field == "level").after == 0.85


def test_file_store_persists_remote_tracking_head(tmp_path) -> None:
    remote = FileRemote(tmp_path / "origin.remote")
    path = tmp_path / "local"
    tree = ObjectTree.open(path, remote=remote)
    tree.add("value", 1)
    tree.commit("One")
    tree.push()
    assert tree.remote_head == tree.head

    reopened = ObjectTree.open(path)
    assert reopened.remote_head == reopened.head


def test_stale_file_store_writer_is_rejected_without_corrupting_winner(tmp_path) -> None:
    path = tmp_path / "shared"
    first = ObjectTree.open(path)
    stale = ObjectTree.open(path)

    first.add("winner", 1)
    with pytest.raises(ConcurrentWriteError):
        stale.add("loser", 2)

    reopened = ObjectTree.open(path)
    assert reopened.get("winner") == 1
    assert not reopened.exists("loser")
    assert not stale.exists("loser")


def test_noncanonical_synthetic_root_is_rejected() -> None:
    store = MemoryStore()
    tree = ObjectTree(store)
    tree.add("value", 1)
    document = store.load()
    assert document.payload is not None
    root = next(node for node in document.payload["working"] if node["parent_id"] is None)
    root["value"] = True
    store.save(document.payload, expected_generation=document.generation)

    with pytest.raises(CorruptStoreError, match="root"):
        ObjectTree(store)


def test_malformed_repository_document_is_reported(tmp_path) -> None:
    path = tmp_path / "corrupt"
    tree = ObjectTree.open(path)
    tree.add("value", 1)
    database = path / "objecttree.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE repository SET payload = ? WHERE id = 1", ("not-json",))

    with pytest.raises(CorruptStoreError):
        ObjectTree.open(path)
