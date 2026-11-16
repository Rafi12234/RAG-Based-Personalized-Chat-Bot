"""Phase 4.2C: read-only, bounded Qdrant large-version reconciliation.

The existing TEST-only recovery path supports <=2048 points and deletes only
after explicit quiescence/confirmation. THIS MODULE NEVER DELETES, upserts,
publishes or marks READY. It scans <=50000 candidate points for one precise
MySQL-derived org/KB/document/version/job identity and reports vector state.

A scan with no expected UUID manifest is NOT integrity verification and is
NEVER enough to resume/reuse/publish orphaned vectors. It is for diagnostics
and for designing safe, verified generation-level repair in a later milestone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID

from .production_qdrant import PRODUCTION_COLLECTION, STAGING, READY, inspect_collection
from .qdrant_indexing import IndexIdentity

MAX_LARGE_VERSION_POINTS = 50_000
DEFAULT_PAGE_SIZE = 256
_FIELDS = ("organization_id", "knowledge_base_id", "document_id",
           "document_version_id", "job_id", "processing_token",
           "index_state", "chunk_index", "page_number")


class VectorAuditError(RuntimeError):
    """A large-version vector state cannot be safely interpreted."""


@dataclass(frozen=True)
class VersionVectorAudit:
    observed_points: int
    staged_points: int
    ready_points: int
    distinct_attempt_tokens: int
    expected_manifest_supplied: bool
    exact_manifest_match: bool | None
    assessment: str

    def as_dict(self) -> dict:
        return {
            "observed_points": self.observed_points,
            "staged_points": self.staged_points,
            "ready_points": self.ready_points,
            "distinct_attempt_tokens": self.distinct_attempt_tokens,
            "expected_manifest_supplied": self.expected_manifest_supplied,
            "exact_manifest_match": self.exact_manifest_match,
            "assessment": self.assessment,
            "authoritative_recovery_approved": False,
            "safe_to_auto_replay": False,
            "mysql_modified": False,
            "qdrant_modified": False,
        }


def _canonical_uuid(value: str) -> str:
    if not isinstance(value, str):
        raise VectorAuditError("Expected canonical UUID string")
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise VectorAuditError("Invalid point UUID") from exc
    if canonical != value:
        raise VectorAuditError("Noncanonical point UUID")
    return canonical


def _identity_filter(identity: IndexIdentity):
    from qdrant_client import models
    values = (
        ("organization_id", identity.organization_id),
        ("knowledge_base_id", identity.knowledge_base_id),
        ("document_id", identity.document_id),
        ("document_version_id", identity.document_version_id),
    )
    return models.Filter(must=[
        models.FieldCondition(key=k, match=models.MatchValue(value=v))
        for k, v in values
    ])


def _expected_set(expected_ids: Iterable[str] | None) -> set[str] | None:
    if expected_ids is None:
        return None
    if isinstance(expected_ids, (bytes, str)):
        raise ValueError("Expected IDs must be an iterable of UUID strings")
    ids: set[str] = set()
    for value in expected_ids:
        point_id = _canonical_uuid(value)
        if point_id in ids:
            raise ValueError("Duplicate expected UUID")
        ids.add(point_id)
        if len(ids) > MAX_LARGE_VERSION_POINTS:
            raise ValueError("Expected manifest exceeds large-version limit")
    return ids


def inspect_large_version(
    client, identity: IndexIdentity, *,
    expected_ids: Iterable[str] | None = None,
    max_points: int = MAX_LARGE_VERSION_POINTS,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> VersionVectorAudit:
    """Scan exact version scope and verify all retrieved payloads.

    Request ONLY metadata, never vectors or the sensitive source text.
    Retains at most 50000 UUID strings and the attempt token set. A point
    appearing on two pages, foreign job/token, missing/invalid payload, bad
    pagination, scope count change or unexpected state fails closed.
    """
    if not isinstance(identity, IndexIdentity):
        raise TypeError("MySQL-derived IndexIdentity required")
    if type(max_points) is not int or not 1 <= max_points <= MAX_LARGE_VERSION_POINTS:
        raise ValueError("Invalid large-version point limit")
    if type(page_size) is not int or not 1 <= page_size <= 512:
        raise ValueError("Invalid Qdrant scroll page size")
    expected = _expected_set(expected_ids)
    if expected is not None and len(expected) > max_points:
        raise ValueError("Expected manifest exceeds configured limit")
    schema = inspect_collection(client)
    if not schema.exists or not schema.compatible or schema.missing_indexes:
        raise VectorAuditError("Qdrant schema is unavailable or incompatible")
    expected_scope = {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "job_id": identity.job_id,
    }
    flt = _identity_filter(identity)

    def count() -> int:
        response = client.count(
            collection_name=PRODUCTION_COLLECTION,
            count_filter=flt, exact=True,
        )
        value = response.count
        if type(value) is not int or not 0 <= value <= max_points:
            raise VectorAuditError("Version point count invalid or above safety bound")
        return value

    initial_count = count()
    observed: set[str] = set()
    tokens: set[str] = set()
    staged = ready = 0
    offset = None
    seen_offsets: set[str] = set()
    while True:
        records, next_offset = client.scroll(
            collection_name=PRODUCTION_COLLECTION,
            scroll_filter=flt, limit=page_size, offset=offset,
            with_payload=list(_FIELDS), with_vectors=False,
        )
        if not isinstance(records, (list, tuple)) or len(records) > page_size:
            raise VectorAuditError("Invalid or oversized Qdrant page")
        if not records and next_offset is not None:
            raise VectorAuditError("Empty Qdrant scroll page with continuation")
        for record in records:
            uid = _canonical_uuid(str(record.id))
            if uid in observed:
                raise VectorAuditError("Repeated point UUID across scroll pages")
            payload = record.payload
            if type(payload) is not dict or any(
                payload.get(field) != value for field, value in expected_scope.items()
            ):
                raise VectorAuditError("Foreign or incomplete Qdrant point scope")
            token = payload.get("processing_token")
            state = payload.get("index_state")
            index = payload.get("chunk_index")
            page = payload.get("page_number")
            if (not isinstance(token, str) or not 1 <= len(token) <= 64
                    or state not in (STAGING, READY)
                    or type(index) is not int or index < 0
                    or (page is not None and (type(page) is not int or page < 1))):
                raise VectorAuditError("Invalid Qdrant point indexing metadata")
            tokens.add(token)
            if state == STAGING:
                staged += 1
            else:
                ready += 1
            observed.add(uid)
            if len(observed) > max_points:
                raise VectorAuditError("Scroll exceeded configured bound")
        if next_offset is None:
            break
        marker = str(next_offset)
        if marker in seen_offsets or marker == str(offset):
            raise VectorAuditError("Qdrant pagination did not advance")
        seen_offsets.add(marker)
        offset = next_offset
        if len(seen_offsets) > max_points:
            raise VectorAuditError("Qdrant pagination exceeds safety bound")

    final_count = count()
    if len(observed) != initial_count or final_count != initial_count:
        raise VectorAuditError("Point count changed during scroll or missing points")
    matches = observed == expected if expected is not None else None
    if len(tokens) > 1:
        assessment = "mixed_attempt_tokens_manual_review"
    elif matches is False:
        assessment = "expected_manifest_mismatch_manual_review"
    elif initial_count == 0:
        assessment = "no_points_seen"
    elif matches is True:
        assessment = "manifest_ids_match_not_authorized_to_resume"
    else:
        assessment = "metadata_only_no_expected_manifest"
    return VersionVectorAudit(initial_count, staged, ready, len(tokens),
                              expected is not None, matches, assessment)
