"""Rollback-only integration test for ingestion processing lifecycle."""

from uuid import uuid4

from .db import get_connection
from .ingestion_claim import claim_queued_job
from .ingestion_lifecycle import renew_processing_lease, record_processing_failure


SELECT_STATE = """
    SELECT
        j.status AS job_status,
        j.attempts,
        j.startedAt,
        j.completedAt,
        j.errorMessage,
        j.dispatchLeaseUntil,
        j.processingLeaseUntil,
        j.processingToken,
        j.nextAttemptAt,
        v.status AS version_status
    FROM IngestionJob AS j
    JOIN DocumentVersion AS v ON v.id = j.documentVersionId
    WHERE j.id = %s
"""


def state(connection, job_id):
    with connection.cursor() as cursor:
        cursor.execute(SELECT_STATE, (job_id,))
        return cursor.fetchone()


def main():
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
                  AND d.deletedAt IS NULL AND j.attempts < 3
                  AND (j.nextAttemptAt IS NULL
                       OR j.nextAttemptAt <= UTC_TIMESTAMP(3))
                ORDER BY j.createdAt, j.id LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("No eligible queued job found for rollback-only test")
            job_id = row["id"]

        original = state(connection, job_id)
        token = uuid4().hex
        wrong_token = uuid4().hex
        assert claim_queued_job(connection, job_id, token)
        assert not renew_processing_lease(connection, job_id, wrong_token)
        assert renew_processing_lease(connection, job_id, token)
        print("PASS: Current owner renews lease; stale token rejected")

        assert not record_processing_failure(connection, job_id, wrong_token, "stale")
        assert record_processing_failure(
            connection, job_id, token, "Simulated failure - rollback-only", retry_delay_seconds=60
        )
        waiting = state(connection, job_id)
        assert waiting["job_status"] == "QUEUED"
        assert waiting["version_status"] == "QUEUED"
        assert waiting["attempts"] == original["attempts"] + 1
        assert waiting["processingToken"] is None
        assert waiting["processingLeaseUntil"] is None
        assert waiting["nextAttemptAt"] is not None
        assert not claim_queued_job(connection, job_id, uuid4().hex)
        print("PASS: Owned failure schedules retry; immediate re-claim rejected")

        # Test exhausted retry path in this same uncommitted transaction.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE IngestionJob
                SET attempts = 2, nextAttemptAt = NULL
                WHERE id = %s AND status = 'QUEUED'
                """,
                (job_id,),
            )
            assert cursor.rowcount == 1
        final_token = uuid4().hex
        assert claim_queued_job(connection, job_id, final_token)
        assert record_processing_failure(connection, job_id, final_token, "Max attempts test")
        exhausted = state(connection, job_id)
        assert exhausted["job_status"] == "FAILED"
        assert exhausted["version_status"] == "FAILED"
        assert exhausted["attempts"] == 3
        assert exhausted["completedAt"] is not None
        assert exhausted["nextAttemptAt"] is None
        print("PASS: Third failed attempt transitions job and version to FAILED")
    finally:
        connection.rollback()
        connection.close()

    with get_connection() as verification:
        assert state(verification, job_id) == original
    print("PASS: All changes rolled back; real document remains unchanged")


if __name__ == "__main__":
    main()
