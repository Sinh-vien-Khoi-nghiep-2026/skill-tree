from __future__ import annotations

import pytest

from examples.career import (
    SKILL_TYPE_ID,
    Career,
    Interest,
    Skill,
    Student,
    average_skill_level,
    create_career_registry,
    recommend_careers,
    register_career_types,
    skill_growth,
    strongest_skills,
)
from objecttree import ObjectTree
from objecttree.remote import MemoryRemote

ALICE = "/students/alice"
PYTHON = f"{ALICE}/skills/programming/python"
STATISTICS = f"{ALICE}/skills/mathematics/statistics"


def test_career_example_smoke() -> None:
    remote = MemoryRemote()
    tree = ObjectTree(remote=remote, author="test-counselor")
    register_career_types(tree)

    encoded_skill = tree.registry.encode(Skill("Python", 0.60))
    assert isinstance(encoded_skill, dict)
    assert encoded_skill["type"] == SKILL_TYPE_ID

    with tree.transaction("Initial assessment"):
        tree.add(ALICE, Student("Alice"))
        tree.add(PYTHON, Skill("Python", 0.60, 0.90))
        tree.add(STATISTICS, Skill("Statistics", 0.40, 0.80))
        tree.add(f"{ALICE}/skills/communication", Skill("Communication", 0.65, 0.85))
        tree.add(
            f"{ALICE}/interests/artificial-intelligence",
            Interest("Artificial Intelligence", 0.88),
        )
        tree.add(f"{ALICE}/interests/systems", Interest("Systems", 0.78))
        tree.add(
            "/career-catalog/ml-engineer",
            Career(
                "Machine Learning Engineer",
                ("Python", "Statistics"),
                ("Artificial Intelligence",),
            ),
        )
        tree.add(
            "/career-catalog/backend-engineer",
            Career("Backend Engineer", ("Python", "Communication"), ("Systems",)),
        )
    initial = tree.show()

    with tree.transaction("Follow-up assessment"):
        tree.set(PYTHON, Skill("Python", 0.82, 0.95))
        tree.set(STATISTICS, Skill("Statistics", 0.62, 0.90))
        tree.add(f"{ALICE}/skills/programming/rust", Skill("Rust", 0.55, 0.75))
        tree.set(
            f"{ALICE}/interests/artificial-intelligence",
            Interest("Artificial Intelligence", 0.94),
        )
    follow_up = tree.show()

    assert [commit.message for commit in tree.log(PYTHON)] == [
        "Follow-up assessment",
        "Initial assessment",
    ]
    python_update = next(
        change
        for change in tree.diff(initial.id, follow_up.id, path=ALICE).updated
        if change.new_path == PYTHON
    )
    level_delta = next(delta for delta in python_update.deltas if delta.field == "level")
    assert level_delta.before == pytest.approx(0.60)
    assert level_delta.after == pytest.approx(0.82)

    assert average_skill_level(tree, ALICE, revision=follow_up.id) == pytest.approx(0.66)
    assert [skill.name for skill in strongest_skills(tree, ALICE)] == [
        "Python",
        "Communication",
        "Statistics",
    ]
    growth = {
        item.skill: item.delta
        for item in skill_growth(
            tree,
            initial.id,
            follow_up.id,
            student_path=ALICE,
        )
    }
    assert growth["Python"] == pytest.approx(0.22)
    assert growth["Statistics"] == pytest.approx(0.22)
    assert "Rust" not in growth

    recommendations = recommend_careers(tree, ALICE, revision=follow_up.id)
    assert recommendations[0].career.name == "Machine Learning Engineer"
    assert recommendations[0].score > recommendations[1].score

    pushed = tree.push()
    assert len(pushed.sent_commits) == 2

    replica = ObjectTree(
        registry=create_career_registry(),
        remote=remote,
        author="test-replica",
    )
    fetched = replica.fetch()
    assert len(fetched.received_commits) == 2
    assert not replica.exists(ALICE)

    pulled = replica.pull()
    assert pulled.fast_forwarded
    assert replica.head == follow_up.id
    assert replica.get(ALICE) == Student("Alice")
    assert replica.get(PYTHON) == Skill("Python", 0.82, 0.95)
