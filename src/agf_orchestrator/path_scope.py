"""One explicit task path contract for execution, review and delivery."""

from __future__ import annotations


def _valid_relative(value: str) -> bool:
    return (
        isinstance(value, str) and bool(value) and value == value.strip()
        and "\\" not in value and ":" not in value and "\x00" not in value
        and all(part not in {"", ".", "..", ".git"} for part in value.split("/"))
    )


def path_in_scope(path: str, allowed_paths: list[str] | tuple[str, ...]) -> bool:
    """Files match exactly; only an explicit trailing slash authorizes descendants.

    This is a lexical permission check, not a filesystem or symlink boundary.
    Repository containment and patch safety must still be verified separately.
    """
    if not _valid_relative(path):
        return False
    if not scope_is_valid(allowed_paths):
        return False
    return any(
        path.startswith(allowed) if allowed.endswith("/") else path == allowed
        for allowed in allowed_paths
    )


def paths_in_scope(paths: list[str], allowed_paths: list[str] | tuple[str, ...]) -> bool:
    return bool(allowed_paths) and all(path_in_scope(path, allowed_paths) for path in paths)


def scope_is_valid(allowed_paths: list[str] | tuple[str, ...]) -> bool:
    if not isinstance(allowed_paths, (list, tuple)) or not allowed_paths:
        return False
    for allowed in allowed_paths:
        if not isinstance(allowed, str):
            return False
        directory = allowed.endswith("/")
        value = allowed[:-1] if directory else allowed
        if not _valid_relative(value):
            return False
    return True
