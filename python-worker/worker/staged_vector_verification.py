"""Read-only Qdrant staging integrity check for a currently leased job.

This is NOT final publication and does not mutate Qdrant or MySQL. The caller
must provide the expected tokenchunk:v2 UUIDs from the current source version.
"""
from __future__ import annotations

from itertools import islice
from typing import Callable, Iterable
from uuid import UUID

from .production_qdrant import PRODUCTION_COLLECTION, STAGING, inspect_collection
from .production_index_staging import count_staged_points
from .qdrant_indexing import IndexIdentity

_PAYLOAD_FIELDS = (
    "organization_id", "knowledge_base_id", "document_id",
    "document_version_id", "processing_token", "index_state",
)


def _batches(source, size):
    iterator = iter(source)
    while batch := list(islice(iterator, size)):
        yield batch


def verify_staged_vector_ids(
    client,
    identity: IndexIdentity,
    processing_token: str,
    expected_ids: Iterable[str],
    *,
    lease_guard: Callable[[], bool],
    batch_size: int = 64,
) -> int:
    """Verify exact expected UUIDs, payload scope, and scoped staged point count.

    Requires live, fresh MySQL lease checks before/after each remote read.
    It fails closed on missing/duplicate/foreign/extra points or ownership loss.
    All Qdrant requests are read-only; NO publish, upsert, or delete calls.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError("batch_size must be between 1 and 128")
    if not callable(lease_guard):
        raise TypeError("lease_guard must be callable")
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("A valid processing token is required")
    if not isinstance(identity, IndexIdentity):
        raise TypeError("identity must be IndexIdentity")
    if not lease_guard():
        raise RuntimeError("Processing lease not owned")
    schema = inspect_collection(client)
    if not schema.exists or not schema.compatible or schema.missing_indexes:
        raise RuntimeError("Production collection is not compatible")

    expected_scope = {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "processing_token": processing_token,
        "index_state": STAGING,
    }
    all_ids: set[str] = set()
    total = 0
    for batch in _batches(expected_ids, batch_size):
        for item in batch:
            if not isinstance(item, str):
                raise ValueError("Expected UUID string")
            try:
                normalized = str(UUID(item))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("Invalid expected point ID") from exc
            if normalized != item:
                raise ValueError("Expected canonical lowercase UUID")
            if normalized in all_ids:
                raise ValueError("Duplicate expected point ID")
            all_ids.add(normalized)
        if not lease_guard():
            raise RuntimeError("Processing lease lost before vector verification")
        actual = client.retrieve(
            collection_name=PRODUCTION_COLLECTION,
            ids=batch,
            with_payload=list(_PAYLOAD_FIELDS),
            with_vectors=False,
        )
        actual_ids = [str(UUID(str(point.id))) for point in actual]
        if len(actual_ids) != len(batch) or set(actual_ids) != set(batch):
            raise RuntimeError("Qdrant staged point IDs do not match expected batch")
        for point in actual:
            payload = point.payload or {}
            if any(payload.get(field) != value for field, value in expected_scope.items()):
                raise RuntimeError("Staged point has foreign or non-staging payload")
        if not lease_guard():
            raise RuntimeError("Processing lease lost after vector verification")
        total += len(batch)

    if total == 0:
        raise ValueError("At least one expected chunk ID is required")
    if not lease_guard():
        raise RuntimeError("Processing lease lost before Qdrant count")
    count = count_staged_points(client, identity, processing_token)
    if not lease_guard():
        raise RuntimeError("Processing lease lost after Qdrant count")
    if count != total:
        raise RuntimeError("Unexpected extra or missing staged points")
    return total
