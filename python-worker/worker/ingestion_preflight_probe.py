"""Read-only Phase 2.9A operator preflight: job state and Qdrant schema.

Usage:
  cd python-worker
  python -m worker.ingestion_preflight_probe JOB_ID

Never claims or completes a job, publishes Celery tasks, or writes to Qdrant.
No processing token, document text, or credentials are printed.
"""
from __future__ import annotations

import argparse
import json

from . import settings  # load .env
from .ingestion_lease_verification import read_job_state
from .production_qdrant import PRODUCTION_COLLECTION, inspect_collection, local_client


def preview_ingestion_readiness(job_id: str) -> dict:
    state = read_job_state(job_id)
    if state is None:
        raise ValueError("Ingestion job not found")
    client = local_client()
    try:
        schema = inspect_collection(client)
        return {
            "job_id": job_id,
            "job_status": state.job_status,
            "document_status": state.document_status,
            "attempts": state.attempts,
            "document_active": state.document_active,
            "processing_token_present": state.token_present,
            "processing_lease_live": state.lease_live,
            "eligible_for_future_claim": state.ready_for_claim,
            "production_collection": PRODUCTION_COLLECTION,
            "collection_exists": schema.exists,
            "collection_compatible": schema.compatible,
            "missing_indexes": list(schema.missing_indexes),
            "mode": "read_only",
            "mysql_modified": False,
            "qdrant_written": False,
            "automatic_ingestion_enabled": False,
        }
    finally:
        client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(preview_ingestion_readiness(args.job_id), indent=2))
        return 0
    except Exception as exc:
        print(f"Read-only ingestion preflight failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
