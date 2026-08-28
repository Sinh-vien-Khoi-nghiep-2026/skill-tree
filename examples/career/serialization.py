"""Explicit serializer registration for the career domain."""

from __future__ import annotations

from objecttree import ObjectTree, SerializerRegistry

from .models import Career, Interest, Skill, Student

STUDENT_TYPE_ID = "career.Student"
SKILL_TYPE_ID = "career.Skill"
INTEREST_TYPE_ID = "career.Interest"
CAREER_TYPE_ID = "career.Career"


def register_career_types[Registrar: (ObjectTree, SerializerRegistry)](
    target: Registrar,
) -> Registrar:
    """Register every persisted career value with stable, module-independent IDs.

    ``target`` may be either an :class:`ObjectTree` or its standalone
    :class:`SerializerRegistry`. Call this once for each new registry.
    """

    target.register_dataclass(Student, type_id=STUDENT_TYPE_ID, version=1)
    target.register_dataclass(Skill, type_id=SKILL_TYPE_ID, version=1)
    target.register_dataclass(Interest, type_id=INTEREST_TYPE_ID, version=1)
    target.register_dataclass(Career, type_id=CAREER_TYPE_ID, version=1)
    return target


def create_career_registry() -> SerializerRegistry:
    """Create a registry ready to decode career objects from history or a remote."""

    return register_career_types(SerializerRegistry())
