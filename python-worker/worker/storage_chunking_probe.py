"""Read-only MySQL -> RustFS -> extraction -> chunking verification.

Run from python-worker: python -m worker.storage_chunking_probe JOB_ID
Never returns document text, changes job state, or writes vectors.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from . import settings  # loads python-worker/.env
from .db import get_connection
from .document_chunking import iter_document_chunks
from .document_extraction import iter_extracted_segments
from .storage_extraction_probe import (
    _s3_client_and_bucket,
    download_into,
    read_job_metadata,
)


def _document_version_id(job_id: str) -> str:
    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT documentVersionId FROM IngestionJob WHERE id = %s",
                (job_id,),
            )
            row = cursor.fetchone()
        if not row:
            raise ValueError("Ingestion job not found")
        return row["documentVersionId"]
    finally:
        connection.close()


def inspect_job_chunks(job_id: str, *, max_chars: int = 1000, overlap_chars: int = 150) -> dict:
    """Summarize real chunks without exposing extracted document content."""
    metadata = read_job_metadata(job_id)
    version_id = _document_version_id(job_id)
    client, bucket = _s3_client_and_bucket()
    try:
        with tempfile.TemporaryDirectory(prefix="docmind-chunks-") as temp:
            suffix = Path(metadata["filename"]).suffix.lower()
            local = Path(temp) / ("download" + suffix)
            bytes_received = download_into(
                client, bucket, metadata["storageKey"],
                local, int(metadata["sizeBytes"]),
            )
            # Extraction is streamed; chunk text isn't sent to stdout or Redis.
            ocr_pages: list[int] = []

            def tracked_segments():
                for segment in iter_extracted_segments(local, metadata["filename"]):
                    if segment.needs_ocr and segment.page_number is not None:
                        ocr_pages.append(segment.page_number)
                    yield segment

            count = 0
            characters = 0
            pages: set[int] = set()
            preview = []
            for chunk in iter_document_chunks(
                tracked_segments(), version_id,
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            ):
                count += 1
                characters += len(chunk.text)
                if chunk.page_number is not None:
                    pages.add(chunk.page_number)
                if len(preview) < 5:
                    preview.append({
                        "chunk_index": chunk.chunk_index,
                        "chunk_id": chunk.chunk_id,
                        "page_number": chunk.page_number,
                        "source_start_segment": chunk.source_start_segment,
                        "source_end_segment": chunk.source_end_segment,
                        "characters": len(chunk.text),
                    })
            return {
                "job_id": job_id,
                "filename": metadata["filename"],
                "job_status": metadata["jobStatus"],
                "document_status": metadata["versionStatus"],
                "downloaded_bytes": bytes_received,
                "chunks": count,
                "chunk_characters_including_overlap": characters,
                "pages_with_text_chunks": sorted(pages),
                "potential_ocr_pages": ocr_pages[:30],
                "potential_ocr_pages_total": len(ocr_pages),
                "max_chars": max_chars,
                "overlap_chars": overlap_chars,
                "preview": preview,
                "read_only": True,
            }
    finally:
        client.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--max-chars", type=int, default=1000)
    parser.add_argument("--overlap-chars", type=int, default=150)
    args = parser.parse_args(argv)
    try:
        result = inspect_job_chunks(
            args.job_id, max_chars=args.max_chars, overlap_chars=args.overlap_chars
        )
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # Do not leak document content, object keys or credentials in CLI errors.
        print(f"Chunking probe failed ({type(exc).__name__}).")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
