"""Run with ``python -m examples.career.demo`` from the repository root."""

from __future__ import annotations

from objecttree import Commit, ObjectTree
from objecttree.remote import MemoryRemote

from .analytics import (
    average_skill_level,
    recommend_careers,
    skill_growth,
    strongest_skills,
)
from .models import Career, Interest, Skill, Student
from .serialization import register_career_types

ALICE_PATH = "/students/alice"
PYTHON_PATH = f"{ALICE_PATH}/skills/programming/python"
STATISTICS_PATH = f"{ALICE_PATH}/skills/mathematics/statistics"


def build_example(
    remote: MemoryRemote | None = None,
) -> tuple[ObjectTree, Commit, Commit, MemoryRemote]:
    """Build two assessment revisions without using any career code in core."""

    remote = remote or MemoryRemote()
    tree = ObjectTree(remote=remote, author="career-demo")
    register_career_types(tree)

    with tree.transaction("Alice initial assessment"):
        tree.add(ALICE_PATH, Student("Alice Nguyen"), tags=("student",))
        tree.add(PYTHON_PATH, Skill("Python", 0.60, 0.90), tags=("assessment",))
        tree.add(STATISTICS_PATH, Skill("Statistics", 0.40, 0.80))
        tree.add(
            f"{ALICE_PATH}/skills/communication",
            Skill("Communication", 0.65, 0.85),
        )
        tree.add(
            f"{ALICE_PATH}/interests/artificial-intelligence",
            Interest("Artificial Intelligence", 0.88),
        )
        tree.add(f"{ALICE_PATH}/interests/systems", Interest("Systems", 0.78))
        tree.add(
            "/career-catalog/ml-engineer",
            Career(
                "Machine Learning Engineer",
                required_skills=("Python", "Statistics"),
                related_interests=("Artificial Intelligence",),
            ),
        )
        tree.add(
            "/career-catalog/data-scientist",
            Career(
                "Data Scientist",
                required_skills=("Python", "Statistics"),
                related_interests=("Artificial Intelligence", "Systems"),
            ),
        )
        tree.add(
            "/career-catalog/backend-engineer",
            Career(
                "Backend Engineer",
                required_skills=("Python", "Communication"),
                related_interests=("Systems",),
            ),
        )
        tree.add(
            "/career-catalog/systems-engineer",
            Career(
                "Systems Engineer",
                required_skills=("Rust", "Communication"),
                related_interests=("Systems",),
            ),
        )
    initial = tree.show()

    with tree.transaction("Alice follow-up assessment"):
        tree.set(PYTHON_PATH, Skill("Python", 0.82, 0.95))
        tree.set(STATISTICS_PATH, Skill("Statistics", 0.62, 0.90))
        tree.add(
            f"{ALICE_PATH}/skills/programming/rust",
            Skill("Rust", 0.55, 0.75),
        )
        tree.set(
            f"{ALICE_PATH}/interests/artificial-intelligence",
            Interest("Artificial Intelligence", 0.94),
        )
    follow_up = tree.show()
    return tree, initial, follow_up, remote


def main() -> None:
    tree, initial, follow_up, remote = build_example()

    print("Two assessment commits:")
    for commit in reversed(tree.log()):
        print(f"  {commit.id[:8]}  {commit.message}")

    print("\nPath history for Python (oldest first):")
    for commit in reversed(tree.log(PYTHON_PATH)):
        print(f"  {commit.message}")

    print("\nSemantic diff for Alice:")
    for change in tree.diff(initial.id, follow_up.id, path=ALICE_PATH):
        print(f"  {change.kind.value.upper():7} {change.path}")
        for delta in change.deltas:
            print(f"           {delta.field}: {delta.before} -> {delta.after}")

    average = average_skill_level(tree, ALICE_PATH, revision=follow_up.id)
    strongest = strongest_skills(tree, ALICE_PATH, revision=follow_up.id)
    growth = skill_growth(
        tree,
        initial.id,
        follow_up.id,
        student_path=ALICE_PATH,
    )
    recommendations = recommend_careers(
        tree,
        ALICE_PATH,
        revision=follow_up.id,
    )

    print(f"\nAverage skill level: {average:.2f}")
    print("Strongest skills: " + ", ".join(skill.name for skill in strongest))
    print("Skill growth:")
    for item in growth:
        print(f"  {item.skill}: {item.before:.2f} -> {item.after:.2f} ({item.delta:+.2f})")
    print("Career recommendations:")
    for item in recommendations:
        print(f"  {item.career.name}: {item.score:.0%}")

    pushed = tree.push()
    replica = ObjectTree(remote=remote, author="counselor-replica")
    register_career_types(replica)
    fetched = replica.fetch()
    working_unchanged = not replica.exists(ALICE_PATH)
    pulled = replica.pull()
    replicated_python = replica.get(PYTHON_PATH)

    print("\nRemote synchronization:")
    print(f"  push sent {len(pushed.sent_commits)} commits")
    print(f"  fetch received {len(fetched.received_commits)} commits")
    print(f"  working tree unchanged by fetch: {working_unchanged}")
    print(f"  pull fast-forwarded: {pulled.fast_forwarded}")
    print(f"  replica Python value: {replicated_python!r}")


if __name__ == "__main__":
    main()
