"""Safe, explicit serialization of Python values."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass as dataclass_decorator
from dataclasses import fields, is_dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from .exceptions import SerializationError, UnknownTypeError, UnsupportedVersionError

T = TypeVar("T")
DumpFunction = Callable[[Any], object]
LoadFunction = Callable[[object], Any]
Migration = Callable[[object], object]
_TAG = "$objecttree"


@runtime_checkable
class Serializer(Protocol):
    """Byte serializer extension point."""

    def dump(self, obj: object) -> bytes: ...

    def load(self, data: bytes) -> object: ...


@dataclass_decorator(frozen=True, slots=True)
class _Registration:
    cls: type[Any]
    type_id: str
    version: int
    dump: DumpFunction
    load: LoadFunction
    migrations: Mapping[int, Migration]


class SerializerRegistry:
    """Registry-backed recursive encoder that never imports or executes unknown types."""

    def __init__(self) -> None:
        self._by_class: dict[type[Any], _Registration] = {}
        self._by_id: dict[str, _Registration] = {}

    def register(
        self,
        cls: type[T],
        dump: Callable[[T], object],
        load: Callable[[object], T],
        *,
        type_id: str | None = None,
        version: int = 1,
        migrations: Mapping[int, Migration] | None = None,
    ) -> None:
        """Register an exact Python type and its trusted conversion callbacks.

        A migration keyed by ``n`` converts decoded data from version ``n`` to
        version ``n + 1``. Migrations are applied sequentially before ``load``.
        """
        if not isinstance(cls, type):
            raise SerializationError("registered object must be a type")
        identifier = f"{cls.__module__}.{cls.__qualname__}" if type_id is None else type_id
        if not identifier or not isinstance(identifier, str):
            raise SerializationError("type_id must be a non-empty string")
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise SerializationError("serializer version must be a positive integer")
        if not callable(dump) or not callable(load):
            raise SerializationError("dump and load callbacks must be callable")
        migration_map = dict(migrations or {})
        if any(
            not isinstance(source, int)
            or isinstance(source, bool)
            or source < 1
            or source >= version
            or not callable(migration)
            for source, migration in migration_map.items()
        ):
            raise SerializationError(
                "migration keys must be versions before the registered version and values callable"
            )
        if cls in self._by_class:
            raise SerializationError(f"type is already registered: {cls!r}")
        if identifier in self._by_id:
            raise SerializationError(f"type_id is already registered: {identifier!r}")
        registration = _Registration(
            cls=cls,
            type_id=identifier,
            version=version,
            dump=dump,
            load=load,
            migrations=migration_map,
        )
        self._by_class[cls] = registration
        self._by_id[identifier] = registration

    def register_dataclass(
        self,
        cls: type[T],
        *,
        type_id: str | None = None,
        version: int = 1,
        migrations: Mapping[int, Migration] | None = None,
    ) -> None:
        """Register a dataclass using its declared fields and keyword constructor."""
        if not isinstance(cls, type) or not is_dataclass(cls):
            raise SerializationError(f"not a dataclass type: {cls!r}")
        names = tuple(field.name for field in fields(cls) if field.init)

        def dump_dataclass(value: T) -> object:
            return {name: getattr(value, name) for name in names}

        def load_dataclass(data: object) -> T:
            if not isinstance(data, Mapping):
                raise SerializationError("dataclass payload must decode to a mapping")
            return cls(**dict(data))

        self.register(
            cls,
            dump_dataclass,
            load_dataclass,
            type_id=type_id,
            version=version,
            migrations=migrations,
        )

    def encode(self, obj: object) -> object:
        """Encode a value into a deterministic JSON-compatible structure."""
        try:
            return self._encode(obj, set())
        except RecursionError as exc:
            raise SerializationError("value nesting is too deep") from exc

    def _encode(self, obj: object, active: set[int]) -> object:
        if obj is None or isinstance(obj, (str, bool, int)):
            return obj
        if isinstance(obj, float):
            if not math.isfinite(obj):
                raise SerializationError("non-finite floats are not supported")
            return obj

        registration = self._by_class.get(type(obj))
        is_container = isinstance(obj, (list, tuple, Mapping))
        if not is_container and registration is None:
            cls = type(obj)
            raise UnknownTypeError(f"type {cls.__module__}.{cls.__qualname__} is not registered")
        identity = id(obj)
        if identity in active:
            raise SerializationError("cyclic values are not supported")
        active.add(identity)
        try:
            if isinstance(obj, list):
                return [self._encode(item, active) for item in obj]
            if isinstance(obj, tuple):
                return {
                    _TAG: "tuple",
                    "items": [self._encode(item, active) for item in obj],
                }
            if isinstance(obj, Mapping):
                if not all(isinstance(key, str) for key in obj):
                    raise SerializationError("mapping keys must be strings")
                return {
                    _TAG: "mapping",
                    "items": [[key, self._encode(obj[key], active)] for key in sorted(obj)],
                }

            assert registration is not None
            try:
                raw_data = registration.dump(obj)
            except SerializationError:
                raise
            except Exception as exc:  # trusted callback boundary
                raise SerializationError(
                    f"serializer for {registration.type_id!r} failed while dumping"
                ) from exc
            return {
                _TAG: "object",
                "type": registration.type_id,
                "version": registration.version,
                "data": self._encode(raw_data, active),
            }
        finally:
            active.remove(identity)

    def decode(self, encoded: object) -> object:
        """Decode data, invoking only callbacks already present in this registry."""
        try:
            return self._decode(encoded, set())
        except RecursionError as exc:
            raise SerializationError("encoded value nesting is too deep") from exc

    def _decode(self, encoded: object, active: set[int]) -> object:
        if encoded is None or isinstance(encoded, (str, bool, int)):
            return encoded
        if isinstance(encoded, float):
            if not math.isfinite(encoded):
                raise SerializationError("encoded non-finite float")
            return encoded
        if not isinstance(encoded, (list, dict)):
            raise SerializationError(f"invalid encoded value: {type(encoded).__name__}")

        identity = id(encoded)
        if identity in active:
            raise SerializationError("cyclic encoded data is not supported")
        active.add(identity)
        try:
            if isinstance(encoded, list):
                return [self._decode(item, active) for item in encoded]

            tag = encoded.get(_TAG)
            if tag == "tuple":
                items = encoded.get("items")
                if set(encoded) != {_TAG, "items"} or not isinstance(items, list):
                    raise SerializationError("malformed tuple envelope")
                return tuple(self._decode(item, active) for item in items)
            if tag == "mapping":
                items = encoded.get("items")
                if set(encoded) != {_TAG, "items"} or not isinstance(items, list):
                    raise SerializationError("malformed mapping envelope")
                result: dict[str, object] = {}
                for pair in items:
                    if (
                        not isinstance(pair, list)
                        or len(pair) != 2
                        or not isinstance(pair[0], str)
                        or pair[0] in result
                    ):
                        raise SerializationError("malformed or duplicate mapping entry")
                    result[pair[0]] = self._decode(pair[1], active)
                return result
            if tag != "object":
                raise SerializationError("unknown serialization envelope")
            if set(encoded) != {_TAG, "type", "version", "data"}:
                raise SerializationError("malformed object envelope")

            type_id = encoded.get("type")
            version = encoded.get("version")
            if not isinstance(type_id, str):
                raise SerializationError("object type identifier must be a string")
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                raise SerializationError("object version must be a positive integer")
            registration = self._by_id.get(type_id)
            if registration is None:
                raise UnknownTypeError(f"no serializer registered for type_id {type_id!r}")
            if version > registration.version:
                raise UnsupportedVersionError(
                    f"{type_id!r} data is version {version}; registered version is "
                    f"{registration.version}"
                )

            data = self._decode(encoded["data"], active)
            current = version
            while current < registration.version:
                migration = registration.migrations.get(current)
                if migration is None:
                    raise UnsupportedVersionError(
                        f"no migration for {type_id!r} from version {current}"
                    )
                try:
                    data = migration(data)
                except SerializationError:
                    raise
                except Exception as exc:  # trusted callback boundary
                    raise SerializationError(
                        f"migration for {type_id!r} from version {current} failed"
                    ) from exc
                current += 1

            try:
                value = registration.load(data)
            except SerializationError:
                raise
            except Exception as exc:  # trusted callback boundary
                raise SerializationError(f"loader for {type_id!r} failed") from exc
            if type(value) is not registration.cls:
                raise SerializationError(
                    f"loader for {type_id!r} returned {type(value).__name__}, "
                    f"expected {registration.cls.__name__}"
                )
            return value
        finally:
            active.remove(identity)

    def semantic_data(self, encoded: object) -> object:
        """Return callback-free plain data used to produce semantic field diffs."""
        if encoded is None or isinstance(encoded, (str, bool, int, float)):
            return encoded
        if isinstance(encoded, list):
            return [self.semantic_data(item) for item in encoded]
        if not isinstance(encoded, dict):
            return repr(encoded)
        tag = encoded.get(_TAG)
        if tag == "tuple":
            return [self.semantic_data(item) for item in encoded.get("items", [])]
        if tag == "mapping":
            items = encoded.get("items", [])
            if not isinstance(items, list):
                return repr(encoded)
            return {
                pair[0]: self.semantic_data(pair[1])
                for pair in items
                if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
            }
        if tag == "object":
            data = self.semantic_data(encoded.get("data"))
            if isinstance(data, dict):
                return {
                    "$type": encoded.get("type"),
                    "$version": encoded.get("version"),
                    **data,
                }
            return {
                "$type": encoded.get("type"),
                "$version": encoded.get("version"),
                "value": data,
            }
        return repr(encoded)


class JsonSerializer:
    """UTF-8 JSON byte serializer backed by a :class:`SerializerRegistry`."""

    def __init__(self, registry: SerializerRegistry | None = None) -> None:
        self.registry = registry or SerializerRegistry()

    def dump(self, obj: object) -> bytes:
        try:
            text = json.dumps(
                self.registry.encode(obj),
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError) as exc:
            raise SerializationError("value cannot be encoded as JSON") from exc
        return text.encode("utf-8")

    def load(self, data: bytes) -> object:
        try:
            encoded = json.loads(
                data.decode("utf-8"),
                parse_constant=lambda value: _raise_invalid_constant(value),
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SerializationError("invalid UTF-8 JSON serializer payload") from exc
        return self.registry.decode(encoded)


def _raise_invalid_constant(value: str) -> object:
    raise SerializationError(f"invalid JSON numeric constant: {value}")
