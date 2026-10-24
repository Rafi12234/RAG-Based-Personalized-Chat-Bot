"""Read-only Qdrant publication audit for DocMind-AI Phase 2.9D.

Counts states for one exact org/KB/document/version; never changes data.
Staging and ready counts can reveal interrupted promotion, but are NOT proof
of correctness without verifying the expected ID manifest and MySQL status.
"""
from __future__ import annotations

from dataclasses import dataclass

from .production_qdrant import PRODUCTION_COLLECTION, STAGING, READY, inspect_collection
from .qdrant_indexing import IndexIdentity


@dataclass(frozen=True)
class PublicationCounts:
    staged: int
    ready: int


def _filter(identity: IndexIdentity, state: str):
    from qdrant_client import models
    values = {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "index_state": state,
    }
    return models.Filter(must=[models.FieldCondition(
        key=k, match=models.MatchValue(value=v)
    ) for k, v in values.items()])


def audit_publication_counts(client, identity: IndexIdentity) -> PublicationCounts:
    if not isinstance(identity, IndexIdentity):
        raise TypeError("IndexIdentity required")
    state = inspect_collection(client)
    if not state.exists or not state.compatible or state.missing_indexes:
        raise RuntimeError("Production Qdrant schema not ready")
    counts = []
    for status in (STAGING, READY):
        response = client.count(
            collection_name=PRODUCTION_COLLECTION,
            count_filter=_filter(identity, status),
            exact=True,
        )
        n = int(response.count)
        if n < 0:
            raise RuntimeError("Invalid Qdrant count")
        counts.append(n)
    return PublicationCounts(staged=counts[0], ready=counts[1])


def interpret_publication_state(job_status: str, document_status: str,
                                counts: PublicationCounts) -> str:
    """Conservative indicator, never an instruction to delete/retry automatically."""
    if job_status == "QUEUED" and document_status == "QUEUED":
        return "review_orphan_vectors" if counts.ready or counts.staged else "not_started"
    if job_status == "RUNNING" and document_status == "PROCESSING":
        if counts.ready and counts.staged:
            return "partial_publication_possible"
        if counts.ready:
            return "publication_possible_verify_manifest"
        if counts.staged:
            return "staging_possible_verify_manifest"
        return "running_no_vectors_observed"
    if job_status == "COMPLETED" and document_status == "READY":
        return "completed_recheck_manifest" if counts.ready else "warning_ready_without_vectors"
    return "review_inconsistent_status_or_leftovers" if counts.ready or counts.staged else "no_vectors_observed"
