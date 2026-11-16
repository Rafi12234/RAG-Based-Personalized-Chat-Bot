"""Read-only multilingual E5 -> strictly tenant/KB/ready-filtered Qdrant search.

Never rely on these filters as user authorization. Express verifies session, derives
scope from MySQL, and independently revalidates every returned hit against MySQL.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

MAX_QUERY_TOKENS = 480
MAX_CANDIDATES = 40


@dataclass(frozen=True)
class RetrievalInput:
    organization_id: str
    knowledge_base_id: str
    question: str
    top_k: int


def search_scoped_ready_points(data: RetrievalInput, *, model=None, tokenizer=None, client=None) -> list[dict]:
    """Return bounded raw candidate payloads for mandatory relational filtering.

    Injected dependencies keep unit tests fully offline. No writes or collection
    initialization occur here. Caller owns a client it supplies.
    """
    from .document_embeddings import embed_query, load_embedding_model
    from .production_qdrant import (
        PRODUCTION_COLLECTION, authorized_ready_filter, inspect_collection, local_client,
    )
    from .storage_qdrant_probe import _tokenizer

    if not isinstance(data.organization_id, str) or not data.organization_id.strip():
        raise ValueError("Invalid organization scope")
    if not isinstance(data.knowledge_base_id, str) or not data.knowledge_base_id.strip():
        raise ValueError("Invalid knowledge base scope")
    if not isinstance(data.question, str) or not data.question.strip() or len(data.question) > 1000:
        raise ValueError("Invalid question")
    if type(data.top_k) is not int or not 1 <= data.top_k <= 10:
        raise ValueError("Invalid topK")

    # Use the same tokenizer family used by Phase 2; count the QUERY prefix.
    tok = tokenizer if tokenizer is not None else _tokenizer()
    tokens = len(tok.encode("query: " + data.question.strip()).ids)
    if tokens > MAX_QUERY_TOKENS:
        raise ValueError("Question exceeds embedding token budget")
    engine = model if model is not None else load_embedding_model()
    vector = embed_query(engine, data.question)

    owns_client = client is None
    qdrant = client if client is not None else local_client()
    try:
        inspected = inspect_collection(qdrant)
        if not inspected.exists or not inspected.compatible or inspected.missing_indexes:
            raise RuntimeError("Qdrant collection is unavailable or incompatible")
        matches = qdrant.query_points(
            collection_name=PRODUCTION_COLLECTION,
            query=list(vector),
            query_filter=authorized_ready_filter(data.organization_id, data.knowledge_base_id),
            limit=min(data.top_k * 4, MAX_CANDIDATES),
            with_payload=[
                "organization_id", "knowledge_base_id", "document_id",
                "document_version_id", "index_state", "chunk_index",
                "page_number", "text",
            ],
            with_vectors=False,
            timeout=10,
        ).points
        if len(matches) > MAX_CANDIDATES:
            raise RuntimeError("Unbounded vector response")
        results = []
        for hit in matches:
            if not isinstance(hit.score, (float, int)) or not isfinite(float(hit.score)):
                # Node validates score again; do not forward malformed hits.
                continue
            results.append({"id": str(hit.id), "score": float(hit.score), "payload": hit.payload})
        return results
    finally:
        if owns_client:
            qdrant.close()
