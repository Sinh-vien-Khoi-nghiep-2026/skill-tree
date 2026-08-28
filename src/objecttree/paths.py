"""Canonical POSIX-like path handling for ObjectTree."""

from __future__ import annotations

from collections.abc import Iterable

from .exceptions import InvalidPathError

ROOT_PATH = "/"


def validate_name(name: str) -> str:
    """Validate and return one node name."""
    if not isinstance(name, str):
        raise InvalidPathError("node names must be strings")
    if not name:
        raise InvalidPathError("node names cannot be empty")
    if name in {".", ".."}:
        raise InvalidPathError(f"reserved node name: {name!r}")
    if "/" in name or "\x00" in name:
        raise InvalidPathError(f"invalid node name: {name!r}")
    return name


def normalize_path(path: str) -> str:
    """Return a canonical absolute path.

    A leading slash is optional. A single trailing slash is accepted, while
    empty internal segments are rejected rather than silently changed.
    """
    if not isinstance(path, str):
        raise InvalidPathError("paths must be strings")
    if "\x00" in path:
        raise InvalidPathError("paths cannot contain NUL bytes")
    if path in {"", "/"}:
        return ROOT_PATH

    raw = path[1:] if path.startswith("/") else path
    if raw.endswith("/"):
        raw = raw[:-1]
    if not raw:
        return ROOT_PATH

    parts = raw.split("/")
    if any(part == "" for part in parts):
        raise InvalidPathError(f"path contains an empty segment: {path!r}")
    for part in parts:
        validate_name(part)
    return "/" + "/".join(parts)


def split_path(path: str) -> tuple[str, ...]:
    canonical = normalize_path(path)
    if canonical == ROOT_PATH:
        return ()
    return tuple(canonical[1:].split("/"))


def join_path(parts: Iterable[str]) -> str:
    checked = tuple(validate_name(part) for part in parts)
    return ROOT_PATH if not checked else "/" + "/".join(checked)


def child_path(parent: str, name: str) -> str:
    canonical = normalize_path(parent)
    checked_name = validate_name(name)
    return f"/{checked_name}" if canonical == ROOT_PATH else f"{canonical}/{checked_name}"


def parent_and_name(path: str) -> tuple[str, str]:
    canonical = normalize_path(path)
    if canonical == ROOT_PATH:
        raise InvalidPathError("the root has no parent or node name")
    parent, _, name = canonical.rpartition("/")
    return parent or ROOT_PATH, validate_name(name)


def is_within(path: str, root: str) -> bool:
    """Return whether *path* is *root* or lies below it."""
    canonical_path = normalize_path(path)
    canonical_root = normalize_path(root)
    if canonical_root == ROOT_PATH:
        return True
    return canonical_path == canonical_root or canonical_path.startswith(canonical_root + "/")
