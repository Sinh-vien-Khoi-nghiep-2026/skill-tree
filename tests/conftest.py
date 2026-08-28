from __future__ import annotations

from dataclasses import dataclass

import pytest

from objecttree import ObjectTree, SerializerRegistry


@dataclass(frozen=True)
class Skill:
    name: str
    level: float
    confidence: float = 1.0


def skill_registry() -> SerializerRegistry:
    registry = SerializerRegistry()
    registry.register_dataclass(Skill, type_id="tests.Skill")
    return registry


@pytest.fixture
def tree() -> ObjectTree:
    return ObjectTree(registry=skill_registry(), author="tester")
