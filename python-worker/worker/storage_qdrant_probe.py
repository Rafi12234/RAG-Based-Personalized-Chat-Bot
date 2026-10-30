"""Phase 2.6A diagnostic: RustFS -> chunks -> local E5 -> Qdrant.

Default: preview only, NO database or Qdrant writes.
Explicit --write-diagnostic: store chunk TEXT and vectors ONLY in a local,
separate diagnostic Qdrant collection. No MySQL changes or READY transition.

Run from python-worker:
    python -m worker.storage_qdrant_probe JOB_ID
    python -m worker.storage_qdrant_probe JOB_ID --write-diagnostic
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from . import settings  # loads python-worker/.env
from .db import get_connection
from .document_chunking import iter_document_chunks
from .document_embeddings import MODEL_NAME, MODEL_TOKEN_LIMIT, VECTOR_SIZE, iter_chunk_embeddings, load_embedding_model
from .document_extraction import iter_extracted_segments
from .qdrant_indexing import (DIAGNOSTIC_COLLECTION, IndexIdentity, count_version_points,
                              diagnostic_client, ensure_diagnostic_collection,
                              upsert_diagnostic)
from .storage_extraction_probe import _s3_client_and_bucket, download_into, read_job_metadata

SAFE_TOKEN_LIMIT = 480  # headroom below E5's nominal 512-token limit


def identity_for_job(job_id: str) -> IndexIdentity:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT j.id AS job_id, v.id AS version_id, v.filename,
                       d.id AS document_id, d.knowledgeBaseId AS kb_id,
                       kb.organizationId AS organization_id, d.deletedAt
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                JOIN KnowledgeBase AS kb ON kb.id = d.knowledgeBaseId
                WHERE j.id = %s LIMIT 1
            """, (job_id,))
            row = cursor.fetchone()
        if row is None or row["deletedAt"] is not None:
            raise ValueError("Missing or archived document")
        return IndexIdentity(
            organization_id=row["organization_id"],
            knowledge_base_id=row["kb_id"],
            document_id=row["document_id"],
            document_version_id=row["version_id"],
            job_id=row["job_id"],
            filename=row["filename"],
        )
    finally:
        connection.close()


def _tokenizer():
    """Load exact E5 tokenizer from its official repository, cached after first download."""
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer
    path = hf_hub_download(repo_id=MODEL_NAME, filename="tokenizer.json")
    return Tokenizer.from_file(path)


def inspect_qdrant_job(job_id: str, *, write_diagnostic: bool = False) -> dict:
    metadata = read_job_metadata(job_id)
    identity = identity_for_job(job_id)
    if identity.filename != metadata["filename"]:
        raise ValueError("Document metadata changed during probe")
    if write_diagnostic and (metadata["jobStatus"] != "QUEUED" or metadata["versionStatus"] != "QUEUED"):
        raise ValueError("Diagnostic writes require an untouched queued document")

    qdrant = diagnostic_client()
    s3, bucket = _s3_client_and_bucket()
    try:
        # Verifies Qdrant connection; neither preview nor writing uses a production collection.
        qdrant.get_collections()
        with tempfile.TemporaryDirectory(prefix="docmind-qdrant-diagnostic-") as tempdir:
            local = Path(tempdir) / ("document" + Path(identity.filename).suffix.lower())
            received = download_into(s3, bucket, metadata["storageKey"], local,
                                     int(metadata["sizeBytes"]))
            tokenizer = _tokenizer()
            pending_ocr = []
            count = 0
            max_tokens = 0

            def extracted():
                for segment in iter_extracted_segments(local, identity.filename):
                    if segment.needs_ocr and segment.page_number is not None:
                        pending_ocr.append(segment.page_number)
                    yield segment

            # Preflight ALL chunks and OCR flags BEFORE any diagnostic writes.
            for chunk in iter_document_chunks(extracted(), identity.document_version_id):
                tokens = len(tokenizer.encode("passage: " + chunk.text).ids)
                if tokens > SAFE_TOKEN_LIMIT:
                    raise ValueError("Chunk exceeds safe E5 token budget; token-aware chunking required")
                max_tokens = max(max_tokens, tokens)
                count += 1
            if not count:
                raise ValueError("Document has no indexable text")
            if write_diagnostic and pending_ocr:
                raise ValueError("OCR candidate pages present; diagnostic write refused")

            model = load_embedding_model()
            # Read a second time to avoid holding all pages/chunks in RAM.
            chunks = iter_document_chunks(
                iter_extracted_segments(local, identity.filename),
                identity.document_version_id,
            )
            embeddings = iter_chunk_embeddings(chunks, model)
            embedded_count = 0
            if write_diagnostic:
                ensure_diagnostic_collection(qdrant)
                embedded_count = upsert_diagnostic(qdrant, embeddings, identity)
                stored = count_version_points(qdrant, identity)
            else:
                for _ in embeddings:
                    embedded_count += 1
                stored = None
            if embedded_count != count:
                raise RuntimeError("Embedding/chunk count changed between passes")
            return {
                "job_id": job_id,
                "filename": identity.filename,
                "job_status": metadata["jobStatus"],
                "document_status": metadata["versionStatus"],
                "downloaded_bytes": received,
                "model": MODEL_NAME,
                "dimensions": VECTOR_SIZE,
                "chunks": count,
                "embeddings_generated": embedded_count,
                "max_model_tokens_per_chunk": max_tokens,
                "safe_token_budget": SAFE_TOKEN_LIMIT,
                "model_token_limit": MODEL_TOKEN_LIMIT,
                "ocr_pages_pending_total": len(pending_ocr),
                "qdrant_collection": DIAGNOSTIC_COLLECTION,
                "qdrant_written": write_diagnostic,
                "qdrant_version_points": stored,
                "mysql_modified": False,
                "production_index": False,
            }
    finally:
        s3.close()
        qdrant.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--write-diagnostic", action="store_true",
                        help="Explicitly store chunk TEXT + vectors in local diagnostic Qdrant collection")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(inspect_qdrant_job(args.job_id,
                                            write_diagnostic=args.write_diagnostic), indent=2))
        return 0
    except Exception as exc:
        # Prevent leaking private document content or storage credentials to logs.
        print(f"Qdrant diagnostic failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
