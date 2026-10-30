"""DocMind-AI Phase 2.9B: read-only, integrated ingestion rehearsal.

Exercises real extraction/OCR, token-safe chunking, local embeddings and
production Qdrant schema inspection WITHOUT claiming a job or writing data.

It is NOT the real ingestion worker. Never mark a document READY from this test.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable
from uuid import UUID

from . import settings  # noqa: F401 - load the existing python-worker/.env
from .document_chunking import iter_document_chunks
from .document_embeddings import (
    ChunkEmbedding, MODEL_NAME, VECTOR_SIZE,
    iter_chunk_embeddings, load_embedding_model,
)
from .document_extraction import iter_extracted_segments
from .document_ocr import iter_pdf_segments_with_ocr
from .ingestion_lease_verification import read_job_state
from .production_qdrant import PRODUCTION_COLLECTION, inspect_collection, local_client
from .storage_extraction_probe import (
    _s3_client_and_bucket, download_into, read_job_metadata,
)
from .storage_qdrant_probe import _tokenizer, identity_for_job
from .token_safe_chunking import SAFE_TOKEN_BUDGET, _count_tokens, iter_token_safe_chunks

MAX_REHEARSAL_CHUNKS = 50_000


@dataclass(frozen=True)
class RehearsalMetrics:
    chunks: int
    embedded_vectors: int
    max_tokens: int
    pages: tuple[int, ...]
    sample_ids: tuple[str, ...]


def inspect_embedded_stream(
    embeddings: Iterable[ChunkEmbedding],
    tokenizer,
    document_version_id: str,
    *,
    max_chunks: int = MAX_REHEARSAL_CHUNKS,
) -> RehearsalMetrics:
    """Validate generated vectors, metadata and token budgets; print no content."""
    if not isinstance(document_version_id, str) or not document_version_id.strip():
        raise ValueError("Document version ID is required")
    if type(max_chunks) is not int or not 1 <= max_chunks <= MAX_REHEARSAL_CHUNKS:
        raise ValueError("Invalid rehearsal chunk limit")
    count = 0
    high_water = 0
    ids: set[str] = set()
    pages: set[int] = set()
    preview: list[str] = []
    for item in embeddings:
        if not isinstance(item, ChunkEmbedding):
            raise TypeError("Expected ChunkEmbedding")
        chunk = item.chunk
        if chunk.document_version_id != document_version_id:
            raise RuntimeError("Embedding belongs to another document version")
        if chunk.chunk_index != count:
            raise RuntimeError("Chunk ordering is not contiguous")
        try:
            canonical_id = str(UUID(chunk.chunk_id))
        except (ValueError, AttributeError, TypeError) as exc:
            raise RuntimeError("Invalid chunk UUID") from exc
        if canonical_id != chunk.chunk_id or canonical_id in ids:
            raise RuntimeError("Duplicate or non-canonical chunk UUID")
        ids.add(canonical_id)
        if len(item.vector) != VECTOR_SIZE:
            raise RuntimeError("Invalid embedding vector dimension")
        tokens = _count_tokens(tokenizer, chunk.text)
        if tokens > SAFE_TOKEN_BUDGET:
            raise RuntimeError("Embedding exceeds model token budget")
        high_water = max(high_water, tokens)
        if chunk.page_number is not None:
            pages.add(chunk.page_number)
        if len(preview) < 4:
            preview.append(chunk.chunk_id)
        count += 1
        if count > max_chunks:
            raise RuntimeError("Rehearsal chunk count limit exceeded")
    if count == 0:
        raise RuntimeError("Document has no indexable chunks")
    return RehearsalMetrics(count, count, high_water, tuple(sorted(pages)), tuple(preview))


def rehearse_ingestion(job_id: str, *, language: str = "eng", max_ocr_pages: int = 10) -> dict:
    """Integrated read-only pipeline with no Celery publish, MySQL or Qdrant writes."""
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("Ingestion job ID is required")
    if type(max_ocr_pages) is not int or not 1 <= max_ocr_pages <= 5000:
        raise ValueError("OCR budget must be between 1 and 5000")
    # Fail closed for jobs already claimed, archived, failed or completed.
    before = read_job_state(job_id)
    if before is None or not before.ready_for_claim:
        raise RuntimeError("Job is not eligible for read-only QUEUED rehearsal")
    metadata = read_job_metadata(job_id)
    identity = identity_for_job(job_id)
    if metadata["filename"] != identity.filename:
        raise RuntimeError("Document metadata changed during preflight")

    qdrant = local_client()
    try:
        schema = inspect_collection(qdrant)
        if not schema.exists or not schema.compatible or schema.missing_indexes:
            raise RuntimeError("Production Qdrant schema must be initialized")
    finally:
        qdrant.close()

    s3, bucket = _s3_client_and_bucket()
    ocr_pages: list[int] = []
    blank_pages: list[int] = []
    try:
        with TemporaryDirectory(prefix="docmind-ingestion-rehearsal-") as tmp:
            ext = Path(identity.filename).suffix.lower()
            local_file = Path(tmp) / ("source" + ext)
            bytes_received = download_into(
                s3, bucket, metadata["storageKey"], local_file,
                int(metadata["sizeBytes"]),
            )
            if ext == ".pdf":
                segments = iter_pdf_segments_with_ocr(
                    local_file, language=language, max_ocr_pages=max_ocr_pages,
                    on_ocr=ocr_pages.append, on_blank=blank_pages.append,
                )
            else:
                segments = iter_extracted_segments(local_file, identity.filename)
            tokenizer = _tokenizer()
            raw_chunks = iter_document_chunks(segments, identity.document_version_id)
            token_chunks = iter_token_safe_chunks(raw_chunks, tokenizer)
            model = load_embedding_model()
            vectors = iter_chunk_embeddings(token_chunks, model)
            metrics = inspect_embedded_stream(vectors, tokenizer, identity.document_version_id)
    finally:
        s3.close()

    # A read-only rehearsal can race with a real worker; refuse to claim success
    # if job status or attempts changed while we were reading the document.
    after = read_job_state(job_id)
    if after is None or not after.ready_for_claim or after.attempts != before.attempts:
        raise RuntimeError("Job state changed during rehearsal")
    return {
        "job_id": job_id,
        "filename": identity.filename,
        "job_status": after.job_status,
        "document_status": after.document_status,
        "downloaded_bytes": bytes_received,
        "model": MODEL_NAME,
        "dimensions": VECTOR_SIZE,
        "chunks": metrics.chunks,
        "embeddings_generated": metrics.embedded_vectors,
        "max_tokens_per_chunk": metrics.max_tokens,
        "safe_token_budget": SAFE_TOKEN_BUDGET,
        "pages_with_embeddings": list(metrics.pages),
        "preview_chunk_ids": list(metrics.sample_ids),
        "ocr_pages_processed": len(ocr_pages),
        "blank_pdf_pages": len(blank_pages),
        "production_collection": PRODUCTION_COLLECTION,
        "production_schema_compatible": True,
        "mode": "read_only_rehearsal",
        "mysql_modified": False,
        "qdrant_written": False,
        "celery_task_published": False,
        "automatic_ingestion_enabled": False,
    }
