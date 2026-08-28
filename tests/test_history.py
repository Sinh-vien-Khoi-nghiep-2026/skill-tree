from __future__ import annotations

import pytest

from objecttree import ChangeKind, NothingToCommitError, ObjectTree, RevisionNotFoundError

from .conftest import Skill


def test_working_state_is_separate_from_head_and_commit_log(tree: ObjectTree) -> None:
    tree.add("skills/python", Skill("Python", 0.6))
    assert tree.head is None
    assert [change.new_path for change in tree.diff().added] == [
        "/skills",
        "/skills/python",
    ]

    first = tree.commit("Initial assessment")
    assert tree.head == first.id
    assert not tree.diff()
    assert tree.show().message == "Initial assessment"
    assert [commit.message for commit in tree.log()] == ["Initial assessment"]

    tree.set("skills/python", Skill("Python", 0.82))
    assert tree.show().id == first.id
    assert tree.get("skills/python") == Skill("Python", 0.82)
    with pytest.raises(NothingToCommitError):
        clean = ObjectTree()
        clean.commit("No changes")


def test_semantic_diff_and_revision_expressions(tree: ObjectTree) -> None:
    with tree.transaction("Initial assessment"):
        tree.add("students/alice/skills/python", Skill("Python", 0.6, 0.9))
    first = tree.head
    with tree.transaction("August assessment"):
        tree.set("students/alice/skills/python", Skill("Python", 0.82, 0.95))
        tree.add("students/alice/skills/rust", Skill("Rust", 0.55))
    second = tree.head

    assert first is not None and second is not None
    diff = tree.diff("HEAD~1", "HEAD", path="students/alice")
    assert [change.new_path for change in diff.added] == ["/students/alice/skills/rust"]
    python = next(change for change in diff.updated if change.new_path.endswith("/python"))
    assert {(delta.field, delta.before, delta.after) for delta in python.deltas} == {
        ("confidence", 0.9, 0.95),
        ("level", 0.6, 0.82),
    }
    assert tree.show("HEAD~1").id == first
    assert tree.show(second).message == "August assessment"
    with pytest.raises(RevisionNotFoundError):
        tree.show("HEAD~2")


def test_path_log_tracks_stable_identity_across_ancestor_rename(tree: ObjectTree) -> None:
    with tree.transaction("Initial"):
        tree.add("students/alice/skills/programming/python", Skill("Python", 0.6))
        tree.add("students/alice/skills/programming/rust", Skill("Rust", 0.4))
    leaf_id = tree.node("students/alice/skills/programming/python").id

    with tree.transaction("Rename category"):
        tree.rename("students/alice/skills/programming", "coding")
    with tree.transaction("Improve Python"):
        tree.set("students/alice/skills/coding/python", Skill("Python", 0.8))

    assert tree.node("students/alice/skills/coding/python").id == leaf_id
    assert [commit.message for commit in tree.log("students/alice/skills/coding/python")] == [
        "Improve Python",
        "Rename category",
        "Initial",
    ]
    assert [commit.message for commit in tree.log("students/alice/skills/coding")] == [
        "Improve Python",
        "Rename category",
        "Initial",
    ]
    assert [commit.message for commit in tree.log("students/alice", limit=2)] == [
        "Improve Python",
        "Rename category",
    ]


def test_diff_reports_moves_updates_additions_and_removals(tree: ObjectTree) -> None:
    with tree.transaction("Base"):
        tree.add("left/item", Skill("Python", 0.5))
        tree.add("left/remove-me", 1)
        tree.add("right")

    with tree.transaction("Restructure"):
        tree.move("left/item", "right/renamed")
        tree.set("right/renamed", Skill("Python", 0.9))
        tree.remove("left/remove-me")
        tree.add("left/new", 2)

    diff = tree.diff("HEAD~1", "HEAD")
    assert {change.kind for change in diff} == {
        ChangeKind.ADD,
        ChangeKind.REMOVE,
        ChangeKind.UPDATE,
        ChangeKind.MOVE,
    }
    moved = diff.moved[0]
    assert moved.old_path == "/left/item"
    assert moved.new_path == "/right/renamed"
    assert diff.updated[0].deltas[-1].after == 0.9
    assert [item.path for item in tree.snapshot("HEAD~1")][-1] == "/right"


def test_subtree_log_includes_removed_descendants(tree: ObjectTree) -> None:
    with tree.transaction("Add skills"):
        tree.add("students/alice/skills/python", Skill("Python", 0.6))
    with tree.transaction("Remove skill"):
        tree.remove("students/alice/skills/python")

    assert [commit.message for commit in tree.log("students/alice/skills")] == [
        "Remove skill",
        "Add skills",
    ]
    assert [commit.message for commit in tree.log("students/alice/skills/python")] == [
        "Remove skill",
        "Add skills",
    ]


def test_path_scoped_diff_explains_an_ancestor_rename(tree: ObjectTree) -> None:
    with tree.transaction("Initial"):
        tree.add("skills/programming/python", Skill("Python", 0.6))
    with tree.transaction("Rename ancestor"):
        tree.rename("skills/programming", "coding")

    scoped = tree.diff("HEAD~1", "HEAD", path="skills/coding/python")
    assert [(change.old_path, change.new_path) for change in scoped.moved] == [
        ("/skills/programming", "/skills/coding")
    ]


def test_public_commit_changes_are_detached_from_internal_history(tree: ObjectTree) -> None:
    tree.add("skill", Skill("Python", 0.6))
    returned = tree.commit("Initial")
    leaf_change = next(change for change in returned.changes if change.new_path == "/skill")
    assert isinstance(leaf_change.after.value, dict)
    leaf_change.after.value["type"] = "tampered.Type"

    assert tree.get("skill") == Skill("Python", 0.6)
    fresh = tree.show()
    fresh_leaf = next(change for change in fresh.changes if change.new_path == "/skill")
    assert fresh_leaf.after.value["type"] == "tests.Skill"
