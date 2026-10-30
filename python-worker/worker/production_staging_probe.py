"""Read-only production staging PREVIEW. Never stage vectors or claim an ingestion job.

Usage (from python-worker):
    python -m worker.production_staging_probe JOB_ID

Uses the exact E5 tokenizer and new tokenchunk:v2 IDs. Documents are read
from local RustFS to a temporary file; neither MySQL nor Qdrant is modified.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from . import settings  # noqa: F401 - loads local .env
from .document_chunking import iter_document_chunks
from .document_embeddings import iter_chunk_embeddings, load_embedding_model, MODEL_NAME, VECTOR_SIZE
from .document_extraction import iter_extracted_segments
from .document_ocr import iter_pdf_segments_with_ocr
from .production_qdrant import inspect_collection, local_client, PRODUCTION_COLLECTION
from .storage_extraction_probe import _s3_client_and_bucket, download_into, read_job_metadata
from .storage_qdrant_probe import identity_for_job, _tokenizer
from .token_safe_chunking import iter_token_safe_chunks, _count_tokens, SAFE_TOKEN_BUDGET


def preview_job(job_id: str) -> dict:
    meta = read_job_metadata(job_id)
    identity = identity_for_job(job_id)
    if identity.filename != meta['filename']:
        raise ValueError('Document metadata changed while previewing')
    client = local_client()
    s3, bucket = _s3_client_and_bucket()
    try:
        inspection = inspect_collection(client)
        if not inspection.exists or inspection.missing_indexes:
            raise RuntimeError('Production Qdrant schema is not initialized')
        tokenizer = _tokenizer()
        model = load_embedding_model()
        with tempfile.TemporaryDirectory(prefix='docmind-staging-preview-') as folder:
            filename = meta['filename']
            path = Path(folder) / ('document' + Path(filename).suffix.lower())
            received = download_into(s3, bucket, meta['storageKey'], path,
                                     int(meta['sizeBytes']))
            ocr_pages: list[int] = []
            def extracted():
                if Path(filename).suffix.lower() == '.pdf':
                    yield from iter_pdf_segments_with_ocr(path, on_ocr=ocr_pages.append,
                                                          max_ocr_pages=10)
                else:
                    yield from iter_extracted_segments(path, filename)
            chunks = iter_token_safe_chunks(
                iter_document_chunks(extracted(), identity.document_version_id), tokenizer)
            embedded = 0
            pages: set[int] = set()
            max_tokens = 0
            preview = []
            for item in iter_chunk_embeddings(chunks, model):
                ch = item.chunk
                tokens = _count_tokens(tokenizer, ch.text)
                if tokens > SAFE_TOKEN_BUDGET:
                    raise RuntimeError('Token limit exceeded')
                max_tokens = max(max_tokens, tokens)
                embedded += 1
                if ch.page_number is not None:
                    pages.add(ch.page_number)
                if len(preview) < 4:
                    preview.append({'chunk_id': ch.chunk_id,
                                    'page_number': ch.page_number, 'tokens': tokens})
            if not embedded:
                raise RuntimeError('No indexable text; staging cannot proceed')
            return {'job_id': job_id, 'filename': filename,
                    'job_status': meta['jobStatus'], 'document_status': meta['versionStatus'],
                    'downloaded_bytes': received, 'chunks_embedded': embedded,
                    'pages': sorted(pages), 'ocr_pages': ocr_pages[:30],
                    'model': MODEL_NAME, 'dimensions': VECTOR_SIZE,
                    'max_tokens': max_tokens, 'safe_token_budget': SAFE_TOKEN_BUDGET,
                    'collection': PRODUCTION_COLLECTION, 'chunk_id_namespace': 'tokenchunk:v2',
                    'preview': preview, 'mysql_modified': False,
                    'qdrant_written': False, 'document_ready': False}
    finally:
        s3.close()
        client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(preview_job(args.job_id), indent=2))
        return 0
    except Exception as exc:
        # Suppress messages that might include private source text or keys.
        print(f'Staging preview failed: {type(exc).__name__}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
