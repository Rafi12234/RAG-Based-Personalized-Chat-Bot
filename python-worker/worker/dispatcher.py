"""One-shot diagnostic dispatcher with transactional MySQL dispatch leases.

Default CLI mode is a rollback-only preview: no Celery messages and no
persistent database changes. --publish is explicit and sends only the
read-only docmind.ingestion.inspect diagnostic task.

Do NOT enable continuous dispatch until real document ingestion exists.
"""

import argparse
import logging
import sys

from .celery_app import celery_app
from .db import get_connection
from .ingestion_dispatch_lease import (
    release_dispatch_lease,
    reserve_dispatch_batch,
)

LOGGER = logging.getLogger("docmind.dispatcher")
TASK_NAME = "docmind.ingestion.inspect"
QUEUE_NAME = "docmind_phase2"
BATCH_SIZE = 25
DISPATCH_LEASE_SECONDS = 120


def reserve_once(*, batch_size: int = BATCH_SIZE, job_id: str | None = None,
                 persist: bool = False) -> list[dict]:
    """Reserve a batch, committing only when explicitly requested.

    The reservation is committed BEFORE publishing to Redis. If the
    process exits after commit and before publication, the lease can
    expire and the job can be considered again on a later run.
    """
    connection = get_connection()
    try:
        connection.begin()
        reservations = reserve_dispatch_batch(
            connection,
            batch_size=batch_size,
            lease_seconds=DISPATCH_LEASE_SECONDS,
            job_id=job_id,
        )
        if persist:
            connection.commit()
        else:
            connection.rollback()
        return reservations
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def release_failed_publication(job_id: str, lease_until) -> bool:
    """Release only the precise reservation whose publication failed."""
    connection = get_connection()
    try:
        connection.begin()
        released = release_dispatch_lease(connection, job_id, lease_until)
        connection.commit()
        return released
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def dispatch_once(*, publish: bool = False, batch_size: int = BATCH_SIZE,
                  job_id: str | None = None) -> dict[str, int]:
    """Preview dispatch or explicitly publish a one-shot diagnostic batch.

    In preview mode, all MySQL changes are rolled back and NO Redis
    messages are sent. In publish mode, a temporary lease prevents
    immediate redispatch; publishing does not process the document.
    """
    reservations = reserve_once(
        batch_size=batch_size, job_id=job_id, persist=publish
    )
    results = {"reserved": len(reservations), "published": 0, "failed": 0}

    if not publish:
        LOGGER.info(
            "PREVIEW: %d eligible job(s), no database changes and no Redis messages",
            len(reservations),
        )
        for reservation in reservations:
            LOGGER.info("PREVIEW job=%s", reservation["job_id"])
        return results

    for reservation in reservations:
        reserved_job_id = reservation["job_id"]
        try:
            task = celery_app.send_task(
                TASK_NAME,
                args=[reserved_job_id],
                queue=QUEUE_NAME,
                retry=False,
            )
            results["published"] += 1
            LOGGER.info(
                "Published diagnostic job=%s task=%s queue=%s",
                reserved_job_id, task.id, QUEUE_NAME,
            )
        except Exception:
            results["failed"] += 1
            LOGGER.exception("Diagnostic publish failed for job=%s", reserved_job_id)
            # Publishing could be ambiguous after a connection problem;
            # downstream job claiming must always remain idempotent.
            released = release_failed_publication(
                reserved_job_id, reservation["lease_until"]
            )
            if not released:
                LOGGER.warning(
                    "Could not release job=%s: its lease was replaced or the job changed",
                    reserved_job_id,
                )
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--publish", action="store_true",
        help="Commit temporary leases and publish read-only Celery diagnostics",
    )
    parser.add_argument("--job-id", default=None, help="Limit to one ingestion job")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        result = dispatch_once(
            publish=args.publish, batch_size=args.batch_size, job_id=args.job_id
        )
        LOGGER.info("Dispatch result: %s", result)
        return 1 if result["failed"] else 0
    except (Exception, KeyboardInterrupt):
        LOGGER.exception("Dispatcher could not complete")
        return 1


if __name__ == "__main__":
    sys.exit(main())
