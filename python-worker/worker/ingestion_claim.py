
"""Atomic MySQL claiming for DocMind-AI ingestion jobs."""

from uuid import uuid4

from .db import get_connection


def claim_queued_job(
    connection,
    job_id: str,
    token: str,
    *,
    max_attempts: int = 3,
    lease_seconds: int = 900,
) -> bool:
    """
    Atomically claim an eligible ingestion job.

    The caller owns the transaction.
    This function never commits or rolls back.
    """
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id must be a nonempty string")

    if not isinstance(token, str) or not (1 <= len(token) <= 64):
        raise ValueError("token must contain 1 to 64 characters")

    if max_attempts < 1 or lease_seconds < 1:
        raise ValueError(
            "max_attempts and lease_seconds must be positive"
        )

    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE IngestionJob AS j
            INNER JOIN DocumentVersion AS v
                ON v.id = j.documentVersionId
            INNER JOIN Document AS d
                ON d.id = v.documentId
            SET j.status = 'RUNNING',
                j.attempts = j.attempts + 1,
                j.startedAt = UTC_TIMESTAMP(3),
                j.completedAt = NULL,
                j.errorMessage = NULL,
                j.processingToken = %s,
                j.processingLeaseUntil =
                    DATE_ADD(
                        UTC_TIMESTAMP(3),
                        INTERVAL %s SECOND
                    ),
                j.dispatchLeaseUntil = NULL,
                j.nextAttemptAt = NULL
            WHERE j.id = %s
              AND j.status = 'QUEUED'
              AND v.status = 'QUEUED'
              AND d.deletedAt IS NULL
              AND j.attempts < %s
              AND (
                  j.nextAttemptAt IS NULL
                  OR j.nextAttemptAt <= UTC_TIMESTAMP(3)
              )
            """,
            (
                token,
                lease_seconds,
                job_id,
                max_attempts,
            ),
        )

        if cursor.rowcount == 0:
            return False

        if cursor.rowcount != 1:
            raise RuntimeError(
                "Unexpected number of updated ingestion jobs"
            )

        cursor.execute(
            """
            UPDATE DocumentVersion AS v
            INNER JOIN IngestionJob AS j
                ON j.documentVersionId = v.id
            SET v.status = 'PROCESSING'
            WHERE j.id = %s
              AND j.processingToken = %s
              AND j.status = 'RUNNING'
              AND v.status = 'QUEUED'
            """,
            (job_id, token),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "Could not transition document version "
                "to PROCESSING"
            )

    return True


def claim_ingestion_job(job_id: str) -> str | None:
    """
    Claim and commit one ingestion job.

    Returns the unique claim token if successful,
    otherwise None.
    """
    token = uuid4().hex
    connection = get_connection()

    try:
        claimed = claim_queued_job(
            connection,
            job_id,
            token,
        )

        if not claimed:
            connection.rollback()
            return None

        connection.commit()
        return token

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()
