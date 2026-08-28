# ObjectTree architecture (v0.1)

ObjectTree is a small **versioned hierarchical object store**. It borrows the working-tree, commit, fetch, pull, and push vocabulary from Git, but it is not a Git implementation.

## 1. Core concepts

- **Working tree**: the mutable normalized node state exposed by CRUD operations.
- **Local history**: immutable, one-parent commits. `HEAD` names the latest local commit.
- **Remote tracking state**: commits downloaded from a remote plus `REMOTE_HEAD`.
- **Remote state**: an independent `RemoteStore`; the core has no networking code.
- **Serializer registry**: the only component allowed to turn Python objects into safe JSON-compatible values and back.

The career-counseling example depends on ObjectTree. ObjectTree never imports domain models.

## 2. Data model

Nodes are normalized records in a mapping keyed by node ID. A record contains:

- stable UUID ID;
- parent ID and sibling-unique human-readable name;
- encoded optional value and metadata;
- tags;
- UTC `created_at` and `updated_at` timestamps.

A derived `(parent_id, name) -> node_id` index performs path resolution. Child objects are not nested inside parent records. This makes moving a subtree an update to one record rather than a rewrite of every descendant.

Public `TreeNode` values are immutable views. Their path is computed, and their Python value/metadata are newly decoded. Mutating a returned object does not mutate the tree; callers use `set()`.

## 3. Identity, location, and value

Identity, location, and value are intentionally independent:

```text
identity: NodeId (stable UUID)
location: parent NodeId + name (path is derived)
value:    safely encoded object snapshot
```

Rename and move preserve identity. Copy creates fresh IDs for the copied node and every descendant. Remove followed by add creates a new identity even when the path is reused.

Paths may be written with or without a leading slash and are canonicalized to `/...`. Empty segments, `.`, `..`, NUL bytes, and invalid names are rejected. `/` is a fixed synthetic root.

## 4. History model

A commit has an ID, one parent, timestamp, message, author, and semantic changes. IDs are SHA-256 hashes of canonical commit JSON. History is linear for locally-created commits; downloaded histories can temporarily diverge because v0.1 has no merge operation.

Commits store deltas, not deep copies of the whole tree. Each change carries complete before/after node records so replay is deterministic:

- `ADD`: a node appeared;
- `REMOVE`: a node disappeared;
- `UPDATE`: encoded value, metadata, or tags changed;
- `MOVE`: parent and/or name changed.

A moved-and-updated node can have both `MOVE` and `UPDATE` entries. Replay applies the final records as one validated batch. An arbitrary revision is reconstructed by following parents and replaying changes from the fixed empty root. This is deliberately simple; repositories intended to hold very long histories can later add checkpoint snapshots without changing the public API.

`diff()` compares node IDs and returns semantic `TreeDiff` categories, including field-level deltas for registered-object data. `log(path)` resolves the node's stable ID and evaluates its subtree before and after each commit, so history survives rename/move and includes removed descendants.

## 5. Storage model

`ObjectStore` is a tiny optimistic-concurrency protocol over a versioned JSON-compatible repository document. The document contains the persisted working tree, all known commits, local `HEAD`, and `REMOTE_HEAD`.

- `MemoryStore` is useful for tests and ephemeral trees.
- `FileStore` stores one canonical JSON document in SQLite under `<repository>/objecttree.sqlite3`.

SQLite was selected over a loose JSON file because standard-library transactions and `BEGIN IMMEDIATE` prevent torn writes. A generation check rejects stale writers. The v0.1 implementation rewrites one repository document per durable operation; this favors understandability over very large-dataset throughput. Commit history itself remains delta-based.

## 6. Serialization model

No pickle and no dynamic import are used. `SerializerRegistry` accepts only JSON primitives, lists/tuples, string-key mappings, and explicitly registered Python types. Encoded objects contain a stable type ID, integer version, and recursively encoded data.

`register_dataclass()` is a convenience that serializes constructor fields and reconstructs via keyword arguments. Custom dump/load callbacks support other classes. Older versions require explicit sequential migrations; future versions raise a version error rather than being guessed.

Registrations are trusted executable application configuration and are never persisted. A process reopening a repository must register its domain classes again before decoding them. Structural loading, commit metadata, removal, and remote transfer work with unknown encoded types because decoding is lazy. Operations that return a decoded node/value require its serializer.

## 7. Remote synchronization model

`RemoteStore` transfers immutable-model commit packs; it does not know Python domain classes. Every ingress/egress pack is deeply detached and canonically validated so nested mutable JSON data cannot alias repository history.

- `fetch()`: import remote commits and update `REMOTE_HEAD`; working tree and local `HEAD` stay unchanged.
- `pull(strategy="fast-forward")`: fetch, then advance local `HEAD` and the clean working tree only when local `HEAD` is an ancestor of remote `HEAD`. Divergence raises `DivergedHistoryError`.
- `push()`: propose local `HEAD`. The remote atomically accepts only when its actual head is an ancestor of the proposal; otherwise it raises `NonFastForwardError`.

`MemoryRemote` and SQLite-backed `FileRemote` implement the same synchronous protocol. The transport-independent pack objects allow a later async adapter without making the core API async.

## 8. Transaction model

`transaction()` snapshots mappings of immutable records and buffers persistence. On failure, the in-memory state is restored and nothing is written. On success, all working changes are saved once and events are derived from the final durable diff, not intermediate calls.

`transaction("message")` additionally creates one commit in the same local-store write. It requires a clean tree at entry so unrelated pre-existing changes are not accidentally included. Nested transactions, explicit commit, and remote operations inside a transaction are rejected.

The per-tree `RLock` provides thread safety within one process. SQLite plus generation checks prevents file corruption and detects stale writers across processes; ObjectTree does not attempt automatic multi-process merging.

## 9. Public API

```python
ObjectTree.open(path, registry=None, remote=None, author=None)

get / node / get_node_by_id / path_for / exists
add / set / remove / move / copy / rename
children / parent / walk / find / filter / count / group_by_type
commit / show / snapshot / log / diff
transaction
fetch / pull / push
on / off
register / register_dataclass
```

Paths are a convenience API. All internal history and mutation logic uses IDs.

## 10. Package structure

```text
src/objecttree/
├── __init__.py          public exports
├── exceptions.py       compact error hierarchy
├── models.py           immutable public/wire models
├── paths.py            canonical path handling
├── serialization.py    safe registry
├── state.py            normalized state and repository encoding
├── history.py          changes, replay, ancestry, semantic diff
├── tree.py             facade, transactions, events, synchronization
├── store/              ObjectStore, MemoryStore, FileStore
└── remote/             RemoteStore, MemoryRemote, FileRemote

examples/career/        domain models and analytics only
tests/                  behavior-focused pytest suite
```

The modules correspond to real extension boundaries; small event and transaction coordination remain in `tree.py` rather than creating one-class modules.
