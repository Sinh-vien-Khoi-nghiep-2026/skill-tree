# ObjectTree

A small, typed **versioned hierarchical object store** for Python 3.12+. Nodes hold real Python objects, retain stable identity across rename/move, and use a Git-like working tree / local history / remote workflow.

ObjectTree is not Git, an ORM, or a database engine. It is deliberately a compact core with serializer, store, and remote extension points.

## Install for development

```bash
python -m pip install -e '.[dev]'
pytest
```

There are no runtime dependencies outside the standard library.

## Quick start

```python
from dataclasses import dataclass

from objecttree import ObjectTree
from objecttree.remote import FileRemote


@dataclass(frozen=True)
class Skill:
    name: str
    level: float
    confidence: float = 1.0


remote = FileRemote("./student-data.remote")
tree = ObjectTree.open("./student-data", remote=remote, author="counselor")
tree.register_dataclass(Skill, type_id="career.Skill", version=1)

with tree.transaction("Alice initial assessment"):
    tree.add(
        "students/alice/skills/python",
        Skill("Python", 0.65),
        metadata={"source": "assessment"},
    )

tree.push()
```

A later assessment:

```python
# Register trusted classes again in every new process before decoding them.
tree = ObjectTree.open("./student-data", remote=remote)
tree.register_dataclass(Skill, type_id="career.Skill", version=1)

with tree.transaction("Alice second assessment"):
    tree.set("students/alice/skills/python", Skill("Python", 0.85))

for commit in tree.log("students/alice/skills/python"):
    print(commit.message)

for change in tree.diff("HEAD~1", "HEAD", path="students/alice"):
    for delta in change.deltas:
        print(change.path, delta.field, delta.before, "->", delta.after)
# /students/alice/skills/python level 0.65 -> 0.85
```

## Tree and query API

```python
tree["students/alice/skills/python"]
tree.get(path)
tree.node(path)  # immutable TreeNode view
tree.exists(path)

tree.add(path, value)
tree.set(path, value)
tree.remove(path)
tree.move(source, target)  # target is the complete destination path
tree.copy(source, target)
tree.rename(path, new_name)

tree.children(path)
tree.parent(path)
tree.walk(path="/")
tree.find(type=Skill)
tree.find(lambda node: isinstance(node.value, Skill) and node.value.level >= 0.7)
tree.count(path)
tree.group_by_type(path)
```

Paths are canonicalized to `/...` and are only a convenience interface. Internally, UUID node IDs are authoritative. Move and rename preserve IDs; copy allocates new IDs.

Values returned by `get()` are decoded copies. Mutating one does not mutate the tree; call `set()` explicitly.

## Working tree and history

Mutations update and durably save the working tree, but do not advance `HEAD` until `commit()`:

```python
tree.commit("Update assessment", author="Alice")
tree.log(limit=10)
tree.show("HEAD~1")
tree.snapshot("HEAD")
tree.diff("HEAD", "WORKING")
```

Commits are one-parent immutable change sets (`ADD`, `REMOVE`, `UPDATE`, `MOVE`), not full-tree copies. `log(path)` follows stable identity through ancestor rename/move and includes changes to descendants. History is linear in v0.1; there are no branches or merges.

Named transactions require a clean working tree and create exactly one commit. Unnamed transactions atomically save working changes without creating history. Both roll back completely on block or storage failure:

```python
with tree.transaction("Update Alice"):
    tree.set(...)
    tree.add(...)
```

## Remote semantics

A remote must be configured explicitly (`MemoryRemote`, `FileRemote`, or a custom `RemoteStore`).

| Operation | Working tree | Local `HEAD` | `REMOTE_HEAD` |
|---|---:|---:|---:|
| `fetch()` | unchanged | unchanged | updated |
| `pull()` fast-forward | updated, must be clean | advanced | updated |
| `push()` | ignored | unchanged | updated after acceptance |

```python
tree.fetch()  # download only
tree.pull(strategy="fast-forward")  # fetch + integrate
tree.push()  # remote rejects non-fast-forward history
```

Divergent pull raises `DivergedHistoryError`; stale push raises `NonFastForwardError`. Merge strategies are an intentional future extension.

## Safe serialization

ObjectTree never uses pickle or dynamic imports. Primitive containers are supported directly; every other exact Python type must be explicitly registered with a stable type ID and version:

```python
tree.register(
    MyType,
    dump_my_type,
    load_my_type,
    type_id="myapp.MyType",
    version=2,
    migrations={1: migrate_v1_to_v2},
)
```

Dataclass registration is a convenience. Registrations are trusted executable application configuration and are **not persisted**, so reopening processes and sync peers register them again. Unknown encoded types do not prevent structural loading, logs, fetch, or push; decoding/value-returning operations require their serializer.

## Persistence and concurrency

- `MemoryStore`: thread-safe, ephemeral repository document.
- `FileStore`: `<repo>/objecttree.sqlite3`, canonical JSON inside a SQLite transaction.
- `MemoryRemote`: thread-safe ephemeral remote.
- `FileRemote`: one SQLite file at the supplied path.

SQLite `BEGIN IMMEDIATE` prevents torn writes. Local stores also use generation checks, so a stale second process gets `ConcurrentWriteError` rather than overwriting newer data. Automatic multi-process merging is out of scope.

A remote push and local tracking-state save cannot be one cross-system transaction. If the latter fails after remote acceptance, `fetch()` safely repairs local tracking state.

## Events

```python
unsubscribe = tree.on("node_updated", lambda event: recalculate(event.node_id))
tree.on("commit", audit)
unsubscribe()
```

Supported events are `node_added`, `node_updated`, `node_removed`, `node_moved`, `commit`, `fetch`, `pull`, and `push`. Handlers run synchronously after durable state and outside operation locks. Handler failures do not roll back state. Remote operations from a handler are rejected to prevent recursive synchronization; schedule them after the callback instead.

## Documentation and example

- [Architecture and design decisions](docs/architecture.md)
- [Detailed operation semantics](docs/semantics.md)
- [Serializer safety and versioning](docs/serialization.md)
- [Career-counseling example](examples/career/README.md)

Run the full example:

```bash
python -m examples.career.demo
```

## v0.1 limits

The implementation favors a small, understandable source tree. It rewrites one JSON repository document per local durable operation and replays deltas to reconstruct revisions; it is intended for small/medium domain repositories, not millions of nodes or commits. There are no branches, merges, network protocol, query language, async core, or automatic cross-process conflict resolution.
