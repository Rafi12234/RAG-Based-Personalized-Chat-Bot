"""Lease renewal and token-fenced failure transitions for ingestion jobs.

All functions participate in the caller's transaction; they never commit.
"""

MAX_ATTEMPTS = 3


def _validate_claim(job_id: str, token: str) -> None:
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id must be a nonempty string")
    if not isinstance(token, str) or not (1 <= len(token) <= 64):
        raise ValueError("token must contain 1 to 64 characters")


def renew_processing_lease(
    connection,
    job_id: str,
    token: str,
    *,
    lease_seconds: int = 900,
) -> bool:
    """Extend an unexpired lease only for its current owner.

    Returns False if the job was archived, recovered, completed,
    or its lease expired. Caller must stop processing on False.
    """
    _validate_claim(job_id, token)
    if not isinstance(lease_seconds, int) or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE IngestionJob AS j
            JOIN DocumentVersion AS v ON v.id = j.documentVersionId
            JOIN Document AS d ON d.id = v.documentId
            SET j.processingLeaseUntil =
                DATE_ADD(UTC_TIMESTAMP(3), INTERVAL %s SECOND)
            WHERE j.id = %s
              AND j.processingToken = %s
              AND j.status = 'RUNNING'
              AND v.status = 'PROCESSING'
              AND d.deletedAt IS NULL
              AND j.processingLeaseUntil > UTC_TIMESTAMP(3)
            """,
            (lease_seconds, job_id, token),
        )
        return cursor.rowcount == 1


def record_processing_failure(
    connection,
    job_id: str,
    token: str,
    error_message: str,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    retry_delay_seconds: int = 60,
) -> bool:
    """Atomically release a claimed job for retry, or mark it failed.

    Only a worker holding the current unexpired token can write.
    This does not publish another Celery task; the future dispatcher
    must honor nextAttemptAt. Caller owns commit/rollback.
    """
    _validate_claim(job_id, token)
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if not isinstance(retry_delay_seconds, int) or retry_delay_seconds < 1:
        raise ValueError("retry_delay_seconds must be a positive integer")
    if not isinstance(error_message, str) or not error_message.strip():
        raise ValueError("error_message must be a nonempty string")
    message = error_message[:2000]

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE IngestionJob AS j
            JOIN DocumentVersion AS v ON v.id = j.documentVersionId
            JOIN Document AS d ON d.id = v.documentId
            SET
                j.status = IF(j.attempts >= %s, 'FAILED', 'QUEUED'),
                v.status = IF(j.attempts >= %s, 'FAILED', 'QUEUED'),
                j.errorMessage = %s,
                j.completedAt = IF(j.attempts >= %s, UTC_TIMESTAMP(3), NULL),
                j.nextAttemptAt = IF(
                    j.attempts >= %s,
                    NULL,
                    DATE_ADD(UTC_TIMESTAMP(3), INTERVAL %s SECOND)
                ),
                j.processingLeaseUntil = NULL,
                j.processingToken = NULL,
                j.dispatchLeaseUntil = NULL
            WHERE j.id = %s
              AND j.processingToken = %s
              AND j.status = 'RUNNING'
              AND v.status = 'PROCESSING'
              AND d.deletedAt IS NULL
              AND j.processingLeaseUntil > UTC_TIMESTAMP(3)
            """,
            (
                max_attempts,
                max_attempts,
                message,
                max_attempts,
                max_attempts,
                retry_delay_seconds,
                job_id,
                token,
            ),
        )
        # A multi-table UPDATE can count changes in both tables.
        return cursor.rowcount > 0
