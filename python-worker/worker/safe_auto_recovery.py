"""Phase 2.11C: conservative expired-lease recovery for ONE disposable test KB.

Preview is READ ONLY. Opt-in recovery only changes MySQL when an expired job
has ZERO scoped Qdrant vectors, after a configurable lease grace interval.

No Qdrant deletes, vector publication or direct Celery dispatch. The existing
2.10 watcher can pick up eligible QUEUED jobs after the standard retry delay.
For attempts containing ANY vectors, use the STOPPED-WORKER recovery operator
from Phase 2.11A/B; never clean them concurrently with ingestion workers.

MySQL and Qdrant cannot share a transaction. Even this conservative protocol
cannot absolutely exclude a late in-flight Qdrant operation; the dispatcher
and pilot worker fail closed if orphan points appear after an expired claim.
NOT suitable for production data or automatic startup.
"""
from __future__ import annotations

import os
from typing import Callable

from . import settings  # noqa: F401 (loads local .env)

FLAG_NAME = "DOCMIND_ENABLE_SAFE_AUTO_RECOVERY"
FLAG_VALUE = "YES_FOR_TESTING"
DEFAULT_GRACE_SECONDS = 180
DEFAULT_LIMIT = 3
MAX_JOBS = 5
MAX_SCAN = 20


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be a nonempty identifier")


def _limits(grace_seconds: int, limit: int, max_limit: int) -> None:
    if type(grace_seconds) is not int or not 120 <= grace_seconds <= 86400:
        raise ValueError("grace_seconds must be between 120 and 86400")
    if type(limit) is not int or not 1 <= limit <= max_limit:
        raise ValueError(f"limit must be 1..{max_limit}")


def list_expired_jobs(knowledge_base_id: str, *, grace_seconds: int = DEFAULT_GRACE_SECONDS,
                      limit: int = MAX_SCAN, connection_factory: Callable | None = None) -> list[str]:
    """Read-only candidate scan, strictly limited to one KB and expired RUNNING leases."""
    _required(knowledge_base_id, "knowledge_base_id")
    _limits(grace_seconds, limit, MAX_SCAN)
    if connection_factory is None:
        from .db import get_connection
        connection_factory = get_connection
    conn = connection_factory()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT j.id AS job_id
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                WHERE d.knowledgeBaseId = %s
                  AND d.deletedAt IS NULL
                  AND j.status = 'RUNNING' AND v.status = 'PROCESSING'
                  AND j.attempts >= 1
                  AND j.processingToken IS NOT NULL
                  AND j.processingLeaseUntil IS NOT NULL
                  AND j.processingLeaseUntil <= DATE_SUB(UTC_TIMESTAMP(3), INTERVAL %s SECOND)
                ORDER BY j.processingLeaseUntil, j.id
                LIMIT %s
                """, (knowledge_base_id, grace_seconds, limit),
            )
            rows = cur.fetchall()
        ids = [item["job_id"] for item in rows]
        if len(ids) != len(set(ids)) or any(not isinstance(x, str) or not x for x in ids):
            raise RuntimeError("Invalid recovery candidates")
        return ids
    finally:
        conn.rollback()
        conn.close()


def preview_expired_jobs(knowledge_base_id: str, *, grace_seconds: int = DEFAULT_GRACE_SECONDS,
                         limit: int = MAX_SCAN, candidate_provider=None, inspector=None) -> dict:
    """Read-only audit: reports empty-vector eligibility versus manual orphan recovery."""
    _required(knowledge_base_id, "knowledge_base_id")
    _limits(grace_seconds, limit, MAX_SCAN)
    if candidate_provider is None:
        candidate_provider = list_expired_jobs
    if inspector is None:
        from .guarded_recovery import inspect_one
        inspector = inspect_one
    ids = candidate_provider(knowledge_base_id, grace_seconds=grace_seconds, limit=limit)
    if len(ids) > limit or len(ids) != len(set(ids)):
        raise RuntimeError("Invalid audit candidate list")
    jobs = []
    for job_id in ids:
        _required(job_id, "job_id")
        try:
            report = inspector(job_id, knowledge_base_id)
            if report.get("job_id") != job_id or report.get("knowledge_base_id") != knowledge_base_id:
                raise RuntimeError("Scope changed")
            if report.get("action") == "expired_recover_empty" and (
                report.get("staging_vectors_found") == 0 and
                report.get("ready_vectors_found") == 0
            ):
                outcome = "eligible_empty_vector_lease"
            elif report.get("action") == "expired_recover_with_orphans":
                outcome = "manual_orphan_repair_required"
            else:
                outcome = "refused_review_required"
        except Exception:
            outcome = "inspection_failed_closed"
        jobs.append({"job_id": job_id, "assessment": outcome})
    return {
        "knowledge_base_id": knowledge_base_id,
        "mode": "read_only_preview",
        "scanned": len(ids), "jobs": jobs,
        "mysql_modified": False, "qdrant_modified": False,
        "redis_task_published": False,
    }


def validate_enablement(knowledge_base_id: str, *, confirm_knowledge_base_id: str | None,
                        env_value: str | None = None, max_jobs: int = DEFAULT_LIMIT,
                        grace_seconds: int = DEFAULT_GRACE_SECONDS) -> None:
    _required(knowledge_base_id, "knowledge_base_id")
    _limits(grace_seconds, max_jobs, MAX_JOBS)
    if confirm_knowledge_base_id != knowledge_base_id:
        raise ValueError("Exact knowledge base confirmation is required")
    actual = os.environ.get(FLAG_NAME) if env_value is None else env_value
    if actual != FLAG_VALUE:
        raise RuntimeError("Test-only recovery is disabled")


def recover_one_empty(job_id: str, knowledge_base_id: str, *, grace_seconds: int = DEFAULT_GRACE_SECONDS,
                      connection_factory=None, client_factory=None, recovery_fn=None,
                      row_reader=None, points_reader=None, identity_builder=None) -> dict:
    """Token-fenced SQL lease recovery ONLY when scoped Qdrant has ZERO points.

    Locks the job row before the final age check and Qdrant verification. No
    Qdrant mutations. Any in-flight foreign/suspicious vectors prevent recovery.
    The outer caller must have validated test-only enablement and exact KB.
    """
    _required(job_id, "job_id")
    _required(knowledge_base_id, "knowledge_base_id")
    _limits(grace_seconds, 1, MAX_JOBS)
    if connection_factory is None:
        from .db import get_connection
        connection_factory = get_connection
    if row_reader is None or points_reader is None or identity_builder is None:
        from .guarded_recovery import read_job, list_scoped_points, _identity
        row_reader = row_reader or read_job
        points_reader = points_reader or list_scoped_points
        identity_builder = identity_builder or _identity
    if client_factory is None:
        from .production_qdrant import local_client
        client_factory = local_client
    if recovery_fn is None:
        from .ingestion_recovery import recover_expired_jobs
        recovery_fn = recover_expired_jobs

    conn = connection_factory()
    client = None
    try:
        conn.begin()
        row = row_reader(conn, job_id, knowledge_base_id, lock=True)
        if (row.get("job_status") != "RUNNING" or row.get("document_status") != "PROCESSING"
                or not row.get("lease_present") or not row.get("lease_expired")
                or not row.get("token_present") or not row.get("processing_token")
                or int(row.get("attempts", 0)) < 1):
            conn.rollback()
            return {"job_id": job_id, "outcome": "not_expired_active_job", "mysql_modified": False}
        # Recheck minimum age AFTER acquiring MySQL row lock. The guard must
        # use DB time, not the local Windows clock, which may drift.
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 AS still_expired
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                WHERE j.id = %s AND d.knowledgeBaseId = %s
                  AND d.deletedAt IS NULL
                  AND j.status = 'RUNNING' AND v.status = 'PROCESSING'
                  AND j.processingToken = %s
                  AND j.processingLeaseUntil <= DATE_SUB(UTC_TIMESTAMP(3), INTERVAL %s SECOND)
                LIMIT 1
                """, (job_id, knowledge_base_id, row["processing_token"], grace_seconds),
            )
            eligible = cur.fetchone()
        if eligible is None:
            conn.rollback()
            return {"job_id": job_id, "outcome": "grace_or_ownership_refused", "mysql_modified": False}

        client = client_factory()
        identity = identity_builder(row)
        # Exact full-version Qdrant inspection (including staging and ready),
        # not just a ready-point count. Refuses malformed, foreign or >2048 pts.
        points = points_reader(client, identity)
        if points:
            conn.rollback()
            return {"job_id": job_id, "outcome": "orphan_vectors_manual_repair_required",
                    "vector_count": len(points), "mysql_modified": False}
        recovered = recovery_fn(conn, job_id=job_id, batch_size=1)
        if (len(recovered) != 1 or recovered[0].get("job_id") != job_id
                or recovered[0].get("status") not in ("QUEUED", "FAILED")):
            raise RuntimeError("Expired lease recovery changed scope")
        # Second count under lock detects a concurrent in-flight Qdrant write
        # that finished while the SQL transition was being prepared. This is
        # not a cross-store transaction; a later delayed write remains possible.
        if points_reader(client, identity):
            raise RuntimeError("Qdrant changed while recovering lease")
        conn.commit()
        return {"job_id": job_id, "outcome": "recovered_empty_vector_job",
                "new_job_status": recovered[0]["status"], "mysql_modified": True,
                "qdrant_modified": False, "redis_task_published": False}
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
        if client is not None:
            client.close()


def recover_expired_batch(knowledge_base_id: str, *, confirm_knowledge_base_id: str,
                          grace_seconds: int = DEFAULT_GRACE_SECONDS,
                          max_jobs: int = DEFAULT_LIMIT, env_value: str | None = None,
                          candidates=None, recover=None) -> dict:
    """Opt-in ONE round. Never delete vectors or enqueue Celery tasks."""
    validate_enablement(knowledge_base_id, confirm_knowledge_base_id=confirm_knowledge_base_id,
                        grace_seconds=grace_seconds, max_jobs=max_jobs, env_value=env_value)
    provider = candidates or list_expired_jobs
    worker = recover or recover_one_empty
    ids = provider(knowledge_base_id, grace_seconds=grace_seconds, limit=max_jobs)
    if len(ids) > max_jobs or len(ids) != len(set(ids)):
        raise RuntimeError("Invalid job batch")
    handled = []
    for job_id in ids:
        _required(job_id, "job_id")
        try:
            report = worker(job_id, knowledge_base_id, grace_seconds=grace_seconds)
            if report.get("job_id") != job_id:
                raise RuntimeError("Recovery returned a different job")
            outcome = report.get("outcome")
            if outcome not in (
                "not_expired_active_job", "grace_or_ownership_refused",
                "orphan_vectors_manual_repair_required", "recovered_empty_vector_job",
            ):
                raise RuntimeError("Unrecognized recovery outcome")
            handled.append({"job_id": job_id, "outcome": outcome})
        except Exception:
            handled.append({"job_id": job_id, "outcome": "recovery_failed_closed"})
    return {"knowledge_base_id": knowledge_base_id, "mode": "test_only_recover_once",
            "scanned": len(ids), "handled": handled,
            "recovered": sum(x["outcome"] == "recovered_empty_vector_job" for x in handled),
            "requires_manual_review": sum(x["outcome"] in (
                "orphan_vectors_manual_repair_required", "recovery_failed_closed") for x in handled),
            "qdrant_modified": False, "redis_task_published": False}
