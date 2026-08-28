from __future__ import annotations

from dataclasses import dataclass

import pytest

from objecttree import (
    JsonSerializer,
    ObjectTree,
    SerializationError,
    SerializerRegistry,
    UnknownTypeError,
    UnsupportedVersionError,
)
from objecttree.store import MemoryStore

from .conftest import Skill, skill_registry


@dataclass(frozen=True)
class Token:
    text: str


@dataclass(frozen=True)
class Versioned:
    name: str
    score: float = 0.0


def test_registered_dataclass_round_trip_and_type_envelope() -> None:
    registry = skill_registry()
    value = Skill("Python", 0.8, 0.9)

    encoded = registry.encode(value)
    assert isinstance(encoded, dict)
    assert encoded["$objecttree"] == "object"
    assert encoded["type"] == "tests.Skill"
    assert encoded["version"] == 1
    assert registry.decode(encoded) == value


def test_custom_serializer_and_nested_safe_values_round_trip() -> None:
    registry = SerializerRegistry()
    registry.register(
        Token,
        lambda token: {"text": token.text},
        lambda data: Token(data["text"]),
        type_id="tests.Token",
    )
    value = {
        "tokens": [Token("a"), Token("b")],
        "position": (1, 2),
        "enabled": True,
    }

    assert registry.decode(registry.encode(value)) == value
    serializer = JsonSerializer(registry)
    assert serializer.load(serializer.dump(value)) == value


def test_unregistered_types_and_unsafe_primitives_are_rejected() -> None:
    registry = SerializerRegistry()
    with pytest.raises(UnknownTypeError):
        registry.encode(Token("secret"))
    with pytest.raises(SerializationError):
        registry.encode({1: "non-string key"})
    with pytest.raises(SerializationError):
        registry.encode(float("nan"))
    with pytest.raises(SerializationError):
        registry.decode({"unexpected": "raw mapping"})


def test_unknown_type_can_be_loaded_structurally_then_registered() -> None:
    store = MemoryStore()
    writer = ObjectTree(store, registry=skill_registry())
    writer.add("skills/python", Skill("Python", 0.7))
    writer.commit("Assessment")

    reader = ObjectTree(store)
    assert reader.exists("skills/python")
    assert reader.log()[0].message == "Assessment"
    with pytest.raises(UnknownTypeError):
        reader.get("skills/python")

    reader.register_dataclass(Skill, type_id="tests.Skill")
    assert reader.get("skills/python") == Skill("Python", 0.7)


def test_version_migration_is_explicit_and_sequential() -> None:
    old = SerializerRegistry()
    old.register(
        Versioned,
        lambda value: {"name": value.name},
        lambda data: Versioned(data["name"]),
        type_id="tests.Versioned",
        version=1,
    )
    encoded = old.encode(Versioned("assessment"))

    current = SerializerRegistry()
    current.register(
        Versioned,
        lambda value: {"name": value.name, "score": value.score},
        lambda data: Versioned(data["name"], data["score"]),
        type_id="tests.Versioned",
        version=2,
        migrations={1: lambda data: {**data, "score": 0.5}},
    )
    assert current.decode(encoded) == Versioned("assessment", 0.5)


def test_missing_migration_and_future_version_fail_closed() -> None:
    registry = SerializerRegistry()
    registry.register_dataclass(
        Versioned,
        type_id="tests.Versioned",
        version=2,
    )
    old_payload = {
        "$objecttree": "object",
        "type": "tests.Versioned",
        "version": 1,
        "data": registry.encode({"name": "old"}),
    }
    future_payload = {**old_payload, "version": 3}

    with pytest.raises(UnsupportedVersionError, match="no migration"):
        registry.decode(old_payload)
    with pytest.raises(UnsupportedVersionError, match="version 3"):
        registry.decode(future_payload)


def test_cycles_invalid_versions_and_wrong_loader_types_fail_closed() -> None:
    registry = SerializerRegistry()
    cyclic: list[object] = []
    cyclic.append(cyclic)
    with pytest.raises(SerializationError, match="cyclic"):
        registry.encode(cyclic)

    registry.register(
        Token,
        lambda token: token.text,
        lambda data: data,
        type_id="tests.BadToken",
    )
    encoded = registry.encode(Token("x"))
    with pytest.raises(SerializationError, match="returned"):
        registry.decode(encoded)

    encoded["version"] = 0
    with pytest.raises(SerializationError, match="positive"):
        registry.decode(encoded)


def test_duplicate_class_or_type_identifier_registration_is_rejected() -> None:
    registry = SerializerRegistry()
    registry.register_dataclass(Skill, type_id="tests.Skill")
    with pytest.raises(SerializationError, match="already registered"):
        registry.register_dataclass(Skill, type_id="tests.SkillAgain")
    with pytest.raises(SerializationError, match="type_id"):
        registry.register_dataclass(Versioned, type_id="tests.Skill")
