"""Rollback-only MySQL test for lease-expiration recovery."""

from uuid import uuid4

from .db import get_connection
from .ingestion_claim import claim_queued_job
from .ingestion_lifecycle import renew_processing_lease, record_processing_failure
from .ingestion_recovery import recover_expired_jobs


STATE_SQL = """
SELECT j.status AS job_status, j.attempts, j.errorMessage,
       j.startedAt, j.completedAt, j.dispatchLeaseUntil,
       j.dispatchAttempts, j.processingLeaseUntil,
       j.processingToken, j.nextAttemptAt,
       v.status AS document_status, d.deletedAt
FROM IngestionJob AS j
JOIN DocumentVersion AS v ON v.id = j.documentVersionId
JOIN Document AS d ON d.id = v.documentId
WHERE j.id = %s
"""


def get_state(connection, job_id: str):
    with connection.cursor() as cursor:
        cursor.execute(STATE_SQL, (job_id,))
        return cursor.fetchone()


def expire_lease(connection, job_id: str, token: str) -> None:
    """Expire a lease inside a transaction that will be rolled back."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE IngestionJob
            SET processingLeaseUntil = DATE_SUB(UTC_TIMESTAMP(3), INTERVAL 2 SECOND)
            WHERE id = %s AND processingToken = %s AND status = 'RUNNING'
            """,
            (job_id, token),
        )
        assert cursor.rowcount == 1


def main() -> None:
    connection = get_connection()
    job_id = None
    original = None
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.id FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                WHERE j.status = 'QUEUED' AND v.status = 'QUEUED'
                  AND d.deletedAt IS NULL AND j.attempts = 0
                  AND (j.nextAttemptAt IS NULL
                       OR j.nextAttemptAt <= UTC_TIMESTAMP(3))
                ORDER BY j.createdAt, j.id LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("No eligible untouched QUEUED job available")
            job_id = row["id"]

        original = get_state(connection, job_id)
        token = uuid4().hex
        assert claim_queued_job(connection, job_id, token)
        assert recover_expired_jobs(connection, job_id=job_id) == []
        print("PASS: Valid processing lease is not recovered")

        expire_lease(connection, job_id, token)
        result = recover_expired_jobs(connection, job_id=job_id)
        assert result == [{"job_id": job_id, "status": "QUEUED"}], result
        retried = get_state(connection, job_id)
        assert retried["job_status"] == "QUEUED"
        assert retried["document_status"] == "QUEUED"
        assert retried["attempts"] == 1
        assert retried["processingToken"] is None
        assert retried["processingLeaseUntil"] is None
        assert retried["nextAttemptAt"] is not None
        assert not renew_processing_lease(connection, job_id, token)
        assert not record_processing_failure(connection, job_id, token, "late failure")
        assert not claim_queued_job(connection, job_id, uuid4().hex)
        print("PASS: Expired lease schedules retry; stale owner is rejected")

        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE IngestionJob SET attempts = 2, nextAttemptAt = NULL WHERE id = %s",
                (job_id,),
            )
        last_token = uuid4().hex
        assert claim_queued_job(connection, job_id, last_token)
        expire_lease(connection, job_id, last_token)
        result = recover_expired_jobs(connection, job_id=job_id)
        assert result == [{"job_id": job_id, "status": "FAILED"}], result
        failed = get_state(connection, job_id)
        assert failed["job_status"] == "FAILED"
        assert failed["document_status"] == "FAILED"
        assert failed["attempts"] == 3
        assert failed["completedAt"] is not None
        assert failed["nextAttemptAt"] is None
        assert failed["processingToken"] is None
        print("PASS: Expired final attempt is marked FAILED")

        # Test archived document recovery, also inside the rollback.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                SET j.status = 'QUEUED', j.attempts = 0,
                    j.completedAt = NULL, j.startedAt = NULL,
                    j.errorMessage = NULL, j.nextAttemptAt = NULL,
                    v.status = 'QUEUED'
                WHERE j.id = %s
                """,
                (job_id,),
            )
        archive_token = uuid4().hex
        assert claim_queued_job(connection, job_id, archive_token)
        expire_lease(connection, job_id, archive_token)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE Document AS d
                JOIN DocumentVersion AS v ON v.documentId = d.id
                JOIN IngestionJob AS j ON j.documentVersionId = v.id
                SET d.deletedAt = UTC_TIMESTAMP(3)
                WHERE j.id = %s
                """,
                (job_id,),
            )
            assert cursor.rowcount == 1
        result = recover_expired_jobs(connection, job_id=job_id)
        assert result == [{"job_id": job_id, "status": "FAILED"}], result
        archived = get_state(connection, job_id)
        assert archived["job_status"] == "FAILED"
        assert archived["nextAttemptAt"] is None
        print("PASS: Archived document cannot be retried")

    finally:
        connection.rollback()
        connection.close()

    if job_id is not None and original is not None:
        with get_connection() as verification:
            assert get_state(verification, job_id) == original
        print("PASS: All changes rolled back; original document unchanged")


if __name__ == "__main__":
    main()
