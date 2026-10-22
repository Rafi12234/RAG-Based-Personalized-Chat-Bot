
"""Celery tasks for DocMind-AI."""

from uuid import uuid4

from .celery_app import celery_app
from .db import get_connection
from .ingestion_claim import claim_queued_job


@celery_app.task(name="docmind.health.ping")
def ping():
    """Verify that the Celery worker is online."""
    return {
        "success": True,
        "worker": "online",
    }


@celery_app.task(name="docmind.ingestion.inspect")
def inspect_ingestion(job_id: str):
    """Read an ingestion job without modifying its state."""
    if not isinstance(job_id, str) or not job_id.strip():
        return {
            "success": False,
            "message": "A valid ingestion job ID is required",
        }

    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    j.id AS job_id,
                    j.status AS job_status,
                    j.attempts,
                    v.filename,
                    v.status AS document_status,
                    d.deletedAt
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v
                    ON v.id = j.documentVersionId
                JOIN Document AS d
                    ON d.id = v.documentId
                WHERE j.id = %s
                LIMIT 1
                """,
                (job_id,),
            )

            job = cursor.fetchone()

        if job is None:
            return {
                "success": False,
                "message": "Ingestion job not found",
            }

        if job["deletedAt"] is not None:
            return {
                "success": False,
                "message": "Document has been archived",
            }

        return {
            "success": True,
            "job_id": job["job_id"],
            "filename": job["filename"],
            "job_status": job["job_status"],
            "document_status": job["document_status"],
            "attempts": job["attempts"],
            "message": "Celery successfully retrieved the ingestion job",
        }

    finally:
        connection.close()


@celery_app.task(name="docmind.ingestion.claim_probe")
def claim_probe(job_id: str):
    """
    Test atomic job claiming through Celery.

    Both claim attempts occur in one transaction.
    The transaction is always rolled back.

    No real document processing happens in this task.
    """
    if not isinstance(job_id, str) or not job_id.strip():
        return {
            "success": False,
            "message": "A valid ingestion job ID is required",
        }

    connection = get_connection()

    try:
        connection.begin()

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    j.status AS job_status,
                    j.attempts,
                    v.status AS document_status
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v
                    ON v.id = j.documentVersionId
                WHERE j.id = %s
                """,
                (job_id,),
            )
            before = cursor.fetchone()

        if before is None:
            return {
                "success": False,
                "message": "Ingestion job not found",
            }

        first_token = uuid4().hex
        second_token = uuid4().hex

        first_claim = claim_queued_job(
            connection,
            job_id,
            first_token,
        )

        if not first_claim:
            return {
                "success": False,
                "job_id": job_id,
                "message": "Job is not eligible for claiming",
            }

        second_claim = claim_queued_job(
            connection,
            job_id,
            second_token,
        )

        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    j.status AS job_status,
                    j.attempts,
                    j.processingToken,
                    v.status AS document_status
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v
                    ON v.id = j.documentVersionId
                WHERE j.id = %s
                """,
                (job_id,),
            )
            claimed_state = cursor.fetchone()

        if (
            second_claim
            or claimed_state is None
            or claimed_state["job_status"] != "RUNNING"
            or claimed_state["document_status"] != "PROCESSING"
            or claimed_state["processingToken"] != first_token
            or claimed_state["attempts"] != before["attempts"] + 1
        ):
            raise RuntimeError(
                "Atomic ingestion claim verification failed"
            )

        return {
            "success": True,
            "job_id": job_id,
            "first_claim": True,
            "second_claim": False,
            "transaction_mode": "rollback_only",
            "message": "Celery atomic claim probe passed",
        }

    finally:
        connection.rollback()
        connection.close()
