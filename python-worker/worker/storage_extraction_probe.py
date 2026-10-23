"""Read-only RustFS -> temporary file -> extraction diagnostic for an ingestion job.

Run ONLY as an operator CLI: python -m worker.storage_extraction_probe JOB_ID
No job status transitions, Celery publish, vector indexing, or content logging.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

import boto3
from botocore.config import Config

from .document_extraction import DocumentExtractionError, summarize_extraction

MAX_UPLOAD_BYTES = 250 * 1024 * 1024  # Match backend/src/routes/documents.ts
DOWNLOAD_BLOCK_SIZE = 1024 * 1024


def read_job_metadata(job_id: str) -> dict:
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("A valid job ID is required")
    from .db import get_connection

    connection = get_connection()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.id, j.status AS jobStatus,
                       v.status AS versionStatus,
                       v.filename, v.mimeType, v.storageKey, v.sizeBytes,
                       d.deletedAt
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                WHERE j.id = %s LIMIT 1
                """,
                (job_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise DocumentExtractionError("Ingestion job not found")
        if row["deletedAt"] is not None:
            raise DocumentExtractionError("Archived document cannot be extracted")
        if not row["storageKey"]:
            raise DocumentExtractionError("Document has no storage key")
        if int(row["sizeBytes"]) <= 0 or int(row["sizeBytes"]) > MAX_UPLOAD_BYTES:
            raise DocumentExtractionError("Document size is outside upload limits")
        return row
    finally:
        connection.close()


def _s3_client_and_bucket():
    names = ["S3_ENDPOINT", "S3_REGION", "S3_BUCKET", "S3_ACCESS_KEY", "S3_SECRET_KEY"]
    missing = [name for name in names if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing Python worker environment variables: " + ", ".join(missing))
    endpoint = os.environ["S3_ENDPOINT"]
    if not endpoint.startswith(("http://", "https://")):
        raise RuntimeError("S3_ENDPOINT must use http:// or https://")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ["S3_REGION"],
        aws_access_key_id=os.environ["S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["S3_SECRET_KEY"],
        config=Config(
            s3={"addressing_style": "path"},
            connect_timeout=10,
            read_timeout=60,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
    return client, os.environ["S3_BUCKET"]


def download_into(client, bucket: str, key: str, destination: Path, expected_bytes: int) -> int:
    """Stream an S3 object with strict byte limits; never buffer it all in RAM."""
    response = client.get_object(Bucket=bucket, Key=key)
    body = response["Body"]
    received = 0
    try:
        remote_size = response.get("ContentLength")
        if remote_size is not None and int(remote_size) != expected_bytes:
            raise DocumentExtractionError("RustFS object size differs from MySQL metadata")
        with destination.open("wb") as handle:
            while True:
                chunk = body.read(DOWNLOAD_BLOCK_SIZE)
                if not chunk:
                    break
                received += len(chunk)
                if received > expected_bytes or received > MAX_UPLOAD_BYTES:
                    raise DocumentExtractionError("Object exceeded its declared size")
                handle.write(chunk)
        if received != expected_bytes:
            raise DocumentExtractionError("Downloaded object is incomplete")
        return received
    finally:
        body.close()


def inspect_job_document(job_id: str) -> dict:
    """Read-only end-to-end storage diagnostic. Never return document text."""
    from . import settings  # loads python-worker/.env without printing secrets

    del settings
    metadata = read_job_metadata(job_id)
    client, bucket = _s3_client_and_bucket()
    try:
        with tempfile.TemporaryDirectory(prefix="docmind-extract-") as tempdir:
            extension = Path(metadata["filename"]).suffix.lower()
            local = Path(tempdir) / ("download" + extension)
            actual_size = download_into(
                client, bucket, metadata["storageKey"],
                local, int(metadata["sizeBytes"]),
            )
            result = summarize_extraction(local, metadata["filename"])
            return {
                "job_id": job_id,
                "filename": metadata["filename"],
                "job_status": metadata["jobStatus"],
                "document_status": metadata["versionStatus"],
                "downloaded_bytes": actual_size,
                "format": result.format,
                "segments": result.segments,
                "characters": result.characters,
                "pages": result.pages,
                "potential_ocr_pages": list(result.potential_ocr_pages[:30]),
                "potential_ocr_pages_total": len(result.potential_ocr_pages),
                "read_only": True,
            }
    finally:
        client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", help="Existing MySQL IngestionJob ID")
    args = parser.parse_args(argv)
    try:
        import json
        print(json.dumps(inspect_job_document(args.job_id), indent=2))
        return 0
    except Exception as exc:
        # Don't print raw storage keys, credentials, or document content.
        print(f"Extraction probe failed ({type(exc).__name__}): {exc.__class__.__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
