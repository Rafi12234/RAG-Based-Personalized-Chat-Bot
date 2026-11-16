"""Phase 4.3A: isolated, staging-only hierarchical Qdrant shadow index.

Writes only to a DISTINCT v2 shadow collection when a caller explicitly
creates it and supplies an active MySQL processing lease. This module is
not wired to the live ingestion dispatcher or user-facing answer path.

No global alias swaps, MySQL READY changes, promotion to ready, deletion,
old-index migration or automatic fallback. Existing docmind_documents_e5_384_v1
remains untouched. ALL shadow data stays index_state='staging'.

A future versioned cutover must verify every page/passsage and the relational
authorization/READY state before using a single shadow vector for retrieval.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import islice
from typing import Callable, Iterable, Iterator
from uuid import UUID

from .document_embeddings import MODEL_NAME, VECTOR_SIZE, _normalize_vector
from .hierarchical_index import (
    HierarchicalPassage, HierarchyManifest, MAX_PASSAGES, MANIFEST_SCHEMA,
    MAX_EMBEDDING_CHARS,
)
from .qdrant_indexing import IndexIdentity

SHADOW_COLLECTION = "docmind_documents_hierarchy_e5_384_shadow_v2"
SHADOW_SCHEMA = "hierarchy-v2-staging-only"
STAGING = "staging"
KEYWORD_FIELDS = (
    "organization_id", "knowledge_base_id", "document_id",
    "document_version_id", "job_id", "index_generation",
    "index_state", "processing_token", "parent_id", "section_id",
)
DEFAULT_BATCH_SIZE = 24


@dataclass(frozen=True)
class ShadowEmbedding:
    passage: HierarchicalPassage
    vector: tuple[float, ...]


@dataclass(frozen=True)
class ShadowStagingResult:
    collection: str
    index_generation: str
    staged_points: int
    manifest_digest: str
    state: str = STAGING
    query_enabled: bool = False
    version_ready: bool = False


def _models():
    from qdrant_client import models
    return models


def _keyword(value) -> bool:
    v = getattr(value, "data_type", None)
    if v is None and isinstance(value, dict):
        v = value.get("data_type")
    return str(getattr(v, "value", v)).lower() == "keyword"


def inspect_shadow_collection(client) -> bool:
    """Read-only: true only when the *separate* shadow schema already exists."""
    if not client.collection_exists(SHADOW_COLLECTION):
        return False
    info = client.get_collection(SHADOW_COLLECTION)
    vectors = info.config.params.vectors
    m = _models()
    if (isinstance(vectors, dict) or
            getattr(vectors, "size", None) != VECTOR_SIZE or
            getattr(vectors, "distance", None) != m.Distance.COSINE):
        raise RuntimeError("Incompatible shadow vector schema")
    payload_schema = getattr(info, "payload_schema", None) or {}
    if any(name not in payload_schema or not _keyword(payload_schema[name])
           for name in KEYWORD_FIELDS):
        raise RuntimeError("Shadow collection has missing or incompatible keyword indexes")
    return True


def initialize_shadow_collection(client, *, explicit_create: bool = False) -> bool:
    """Opt-in new-collection initializer; refuses to repair/mutate existing.

    When called with explicit_create=False this is inspection only. If the
    collection exists, it must already match the contract. No deletion ever.
    """
    prior = inspect_shadow_collection(client)
    if prior or not explicit_create:
        return prior
    m = _models()
    if client.collection_exists(SHADOW_COLLECTION):
        raise RuntimeError("Shadow name collision")
    client.create_collection(
        collection_name=SHADOW_COLLECTION,
        vectors_config=m.VectorParams(size=VECTOR_SIZE, distance=m.Distance.COSINE),
    )
    for name in KEYWORD_FIELDS:
        client.create_payload_index(
            collection_name=SHADOW_COLLECTION,
            field_name=name, field_schema=m.PayloadSchemaType.KEYWORD, wait=True,
        )
    if not inspect_shadow_collection(client):
        raise RuntimeError("Shadow collection initialization incomplete")
    return True


def iter_shadow_embeddings(
    passages: Iterable[HierarchicalPassage], model, *,
    batch_size: int = 8,
) -> Iterator[ShadowEmbedding]:
    """Stream bounded ONNX E5 embedding batches with section-aware context."""
    if type(batch_size) is not int or not 1 <= batch_size <= 32:
        raise ValueError("Invalid embedding batch size")
    if not callable(getattr(model, "embed", None)):
        raise TypeError("Embedding model required")
    source = iter(passages)
    while batch := list(islice(source, batch_size)):
        for passage in batch:
            if (not isinstance(passage, HierarchicalPassage) or
                    not passage.embedding_text.strip() or
                    len(passage.embedding_text) > MAX_EMBEDDING_CHARS or
                    not 1 <= passage.token_count <= 480):
                raise ValueError("Unbounded or invalid hierarchy passage")
        vectors = list(model.embed(
            ["passage: " + p.embedding_text for p in batch], batch_size=len(batch)))
        if len(vectors) != len(batch):
            raise ValueError("Embedding model omitted hierarchical passages")
        for passage, values in zip(batch, vectors):
            yield ShadowEmbedding(passage, _normalize_vector(values))


def _scope_filter(identity: IndexIdentity, manifest: HierarchyManifest):
    m = _models()
    fields = (
        ("organization_id", identity.organization_id),
        ("knowledge_base_id", identity.knowledge_base_id),
        ("document_id", identity.document_id),
        ("document_version_id", identity.document_version_id),
        ("index_generation", manifest.digest),
    )
    return m.Filter(must=[
        m.FieldCondition(key=key, match=m.MatchValue(value=value))
        for key, value in fields
    ])


def _point(item: ShadowEmbedding, identity: IndexIdentity,
           manifest: HierarchyManifest, processing_token: str):
    p = item.passage
    if p.document_version_id != manifest.document_version_id:
        raise ValueError("Passage/document version mismatch")
    if str(UUID(p.passage_id)) != p.passage_id:
        raise ValueError("Noncanonical passage UUID")
    if len(item.vector) != VECTOR_SIZE:
        raise ValueError("Unexpected embedding dimension")
    m = _models()
    payload = {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "job_id": identity.job_id,
        "filename": identity.filename,
        "index_state": STAGING,
        "processing_token": processing_token,
        "index_generation": manifest.digest,
        "source_sha256": manifest.source_sha256,
        "index_schema_version": SHADOW_SCHEMA,
        "embedding_model": MODEL_NAME,
        "chunk_index": p.passage_index,
        "parent_id": p.parent_id,
        "section_id": p.section_id or "",
        "previous_in_parent": p.previous_in_parent,
        "section_path": list(p.section_path),
        "source_block_id": p.source_block_id,
        "source_locator": p.source_locator,
        "source_kind": p.source_kind,
        "page_number": p.page_number,
        "bbox": list(p.bbox) if p.bbox is not None else None,
        "source_char_start": p.char_start,
        "source_char_end": p.char_end,
        "token_count": p.token_count,
        "text": p.text,  # Private original source passage, never printed in audits.
    }
    return m.PointStruct(id=p.passage_id, vector=list(item.vector), payload=payload)


def stage_shadow_index(
    client, identity: IndexIdentity, manifest: HierarchyManifest,
    embeddings: Iterable[ShadowEmbedding], processing_token: str, *,
    lease_guard: Callable[[], bool], batch_size: int = DEFAULT_BATCH_SIZE,
) -> ShadowStagingResult:
    """Stage exactly one complete immutable manifest into a NEW generation.

    A version/generation with any existing point is refused, even if identical.
    This is deliberate: interrupted batch staging requires a separate recovery
    protocol, never blind overwrite. No shadow point is made user-visible.
    """
    if not isinstance(identity, IndexIdentity) or not isinstance(manifest, HierarchyManifest):
        raise TypeError("MySQL-derived identity and sealed manifest required")
    if (manifest.schema != MANIFEST_SCHEMA or
            manifest.document_version_id != identity.document_version_id or
            manifest.embedding_model != MODEL_NAME or
            manifest.passage_count != len(manifest.point_ids) or
            not 1 <= manifest.passage_count <= MAX_PASSAGES):
        raise ValueError("Incompatible hierarchy manifest/identity")
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("Processing token required")
    if not callable(lease_guard):
        raise TypeError("Fresh MySQL processing-lease guard required")
    if type(batch_size) is not int or not 1 <= batch_size <= 64:
        raise ValueError("Invalid staging batch size")
    if not lease_guard():
        raise RuntimeError("No valid processing lease for shadow staging")
    if not inspect_shadow_collection(client):
        raise RuntimeError("Shadow collection not initialized")
    scope = _scope_filter(identity, manifest)
    count = client.count(
        collection_name=SHADOW_COLLECTION, count_filter=scope, exact=True).count
    if type(count) is not int or count != 0:
        raise RuntimeError("Existing shadow generation must be manually audited")
    count_written = 0
    it = iter(embeddings)
    while batch := list(islice(it, batch_size)):
        if not lease_guard():
            raise RuntimeError("Lease lost before shadow index batch")
        points = []
        for item in batch:
            if not isinstance(item, ShadowEmbedding):
                raise TypeError("Expected hierarchical shadow embedding")
            p = item.passage
            if (count_written >= manifest.passage_count or
                    p.passage_index != count_written or
                    p.passage_id != manifest.point_ids[count_written]):
                raise RuntimeError("Shadow passage does not match immutable manifest")
            points.append(_point(item, identity, manifest, processing_token))
            count_written += 1
        client.upsert(collection_name=SHADOW_COLLECTION, points=points, wait=True)
        if not lease_guard():
            raise RuntimeError("Lease lost after shadow index batch")
    if count_written != manifest.passage_count:
        raise RuntimeError("Shadow stage incomplete; generation remains non-visible")
    if not lease_guard():
        raise RuntimeError("Lease lost before shadow count verification")
    count = client.count(collection_name=SHADOW_COLLECTION,
                         count_filter=scope, exact=True).count
    if count != count_written:
        raise RuntimeError("Shadow count mismatch; do not publish")
    if not lease_guard():
        raise RuntimeError("Lease lost after shadow count verification")
    return ShadowStagingResult(
        SHADOW_COLLECTION, manifest.digest, count_written, manifest.digest)
