
"""Celery tasks for DocMind-AI."""

from .celery_app import celery_app
from .db import get_connection


@celery_app.task(name="docmind.health.ping")
def ping():
    """Verify that the Celery worker is online."""
    return {
        "success": True,
        "worker": "online",
    }


@celery_app.task(name="docmind.ingestion.inspect")
def inspect_ingestion(job_id: str):
    """
    Verify that Celery can retrieve an ingestion job from MySQL.

    This task is read-only. It does not extract documents,
    change processing statuses, or write vectors.
    """
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
