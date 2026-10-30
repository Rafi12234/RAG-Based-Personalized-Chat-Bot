"""Read-only token-safe chunking probe for DocMind-AI Phase 2.7A.

Usage: python -m worker.storage_token_chunk_probe INGESTION_JOB_ID
No MySQL modifications, Redis messages, Qdrant collection writes, or document
text output. The E5 tokenizer may be downloaded on the first run.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from . import settings  # load python-worker/.env
from .document_chunking import iter_document_chunks
from .document_embeddings import MODEL_NAME
from .document_extraction import iter_extracted_segments
from .storage_qdrant_probe import identity_for_job, _tokenizer
from .storage_extraction_probe import read_job_metadata, _s3_client_and_bucket, download_into
from .token_safe_chunking import iter_token_safe_chunks, _count_tokens, SAFE_TOKEN_BUDGET


def inspect_job_token_chunks(job_id: str) -> dict:
    metadata = read_job_metadata(job_id)
    identity = identity_for_job(job_id)
    if metadata["filename"] != identity.filename:
        raise ValueError("Document metadata changed during probe")
    tokenizer = _tokenizer()
    s3, bucket = _s3_client_and_bucket()
    try:
        with tempfile.TemporaryDirectory(prefix="docmind-token-safe-") as tempdir:
            local = Path(tempdir) / ("document" + Path(identity.filename).suffix.lower())
            received = download_into(
                s3, bucket, metadata["storageKey"], local,
                int(metadata["sizeBytes"]),
            )
            ocr_pages = []
            original_count = 0
            def extracted():
                for segment in iter_extracted_segments(local, identity.filename):
                    if segment.needs_ocr and segment.page_number is not None:
                        ocr_pages.append(segment.page_number)
                    yield segment
            def source_chunks():
                nonlocal original_count
                for chunk in iter_document_chunks(extracted(), identity.document_version_id):
                    original_count += 1
                    yield chunk
            count = 0
            max_observed = 0
            pages = set()
            preview = []
            for ch in iter_token_safe_chunks(source_chunks(), tokenizer):
                tokens = _count_tokens(tokenizer, ch.text)
                max_observed = max(max_observed, tokens)
                count += 1
                if ch.page_number is not None:
                    pages.add(ch.page_number)
                if len(preview) < 5:
                    preview.append({
                        "chunk_index": ch.chunk_index,
                        "chunk_id": ch.chunk_id,
                        "page_number": ch.page_number,
                        "tokens_including_prefix": tokens,
                        "characters": len(ch.text),
                    })
            return {
                "job_id": job_id,
                "filename": identity.filename,
                "job_status": metadata["jobStatus"],
                "document_status": metadata["versionStatus"],
                "downloaded_bytes": received,
                "model": MODEL_NAME,
                "original_chunks": original_count,
                "token_safe_chunks": count,
                "maximum_observed_tokens": max_observed,
                "safe_token_budget": SAFE_TOKEN_BUDGET,
                "pages_with_chunks": sorted(pages),
                "pending_ocr_page_count": len(ocr_pages),
                "new_chunk_id_namespace": "tokenchunk:v2",
                "preview": preview,
                "mysql_modified": False,
                "qdrant_written": False,
                "read_only": True,
            }
    finally:
        s3.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(inspect_job_token_chunks(args.job_id), indent=2))
        return 0
    except Exception as exc:
        # Error details could include private object keys or document content.
        print(f"Token-safe chunking probe failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
