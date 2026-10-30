"""Read-only DocMind-AI operator OCR diagnostic for an existing ingestion job.

Usage (from python-worker):
  python -m worker.storage_ocr_probe JOB_ID
  python -m worker.storage_ocr_probe JOB_ID --language eng+ben

No Celery publish, MySQL writes, Qdrant writes or document-content logging.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from . import settings  # loads python-worker/.env
from .document_ocr import iter_pdf_segments_with_ocr
from .storage_extraction_probe import (
    _s3_client_and_bucket,
    download_into,
    read_job_metadata,
)


def inspect_job_ocr(job_id: str, *, language: str = "eng", dpi: int = 180,
                    max_ocr_pages: int = 10, tessdata: str | None = None) -> dict:
    """Download into a temporary file; report OCR counts without returning text."""
    metadata = read_job_metadata(job_id)
    if Path(metadata["filename"]).suffix.lower() != ".pdf":
        raise ValueError("OCR probe currently supports PDF jobs only")
    s3, bucket = _s3_client_and_bucket()
    try:
        with tempfile.TemporaryDirectory(prefix="docmind-ocr-") as temp:
            local = Path(temp) / "download.pdf"
            downloaded = download_into(
                s3, bucket, metadata["storageKey"], local, int(metadata["sizeBytes"])
            )
            ocr_pages: list[int] = []
            blank_pages: list[int] = []
            pages = 0
            text_chars = 0
            for segment in iter_pdf_segments_with_ocr(
                local, language=language, dpi=dpi, tessdata=tessdata,
                max_ocr_pages=max_ocr_pages,
                on_ocr=ocr_pages.append,
                on_blank=blank_pages.append,
            ):
                pages += 1
                text_chars += len(segment.text)
            return {
                "job_id": job_id,
                "filename": metadata["filename"],
                "job_status": metadata["jobStatus"],
                "document_status": metadata["versionStatus"],
                "downloaded_bytes": downloaded,
                "pages": pages,
                "characters_after_ocr": text_chars,
                "ocr_pages": ocr_pages[:30],
                "ocr_pages_total": len(ocr_pages),
                "blank_pages_total": len(blank_pages),
                "ocr_language": language,
                "ocr_dpi": dpi,
                "read_only": True,
                "mysql_modified": False,
                "qdrant_written": False,
            }
    finally:
        s3.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    parser.add_argument("--language", default="eng")
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--max-ocr-pages", type=int, default=10)
    parser.add_argument("--tessdata", default=None)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(inspect_job_ocr(
            args.job_id, language=args.language, dpi=args.dpi,
            max_ocr_pages=args.max_ocr_pages, tessdata=args.tessdata,
        ), indent=2))
        return 0
    except Exception as exc:
        # Keep storage keys, passwords and extracted text out of CLI errors.
        print(f"OCR probe failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
