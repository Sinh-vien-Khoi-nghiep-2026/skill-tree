"""Career-counseling values stored by the ObjectTree example."""

from __future__ import annotations

from dataclasses import dataclass


def _name(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _score(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{field} must be a number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be between 0.0 and 1.0")
    return result


@dataclass(frozen=True, slots=True)
class Student:
    """A student profile; the path slug remains separate from the display name."""

    name: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name, "student name"))


@dataclass(frozen=True, slots=True)
class Skill:
    """A normalized assessment score and the assessor's confidence in it."""

    name: str
    level: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name, "skill name"))
        object.__setattr__(self, "level", _score(self.level, "skill level"))
        object.__setattr__(self, "confidence", _score(self.confidence, "skill confidence"))


@dataclass(frozen=True, slots=True)
class Interest:
    """A student's normalized affinity for a topic."""

    name: str
    score: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name, "interest name"))
        object.__setattr__(self, "score", _score(self.score, "interest score"))


@dataclass(frozen=True, slots=True)
class Career:
    """A small career profile used by the recommendation example."""

    name: str
    required_skills: tuple[str, ...] = ()
    related_interests: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _name(self.name, "career name"))
        object.__setattr__(
            self,
            "required_skills",
            tuple(_name(item, "required skill") for item in self.required_skills),
        )
        object.__setattr__(
            self,
            "related_interests",
            tuple(_name(item, "related interest") for item in self.related_interests),
        )
