# Safe object serialization

## Threat model

ObjectTree does not use `pickle`, import a type named by stored data, or execute a loader that the application did not explicitly register. Unknown type envelopes remain opaque until a matching trusted registration is present.

Supported built-in values are:

- `None`, strings, booleans, integers, and finite floats;
- lists and tuples;
- mappings with string keys;
- instances whose **exact type** is registered.

Non-finite floats, cycles, non-string mapping keys, malformed envelopes, unsupported versions, and unregistered object types fail closed with `SerializationError` subclasses.

## Registry

```python
registry.register(
    Skill,
    dump=lambda value: {
        "name": value.name,
        "level": value.level,
    },
    load=lambda data: Skill(**data),
    type_id="career.Skill",
    version=1,
)
```

`dump` returns another supported value. ObjectTree recursively encodes it. `load` receives fully decoded data and must return an instance of the registered class.

Choose type IDs as stable application schema identifiers, not necessarily current module paths. Duplicate classes or IDs are rejected.

## Dataclasses

```python
registry.register_dataclass(
    Skill,
    type_id="career.Skill",
    version=1,
)
```

The convenience serializer writes declared `init=True` fields and reconstructs with keyword arguments. Nested values are recursively encoded, so a nested custom class also needs registration. Classes requiring special constructors, invariants, or schema shape should use explicit callbacks.

The encoded object envelope is conceptually:

```json
{
  "$objecttree": "object",
  "type": "career.Skill",
  "version": 1,
  "data": {
    "$objecttree": "mapping",
    "items": [["level", 0.8], ["name", "Python"]]
  }
}
```

Mappings and tuples are tagged too, preventing a user mapping from being confused with an object envelope. Mapping entries are sorted to produce deterministic commit hashes.

## Versions and migrations

A registration has one current positive integer version. An older envelope is accepted only when every sequential migration is supplied:

```python
registry.register_dataclass(
    SkillV2,
    type_id="career.Skill",
    version=2,
    migrations={
        1: lambda old: {**old, "confidence": 1.0},
    },
)
```

Migration `n` converts decoded data from version `n` to `n + 1`. Missing migration steps and data newer than the registered version raise `UnsupportedVersionError`. ObjectTree never guesses a schema conversion.

Changing a dumper's meaning without incrementing its version is an application schema bug. Dump output must be deterministic because encoded values participate in commit IDs.

## Reopening and synchronization

Registrations contain Python callables, are trusted process configuration, and are never persisted. Re-register before decoding after every process start:

```python
registry = SerializerRegistry()
registry.register_dataclass(Skill, type_id="career.Skill")
tree = ObjectTree.open("./student-data", registry=registry)
```

Alternatively, open first and register before `get()`, `node()`, traversal, query, or snapshot decoding:

```python
tree = ObjectTree.open("./student-data")
tree.register_dataclass(Skill, type_id="career.Skill")
```

Opening structure, checking `exists()`, removing by path, reading commit metadata, fetch, and push do not decode domain values. Operations that return a decoded node/value (including move/copy return values) require the serializer for that node.

## Byte serializer extension point

`Serializer` is the minimal `dump(object) -> bytes` / `load(bytes) -> object` protocol. `JsonSerializer` is the standard implementation around `SerializerRegistry`:

```python
serializer = JsonSerializer(registry)
payload = serializer.dump(value)
assert serializer.load(payload) == value
```

Stores and remotes persist canonical repository JSON rather than arbitrary serializer bytes so they can validate and replay history without importing domain classes.

## Trust boundary

Registration callbacks and migrations are application code and therefore trusted. Do not register a loader from an untrusted plugin merely because data requests its type ID. File/remote contents are data only; without a registration, they cannot choose executable code.
