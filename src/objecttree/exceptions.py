"""Exception hierarchy for ObjectTree."""


class ObjectTreeError(Exception):
    """Base class for all library-defined errors."""


class NodeNotFoundError(ObjectTreeError, KeyError):
    """Raised when a path or node ID cannot be resolved."""


class NodeAlreadyExistsError(ObjectTreeError):
    """Raised when a destination path is already occupied."""


class InvalidPathError(ObjectTreeError, ValueError):
    """Raised when a path or node name is invalid."""


class SerializationError(ObjectTreeError):
    """Base class for safe serialization failures."""


class UnknownTypeError(SerializationError):
    """Raised when an object type has not been explicitly registered."""


class UnsupportedVersionError(SerializationError):
    """Raised when encoded object data has an unsupported version."""


class StoreError(ObjectTreeError):
    """Base class for local persistence failures."""


class CorruptStoreError(StoreError):
    """Raised when persisted repository data is malformed."""


class ConcurrentWriteError(StoreError):
    """Raised when another writer advanced a store generation."""


class RevisionNotFoundError(ObjectTreeError):
    """Raised when a revision expression cannot be resolved."""


class NothingToCommitError(ObjectTreeError):
    """Raised when the working tree is identical to HEAD."""


class TransactionError(ObjectTreeError):
    """Raised for invalid transaction usage."""


class DirtyWorkingTreeError(TransactionError):
    """Raised when an operation requires a clean working tree."""


class RemoteError(ObjectTreeError):
    """Base class for remote synchronization failures."""


class RemoteNotConfiguredError(RemoteError):
    """Raised when synchronization is attempted without a remote."""


class DivergedHistoryError(RemoteError):
    """Raised when local and remote histories require a merge."""


class NonFastForwardError(RemoteError):
    """Raised when a push would discard commits at the remote."""
