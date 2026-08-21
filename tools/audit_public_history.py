from __future__ import annotations

import math
import re
import subprocess
import sys
from collections import Counter


SECRET_PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("github-token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b")),
    ("openai-key", re.compile(rb"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("anthropic-key", re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("google-api-key", re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("aws-access-key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("slack-token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("user-home-path", re.compile(rb"/(?:Users|home)/[A-Za-z0-9._-]+/")),
]

ASSIGNMENT_RE = re.compile(
    rb"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|secret|client[_-]?secret)\b"
    rb"\s*[:=]\s*[\"']([^\"'\r\n]{12,})[\"']"
)

SAFE_MARKERS = (
    b"example",
    b"dummy",
    b"fake",
    b"fixture",
    b"placeholder",
    b"test",
    b"changeme",
    b"redacted",
    b"<",
    b"${",
)


def run(*args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        args,
        input=input_bytes,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def entropy(value: bytes) -> float:
    if not value:
        return 0.0
    counts = Counter(value)
    size = len(value)
    return -sum((count / size) * math.log2(count / size) for count in counts.values())


def looks_like_real_secret(value: bytes) -> bool:
    lowered = value.lower()
    if any(marker in lowered for marker in SAFE_MARKERS):
        return False
    if len(value) < 20:
        return False
    return entropy(value) >= 3.5


def all_objects() -> list[tuple[str, str]]:
    output = run("git", "rev-list", "--objects", "--all").decode("utf-8", errors="replace")
    objects: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line:
            continue
        sha, _, path = line.partition(" ")
        objects.append((sha, path))
    return objects


def object_type(sha: str) -> str:
    return run("git", "cat-file", "-t", sha).decode().strip()


def object_size(sha: str) -> int:
    return int(run("git", "cat-file", "-s", sha).decode().strip())


def scan_blob(sha: str, path: str) -> list[str]:
    findings: list[str] = []
    size = object_size(sha)
    if size > 10 * 1024 * 1024:
        findings.append(f"oversized-blob {sha} {path or '<no-path>'} size={size}")
        return findings

    data = run("git", "cat-file", "blob", sha)
    if b"\x00" in data[:8192]:
        return findings

    for name, pattern in SECRET_PATTERNS:
        if pattern.search(data):
            findings.append(f"{name} {sha} {path or '<no-path>'}")

    for match in ASSIGNMENT_RE.finditer(data):
        value = match.group(1).strip()
        if looks_like_real_secret(value):
            findings.append(f"high-entropy-secret-assignment {sha} {path or '<no-path>'}")
            break

    return findings


def main() -> int:
    shallow = run("git", "rev-parse", "--is-shallow-repository").decode().strip()
    if shallow != "false":
        print("ERROR: repository is shallow; full-history audit is invalid", file=sys.stderr)
        return 2

    commits = int(run("git", "rev-list", "--all", "--count").decode().strip())
    objects = all_objects()
    blob_shas: set[str] = set()
    findings: list[str] = []

    for sha, path in objects:
        if path and re.search(
            r"(?:^|/)(?:\.env(?:\..*)?|id_(?:rsa|dsa|ecdsa|ed25519)|.*\.(?:pem|p12|pfx|key))$",
            path,
            re.I,
        ):
            findings.append(f"sensitive-filename {sha} {path}")
        if object_type(sha) != "blob" or sha in blob_shas:
            continue
        blob_shas.add(sha)
        findings.extend(scan_blob(sha, path))

    print(f"audited_commits={commits}")
    print(f"audited_unique_blobs={len(blob_shas)}")

    if findings:
        print("PUBLIC_HISTORY_AUDIT=FAIL")
        for finding in sorted(set(findings)):
            print(finding)
        return 1

    print("PUBLIC_HISTORY_AUDIT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
