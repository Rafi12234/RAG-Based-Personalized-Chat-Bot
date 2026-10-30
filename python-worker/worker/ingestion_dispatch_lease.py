"""Transactional dispatch reservations for queued DocMind-AI ingestion jobs.

No Celery tasks are published by this module. Callers own the transaction:
commit after reserving, or roll back on failure. MySQL 8+ required.
"""

from datetime import datetime


MAX_ATTEMPTS = 3


def reserve_dispatch_batch(
    connection,
    *,
    batch_size: int = 25,
    lease_seconds: int = 120,
    max_attempts: int = MAX_ATTEMPTS,
    job_id: str | None = None,
) -> list[dict]:
    """Reserve eligible QUEUED jobs, skipping rows claimed by other dispatchers.

    Returns dictionaries with job_id and exact lease_until timestamp.
    Must be executed inside a caller-managed transaction. Does not publish.
    """
    if not isinstance(batch_size, int) or isinstance(batch_size, bool) or not 1 <= batch_size <= 100:
        raise ValueError("batch_size must be between 1 and 100")
    if not isinstance(lease_seconds, int) or isinstance(lease_seconds, bool) or lease_seconds < 1:
        raise ValueError("lease_seconds must be a positive integer")
    if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if job_id is not None and (not isinstance(job_id, str) or not job_id.strip()):
        raise ValueError("job_id must be a nonempty string when provided")

    job_filter = " AND j.id = %s" if job_id is not None else ""
    select_sql = (
        """
        SELECT j.id
        FROM IngestionJob AS j
        JOIN DocumentVersion AS v ON v.id = j.documentVersionId
        JOIN Document AS d ON d.id = v.documentId
        WHERE j.status = 'QUEUED'
          AND v.status = 'QUEUED'
          AND d.deletedAt IS NULL
          AND j.attempts < %s
          AND (j.nextAttemptAt IS NULL
               OR j.nextAttemptAt <= UTC_TIMESTAMP(3))
          AND (j.dispatchLeaseUntil IS NULL
               OR j.dispatchLeaseUntil <= UTC_TIMESTAMP(3))
        """
        + job_filter
        + " ORDER BY j.createdAt, j.id LIMIT %s FOR UPDATE SKIP LOCKED"
    )
    select_params = [max_attempts]
    if job_id is not None:
        select_params.append(job_id)
    select_params.append(batch_size)

    reserved: list[dict] = []
    with connection.cursor() as cursor:
        cursor.execute(select_sql, tuple(select_params))
        rows = cursor.fetchall()

        for row in rows:
            cursor.execute(
                """
                UPDATE IngestionJob
                SET dispatchLeaseUntil = DATE_ADD(
                        UTC_TIMESTAMP(3), INTERVAL %s SECOND
                    ),
                    dispatchAttempts = dispatchAttempts + 1
                WHERE id = %s
                  AND status = 'QUEUED'
                  AND (dispatchLeaseUntil IS NULL
                       OR dispatchLeaseUntil <= UTC_TIMESTAMP(3))
                """,
                (lease_seconds, row["id"]),
            )
            if cursor.rowcount != 1:
                raise RuntimeError(f"Could not reserve ingestion job {row['id']}")

            cursor.execute(
                """
                SELECT dispatchLeaseUntil
                FROM IngestionJob
                WHERE id = %s
                """,
                (row["id"],),
            )
            lease = cursor.fetchone()["dispatchLeaseUntil"]
            reserved.append({"job_id": row["id"], "lease_until": lease})

    return reserved


def release_dispatch_lease(connection, job_id: str, lease_until: datetime) -> bool:
    """Release precisely the matching reservation after publish failure.

    A later, renewed reservation with a different timestamp is not cleared.
    Caller owns commit/rollback; dispatchAttempts remains an audit counter.
    """
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id must be a nonempty string")
    if not isinstance(lease_until, datetime):
        raise ValueError("lease_until must be a datetime from the database")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE IngestionJob
            SET dispatchLeaseUntil = NULL
            WHERE id = %s
              AND status = 'QUEUED'
              AND dispatchLeaseUntil = %s
            """,
            (job_id, lease_until),
        )
        return cursor.rowcount == 1
