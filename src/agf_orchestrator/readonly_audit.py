"""Evidence-linked deterministic read-only repository audit.

This is *not* an Architect or a signed Objective. No provider, shell, test
runner, installer, git write, network call or user-supplied command is executed.
Every finding is labelled a static signal, not a confirmed runtime exploit.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from .project_registry import parse_remote_url

MAX_FILES = 4000
MAX_BYTES = 48 * 1024 * 1024
MAX_FILE_BYTES = 512 * 1024
MAX_FINDINGS = 120
MAX_FINDINGS_PER_RULE = 12
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SECRET = re.compile(
    r"(?i)(^|[./_-])(secret|credential|password|private|token|apikey|api_key|"
    r"keyfile|\.env)([./_-]|$)"
)
FEATURES = {
    "automatic_trailers": ("auto_preview", "automatic_preview", "trailer", "autoplay"),
    "trakt_watched": ("trakt", "watched", "watch_history", "completed"),
    "season_navigation": ("season", "episode", "temporada", "episodio"),
    "pvr_iptv": ("pvr", "iptv", "epg", "m3u"),
    "smart_search": ("smart_search", "search"),
    "touch_remote": ("touch", "remote", "dpad", "control"),
    "tvos_android": ("tvos", "android", "apple_tv", "tablet"),
    "startup_performance": ("performance", "cache", "latency", "index"),
    "installer_dependencies": ("dependency", "installer", "repository", "install"),
}
STATIC_RULES = {
    "tls_verification_disabled": (
        re.compile(r"\bverify\s*=\s*False\b"), "HIGH", "security",
        "Potential disabled TLS peer verification; manually review call context.",
    ),
    "subprocess_shell_enabled": (
        re.compile(r"\bshell\s*=\s*True\b"), "MEDIUM", "security",
        "Shell invocation exists; review whether any argument is untrusted.",
    ),
    "dynamic_eval_exec": (
        re.compile(r"(?<![\w.])(?:eval|exec)\s*\("), "MEDIUM", "security",
        "Dynamic Python evaluation appears; review provenance of input.",
    ),
    "zip_extractall": (
        re.compile(r"\bextractall\s*\("), "MEDIUM", "security",
        "ZIP extraction exists; review traversal and extracted-file boundaries.",
    ),
    "broad_exception_silence": (
        re.compile(r"^\s*except\s*(?:Exception\s*)?:\s*pass\s*(?:#.*)?$"), "LOW",
        "reliability", "Exception appears silenced; verify required error reporting.",
    ),
}
TEXT_EXT = frozenset({".py", ".xml", ".yml", ".yaml", ".json", ".sh", ".md"})
IGNORED = frozenset({
    ".git", "__pycache__", "venv", ".venv", "node_modules", "dist", "build",
    "vendor", ".pytest_cache", ".mypy_cache", ".ruff_cache",
})


class ReadOnlyAuditError(ValueError):
    """Audit cannot establish trustworthy read-only source evidence."""


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, timeout=45,
            capture_output=True, shell=False,
        ).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ReadOnlyAuditError("read-only Git source verification failed") from exc


def _identity(root: Path, project, session) -> None:
    if not root.is_dir() or root.is_symlink():
        raise ReadOnlyAuditError("registered audit root is unsafe")
    if project.status.value != "ACTIVE":
        raise ReadOnlyAuditError("registered audit project is not ACTIVE")
    if project.policy.allow_live_execution or project.policy.allow_delivery:
        raise ReadOnlyAuditError("audit requires an execution- and delivery-disabled project")
    if not SHA_RE.fullmatch(session.base_sha) or project.current_head_sha != session.base_sha:
        raise ReadOnlyAuditError("audit target SHA is stale")
    if _git(root, "rev-parse", "HEAD").decode().strip() != session.base_sha:
        raise ReadOnlyAuditError("audit target HEAD changed")
    if _git(root, "branch", "--show-current").decode().strip() != project.default_branch:
        raise ReadOnlyAuditError("audit target branch changed")
    origin = _git(root, "config", "--get", "remote.origin.url").decode().strip()
    if parse_remote_url(origin).identity != parse_remote_url(project.origin_url).identity:
        raise ReadOnlyAuditError("audit target origin changed")
    if _git(root, "status", "--porcelain=v1", "-z"):
        raise ReadOnlyAuditError("read-only audit requires a clean target checkout")


def _safe_file(root: Path, rel: str) -> Path | None:
    posix = PurePosixPath(rel)
    if (
        posix.is_absolute() or not rel or rel != posix.as_posix()
        or any(part in ("", ".", "..") or part in IGNORED for part in posix.parts)
        or SECRET.search(rel)
    ):
        return None
    cur = root
    try:
        for part in posix.parts:
            cur = cur / part
            mode = cur.lstat().st_mode
            if stat.S_ISLNK(mode):
                return None
        if not stat.S_ISREG(cur.lstat().st_mode):
            return None
    except OSError:
        return None
    return cur


def _findings(root: Path, names: list[str]) -> tuple[list[dict[str, Any]], dict, dict]:
    items: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    coverage: dict[str, list[dict[str, Any]]] = {key: [] for key in FEATURES}
    tally: Counter[str] = Counter()
    total_bytes = 0

    def add(rule: str, severity: str, category: str, explanation: str,
            rel: str, line: int, *, confidence: str = "REVIEW_REQUIRED") -> None:
        if len(items) >= MAX_FINDINGS or counts[rule] >= MAX_FINDINGS_PER_RULE:
            tally["findings_limited"] += 1
            return
        counts[rule] += 1
        items.append({
            "id": f"finding-{len(items)+1:03d}", "rule": rule, "severity": severity,
            "category": category, "confidence": confidence, "file": rel,
            "line": line, "explanation": explanation,
            "evidence": "tracked file and line at pinned commit; no runtime assertion",
        })

    for rel in names:
        if len(names) > MAX_FILES:
            raise ReadOnlyAuditError("audit exceeds configured tracked-file budget")
        file = _safe_file(root, rel)
        if file is None:
            tally["unsafe_or_sensitive_skipped"] += 1
            continue
        if file.suffix.lower() not in TEXT_EXT:
            tally["nontext_skipped"] += 1
            continue
        size_before = file.stat()
        if size_before.st_size > MAX_FILE_BYTES:
            tally["oversized_skipped"] += 1
            continue
        if total_bytes + size_before.st_size > MAX_BYTES:
            tally["byte_budget_skipped"] += 1
            continue
        try:
            with file.open("rb") as handle:
                raw = handle.read(MAX_FILE_BYTES + 1)
                after = os.fstat(handle.fileno())
            if (
                len(raw) > MAX_FILE_BYTES
                or (size_before.st_ino, size_before.st_dev, size_before.st_size,
                    size_before.st_mtime_ns) !=
                   (after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns)
            ):
                raise ReadOnlyAuditError("audit source file changed during read")
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            tally["non_utf8_skipped"] += 1
            continue
        total_bytes += len(raw)
        tally["scanned_text_files"] += 1
        if file.suffix.lower() == ".py":
            tally["python_files"] += 1
            try:
                parsed = ast.parse(content, filename=rel)
            except SyntaxError as exc:
                add("python3_syntax_error", "MEDIUM", "compatibility",
                    "Python 3 parser rejected this file; legacy Python 2 may be intentional.",
                    rel, exc.lineno or 1)
            else:
                for node in ast.walk(parsed):
                    if (
                        isinstance(node, ast.ExceptHandler) and len(node.body) == 1
                        and isinstance(node.body[0], ast.Pass)
                    ):
                        add(
                            "silenced_exception_block", "LOW", "reliability",
                            "Exception handler only passes; review error observability.",
                            rel, node.lineno,
                        )
        if file.suffix.lower() == ".xml":
            tally["xml_files"] += 1
            try:
                ET.fromstring(content)
            except ET.ParseError as exc:
                add("xml_parse_error", "HIGH", "packaging",
                    "Tracked XML failed ElementTree parsing; review Kodi-specific fragments.",
                    rel, exc.position[0], confidence="CONFIRMED_STATIC")
        for line_number, line in enumerate(content.splitlines(), 1):
            for rule, (pattern, severity, category, description) in STATIC_RULES.items():
                if pattern.search(line):
                    test_or_legacy = "/tests/" in rel or rel.startswith("source/")
                    add(
                        rule, "LOW" if test_or_legacy else severity, category,
                        description, rel, line_number,
                        confidence="HISTORICAL_OR_TEST_REVIEW" if test_or_legacy
                        else "REVIEW_REQUIRED",
                    )
            lowered = line.lower()
            for name, tokens in FEATURES.items():
                if len(coverage[name]) >= 7:
                    continue
                if any(token in lowered or token in rel.lower() for token in tokens):
                    if not coverage[name] or coverage[name][-1]["file"] != rel:
                        coverage[name].append({"file": rel, "line": line_number})
    tally["scanned_bytes"] = total_bytes
    tally["tracked_files"] = len(names)
    tally["finding_count"] = len(items)
    return items, dict(tally), coverage


def _check_indexed_zip(
    path: Path, relative: str, addon_id: str, version: str,
) -> list[dict[str, Any]]:
    """Inspect only central directory and bounded addon.xml, never extract."""
    issues: list[dict[str, Any]] = []

    def issue(rule: str, explanation: str) -> None:
        issues.append({
            "rule": rule, "severity": "HIGH", "confidence": "CONFIRMED_STATIC",
            "file": relative, "line": 1, "explanation": explanation,
        })

    if path.stat().st_size > 64 * 1024 * 1024:
        issue("zip_exceeds_review_budget", "Stable ZIP exceeds bounded structural review.")
        return issues
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 10_000 or sum(i.file_size for i in infos) > 500_000_000:
                issue("zip_exceeds_review_budget", "Stable ZIP has oversized entry inventory.")
                return issues
            names: set[str] = set()
            for entry in infos:
                filename = entry.filename
                parts = PurePosixPath(filename).parts
                if (
                    filename.startswith(("/", "\\\\"))
                    or "\\\\" in filename
                    or any(part in ("", ".", "..") for part in parts)
                    or ((entry.external_attr >> 16) & 0o170000) == 0o120000
                ):
                    issue(
                        "unsafe_zip_path", "ZIP contains an unsafe or symlink entry path."
                    )
                    break
                if filename in names:
                    issue("duplicate_zip_entry", "ZIP contains a duplicated member name.")
                    break
                names.add(filename)
            expected = f"{addon_id}/addon.xml"
            if expected not in names:
                issue("zip_missing_addon_metadata", "ZIP lacks the expected root add-on metadata.")
            else:
                record = archive.getinfo(expected)
                if record.file_size > 262_144:
                    issue("zip_metadata_oversized", "ZIP root addon.xml exceeds audit bounds.")
                else:
                    with archive.open(record, "r") as stream:
                        raw = stream.read(262_145)
                    if len(raw) > 262_144:
                        issue("zip_metadata_oversized", "ZIP metadata grew beyond read budget.")
                    else:
                        metadata = ET.fromstring(raw)
                        if (
                            metadata.tag != "addon"
                            or metadata.get("id") != addon_id
                            or metadata.get("version") != version
                        ):
                            issue(
                                "zip_index_metadata_mismatch",
                                "ZIP addon.xml identity/version differs from the Stable index.",
                            )
    except (zipfile.BadZipFile, OSError, ET.ParseError, RuntimeError, ValueError):
        issue("zip_metadata_unreadable", "Stable ZIP metadata failed safe bounded inspection.")
    return issues


def _stable_manifest(root: Path, names: set[str]) -> list[dict[str, Any]]:
    """Verify Kodi Stable index consistency without opening or extracting ZIPs."""
    output: list[dict[str, Any]] = []
    index = "repo/addons.xml"
    if index not in names:
        return output
    root_xml = ET.fromstring((root / index).read_bytes())
    if root_xml.tag != "addons":
        raise ReadOnlyAuditError("Kodi Stable index has unsupported root")
    known: set[str] = set()
    for addon in root_xml.findall("addon"):
        name, version = addon.get("id"), addon.get("version")
        if not name or not version:
            continue
        if name in known:
            output.append({
                "rule": "duplicate_addon_id", "severity": "HIGH",
                "confidence": "CONFIRMED_STATIC", "file": index, "line": 1,
                "explanation": f"Stable index duplicates an add-on ID: {name}.",
            })
        known.add(name)
        expected = f"repo/{name}/{name}-{version}.zip"
        if expected not in names:
            output.append({
                "rule": "missing_indexed_zip", "severity": "HIGH",
                "confidence": "CONFIRMED_STATIC", "file": index, "line": 1,
                "explanation": (
                    f"Stable index points to {name} {version} but matching ZIP "
                    "is absent from the pinned Git tree."
                ),
                "expected_path": expected,
            })
        elif _safe_file(root, expected) is not None:
            output.extend(_check_indexed_zip(root / expected, expected, name, version))
    md5_name = "repo/addons.xml.md5"
    if md5_name in names:
        expected = (root / md5_name).read_text(encoding="ascii").strip().lower()
        current = hashlib.md5((root / index).read_bytes(), usedforsecurity=False).hexdigest()
        if expected != current:
            output.append({
                "rule": "addons_index_hash_mismatch", "severity": "HIGH",
                "confidence": "CONFIRMED_STATIC", "file": md5_name, "line": 1,
                "explanation": "Legacy Kodi index checksum disagrees with addons.xml.",
            })
    return output


def _stable_package_source_findings(
    root: Path, tracked: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, list[dict[str, Any]]]]:
    """Inspect bounded source in currently indexed Stable ZIPs in memory only."""
    issues: list[dict[str, Any]] = []
    figures: Counter[str] = Counter()
    by_feature: dict[str, list[dict[str, Any]]] = {key: [] for key in FEATURES}
    index = root / "repo/addons.xml"
    if "repo/addons.xml" not in tracked:
        return issues, dict(figures), by_feature
    source_budget = 32 * 1024 * 1024
    for addon in ET.parse(index).getroot().findall("addon"):
        ident, version = addon.get("id"), addon.get("version")
        if not ident or not version:
            continue
        relative = f"repo/{ident}/{ident}-{version}.zip"
        if relative not in tracked:
            continue
        package = _safe_file(root, relative)
        if package is None or package.stat().st_size > 64 * 1024 * 1024:
            figures["packages_skipped"] += 1
            continue
        try:
            with zipfile.ZipFile(package) as archive:
                figures["stable_packages"] += 1
                for record in archive.infolist():
                    member = record.filename
                    inner = PurePosixPath(member)
                    if (
                        inner.is_absolute() or not inner.parts
                        or any(x in ("", ".", "..") for x in inner.parts)
                        or SECRET.search(member)
                        or "\\\\" in member
                        or record.is_dir()
                        or ((record.external_attr >> 16) & 0o170000) == 0o120000
                    ):
                        figures["package_entries_skipped"] += 1
                        continue
                    ext = inner.suffix.lower()
                    if ext not in {".py", ".xml", ".sh"}:
                        continue
                    if record.file_size > 256 * 1024 or record.file_size > source_budget:
                        figures["package_entries_skipped"] += 1
                        continue
                    with archive.open(record, "r") as source:
                        data = source.read(256 * 1024 + 1)
                    if len(data) != record.file_size or len(data) > 256 * 1024:
                        raise ReadOnlyAuditError("published package content exceeded bounds")
                    try:
                        content = data.decode("utf-8")
                    except UnicodeError:
                        figures["package_entries_skipped"] += 1
                        continue
                    figures["package_entries_scanned"] += 1
                    source_budget -= len(data)
                    if ext == ".py":
                        figures["stable_python_entries"] += 1
                        try:
                            ast.parse(content)
                        except SyntaxError as exc:
                            if len(issues) < 80:
                                issues.append({
                                    "rule": "stable_python3_syntax",
                                    "severity": "MEDIUM", "confidence": "CONFIRMED_STATIC",
                                    "category": "compatibility", "file": relative,
                                    "package_member": member, "line": exc.lineno or 1,
                                    "explanation": "Stable module fails Python 3 parsing.",
                                })
                    if ext == ".xml":
                        figures["stable_xml_entries"] += 1
                        try:
                            ET.fromstring(content)
                        except ET.ParseError as exc:
                            if len(issues) < 80:
                                issues.append({
                                    "rule": "stable_xml_parse",
                                    "severity": "MEDIUM", "confidence": "CONFIRMED_STATIC",
                                    "category": "compatibility", "file": relative,
                                    "package_member": member, "line": exc.position[0],
                                    "explanation": (
                                        "Published XML fails ElementTree parsing; "
                                        "Kodi skin includes may require runtime confirmation."
                                    ),
                                })
                    for line_number, line in enumerate(content.splitlines(), 1):
                        for feature, tokens in FEATURES.items():
                            if len(by_feature[feature]) < 5 and any(
                                token in line.lower() or token in member.lower()
                                for token in tokens
                            ):
                                if not by_feature[feature] or (
                                    by_feature[feature][-1]["package_member"] != member
                                ):
                                    by_feature[feature].append({
                                        "file": relative,
                                        "package_member": member, "line": line_number,
                                    })
                        for rule in (
                            "tls_verification_disabled", "subprocess_shell_enabled",
                            "dynamic_eval_exec", "zip_extractall",
                        ):
                            regex, severity, category, explanation = STATIC_RULES[rule]
                            if (
                                regex.search(line) and len(issues) < 80
                                and sum(v["rule"] == rule for v in issues) < 12
                            ):
                                issues.append({
                                    "rule": "stable_" + rule,
                                    "severity": severity,
                                    "confidence": "REVIEW_REQUIRED",
                                    "category": category, "file": relative,
                                    "package_member": member, "line": line_number,
                                    "explanation": explanation,
                                })
        except (zipfile.BadZipFile, OSError, ValueError, RuntimeError) as exc:
            raise ReadOnlyAuditError(
                "indexed Stable ZIP could not be audited without extraction"
            ) from exc
    return issues, dict(figures), by_feature


def build_readonly_audit(project, session) -> dict[str, Any]:
    """Generate reproducible observations for a pinned, clean, registered project."""
    root = Path(project.repository_root)
    _identity(root, project, session)
    raw_names = _git(root, "ls-files", "-z", "--cached")
    try:
        names = [p.decode("utf-8") for p in raw_names.split(b"\0") if p]
    except UnicodeError as exc:
        raise ReadOnlyAuditError("tracked path encoding is unsupported") from exc
    if len(names) > MAX_FILES or len(names) != len(set(names)):
        raise ReadOnlyAuditError("tracked path inventory is oversized or ambiguous")
    findings, metrics, coverage = _findings(root, names)
    stable = _stable_manifest(root, set(names))
    package_findings, package_counts, published_features = _stable_package_source_findings(
        root, set(names),
    )
    for index_issue in stable:
        if len(findings) >= MAX_FINDINGS:
            break
        findings.append({"id": f"finding-{len(findings)+1:03d}",
                         "category": "packaging", **index_issue})
    for finding in package_findings:
        if len(findings) >= MAX_FINDINGS:
            break
        findings.append({"id": f"finding-{len(findings)+1:03d}", **finding})
    _identity(root, project, session)
    observed = datetime.now(UTC).replace(microsecond=0).isoformat()
    report = {
        "schema_version": "1.0", "audit_kind": "READ_ONLY_STATIC",
        "outcome": "STATIC_AUDIT_COMPLETED", "session_id": session.session_id,
        "project_id": project.project_id, "base_sha": session.base_sha,
        "repository_origin": project.origin_url,
        "created_at": observed,
        "safety": {
            "execution": False, "network_provider": False,
            "signature_or_approval": False, "delivery": False,
            "repo_writes": False, "source_before_and_after_clean": True,
        },
        "scope": {
            "tracked_paths": len(names), "scanned_text_files": metrics.get("scanned_text_files", 0),
            "python_files": metrics.get("python_files", 0),
            "xml_files": metrics.get("xml_files", 0),
            "scanned_bytes": metrics.get("scanned_bytes", 0),
            "excluded_sensitive_or_unsafe": metrics.get("unsafe_or_sensitive_skipped", 0),
            "skipped_nontext": metrics.get("nontext_skipped", 0),
            "oversize_or_budget_skipped": (
                metrics.get("oversized_skipped", 0) + metrics.get("byte_budget_skipped", 0)
            ),
        },
        "findings": findings,
        "feature_evidence": {
            key: {
                "historical_or_repository_signals": value,
                "published_stable_signals": published_features[key],
                "runtime_validated": False,
            }
            for key, value in coverage.items()
        },
        "published_package_coverage": package_counts,
        "limitations": [
            "Static source patterns are indicators for human review, not proven vulnerabilities.",
            "Feature references do not establish Kodi runtime or platform correctness.",
            "Historical source snapshots are not necessarily part of the current Stable.",
            "Findings in tests or historical source snapshots do not establish Stable risk.",
            "Published package signals were read in memory; ZIPs were never extracted.",
            "Tests, installers, ZIP extraction and live models were not run.",
            "Binary archives, oversized files, sensitive paths and secret contents were excluded.",
            "No approved Owner Objective or signed Architect profile is asserted by this audit.",
        ],
    }
    report["report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return report
