"""DocMind-AI Phase 2.8A: production Qdrant collection *contract*.

This module is not the automatic ingestion worker. Schema inspection is read-only.
Collection creation/index repair only occurs with an explicit initialize=True.
It never updates MySQL, upserts points, deletes existing points, or changes
an ingestion job to READY. The ready-state query filter is a required guard;
callers must ALSO authorize organization/knowledge-base access in MySQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Pinned schema for the already-verified multilingual E5-small embedding model.
# Imported lazily from the existing modules only when staging payloads are built.
VECTOR_SIZE = 384

PRODUCTION_COLLECTION = "docmind_documents_e5_384_v1"
REQUIRED_KEYWORD_INDEXES = (
    "organization_id", "knowledge_base_id", "document_id",
    "document_version_id", "index_state", "processing_token",
)
READY = "ready"
STAGING = "staging"


def _models():
    from qdrant_client import models
    return models


def local_client():
    """Restrict this early deployment diagnostic to loopback Qdrant."""
    from .qdrant_indexing import diagnostic_client
    return diagnostic_client()


def _is_keyword(value: Any) -> bool:
    data_type = getattr(value, "data_type", None)
    if data_type is None and isinstance(value, dict):
        data_type = value.get("data_type")
    return str(getattr(data_type, "value", data_type)).lower() == "keyword"


@dataclass(frozen=True)
class CollectionInspection:
    exists: bool
    compatible: bool
    missing_indexes: tuple[str, ...]
    created: bool = False
    indexes_added: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "collection": PRODUCTION_COLLECTION,
            "exists": self.exists,
            "compatible": self.compatible,
            "dimensions": VECTOR_SIZE,
            "distance": "Cosine",
            "required_indexes": list(REQUIRED_KEYWORD_INDEXES),
            "missing_indexes": list(self.missing_indexes),
            "created": self.created,
            "indexes_added": list(self.indexes_added),
        }


def inspect_collection(client) -> CollectionInspection:
    """Read-only check. Refuse a name collision with an incompatible schema."""
    if not client.collection_exists(PRODUCTION_COLLECTION):
        return CollectionInspection(False, True, REQUIRED_KEYWORD_INDEXES)
    info = client.get_collection(PRODUCTION_COLLECTION)
    models = _models()
    vectors = info.config.params.vectors
    if (
        isinstance(vectors, dict)
        or getattr(vectors, "size", None) != VECTOR_SIZE
        or getattr(vectors, "distance", None) != models.Distance.COSINE
    ):
        raise ValueError("Production collection exists with incompatible vectors")
    schema = getattr(info, "payload_schema", None) or {}
    wrong = [name for name in REQUIRED_KEYWORD_INDEXES if name in schema and not _is_keyword(schema[name])]
    if wrong:
        raise ValueError("Production collection has incompatible payload-index type(s): " + ", ".join(wrong))
    missing = tuple(name for name in REQUIRED_KEYWORD_INDEXES if name not in schema)
    return CollectionInspection(True, True, missing)


def initialize_collection(client) -> CollectionInspection:
    """EXPLICIT operation: create a distinct empty collection or repair indexes.

    Existing points are never cleared. If an incompatible collection already
    exists, fail rather than overwriting data.
    """
    prior = inspect_collection(client)
    models = _models()
    created = not prior.exists
    if created:
        client.create_collection(
            collection_name=PRODUCTION_COLLECTION,
            vectors_config=models.VectorParams(
                size=VECTOR_SIZE, distance=models.Distance.COSINE
            ),
        )
    added: list[str] = []
    for name in prior.missing_indexes:
        client.create_payload_index(
            collection_name=PRODUCTION_COLLECTION,
            field_name=name,
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
        added.append(name)
    result = inspect_collection(client)
    if result.missing_indexes:
        raise RuntimeError("Production Qdrant payload indexes still missing")
    return CollectionInspection(True, True, (), created=created, indexes_added=tuple(added))


def _base_payload(item, identity) -> dict:
    from .qdrant_indexing import payload_for
    return payload_for(item, identity)


def staging_payload(item, identity, processing_token: str) -> dict:
    """Build a *non-visible* payload; does NOT upsert any Qdrant points.

    A future worker must check its live MySQL processing lease before every
    remote operation and handle cross-database partial failure/cleanup.
    """
    if not isinstance(processing_token, str) or not (1 <= len(processing_token) <= 64):
        raise ValueError("processing_token must be 1 to 64 characters")
    data = _base_payload(item, identity)
    data.update({
        "index_state": STAGING,
        "processing_token": processing_token,
        "index_schema_version": "v1",
        "chunker_version": "tokenchunk:v2",
    })
    return data


def authorized_ready_filter(organization_id: str, knowledge_base_id: str,
                            *, document_id: str | None = None):
    """Always restrict vector retrieval by tenant, KB, and ready state.

    Caller MUST establish the user's membership in the specified organization
    and knowledge base using the app's MySQL authorization before calling.
    This filter alone is not authorization.
    """
    for name, value in (("organization_id", organization_id),
                        ("knowledge_base_id", knowledge_base_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
    if document_id is not None and (not isinstance(document_id, str) or not document_id.strip()):
        raise ValueError("document_id must be nonempty when provided")
    models = _models()
    items = [
        ("organization_id", organization_id),
        ("knowledge_base_id", knowledge_base_id),
        ("index_state", READY),
    ]
    if document_id is not None:
        items.append(("document_id", document_id))
    return models.Filter(must=[models.FieldCondition(
        key=key, match=models.MatchValue(value=value)
    ) for key, value in items])
