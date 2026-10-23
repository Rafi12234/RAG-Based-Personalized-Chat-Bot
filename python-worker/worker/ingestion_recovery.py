"""Recover expired DocMind-AI ingestion leases.

The caller owns the transaction. This module never commits or rolls back.
Do not schedule recovery until the actual ingestion worker is implemented.
"""

MAX_ATTEMPTS = 3


def recover_expired_jobs(
    connection,
    *,
    max_attempts: int = MAX_ATTEMPTS,
    retry_delay_seconds: int = 60,
    batch_size: int = 25,
    job_id: str | None = None,
) -> list[dict[str, str]]:
    """Recover expired RUNNING jobs with row locks and token fencing.

    Non-archived jobs with attempts remaining return to QUEUED with a
    delay. Exhausted or archived jobs become FAILED. This method never
    marks a document READY or republishes a Celery task.
    """
    if not isinstance(max_attempts, int) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if not isinstance(retry_delay_seconds, int) or retry_delay_seconds < 1:
        raise ValueError("retry_delay_seconds must be a positive integer")
    if not isinstance(batch_size, int) or batch_size < 1 or batch_size > 100:
        raise ValueError("batch_size must be between 1 and 100")
    if job_id is not None and (not isinstance(job_id, str) or not job_id.strip()):
        raise ValueError("job_id must be a nonempty string when supplied")

    where_job = " AND j.id = %s" if job_id is not None else ""
    query = (
        """
        SELECT j.id, j.attempts, j.processingToken, d.deletedAt
        FROM IngestionJob AS j
        JOIN DocumentVersion AS v ON v.id = j.documentVersionId
        JOIN Document AS d ON d.id = v.documentId
        WHERE j.status = 'RUNNING'
          AND v.status = 'PROCESSING'
          AND j.processingToken IS NOT NULL
          AND j.processingLeaseUntil IS NOT NULL
          AND j.processingLeaseUntil <= UTC_TIMESTAMP(3)
        """
        + where_job
        + " ORDER BY j.processingLeaseUntil, j.id LIMIT %s FOR UPDATE"
    )
    args = ([job_id] if job_id is not None else []) + [batch_size]

    recovered: list[dict[str, str]] = []
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(args))
        rows = cursor.fetchall()

        for row in rows:
            archived = row["deletedAt"] is not None
            exhausted = row["attempts"] >= max_attempts
            terminal = archived or exhausted
            new_status = "FAILED" if terminal else "QUEUED"
            if archived:
                message = "Recovery stopped: document archived while processing"
            elif exhausted:
                message = "Processing lease expired; retry limit reached"
            else:
                message = "Processing lease expired; retry scheduled"

            # Token and expired-lease conditions fence out stale ownership.
            cursor.execute(
                """
                UPDATE IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                SET j.status = %s,
                    v.status = %s,
                    j.errorMessage = %s,
                    j.completedAt = CASE WHEN %s = 'FAILED'
                        THEN UTC_TIMESTAMP(3) ELSE NULL END,
                    j.nextAttemptAt = CASE WHEN %s = 'QUEUED'
                        THEN DATE_ADD(UTC_TIMESTAMP(3), INTERVAL %s SECOND)
                        ELSE NULL END,
                    j.startedAt = CASE WHEN %s = 'QUEUED'
                        THEN NULL ELSE j.startedAt END,
                    j.processingToken = NULL,
                    j.processingLeaseUntil = NULL,
                    j.dispatchLeaseUntil = NULL
                WHERE j.id = %s
                  AND j.status = 'RUNNING'
                  AND v.status = 'PROCESSING'
                  AND j.processingToken = %s
                  AND j.processingLeaseUntil <= UTC_TIMESTAMP(3)
                """,
                (
                    new_status,
                    new_status,
                    message,
                    new_status,
                    new_status,
                    retry_delay_seconds,
                    new_status,
                    row["id"],
                    row["processingToken"],
                ),
            )
            # MySQL multi-table UPDATE can count changed rows in both tables.
            if cursor.rowcount < 1:
                raise RuntimeError(
                    f"Could not recover expired ingestion job {row['id']}"
                )
            recovered.append({"job_id": row["id"], "status": new_status})

    return recovered
