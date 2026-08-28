"""Career-specific analytics built only from ObjectTree's public read API."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from objecttree import ObjectTree, TreeNode

from .models import Career, Interest, Skill


@dataclass(frozen=True, slots=True)
class SkillGrowth:
    """The level change for one stable skill node across two revisions."""

    skill: str
    before: float
    after: float

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True, slots=True)
class CareerRecommendation:
    """A transparent score rather than a domain object persisted in the tree."""

    career: Career
    score: float
    skill_score: float
    interest_score: float


def average_skill_level(
    tree: ObjectTree,
    student_path: str,
    *,
    revision: str = "WORKING",
) -> float:
    """Return the arithmetic mean of assessed levels, or ``0.0`` if empty."""

    nodes = tuple(tree.snapshot(revision))
    student = _require_path(nodes, student_path)
    skills = _skills(nodes, student.id).values()
    levels = [skill.level for skill in skills]
    return sum(levels) / len(levels) if levels else 0.0


def strongest_skills(
    tree: ObjectTree,
    student_path: str,
    *,
    revision: str = "WORKING",
    limit: int = 3,
) -> tuple[Skill, ...]:
    """Return skills by level, then confidence, with deterministic tie breaking."""

    if limit < 0:
        raise ValueError("limit cannot be negative")
    nodes = tuple(tree.snapshot(revision))
    student = _require_path(nodes, student_path)
    ordered = sorted(
        _skills(nodes, student.id).values(),
        key=lambda skill: (-skill.level, -skill.confidence, skill.name.casefold()),
    )
    return tuple(ordered[:limit])


def skill_growth(
    tree: ObjectTree,
    old_revision: str,
    new_revision: str,
    *,
    student_path: str,
) -> tuple[SkillGrowth, ...]:
    """Compare levels for skill node IDs present in both revisions.

    Matching stable IDs means a renamed or moved skill remains comparable. A
    removed and re-added skill is intentionally not treated as continuous.
    """

    before_nodes = tuple(tree.snapshot(old_revision))
    after_nodes = tuple(tree.snapshot(new_revision))
    canonical = _canonical_path(student_path)
    anchor = _find_path(after_nodes, canonical) or _find_path(before_nodes, canonical)
    if anchor is None:
        raise ValueError(f"student path does not exist in either revision: {canonical}")

    before = _skills(before_nodes, anchor.id)
    after = _skills(after_nodes, anchor.id)
    growth = [
        SkillGrowth(after[node_id].name, before[node_id].level, after[node_id].level)
        for node_id in before.keys() & after.keys()
    ]
    growth.sort(key=lambda item: item.skill.casefold())
    return tuple(growth)


def recommend_careers(
    tree: ObjectTree,
    student_path: str,
    careers: Iterable[Career] | None = None,
    *,
    revision: str = "WORKING",
    limit: int = 3,
) -> tuple[CareerRecommendation, ...]:
    """Rank careers with a deliberately small and explainable heuristic.

    Skill fit is the mean of ``level * confidence`` for required skills, with
    missing skills scoring zero. Interest fit follows the same missing-as-zero
    rule. When both dimensions are present, the total is 70% skill and 30%
    interest; a career defining only one dimension uses that dimension alone.
    """

    if limit < 0:
        raise ValueError("limit cannot be negative")
    nodes = tuple(tree.snapshot(revision))
    student = _require_path(nodes, student_path)
    skill_values = _skills(nodes, student.id).values()
    interest_values = _interests(nodes, student.id).values()

    skill_scores: dict[str, float] = {}
    for skill in skill_values:
        key = _key(skill.name)
        skill_scores[key] = max(skill_scores.get(key, 0.0), skill.level * skill.confidence)
    interest_scores: dict[str, float] = {}
    for interest in interest_values:
        key = _key(interest.name)
        interest_scores[key] = max(interest_scores.get(key, 0.0), interest.score)

    candidates = (
        tuple(careers)
        if careers is not None
        else tuple(node.value for node in nodes if isinstance(node.value, Career))
    )
    recommendations: list[CareerRecommendation] = []
    for career in candidates:
        skill_score = _criteria_score(career.required_skills, skill_scores)
        interest_score = _criteria_score(career.related_interests, interest_scores)
        if career.required_skills and career.related_interests:
            score = 0.7 * skill_score + 0.3 * interest_score
        elif career.required_skills:
            score = skill_score
        elif career.related_interests:
            score = interest_score
        else:
            score = 0.0
        recommendations.append(CareerRecommendation(career, score, skill_score, interest_score))

    recommendations.sort(key=lambda item: (-item.score, item.career.name.casefold()))
    return tuple(recommendations[:limit])


def _canonical_path(path: str) -> str:
    if not isinstance(path, str):
        raise TypeError("student_path must be a string")
    stripped = path.strip("/")
    parts = stripped.split("/") if stripped else []
    if not parts or any(not part or part in {".", ".."} or "\x00" in part for part in parts):
        raise ValueError(f"invalid student path: {path!r}")
    return "/" + "/".join(parts)


def _require_path(nodes: Sequence[TreeNode[object]], path: str) -> TreeNode[object]:
    canonical = _canonical_path(path)
    node = _find_path(nodes, canonical)
    if node is None:
        raise ValueError(f"student path does not exist at this revision: {canonical}")
    return node


def _find_path(
    nodes: Sequence[TreeNode[object]],
    canonical_path: str,
) -> TreeNode[object] | None:
    return next((node for node in nodes if node.path == canonical_path), None)


def _skills(
    nodes: Sequence[TreeNode[object]],
    student_id: str,
) -> dict[str, Skill]:
    return _typed_descendants(nodes, student_id, "skills", Skill)


def _interests(
    nodes: Sequence[TreeNode[object]],
    student_id: str,
) -> dict[str, Interest]:
    return _typed_descendants(nodes, student_id, "interests", Interest)


def _typed_descendants[ValueT](
    nodes: Sequence[TreeNode[object]],
    root_id: str,
    branch: str,
    value_type: type[ValueT],
) -> dict[str, ValueT]:
    root = next((node for node in nodes if node.id == root_id), None)
    if root is None:
        return {}
    prefix = f"{root.path}/{branch}/"
    return {
        node.id: node.value
        for node in nodes
        if node.path.startswith(prefix) and isinstance(node.value, value_type)
    }


def _criteria_score(names: tuple[str, ...], scores: dict[str, float]) -> float:
    if not names:
        return 0.0
    return sum(scores.get(_key(name), 0.0) for name in names) / len(names)


def _key(name: str) -> str:
    return name.strip().casefold()
