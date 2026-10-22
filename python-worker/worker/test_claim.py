
"""Rollback-only atomic ingestion claim test."""

from uuid import uuid4

from .db import get_connection
from .ingestion_claim import claim_queued_job


def get_state(connection, job_id):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                j.status AS job_status,
                j.attempts,
                j.processingToken,
                j.processingLeaseUntil,
                v.status AS version_status
            FROM IngestionJob AS j
            JOIN DocumentVersion AS v
                ON v.id = j.documentVersionId
            WHERE j.id = %s
            """,
            (job_id,),
        )

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
                SELECT j.id
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v
                    ON v.id = j.documentVersionId
                JOIN Document AS d
                    ON d.id = v.documentId
                WHERE j.status = 'QUEUED'
                  AND v.status = 'QUEUED'
                  AND d.deletedAt IS NULL
                  AND j.attempts < 3
                  AND (
                      j.nextAttemptAt IS NULL
                      OR j.nextAttemptAt <= UTC_TIMESTAMP(3)
                  )
                ORDER BY j.createdAt, j.id
                LIMIT 1
                """
            )

            result = cursor.fetchone()

        if not result:
            raise RuntimeError(
                "No eligible queued job is available "
                "for the rollback-only test"
            )

        job_id = result["id"]
        original = get_state(connection, job_id)

        first_token = uuid4().hex
        second_token = uuid4().hex

        first_claim = claim_queued_job(
            connection,
            job_id,
            first_token,
        )

        assert first_claim is True

        running = get_state(connection, job_id)

        assert running["job_status"] == "RUNNING"
        assert running["version_status"] == "PROCESSING"
        assert running["attempts"] == original["attempts"] + 1
        assert running["processingToken"] == first_token
        assert running["processingLeaseUntil"] is not None

        print(
            f"PASS: First claim succeeded for job {job_id}"
        )

        second_claim = claim_queued_job(
            connection,
            job_id,
            second_token,
        )

        assert second_claim is False

        print(
            "PASS: Second claim rejected "
            "while job is RUNNING"
        )

    finally:
        # The test must never permanently change real jobs.
        connection.rollback()
        connection.close()

    with get_connection() as verify_connection:
        restored = get_state(verify_connection, job_id)

        assert restored == original, (
            "Rollback did not restore the original job state"
        )

    print(
        "PASS: Rollback preserved the original database state"
    )


if __name__ == "__main__":
    main()
