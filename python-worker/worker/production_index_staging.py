"""Phase 2.8B: guarded, bounded Qdrant staging primitives.

No automatic invocation. A FUTURE processing worker must supply a CURRENT
MySQL-validated lease guard and a current processing token. All vectors are
written with index_state='staging', never 'ready'. No MySQL mutations.

Never use this helper from a QUEUED job or a read-only diagnostic CLI.
"""
from __future__ import annotations

from itertools import islice
from typing import Callable, Iterable

from .document_embeddings import ChunkEmbedding
from .production_qdrant import (PRODUCTION_COLLECTION, STAGING,
                                 inspect_collection, staging_payload)
from .qdrant_indexing import IndexIdentity

DEFAULT_BATCH_SIZE = 32


def _models():
    from qdrant_client import models
    return models


def make_staging_point(item: ChunkEmbedding, identity: IndexIdentity, processing_token: str):
    """Construct a Qdrant point whose payload remains hidden from ready filters."""
    models = _models()
    payload = staging_payload(item, identity, processing_token)
    if payload.get('index_state') != STAGING or payload.get('processing_token') != processing_token:
        raise ValueError('Staging visibility contract violated')
    return models.PointStruct(id=item.chunk.chunk_id,
                              vector=list(item.vector), payload=payload)


def staging_filter(identity: IndexIdentity, processing_token: str):
    """Exact tenant/version/token filter for staged count verification."""
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError('A processing token is required')
    models = _models()
    values = (
        ('organization_id', identity.organization_id),
        ('knowledge_base_id', identity.knowledge_base_id),
        ('document_version_id', identity.document_version_id),
        ('processing_token', processing_token),
        ('index_state', STAGING),
    )
    return models.Filter(must=[models.FieldCondition(
        key=key, match=models.MatchValue(value=value)) for key, value in values])


def count_staged_points(client, identity: IndexIdentity, processing_token: str) -> int:
    response = client.count(collection_name=PRODUCTION_COLLECTION,
                            count_filter=staging_filter(identity, processing_token),
                            exact=True)
    return int(response.count)


def stage_batches(
    client,
    embeddings: Iterable[ChunkEmbedding],
    identity: IndexIdentity,
    processing_token: str,
    *,
    lease_guard: Callable[[], bool],
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Stage vectors ONLY while the calling worker still owns an active DB lease.

    Lease guard must check job/token/status/expiry and active document against
    MySQL, with a fresh database transaction each time; it must not be a cached
    boolean. Checks both BEFORE and AFTER each remote write. On lease loss,
    raises, leaving any staged points NON-VISIBLE. Cleanup/recovery belongs to
    a later integration milestone. A separate completion step is required to
    make any point ready. Never call this for a read-only diagnostic job.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError('batch_size must be an integer between 1 and 128')
    if not callable(lease_guard):
        raise TypeError('lease_guard must be a callable')
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError('A processing token is required')
    schema = inspect_collection(client)
    if not schema.exists or not schema.compatible or schema.missing_indexes:
        raise RuntimeError('Production collection is not initialized/compatible')

    source = iter(embeddings)
    written = 0
    while batch := list(islice(source, batch_size)):
        if not lease_guard():
            raise RuntimeError('Processing lease is not valid before Qdrant stage')
        points = [make_staging_point(item, identity, processing_token) for item in batch]
        client.upsert(collection_name=PRODUCTION_COLLECTION, points=points, wait=True)
        written += len(points)
        if not lease_guard():
            raise RuntimeError('Processing lease expired during Qdrant stage')
    return written
