"""Phase 2.10: strictly scoped, opt-in automatic dispatcher for a TEST knowledge base.

Preview is the default and writes nothing. --publish-once / --watch require BOTH
an exact CLI knowledge-base confirmation and a per-CMD opt-in environment flag.
This uses the existing transactional dispatch reservation; it does NOT modify
existing dispatcher.py or permit unscoped dispatch. The pilot worker must be
running separately, with its independent DOCMIND_ENABLE_PILOT_INGESTION flag.

Do not enable on client/production data. Retry cleanup and reconciliation are
not implemented: preflight rejects any version with existing Qdrant points.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime
from typing import Callable

from . import settings  # Load .env when invoked via Python -m.

LOGGER = logging.getLogger("docmind.auto_dispatcher")

AUTODISPATCH_FLAG = "DOCMIND_ENABLE_AUTODISPATCH"
AUTODISPATCH_VALUE = "YES_FOR_TESTING"
TASK_NAME = "docmind.ingestion.live_pilot_v1"
QUEUE_NAME = "docmind_phase2_live"
DISPATCH_LEASE_SECONDS = 120
DEFAULT_BATCH_SIZE = 3
DEFAULT_SCAN_LIMIT = 50
DEFAULT_INTERVAL = 30
MAX_BATCH_SIZE = 10
MAX_SCAN_LIMIT = 200


def _required_string(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be a nonempty identifier (max 128 characters)")


def validate_options(knowledge_base_id: str, *, publish: bool = False,
                     confirm_knowledge_base_id: str | None = None,
                     batch_size: int = DEFAULT_BATCH_SIZE,
                     scan_limit: int = DEFAULT_SCAN_LIMIT,
                     env_flag: str | None = None) -> None:
    _required_string(knowledge_base_id, "knowledge_base_id")
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError("batch_size must be between 1 and 10")
    if type(scan_limit) is not int or not batch_size <= scan_limit <= MAX_SCAN_LIMIT:
        raise ValueError("scan_limit must be between batch_size and 200")
    if publish:
        if confirm_knowledge_base_id != knowledge_base_id:
            raise ValueError("Exact --confirm-knowledge-base-id is required")
        enabled = os.environ.get(AUTODISPATCH_FLAG) if env_flag is None else env_flag
        if enabled != AUTODISPATCH_VALUE:
            raise RuntimeError("Automatic test dispatcher is disabled")


def _connection_factory(factory=None):
    if factory is not None:
        return factory
    from .db import get_connection
    return get_connection


def list_candidate_jobs(knowledge_base_id: str, *, limit: int = DEFAULT_SCAN_LIMIT,
                        connection_factory=None) -> list[str]:
    """Read only: find eligible jobs from this exact knowledge base, not other KBs.

    The subsequent transactional reservation re-checks the job. This list is
    NOT an authorization decision and does not lock or dispatch any rows.
    """
    _required_string(knowledge_base_id, "knowledge_base_id")
    if type(limit) is not int or not 1 <= limit <= MAX_SCAN_LIMIT:
        raise ValueError("scan limit must be 1..200")
    conn = _connection_factory(connection_factory)()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.id AS job_id
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                JOIN KnowledgeBase AS kb ON kb.id = d.knowledgeBaseId
                WHERE kb.id = %s
                  AND j.status = 'QUEUED'
                  AND v.status = 'QUEUED'
                  AND d.deletedAt IS NULL
                  AND j.attempts < 3
                  AND (j.nextAttemptAt IS NULL OR j.nextAttemptAt <= UTC_TIMESTAMP(3))
                  AND (j.dispatchLeaseUntil IS NULL OR j.dispatchLeaseUntil <= UTC_TIMESTAMP(3))
                ORDER BY j.createdAt, j.id
                LIMIT %s
                """,
                (knowledge_base_id, limit),
            )
            rows = cursor.fetchall()
        return [row["job_id"] for row in rows]
    finally:
        conn.rollback()  # terminate the read transaction, even with autocommit disabled
        conn.close()


def reserve_exact_scoped_job(job_id: str, knowledge_base_id: str, *,
                             connection_factory=None) -> dict | None:
    """Commit one reservation ONLY if its currently linked KB matches exactly.

    The existing MySQL helper performs SELECT ... FOR UPDATE SKIP LOCKED and
    checks eligibility again. The KB is rechecked inside the SAME transaction.
    A mismatch rolls back the reservation, preventing a cross-KB dispatch.
    """
    _required_string(job_id, "job_id")
    _required_string(knowledge_base_id, "knowledge_base_id")
    from .ingestion_dispatch_lease import reserve_dispatch_batch
    conn = _connection_factory(connection_factory)()
    try:
        conn.begin()
        reservations = reserve_dispatch_batch(
            conn, batch_size=1, lease_seconds=DISPATCH_LEASE_SECONDS, job_id=job_id
        )
        if not reservations:
            conn.rollback()
            return None
        if len(reservations) != 1 or reservations[0]["job_id"] != job_id:
            raise RuntimeError("Unexpected reservation")
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT d.knowledgeBaseId AS knowledge_base_id, d.deletedAt,
                       j.status AS job_status, v.status AS version_status
                FROM IngestionJob j
                JOIN DocumentVersion v ON v.id = j.documentVersionId
                JOIN Document d ON d.id = v.documentId
                WHERE j.id = %s
                LIMIT 1
                """,
                (job_id,),
            )
            row = cursor.fetchone()
        if (row is None or row["knowledge_base_id"] != knowledge_base_id
                or row["deletedAt"] is not None or row["job_status"] != "QUEUED"
                or row["version_status"] != "QUEUED"):
            raise RuntimeError("Reservation scope or status changed")
        conn.commit()
        return reservations[0]
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_matching_reservation(job_id: str, lease_until: datetime, *,
                                 connection_factory=None) -> bool:
    """Only release the matching reservation after a broker exception.

    Broker results can be ambiguous. If already published, duplicate delivery
    remains safe only because the ingestion worker atomically claims the job.
    """
    from .ingestion_dispatch_lease import release_dispatch_lease
    conn = _connection_factory(connection_factory)()
    try:
        conn.begin()
        released = release_dispatch_lease(conn, job_id, lease_until)
        conn.commit()
        return released
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def dispatch_scope_once(knowledge_base_id: str, *, publish: bool = False,
                        confirm_knowledge_base_id: str | None = None,
                        batch_size: int = DEFAULT_BATCH_SIZE,
                        scan_limit: int = DEFAULT_SCAN_LIMIT,
                        connection_factory=None,
                        preflight: Callable[[str], dict] | None = None,
                        sender=None,
                        env_flag: str | None = None) -> dict:
    """Preview or dispatch eligible clean jobs in ONE test KB only.

    Preview checks Qdrant pilot prerequisites but does not reserve, claim, or
    publish. Publish requires 2 independent opt-ins and is limited to the
    specified KB. Preflight errors fail closed for that job; no raw source or
    secret error messages are returned.
    """
    validate_options(knowledge_base_id, publish=publish,
                     confirm_knowledge_base_id=confirm_knowledge_base_id,
                     batch_size=batch_size, scan_limit=scan_limit, env_flag=env_flag)
    if preflight is None:
        from .live_ingestion_worker import preview_pilot
        preflight = preview_pilot
    candidates = list_candidate_jobs(
        knowledge_base_id, limit=scan_limit, connection_factory=connection_factory
    )
    counts = {
        "knowledge_base_id": knowledge_base_id,
        "mode": "publish_once" if publish else "read_only_preview",
        "scanned": len(candidates), "preflight_rejected": 0,
        "eligible": 0, "reserved": 0, "published": 0, "failed": 0,
        "mysql_dispatch_reservations_modified": False,
        "qdrant_written_by_dispatcher": False,
        "automatic_processing_enabled": publish,
    }
    eligible: list[str] = []
    for job_id in candidates:
        try:
            result = preflight(job_id)
            if not result.get("eligible_for_explicit_pilot") or not result.get(
                "production_collection_clean_for_version"
            ):
                raise RuntimeError("Pilot prerequisite rejected")
            eligible.append(job_id)
            if len(eligible) >= batch_size:
                break
        except Exception:
            counts["preflight_rejected"] += 1
            LOGGER.warning("Preflight refused job=%s (no dispatch)", job_id)
    counts["eligible"] = len(eligible)
    if not publish:
        return counts

    if sender is None:
        from .celery_app import celery_app
        sender = celery_app.send_task
    for job_id in eligible:
        reservation = reserve_exact_scoped_job(
            job_id, knowledge_base_id, connection_factory=connection_factory
        )
        if reservation is None:
            continue  # raced with another dispatcher or existing job claim
        counts["reserved"] += 1
        counts["mysql_dispatch_reservations_modified"] = True
        try:
            message = sender(TASK_NAME, args=[job_id], queue=QUEUE_NAME, retry=False)
            counts["published"] += 1
            LOGGER.info("Submitted job=%s task=%s", job_id, getattr(message, "id", "unknown"))
        except Exception:
            counts["failed"] += 1
            LOGGER.error("Broker publication failed for job=%s", job_id)
            try:
                release_matching_reservation(
                    job_id, reservation["lease_until"],
                    connection_factory=connection_factory,
                )
            except Exception:
                LOGGER.error("Reservation cleanup failed for job=%s", job_id)
    return counts


def watch_knowledge_base(knowledge_base_id: str, *, confirm_knowledge_base_id: str,
                         interval_seconds: int = DEFAULT_INTERVAL,
                         batch_size: int = DEFAULT_BATCH_SIZE,
                         scan_limit: int = DEFAULT_SCAN_LIMIT,
                         dispatch_once=dispatch_scope_once,
                         wait=time.sleep,
                         env_flag: str | None = None) -> None:
    """Run explicit foreground watcher for NEW uploads to a disposable test KB.

    No background daemon or automatic startup. Stop with Ctrl+C. The worker
    has its own opt-in and must be running separately.
    """
    if type(interval_seconds) is not int or not 10 <= interval_seconds <= 3600:
        raise ValueError("interval_seconds must be 10..3600")
    validate_options(knowledge_base_id, publish=True,
                     confirm_knowledge_base_id=confirm_knowledge_base_id,
                     batch_size=batch_size, scan_limit=scan_limit,
                     env_flag=env_flag)
    while True:
        # Re-check operator enablement every round; turning off the flag in a
        # separate process won't affect an existing CMD, so Ctrl+C is the stop.
        result = dispatch_once(
            knowledge_base_id, publish=True,
            confirm_knowledge_base_id=confirm_knowledge_base_id,
            batch_size=batch_size, scan_limit=scan_limit, env_flag=env_flag,
        )
        LOGGER.info("Dispatch round: %s", json.dumps(result))
        wait(interval_seconds)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish-once", action="store_true",
                      help="Explicit test-only DB leases + Redis messages")
    mode.add_argument("--watch", action="store_true",
                      help="Foreground repeated dispatch for test KB; Ctrl+C to stop")
    parser.add_argument("--confirm-knowledge-base-id")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--scan-limit", type=int, default=DEFAULT_SCAN_LIMIT)
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.watch:
            print("TEST-ONLY watcher started. Ctrl+C to stop; do not use a customer knowledge base.", flush=True)
            watch_knowledge_base(
                args.knowledge_base_id,
                confirm_knowledge_base_id=args.confirm_knowledge_base_id,
                interval_seconds=args.interval_seconds,
                batch_size=args.batch_size, scan_limit=args.scan_limit,
            )
            return 0
        result = dispatch_scope_once(
            args.knowledge_base_id,
            publish=args.publish_once,
            confirm_knowledge_base_id=args.confirm_knowledge_base_id,
            batch_size=args.batch_size, scan_limit=args.scan_limit,
        )
        print(json.dumps(result, indent=2))
        return 1 if result["failed"] else 0
    except KeyboardInterrupt:
        print("Stopped automatic test dispatcher.")
        return 0
    except Exception as exc:
        # Avoid raw SDK exception messages leaking private text/storage keys.
        print("Automatic dispatcher failed: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
