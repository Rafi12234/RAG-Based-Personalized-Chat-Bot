"""Read-only diagnostic: MySQL -> RustFS -> extraction -> chunks -> embeddings.

Usage:
    cd python-worker
    python -m worker.storage_embedding_probe INGESTION_JOB_ID

Model files may be downloaded on first run. No document content or vector values
are printed. No MySQL statuses or Qdrant collections are modified.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import tempfile

from . import settings  # noqa: F401 - load local .env
from .document_chunking import iter_document_chunks
from .document_embeddings import (
    MODEL_NAME, MODEL_TOKEN_LIMIT, VECTOR_SIZE,
    iter_chunk_embeddings, load_embedding_model,
)
from .document_extraction import iter_extracted_segments
from .storage_chunking_probe import _document_version_id
from .storage_extraction_probe import (
    _s3_client_and_bucket, download_into, read_job_metadata,
)


def inspect_job_embeddings(job_id: str, *, max_chars=1000, overlap_chars=150) -> dict:
    """Generate real local vectors, return only counts and metadata, discard vectors."""
    metadata = read_job_metadata(job_id)
    version_id = _document_version_id(job_id)
    client, bucket = _s3_client_and_bucket()
    try:
        with tempfile.TemporaryDirectory(prefix="docmind-embeddings-") as tempdir:
            suffix = Path(metadata["filename"]).suffix.lower()
            path = Path(tempdir) / ("document" + suffix)
            received = download_into(
                client, bucket, metadata["storageKey"],
                path, int(metadata["sizeBytes"]),
            )
            ocr_pages = []
            def segments():
                for segment in iter_extracted_segments(path, metadata["filename"]):
                    if segment.needs_ocr and segment.page_number is not None:
                        ocr_pages.append(segment.page_number)
                    yield segment

            chunks = iter_document_chunks(
                segments(), version_id,
                max_chars=max_chars, overlap_chars=overlap_chars,
            )
            model = load_embedding_model()
            count = 0
            preview_ids = []
            pages = set()
            min_norm = float("inf")
            max_norm = 0.0
            for item in iter_chunk_embeddings(chunks, model):
                count += 1
                if item.chunk.page_number is not None:
                    pages.add(item.chunk.page_number)
                if len(preview_ids) < 4:
                    preview_ids.append(item.chunk.chunk_id)
                norm = math.sqrt(math.fsum(x * x for x in item.vector))
                min_norm = min(min_norm, norm)
                max_norm = max(max_norm, norm)

            return {
                "job_id": job_id,
                "filename": metadata["filename"],
                "job_status": metadata["jobStatus"],
                "document_status": metadata["versionStatus"],
                "downloaded_bytes": received,
                "model": MODEL_NAME,
                "dimensions": VECTOR_SIZE,
                "embeddings_generated": count,
                "pages_with_embeddings": sorted(pages),
                "preview_chunk_ids": preview_ids,
                "vector_norm_range": [round(min_norm, 5), round(max_norm, 5)] if count else [],
                "ocr_pages_pending": ocr_pages[:30],
                "ocr_pages_pending_total": len(ocr_pages),
                "model_token_limit": MODEL_TOKEN_LIMIT,
                "token_safe_chunking_implemented": False,
                "read_only": True,
                "qdrant_written": False,
            }
    finally:
        client.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--max-chars", type=int, default=1000)
    parser.add_argument("--overlap-chars", type=int, default=150)
    args = parser.parse_args(argv)
    try:
        result = inspect_job_embeddings(
            args.job_id, max_chars=args.max_chars, overlap_chars=args.overlap_chars
        )
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # Suppress exception messages, which may contain object keys or private text.
        print(f"Embedding probe failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
