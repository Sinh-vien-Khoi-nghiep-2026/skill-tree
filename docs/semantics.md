# ObjectTree operation semantics

## Repository states

```text
Working Tree          mutable CRUD state, persisted even when dirty
Local History / HEAD  immutable one-parent commits
Remote Tracking       downloaded commits plus REMOTE_HEAD
Remote Store           independent authoritative HEAD
```

The empty committed state is `ROOT`; it contains only the synthetic `/` node. Supported revision expressions are exact commit IDs, `HEAD`, `HEAD~N`, `ROOT`, `REMOTE_HEAD`, and (for snapshots/diffs) `WORKING`.

## Paths and identity

Paths with or without a leading slash resolve to canonical absolute paths. Node identity is a UUID, not a path. A path can change and later be reused by another identity.

- `rename()` and `move()` retain the source node ID. Descendant IDs are also unchanged.
- `copy()` allocates a fresh ID for every copied node.
- `remove()` followed by `add()` is a new identity.
- Sibling names are unique and case-sensitive.

`move(source, target)` and `copy(source, target)` interpret `target` as the complete destination path; its parent must already exist. `add()` creates missing value-less parent containers by default.

## Working mutations and commits

CRUD operations atomically validate and persist the working tree. They do not implicitly create history. `diff("HEAD", "WORKING")` exposes uncommitted changes.

`commit(message)` computes a canonical semantic delta from current `HEAD` to the working tree, validates it by replay, stores it, and advances `HEAD`. An unchanged tree raises `NothingToCommitError`.

Changes are ordered canonical operations:

1. additions;
2. removals;
3. value/metadata/tag updates;
4. direct parent/name moves.

A node moved and updated in one commit appears in both update and move categories, with the same complete before/final records. Values are compared as safe encoded data. Registered-object envelopes are unwrapped for field deltas, so a dataclass level change appears as `level: 0.60 -> 0.82`.

Public commit/diff values are deeply detached copies. Mutating a returned nested dictionary cannot mutate repository history.

## Path logs and scoped diffs

`log()` walks the selected commit ancestry newest first. `log(path)` anchors a stable node ID and checks that identity's subtree before and after every commit. Consequently it:

- follows direct and ancestor rename/move;
- includes added or removed descendants;
- can find the newest historical identity at a path that is now removed;
- does not combine a removed identity with a newly-added identity that reuses its path.

An optional `revision=` anchors the search at an older revision. `limit` is applied after path filtering.

A scoped `diff(a, b, path=...)` includes the IDs in that subtree in either endpoint. If an ancestor was directly moved and thereby changed the subtree's derived paths, the responsible ancestor move is included to explain the location change.

## Transactions

### Unnamed

```python
with tree.transaction():
    ...
```

Working mutations are held in memory and saved once on successful exit. No commit is created. On failure, working state, heads, and known commits are restored. Net-zero intermediate operations do not write or emit events.

### Named

```python
with tree.transaction("Assessment August 2026"):
    ...
```

The tree must be clean at entry. Successful exit computes final-state node events, creates one commit, and writes working state/history/HEAD together to the local store. No intermediate state is visible durably. Nested transactions, explicit commits, and remote operations are rejected.

The per-instance `RLock` is held for the transaction block so another thread cannot observe partial in-memory state.

## Events

Events are synchronous and dispatched only after durable success, outside operation locks. Named/unnamed transaction events describe the final durable diff rather than intermediate calls. A fast-forward pull emits `fetch`, relevant node events, then `pull`.

An exception in a handler propagates to its caller, but durability has already happened and is not rolled back. Remote sync called from an event handler is rejected to avoid recursive `fetch`/`pull`; schedule follow-up synchronization outside the callback.

## Fetch

`fetch()` obtains the remote pack, deeply detaches and validates every commit, imports unknown commits, updates `REMOTE_HEAD`, and persists tracking state. The v0.1 `RemotePack` contract carries the complete single-parent ancestry of its advertised head (not a thin/incremental pack); transports can optimize this in a future compatible protocol version.

```text
Working Tree   unchanged
Local HEAD     unchanged
REMOTE_HEAD    remote's current head
```

Domain serializers are not needed because transfer and replay operate on encoded values.

## Pull (`fast-forward` only)

`pull()` performs fetch first, then uses this table:

| Relationship after fetch | Result |
|---|---|
| local equals remote | no integration |
| remote is ancestor of local | local is already ahead; no integration |
| local is ancestor of remote and working is clean | advance HEAD and working tree |
| local is ancestor of remote and working is dirty | `DirtyWorkingTreeError` |
| neither is ancestor | `DivergedHistoryError` |

Fetch remains successful even if later integration raises, matching Git's remote-tracking behavior.

## Push

`push()` sends the committed ancestry of local `HEAD`; uncommitted working changes are ignored. The remote validates hashes, canonical semantic replay, complete ancestry, and that its actual HEAD is an ancestor of the proposal in one remote transaction. Otherwise it raises `NonFastForwardError`.

After acceptance, local `REMOTE_HEAD` is saved. This final save is not atomically coupled to an independent remote store. If it fails, the remote may already have advanced; a subsequent `fetch()` is the idempotent recovery path.

## Concurrency limits

One `ObjectTree` is thread-safe. SQLite prevents partial file writes, while optimistic generations reject stale local writers. The library deliberately does not merge writes from independent processes, lock an entire transaction across multiple repository files, or provide distributed consensus.
