
"""Dispatch queued DocMind-AI ingestion jobs to Celery.

Milestone 2.1B: one-shot diagnostic dispatcher.
No database statuses are changed by this module.
"""

import logging
import sys

from .celery_app import celery_app
from .db import get_connection


LOGGER = logging.getLogger("docmind.dispatcher")

TASK_NAME = "docmind.ingestion.inspect"
QUEUE_NAME = "docmind_phase2"
BATCH_SIZE = 25


def find_queued_jobs() -> list[str]:
    """Find queued jobs belonging to active documents."""
    connection = get_connection()

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.id
                FROM IngestionJob AS j
                INNER JOIN DocumentVersion AS v
                    ON v.id = j.documentVersionId
                INNER JOIN Document AS d
                    ON d.id = v.documentId
                WHERE j.status = 'QUEUED'
                  AND v.status = 'QUEUED'
                  AND d.deletedAt IS NULL
                ORDER BY j.createdAt ASC, j.id ASC
                LIMIT %s
                """,
                (BATCH_SIZE,),
            )

            rows = cursor.fetchall()
            return [row["id"] for row in rows]

    finally:
        connection.close()


def dispatch_once() -> int:
    """Publish one batch of pending jobs for diagnostic inspection."""
    job_ids = find_queued_jobs()

    if not job_ids:
        LOGGER.info("No queued ingestion jobs found.")
        return 0

    LOGGER.info("Found %d queued ingestion job(s).", len(job_ids))

    published = 0

    for job_id in job_ids:
        try:
            task = celery_app.send_task(
                TASK_NAME,
                args=[job_id],
                queue=QUEUE_NAME,
                retry=False,
            )

            published += 1
            LOGGER.info(
                "Published job=%s task=%s queue=%s",
                job_id,
                task.id,
                QUEUE_NAME,
            )

        except Exception:
            LOGGER.exception(
                "Could not publish ingestion job %s",
                job_id,
            )

    LOGGER.info(
        "Dispatch finished: %d/%d tasks published.",
        published,
        len(job_ids),
    )

    if published != len(job_ids):
        raise RuntimeError(
            "One or more ingestion jobs could not be published."
        )

    return published


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        count = dispatch_once()
        LOGGER.info("Successfully published %d task(s).", count)
        return 0

    except Exception:
        LOGGER.exception("Dispatcher failed.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
