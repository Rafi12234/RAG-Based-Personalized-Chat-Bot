"""DocMind-AI Phase 2.6A: Qdrant diagnostic indexing primitives.

No implicit network or writes. Only the caller may explicitly create or upsert.
This is a sandbox collection, NOT the production ingestion pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from math import isfinite
from typing import Iterable
from urllib.parse import urlparse
from uuid import UUID
import os

from .document_embeddings import ChunkEmbedding, MODEL_NAME, VECTOR_SIZE

DIAGNOSTIC_COLLECTION = "docmind_phase2_diagnostic_e5_384"
POINT_BATCH_SIZE = 32


@dataclass(frozen=True)
class IndexIdentity:
    organization_id: str
    knowledge_base_id: str
    document_id: str
    document_version_id: str
    job_id: str
    filename: str

    def __post_init__(self):
        for field in ("organization_id", "knowledge_base_id", "document_id",
                      "document_version_id", "job_id", "filename"):
            v = getattr(self, field)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"{field} is required")


def _qmodels():
    from qdrant_client import models
    return models


def diagnostic_client():
    """Use only loopback for the diagnostic collection; never a remote Qdrant server."""
    url = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333")
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Diagnostic QDRANT_URL must be local http://127.0.0.1:6333")
    if parsed.username or parsed.password or parsed.path not in ("", "/"):
        raise ValueError("Unsupported diagnostic QDRANT_URL")
    from qdrant_client import QdrantClient
    return QdrantClient(url=url, timeout=30)


def payload_for(item: ChunkEmbedding, identity: IndexIdentity) -> dict:
    """Source-scoped payload that retrieval MUST tenant-filter later."""
    ch = item.chunk
    if ch.document_version_id != identity.document_version_id:
        raise ValueError("Chunk version does not match MySQL identity")
    if not isinstance(ch.text, str) or not ch.text.strip():
        raise ValueError("Cannot store empty chunk text")
    try:
        UUID(ch.chunk_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("Chunk ID must be a UUID") from exc
    if len(item.vector) != VECTOR_SIZE or not all(isfinite(float(x)) for x in item.vector):
        raise ValueError("Invalid embedding dimensions or values")
    if ch.chunk_index < 0:
        raise ValueError("Invalid chunk index")
    if ch.page_number is not None and ch.page_number < 1:
        raise ValueError("PDF page numbers are one-based")
    return {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "job_id": identity.job_id,
        "filename": identity.filename,
        "chunk_index": ch.chunk_index,
        "page_number": ch.page_number,
        "source_kind": ch.source_kind,
        "source_start_segment": ch.source_start_segment,
        "source_end_segment": ch.source_end_segment,
        "source_char_start": ch.group_char_start,
        "source_char_end": ch.group_char_end,
        "embedding_model": MODEL_NAME,
        "text": ch.text,  # Local Qdrant only; never print in diagnostics.
    }


def point_for(item: ChunkEmbedding, identity: IndexIdentity):
    models = _qmodels()
    return models.PointStruct(
        id=item.chunk.chunk_id,
        vector=list(item.vector),
        payload=payload_for(item, identity),
    )


def ensure_diagnostic_collection(client) -> bool:
    """Create the distinct sandbox collection if missing; verify existing schema."""
    models = _qmodels()
    if client.collection_exists(DIAGNOSTIC_COLLECTION):
        info = client.get_collection(DIAGNOSTIC_COLLECTION)
        params = info.config.params.vectors
        if isinstance(params, dict) or params.size != VECTOR_SIZE or params.distance != models.Distance.COSINE:
            raise ValueError("Existing diagnostic collection has incompatible vector settings")
        return False
    client.create_collection(
        collection_name=DIAGNOSTIC_COLLECTION,
        vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
    )
    for field in ("organization_id", "knowledge_base_id", "document_version_id"):
        client.create_payload_index(
            collection_name=DIAGNOSTIC_COLLECTION,
            field_name=field,
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
    return True


def upsert_diagnostic(client, embeddings: Iterable[ChunkEmbedding], identity: IndexIdentity,
                      *, batch_size: int = POINT_BATCH_SIZE) -> int:
    """Upsert bounded batches in diagnostic collection; never change MySQL state."""
    if type(batch_size) is not int or not 1 <= batch_size <= 128:
        raise ValueError("batch_size must be 1..128")
    iterator = iter(embeddings)
    written = 0
    while batch := list(islice(iterator, batch_size)):
        points = [point_for(item, identity) for item in batch]
        client.upsert(collection_name=DIAGNOSTIC_COLLECTION, points=points, wait=True)
        written += len(points)
    return written


def count_version_points(client, identity: IndexIdentity) -> int:
    models = _qmodels()
    conditions = [models.FieldCondition(key=k, match=models.MatchValue(value=v))
                  for k, v in (("organization_id", identity.organization_id),
                               ("document_version_id", identity.document_version_id))]
    result = client.count(collection_name=DIAGNOSTIC_COLLECTION,
                          count_filter=models.Filter(must=conditions), exact=True)
    return int(result.count)
