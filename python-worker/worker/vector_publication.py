"""DocMind-AI Phase 2.9D: narrowly scoped, retryable Qdrant publication.

Building block ONLY. Does not claim/renew/complete MySQL jobs and has no CLI
entrypoint. The future worker must own an unexpired MySQL processing token;
`lease_guard` must query a FRESH DB transaction on every call.

Staging -> ready promotion is idempotent for the SAME job/token. A failure may
leave a mixture of staging and ready points; next invocation by the SAME
still-active owner can resume. A stale/new owner may NOT reuse points from
another token. There is no cross-store atomicity; MySQL READY must be checked
by all future user-facing retrievals, even when Qdrant says index_state=ready.
"""
from __future__ import annotations

from itertools import islice
from typing import Callable, Iterable
from uuid import UUID

from .production_qdrant import (PRODUCTION_COLLECTION, READY, STAGING,
                                inspect_collection)
from .qdrant_indexing import IndexIdentity

BATCH_SIZE = 64
MAX_PUBLICATION_IDS = 50_000
_SCOPE_FIELDS = (
    "organization_id", "knowledge_base_id", "document_id", "document_version_id",
    "job_id", "processing_token", "index_state",
)


def _validate_ids(source: Iterable[str]) -> tuple[str, ...]:
    """Check *all* IDs before the first Qdrant write; cap memory and scope."""
    if isinstance(source, (str, bytes)):
        raise TypeError("Expected an iterable of UUID strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in source:
        if not isinstance(item, str):
            raise ValueError("Expected UUID string")
        try:
            canonical = str(UUID(item))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("Invalid point UUID") from exc
        if canonical != item or canonical in seen:
            raise ValueError("Duplicate or non-canonical point UUID")
        result.append(canonical)
        seen.add(canonical)
        if len(result) > MAX_PUBLICATION_IDS:
            raise ValueError("Publication chunk limit exceeded")
    if not result:
        raise ValueError("At least one point is required")
    return tuple(result)


def _scope(identity: IndexIdentity, token: str) -> dict[str, str]:
    return {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "job_id": identity.job_id,
        "processing_token": token,
    }


def _check_rows(rows, batch: tuple[str, ...], scope: dict[str, str]) -> dict[str, str]:
    if len(rows) != len(batch):
        raise RuntimeError("Missing staged or published Qdrant points")
    expected = set(batch)
    states: dict[str, str] = {}
    for point in rows:
        try:
            identifier = str(UUID(str(point.id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise RuntimeError("Invalid returned Qdrant point ID") from exc
        if identifier not in expected or identifier in states:
            raise RuntimeError("Extra/duplicate/foreign returned point")
        payload = point.payload or {}
        if any(payload.get(k) != v for k, v in scope.items()):
            raise RuntimeError("Qdrant scope or processing token mismatch")
        state = payload.get("index_state")
        if state not in (STAGING, READY):
            raise RuntimeError("Unexpected Qdrant publication state")
        states[identifier] = state
    if set(states) != expected:
        raise RuntimeError("Missing expected Qdrant IDs")
    return states


def _read_batch(client, batch: tuple[str, ...], scope: dict[str, str]):
    rows = client.retrieve(
        collection_name=PRODUCTION_COLLECTION,
        ids=list(batch),
        with_payload=list(_SCOPE_FIELDS),
        with_vectors=False,
    )
    return _check_rows(rows, batch, scope)


def _pending_filter(identity: IndexIdentity, token: str, ids: list[str]):
    """Filter includes exact UUIDs AND scope/token/state, not just UUIDs."""
    from qdrant_client import models
    conditions = [
        models.FieldCondition(key=key, match=models.MatchValue(value=value))
        for key, value in {**_scope(identity, token), "index_state": STAGING}.items()
    ]
    conditions.append(models.HasIdCondition(has_id=ids))
    return models.Filter(must=conditions)


def publish_verified_staging(
    client,
    identity: IndexIdentity,
    processing_token: str,
    expected_ids: Iterable[str],
    *,
    lease_guard: Callable[[], bool],
    batch_size: int = BATCH_SIZE,
) -> int:
    """Promote a currently-owned document's expected points from staging to ready.

    Preconditions: the caller already staged and validated exactly the expected
    UUIDs, owns a live DB lease, and has a plan for cross-store crash recovery.
    This function re-reads scope on every batch; only STAGING points matching
    the exact processing token can change. READY points from the SAME attempt
    are accepted for resume. After promotion it re-reads the batch. For full
    scoped READY count, call `verify_ready_vector_ids` before MySQL completion.

    Does not complete MySQL, cleanup old attempts or trigger Celery dispatch.
    """
    if not isinstance(identity, IndexIdentity):
        raise TypeError("IndexIdentity required")
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("processing token required")
    if type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError("batch_size must be between 1 and 128")
    if not callable(lease_guard):
        raise TypeError("fresh lease guard required")
    ids = _validate_ids(expected_ids)
    if not lease_guard():
        raise RuntimeError("Current processing lease is not valid")
    inspection = inspect_collection(client)
    if not inspection.exists or not inspection.compatible or inspection.missing_indexes:
        raise RuntimeError("Production Qdrant schema is not compatible")

    scope = _scope(identity, processing_token)
    for index in range(0, len(ids), batch_size):
        batch = ids[index:index + batch_size]
        if not lease_guard():
            raise RuntimeError("Processing lease lost before Qdrant read")
        before = _read_batch(client, batch, scope)
        pending = [id_ for id_, state in before.items() if state == STAGING]
        if pending:
            if not lease_guard():
                raise RuntimeError("Processing lease lost before publication")
            client.set_payload(
                collection_name=PRODUCTION_COLLECTION,
                payload={"index_state": READY},
                points=_pending_filter(identity, processing_token, pending),
                wait=True,
            )
        if not lease_guard():
            raise RuntimeError("Processing lease lost after publication")
        after = _read_batch(client, batch, scope)
        if set(after.values()) != {READY}:
            raise RuntimeError("Publication did not make all expected points ready")
        if not lease_guard():
            raise RuntimeError("Processing lease lost after verification")
    return len(ids)
