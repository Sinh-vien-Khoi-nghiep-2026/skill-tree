from __future__ import annotations

import pytest

from objecttree import (
    InvalidPathError,
    NodeAlreadyExistsError,
    NodeNotFoundError,
    ObjectTree,
)

from .conftest import Skill


def test_add_get_set_and_path_convenience(tree: ObjectTree) -> None:
    added = tree.add("students/alice/skills/python", Skill("Python", 0.6))

    assert added.path == "/students/alice/skills/python"
    assert tree.get("/students/alice/skills/python") == Skill("Python", 0.6)
    assert tree["students/alice/skills/python"] == Skill("Python", 0.6)
    assert tree.exists("students/alice")

    updated = tree.set(added.path, Skill("Python", 0.8))
    assert updated.id == added.id
    assert tree.get(added.path) == Skill("Python", 0.8)


def test_add_validates_duplicates_paths_and_missing_parents(tree: ObjectTree) -> None:
    tree.add("a/b", 1)
    with pytest.raises(NodeAlreadyExistsError):
        tree.add("/a/b", 2)
    with pytest.raises(NodeNotFoundError):
        tree.add("missing/child/leaf", 1, create_parents=False)
    with pytest.raises(InvalidPathError):
        tree.add("a//bad", 1)
    with pytest.raises(InvalidPathError):
        tree.add("a/../bad", 1)


def test_metadata_tags_and_detached_values(tree: ObjectTree) -> None:
    original = {"items": [1]}
    node = tree.add(
        "data",
        original,
        metadata={"source": "assessment", "confidence": 0.9},
        tags=("current", "assessment", "current"),
    )
    original["items"].append(2)

    assert tree.get("data") == {"items": [1]}
    assert node.tags == frozenset({"current", "assessment"})
    assert node.metadata["source"] == "assessment"

    returned = tree.get("data")
    assert isinstance(returned, dict)
    returned["items"].append(3)
    assert tree.get("data") == {"items": [1]}

    tree.update_metadata("data", {"confidence": 0.95, "reviewed": True})
    tree.set_tags("data", ["reviewed"])
    current = tree.node("data")
    assert current.metadata == {
        "source": "assessment",
        "confidence": 0.95,
        "reviewed": True,
    }
    assert current.tags == frozenset({"reviewed"})


def test_remove_recursive_and_non_recursive_guard(tree: ObjectTree) -> None:
    tree.add("a/b/c", 3)
    with pytest.raises(InvalidPathError):
        tree.remove("a", recursive=False)

    tree.remove("a/b")
    assert tree.exists("a")
    assert not tree.exists("a/b")
    with pytest.raises(NodeNotFoundError):
        tree.get("a/b/c")


def test_rename_and_move_preserve_identity_for_entire_subtree(tree: ObjectTree) -> None:
    branch = tree.add("students/alice/skills/programming", None)
    leaf = tree.add("students/alice/skills/programming/python", Skill("Python", 0.7))

    renamed = tree.rename(branch.path, "coding")
    assert renamed.id == branch.id
    assert tree.node("students/alice/skills/coding/python").id == leaf.id

    tree.add("archive")
    moved = tree.move("students/alice/skills/coding", "archive/programming")
    assert moved.id == branch.id
    assert tree.node("archive/programming/python").id == leaf.id
    with pytest.raises(InvalidPathError):
        tree.move("archive", "archive/programming/inside")


def test_copy_allocates_new_identity_for_every_node(tree: ObjectTree) -> None:
    source = tree.add("source")
    source_child = tree.add("source/child", Skill("Python", 0.4))
    tree.add("copies")

    copied = tree.copy("source", "copies/source-copy")
    copied_child = tree.node("copies/source-copy/child")

    assert copied.id != source.id
    assert copied_child.id != source_child.id
    assert copied_child.value == source_child.value


def test_children_parent_walk_find_count_and_group_by_type(tree: ObjectTree) -> None:
    tree.add("students/alice/skills/rust", Skill("Rust", 0.4))
    tree.add("students/alice/skills/python", Skill("Python", 0.8))
    tree.add("students/alice/active", True)

    assert [node.name for node in tree.children("students/alice/skills")] == [
        "python",
        "rust",
    ]
    assert tree.parent("students/alice/skills/python").path == "/students/alice/skills"
    assert tree.parent("/") is None
    assert [node.path for node in tree.walk("students/alice/skills")] == [
        "/students/alice/skills",
        "/students/alice/skills/python",
        "/students/alice/skills/rust",
    ]
    assert [node.value.name for node in tree.find(type=Skill)] == ["Python", "Rust"]
    assert [
        node.value.name
        for node in tree.find(
            lambda node: isinstance(node.value, Skill) and node.value.level >= 0.7
        )
    ] == ["Python"]
    assert tree.count("students/alice/skills") == 2
    assert tree.count("students/alice/skills", include_self=True) == 3
    groups = tree.group_by_type("students/alice")
    assert len(groups[Skill]) == 2
    assert len(groups[bool]) == 1
