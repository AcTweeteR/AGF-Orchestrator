"""Canonical, non-network Git remote identity handling."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse


class RemoteIdentityError(ValueError):
    """Raised when a Git remote cannot be safely canonicalized."""


class RemoteInfo:
    def __init__(self, normalized: str, identity: str, host: str, scheme: str):
        self.normalized = normalized
        self.identity = identity
        self.host = host
        self.scheme = scheme


_SCP_REMOTE = re.compile(r"^(?P<user>[A-Za-z0-9._-]+)@(?P<host>[^:/\s]+):(?P<path>[^\s]+)$")
_SAFE_PATH = re.compile(r"^[^\x00-\x20\x7f]+$")


def _path(value: str) -> str:
    if not value or not _SAFE_PATH.fullmatch(value):
        raise RemoteIdentityError("origin has unsupported whitespace or control characters")
    if "\\" in value or any(part == ".." for part in value.split("/")):
        raise RemoteIdentityError("origin path traversal is unsupported")
    value = value.strip("/")
    if not value:
        raise RemoteIdentityError("origin repository path is missing")
    if value.endswith(".git"):
        value = value[:-4]
    if not value or value.endswith("/") or any(not part for part in value.split("/")):
        raise RemoteIdentityError("origin repository path is malformed")
    return value


def _display_path(value: str) -> str:
    """Retain a safe URL spelling for display/state compatibility."""
    if not value or not _SAFE_PATH.fullmatch(value) or "\\" in value:
        raise RemoteIdentityError("origin path is malformed")
    return value.strip("/")


def parse_remote_url(value: str) -> RemoteInfo:
    """Parse a remote and provide both safe display text and equality identity."""
    if not isinstance(value, str) or not value or not _SAFE_PATH.fullmatch(value):
        raise RemoteIdentityError("origin has unsupported whitespace or control characters")

    scp = _SCP_REMOTE.fullmatch(value)
    if scp:
        if "/" not in scp.group("path"):
            raise RemoteIdentityError("origin SCP path is malformed")
        host = scp.group("host").lower()
        path = _path(scp.group("path"))
        display_path = _display_path(scp.group("path"))
        return RemoteInfo(
            f"ssh://{scp.group('user')}@{host}/{display_path}",
            f"{host}/{path}",
            host,
            "ssh",
        )

    parsed = urlparse(value)
    if parsed.scheme not in {"https", "ssh", "git", "file"}:
        raise RemoteIdentityError("origin scheme is unsupported or missing")
    if parsed.username and parsed.scheme in {"https", "http"}:
        raise RemoteIdentityError("origin contains credentials")
    if parsed.password or parsed.query or parsed.fragment:
        raise RemoteIdentityError("origin contains credentials or unsupported URL components")
    if not parsed.hostname:
        if parsed.scheme != "file" or not parsed.path.startswith("/"):
            raise RemoteIdentityError("origin host is missing")
        canonical = Path(parsed.path).resolve().as_uri()
        return RemoteInfo(canonical, canonical, "", "file")
    if parsed.scheme == "file":
        raise RemoteIdentityError("file origin host is unsupported")
    if not parsed.path.startswith("/"):
        raise RemoteIdentityError("origin path is malformed")
    try:
        port = f":{parsed.port}" if parsed.port else ""
    except ValueError as exc:
        raise RemoteIdentityError("origin port is malformed") from exc
    host = parsed.hostname.lower()
    path = _path(parsed.path)
    display_path = _display_path(parsed.path)
    identity = f"{host}{port}/{path}"
    user = f"{parsed.username}@" if parsed.username else ""
    normalized = f"{parsed.scheme}://{user}{host}{port}/{display_path}"
    return RemoteInfo(normalized, identity, host, parsed.scheme)


def canonical_remote_identity(value: str) -> str:
    """Return the stable equality key without DNS or network access."""
    return parse_remote_url(value).identity
