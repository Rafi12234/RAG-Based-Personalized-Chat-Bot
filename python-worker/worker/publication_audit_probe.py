"""Read-only inspection of publication state for one DocMind-AI job.

Usage: python -m worker.publication_audit_probe JOB_ID
Never returns vector contents, processing tokens, text, or user credentials.
No writes to Qdrant, MySQL, Redis or RustFS.
"""
from __future__ import annotations

import argparse
import json

from . import settings  # noqa: F401 - loads the existing .env
from .ingestion_lease_verification import read_job_state
from .production_qdrant import PRODUCTION_COLLECTION, local_client
from .publication_audit import audit_publication_counts, interpret_publication_state
from .storage_qdrant_probe import identity_for_job


def inspect_publication(job_id: str) -> dict:
    state = read_job_state(job_id)
    if state is None:
        raise ValueError("Ingestion job not found")
    identity = identity_for_job(job_id)
    client = local_client()
    try:
        counts = audit_publication_counts(client, identity)
    finally:
        client.close()
    return {
        "job_id": job_id,
        "job_status": state.job_status,
        "document_status": state.document_status,
        "attempts": state.attempts,
        "processing_token_present": state.token_present,
        "processing_lease_live": state.lease_live,
        "collection": PRODUCTION_COLLECTION,
        "staging_points_for_version": counts.staged,
        "ready_points_for_version": counts.ready,
        "assessment": interpret_publication_state(state.job_status, state.document_status, counts),
        "authoritative_integrity_verified": False,
        "mysql_modified": False,
        "qdrant_written": False,
        "automatic_ingestion_enabled": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(inspect_publication(args.job_id), indent=2))
        return 0
    except Exception as exc:
        # Keep storage keys and credentials out of CLI diagnostics.
        print(f"Read-only publication audit failed: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
