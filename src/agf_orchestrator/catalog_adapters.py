"""Adapters that turn external catalogs into unverified capability candidates."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .capability_extensions import CandidateStatus, ToolCandidate, seal


class CatalogAdapterError(ValueError):
    """Raised when external catalog input cannot be bounded safely."""


_SLUG = re.compile(r"[^a-z0-9]+")
_MAX_ENTRIES = 5000


def public_apis_candidates(
    project_id: str,
    entries: Iterable[Mapping[str, Any]],
    *,
    observed_at: str,
    catalog_source: str = "public-apis-style-catalog",
) -> tuple[ToolCandidate, ...]:
    """Convert public-apis-style rows to UNVERIFIED discovery candidates.

    Catalog metadata is deliberately not converted into verification checks. A
    later independent verification step must provide evidence before a candidate
    can become VERIFIED/usable.
    """
    rows = list(entries)
    if len(rows) > _MAX_ENTRIES:
        raise CatalogAdapterError("catalog exceeds bounded entry count")
    candidates: list[ToolCandidate] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise CatalogAdapterError("catalog entry must be a mapping")
        name = _required_text(row, "API")
        category = _required_text(row, "Category")
        description = _optional_text(row.get("Description"))
        capability = _slug(category)
        digest = hashlib.sha256(
            f"{name}\0{category}\0{description}".encode("utf-8")
        ).hexdigest()[:12]
        candidate_id = f"candidate-{_slug(name)[:45]}-{digest}"
        candidate = seal(
            ToolCandidate(
                schema_version="1.0",
                candidate_id=candidate_id,
                project_id=project_id,
                capability=capability,
                endpoint_label=name,
                catalog_source=catalog_source,
                status=CandidateStatus.UNVERIFIED,
                checks=(),
                observed_at=observed_at,
                candidate_sha256="",
            )
        )
        candidate.validate()
        candidates.append(candidate)
    return tuple(candidates)


def _required_text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise CatalogAdapterError(f"catalog field {key} is invalid")
    return value.strip()


def _optional_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 2000:
        raise CatalogAdapterError("catalog description is invalid")
    return value.strip()


def _slug(value: str) -> str:
    slug = _SLUG.sub("-", value.lower()).strip("-")
    if not slug:
        raise CatalogAdapterError("catalog value cannot be normalized")
    return slug[:80]
