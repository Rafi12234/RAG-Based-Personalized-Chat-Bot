"""Phase 2.9C: guarded COMPLETED/READY transition for DocMind-AI.

Building block only. No automatic invocation, dispatch or vector publication.
The caller must verify EXACT published ready vectors before calling.
This mutation cannot provide an atomic transaction across MySQL and Qdrant;
a future reconciler must handle interrupted cross-store publication.
"""
from __future__ import annotations

from typing import Callable


def complete_after_publication(connection, job_id: str, processing_token: str,
                               *, published_vectors_verified: Callable[[], int],
                               expected_count: int) -> int:
    """Finish an owned job in one MySQL transaction AFTER verifying ready vectors.

    `published_vectors_verified` must independently check the exact IDs, scope,
    token and ready status in Qdrant; it returns verified count or raises.
    Never call this with a cached boolean. Connection is caller-supplied.
    The function never creates a Qdrant point or issues a Celery message.
    """
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("job_id required")
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("processing_token required")
    if type(expected_count) is not int or expected_count < 1:
        raise ValueError("expected_count must be positive")
    if not callable(published_vectors_verified):
        raise TypeError("published_vectors_verified must be callable")

    # Verify outside the DB transaction to avoid holding a database lock during
    # potentially slow Qdrant operations. The following SQL fences out stale claims.
    verified = published_vectors_verified()
    if type(verified) is not int or verified != expected_count:
        raise RuntimeError("Published Qdrant points do not match expected count")

    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                SET j.status = 'COMPLETED',
                    v.status = 'READY',
                    j.completedAt = UTC_TIMESTAMP(3),
                    j.errorMessage = NULL,
                    j.processingToken = NULL,
                    j.processingLeaseUntil = NULL,
                    j.dispatchLeaseUntil = NULL,
                    j.nextAttemptAt = NULL
                WHERE j.id = %s
                  AND j.processingToken = %s
                  AND j.status = 'RUNNING'
                  AND v.status = 'PROCESSING'
                  AND d.deletedAt IS NULL
                  AND j.processingLeaseUntil > UTC_TIMESTAMP(3)
                """,
                (job_id, processing_token),
            )
            # MySQL reports changed rows in both joined tables.
            if cursor.rowcount < 1:
                raise RuntimeError("Stale, expired, archived or ineligible claim")
        connection.commit()
        return verified
    except BaseException:
        connection.rollback()
        raise
