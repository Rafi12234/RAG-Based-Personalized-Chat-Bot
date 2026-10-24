"""Phase 2.9C: read-only Qdrant verification of EXACT ready points.

A current leased worker must provide the expected UUIDs. This is NOT a
vector publisher. No MySQL or Qdrant writes occur here.
"""
from __future__ import annotations

from itertools import islice
from typing import Callable, Iterable
from uuid import UUID

from .production_qdrant import PRODUCTION_COLLECTION, READY, inspect_collection
from .qdrant_indexing import IndexIdentity


def _batches(items, n):
    iterator = iter(items)
    while batch := list(islice(iterator, n)):
        yield batch


def verify_ready_vector_ids(client, identity: IndexIdentity, processing_token: str,
                            expected_ids: Iterable[str], *,
                            lease_guard: Callable[[], bool], batch_size: int = 64) -> int:
    """Confirm all expected IDs, READY payload scope and scoped count.

    This verification never authorizes retrieval by itself: future query APIs
    MUST also verify caller tenancy and DocumentVersion READY in MySQL.
    """
    if not isinstance(identity, IndexIdentity):
        raise TypeError("identity must be IndexIdentity")
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("processing_token required")
    if type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError("invalid batch size")
    if not callable(lease_guard) or not lease_guard():
        raise RuntimeError("Lease not owned")
    schema = inspect_collection(client)
    if not schema.exists or not schema.compatible or schema.missing_indexes:
        raise RuntimeError("Production collection missing or incompatible")

    scope = {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "processing_token": processing_token,
        "index_state": READY,
    }
    seen: set[str] = set()
    for batch in _batches(expected_ids, batch_size):
        normalized = []
        for point_id in batch:
            if not isinstance(point_id, str):
                raise ValueError("Expected UUID string")
            try:
                canon = str(UUID(point_id))
            except ValueError as exc:
                raise ValueError("Invalid UUID") from exc
            if canon != point_id or canon in seen:
                raise ValueError("Duplicate or non-canonical point ID")
            normalized.append(canon)
            seen.add(canon)
        if not lease_guard():
            raise RuntimeError("Lease lost before Qdrant read")
        points = client.retrieve(collection_name=PRODUCTION_COLLECTION, ids=batch,
                                 with_payload=list(scope), with_vectors=False)
        returned_ids = [str(UUID(str(point.id))) for point in points]
        if len(returned_ids) != len(batch) or set(returned_ids) != set(normalized):
            raise RuntimeError("Published points missing, duplicated or foreign")
        for point in points:
            if any((point.payload or {}).get(key) != value for key, value in scope.items()):
                raise RuntimeError("Published point has mismatched scope or visibility")
        if not lease_guard():
            raise RuntimeError("Lease lost after Qdrant read")

    if not seen:
        raise ValueError("Expected at least one point")
    if not lease_guard():
        raise RuntimeError("Lease lost before scoped count")
    from qdrant_client import models
    condition = models.Filter(must=[
        models.FieldCondition(key=key, match=models.MatchValue(value=value))
        for key, value in scope.items()
    ])
    count = int(client.count(collection_name=PRODUCTION_COLLECTION,
                             count_filter=condition, exact=True).count)
    if not lease_guard():
        raise RuntimeError("Lease lost after scoped count")
    if count != len(seen):
        raise RuntimeError("Extra or missing scoped ready points")
    return count
