"""Read-only, fresh MySQL lease-fencing checks for DocMind-AI ingestion.

Use a new DB connection on every check, including inside a staging guard.
This module deliberately NEVER claims, renews, completes, or retries a job.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .ingestion_lifecycle import MAX_ATTEMPTS


@dataclass(frozen=True)
class JobState:
    job_status: str
    document_status: str
    attempts: int
    document_active: bool
    token_present: bool
    lease_live: bool
    ready_for_claim: bool


def _validate_job_id(job_id: str) -> None:
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("An ingestion job ID is required")


def _connection(connection_factory):
    if connection_factory is None:
        from .db import get_connection
        return get_connection()
    return connection_factory()


def read_job_state(job_id: str, *, connection_factory=None) -> JobState | None:
    """Read safe, nonsensitive job flags; do not return the processing token."""
    _validate_job_id(job_id)
    connection = _connection(connection_factory)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.status AS job_status,
                       v.status AS document_status,
                       j.attempts,
                       (d.deletedAt IS NULL) AS document_active,
                       (j.processingToken IS NOT NULL) AS token_present,
                       (j.processingLeaseUntil > UTC_TIMESTAMP(3)) AS lease_live,
                       (j.nextAttemptAt IS NULL OR
                        j.nextAttemptAt <= UTC_TIMESTAMP(3)) AS retry_due
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                WHERE j.id = %s LIMIT 1
                """,
                (job_id,),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        active = bool(row["document_active"])
        return JobState(
            job_status=row["job_status"],
            document_status=row["document_status"],
            attempts=int(row["attempts"]),
            document_active=active,
            token_present=bool(row["token_present"]),
            lease_live=bool(row["lease_live"]),
            ready_for_claim=(
                active and row["job_status"] == "QUEUED"
                and row["document_status"] == "QUEUED"
                and bool(row["retry_due"])
                and int(row["attempts"]) < MAX_ATTEMPTS
            ),
        )
    finally:
        connection.rollback()  # terminate the read transaction
        connection.close()


def check_current_processing_lease(
    job_id: str, processing_token: str, *, connection_factory=None
) -> bool:
    """Return True only for a current, unexpired, active RUNNING claim.

    The token is compared inside a parameterized SQL query and NEVER returned.
    A new connection avoids reusing a stale REPEATABLE READ snapshot.
    """
    _validate_job_id(job_id)
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("A valid processing token is required")
    connection = _connection(connection_factory)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 AS valid
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                WHERE j.id = %s
                  AND j.processingToken = %s
                  AND j.status = 'RUNNING'
                  AND v.status = 'PROCESSING'
                  AND d.deletedAt IS NULL
                  AND j.processingLeaseUntil > UTC_TIMESTAMP(3)
                LIMIT 1
                """,
                (job_id, processing_token),
            )
            return cursor.fetchone() is not None
    finally:
        connection.rollback()
        connection.close()


def make_fresh_lease_guard(
    job_id: str, processing_token: str, *, connection_factory=None
) -> Callable[[], bool]:
    """Create a guard for stage_batches() and later read-only verification.

    The callback calls MySQL afresh for EVERY use. It never caches True.
    """
    _validate_job_id(job_id)
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("A valid processing token is required")

    def guard() -> bool:
        return check_current_processing_lease(
            job_id, processing_token, connection_factory=connection_factory
        )
    return guard
