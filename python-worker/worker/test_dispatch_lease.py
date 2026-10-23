"""Rollback-only MySQL integration test for dispatch leases.

Does not send Celery messages or modify real jobs permanently.
"""

from .db import get_connection
from .ingestion_dispatch_lease import (
    release_dispatch_lease,
    reserve_dispatch_batch,
)


STATE_SQL = """
    SELECT j.status AS job_status, j.attempts,
           j.dispatchAttempts, j.dispatchLeaseUntil,
           j.processingToken, j.processingLeaseUntil,
           j.nextAttemptAt, v.status AS version_status
    FROM IngestionJob AS j
    JOIN DocumentVersion AS v ON v.id = j.documentVersionId
    WHERE j.id = %s
"""


def read_state(connection, job_id: str) -> dict:
    with connection.cursor() as cursor:
        cursor.execute(STATE_SQL, (job_id,))
        state = cursor.fetchone()
    if state is None:
        raise RuntimeError("Ingestion job disappeared during test")
    return state


def main() -> None:
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
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                WHERE j.status = 'QUEUED'
                  AND v.status = 'QUEUED'
                  AND d.deletedAt IS NULL
                  AND j.attempts < 3
                  AND (j.nextAttemptAt IS NULL
                       OR j.nextAttemptAt <= UTC_TIMESTAMP(3))
                  AND (j.dispatchLeaseUntil IS NULL
                       OR j.dispatchLeaseUntil <= UTC_TIMESTAMP(3))
                ORDER BY j.createdAt, j.id LIMIT 1
                """
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("No eligible QUEUED job found for rollback-only test")
            job_id = row["id"]

        original = read_state(connection, job_id)
        first = reserve_dispatch_batch(connection, batch_size=1, job_id=job_id)
        assert len(first) == 1 and first[0]["job_id"] == job_id, first
        after_first = read_state(connection, job_id)
        assert after_first["job_status"] == "QUEUED"
        assert after_first["version_status"] == "QUEUED"
        assert after_first["dispatchAttempts"] == original["dispatchAttempts"] + 1
        assert after_first["dispatchLeaseUntil"] == first[0]["lease_until"]
        print("PASS: Eligible job reserved without changing processing status")

        duplicate = reserve_dispatch_batch(connection, batch_size=1, job_id=job_id)
        assert duplicate == [], duplicate
        print("PASS: Active dispatch lease rejects repeat reservation")

        # Simulate the passage of time inside this uncommitted transaction.
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE IngestionJob
                SET dispatchLeaseUntil = DATE_SUB(UTC_TIMESTAMP(3), INTERVAL 2 SECOND)
                WHERE id = %s AND status = 'QUEUED'
                """,
                (job_id,),
            )
            assert cursor.rowcount == 1

        second = reserve_dispatch_batch(connection, batch_size=1, job_id=job_id)
        assert len(second) == 1 and second[0]["job_id"] == job_id, second
        after_second = read_state(connection, job_id)
        assert after_second["dispatchAttempts"] == original["dispatchAttempts"] + 2
        assert after_second["dispatchLeaseUntil"] == second[0]["lease_until"]
        assert second[0]["lease_until"] != first[0]["lease_until"]
        assert not release_dispatch_lease(connection, job_id, first[0]["lease_until"])
        assert release_dispatch_lease(connection, job_id, second[0]["lease_until"])
        assert read_state(connection, job_id)["dispatchLeaseUntil"] is None
        print("PASS: Expired reservation reacquired; stale release rejected")
        print("PASS: Current reservation released safely")

    finally:
        connection.rollback()
        connection.close()

    if job_id is not None and original is not None:
        with get_connection() as verification:
            assert read_state(verification, job_id) == original
        print("PASS: All changes rolled back; original document unchanged")


if __name__ == "__main__":
    main()
