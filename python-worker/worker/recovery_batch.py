"""Phase 2.11B: bounded TEST-KB recovery scanner and quiesced batch repair.

DEFAULT SCAN: read-only. Optional watch: read-only.
BATCH REPAIR: test-only; MUST STOP all ingestion workers/dispatchers first.
Uses Phase 2.11A's per-job row lock and Qdrant exact-scope safety guards.
This is NOT a zero-downtime production recovery service.
"""
from __future__ import annotations

import os
from typing import Callable

from . import settings  # noqa: F401, load existing worker .env

OPT_IN_NAME = "DOCMIND_ENABLE_TEST_RECOVERY"
OPT_IN_VALUE = "YES_FOR_TESTING"
REPAIR_CONFIRM = "REPAIR_DISPOSABLE_TEST_KB"
DEFAULT_SCAN_LIMIT = 20
MAX_SCAN_LIMIT = 100
MAX_REPAIR_JOBS = 5
RECOVERABLE_ACTIONS = frozenset((
    "expired_recover_with_orphans", "expired_recover_empty",
    "queued_orphan_cleanup", "failed_orphan_cleanup",
))
CLEAN_ACTIONS = frozenset(("queued_clean_no_action", "failed_clean_no_action"))


def _required_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be a nonempty ID (max 128 characters)")


def list_recovery_candidates(
    knowledge_base_id: str, *, limit: int = DEFAULT_SCAN_LIMIT,
    connection_factory: Callable | None = None,
) -> list[str]:
    """List only expired RUNNING or previously attempted QUEUED/FAILED jobs.

    Pure SQL SELECT in a rolled-back read transaction. Never return raw tokens,
    storage keys, document text, or finished documents. Each job is rechecked
    via Phase 2.11A's exact tenant+KB Qdrant audit before any mutation.
    """
    _required_identifier(knowledge_base_id, "knowledge_base_id")
    if type(limit) is not int or not 1 <= limit <= MAX_SCAN_LIMIT:
        raise ValueError("scan limit must be 1..100")
    if connection_factory is None:
        from .db import get_connection
        connection_factory = get_connection
    connection = connection_factory()
    try:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT j.id AS job_id
                  FROM IngestionJob AS j
                  JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                  JOIN Document AS d ON d.id = v.documentId
                 WHERE d.knowledgeBaseId = %s
                   AND d.deletedAt IS NULL
                   AND (
                      (j.status = 'RUNNING' AND v.status = 'PROCESSING'
                       AND j.processingToken IS NOT NULL
                       AND j.processingLeaseUntil IS NOT NULL
                       AND j.processingLeaseUntil <= UTC_TIMESTAMP(3))
                      OR
                      (j.status = 'QUEUED' AND v.status = 'QUEUED'
                       AND j.attempts >= 1)
                      OR
                      (j.status = 'FAILED' AND v.status = 'FAILED'
                       AND j.attempts >= 1)
                   )
                 ORDER BY j.createdAt, j.id LIMIT %s
                """,
                (knowledge_base_id, limit),
            )
            rows = cur.fetchall()
        result = [row["job_id"] for row in rows]
        if len(result) != len(set(result)) or any(not isinstance(x, str) or not x for x in result):
            raise RuntimeError("Unexpected recovery candidate identifiers")
        return result
    finally:
        connection.rollback()
        connection.close()


def inspect_batch(
    knowledge_base_id: str, *, limit: int = DEFAULT_SCAN_LIMIT,
    candidate_provider: Callable | None = None,
    inspect_job: Callable | None = None,
) -> dict:
    """Read-only recovery report; each candidate has a fresh state/vector audit."""
    _required_identifier(knowledge_base_id, "knowledge_base_id")
    if candidate_provider is None:
        candidate_provider = list_recovery_candidates
    if inspect_job is None:
        from .guarded_recovery import inspect_one
        inspect_job = inspect_one
    jobs = candidate_provider(knowledge_base_id, limit=limit)
    if len(jobs) > limit:
        raise RuntimeError("Recovery scan exceeded its configured limit")
    results: list[dict] = []
    totals = {"recoverable": 0, "clean": 0, "refused": 0}
    for job_id in jobs:
        try:
            report = inspect_job(job_id, knowledge_base_id)
            if report.get("job_id") != job_id or report.get("knowledge_base_id") != knowledge_base_id:
                raise RuntimeError("Recovery inspection changed scope")
            action = report.get("action")
            group = ("recoverable" if action in RECOVERABLE_ACTIONS else
                     "clean" if action in CLEAN_ACTIONS else "refused")
            info = {
                "job_id": job_id, "action": action if isinstance(action, str) else "unknown",
                "group": group,
                "staging_vectors_found": report.get("staging_vectors_found"),
                "ready_vectors_found": report.get("ready_vectors_found"),
            }
        except Exception:
            group = "refused"
            info = {"job_id": job_id, "action": "inspection_refused", "group": group}
        totals[group] += 1
        results.append(info)
    return {
        "knowledge_base_id": knowledge_base_id,
        "mode": "read_only_audit",
        "scanned": len(jobs),
        **totals,
        "jobs": results,
        "mysql_modified": False,
        "qdrant_modified": False,
        "redis_task_published": False,
        "unattended_repair_enabled": False,
    }


def validate_repair_options(
    knowledge_base_id: str, *, confirm_knowledge_base_id: str | None,
    confirm_phrase: str | None, workers_stopped: bool,
    max_jobs: int, env_value: str | None = None,
) -> None:
    _required_identifier(knowledge_base_id, "knowledge_base_id")
    if type(max_jobs) is not int or not 1 <= max_jobs <= MAX_REPAIR_JOBS:
        raise ValueError("max_jobs must be between 1 and 5")
    if confirm_knowledge_base_id != knowledge_base_id or confirm_phrase != REPAIR_CONFIRM:
        raise ValueError("Exact knowledge base and phrase confirmations required")
    if workers_stopped is not True:
        raise RuntimeError("Stop all ingestion workers and dispatchers first")
    enabled = os.environ.get(OPT_IN_NAME) if env_value is None else env_value
    if enabled != OPT_IN_VALUE:
        raise RuntimeError("Test-only recovery opt-in missing")


def repair_batch(
    knowledge_base_id: str, *, confirm_knowledge_base_id: str | None,
    confirm_phrase: str | None, workers_stopped: bool = False,
    max_jobs: int = 1, env_value: str | None = None,
    candidate_provider: Callable | None = None,
    inspect_job: Callable | None = None,
    repair_job: Callable | None = None,
) -> dict:
    """Repair at most 5 separately confirmed, eligible disposable TEST jobs.

    Caller must ensure no writers are running. Each candidate is inspected
    immediately before the Phase 2.11A per-job repair, which rechecks state
    under a MySQL row lock and verifies exact Qdrant point scope. Fail closed:
    stop on first refusal or error; do not silently skip suspicious states.
    """
    validate_repair_options(
        knowledge_base_id, confirm_knowledge_base_id=confirm_knowledge_base_id,
        confirm_phrase=confirm_phrase, workers_stopped=workers_stopped,
        max_jobs=max_jobs, env_value=env_value,
    )
    if candidate_provider is None:
        candidate_provider = list_recovery_candidates
    if inspect_job is None or repair_job is None:
        from .guarded_recovery import inspect_one, repair_one
        inspect_job = inspect_job or inspect_one
        repair_job = repair_job or repair_one

    candidates = candidate_provider(knowledge_base_id, limit=max_jobs)
    if len(candidates) > max_jobs or len(candidates) != len(set(candidates)):
        raise RuntimeError("Invalid or excessive recovery candidates")
    handled = []
    for job_id in candidates:
        _required_identifier(job_id, "job_id")
        try:
            inspected = inspect_job(job_id, knowledge_base_id)
            if inspected.get("job_id") != job_id or inspected.get("knowledge_base_id") != knowledge_base_id:
                raise RuntimeError("Recovery target scope mismatch")
            action = inspected.get("action")
            if action in CLEAN_ACTIONS:
                handled.append({"job_id": job_id, "outcome": "already_clean"})
                continue
            if action not in RECOVERABLE_ACTIONS:
                raise RuntimeError("Ineligible or suspicious recovery candidate")
            repaired = repair_job(
                job_id, knowledge_base_id,
                confirm_job_id=job_id,
                confirm_knowledge_base_id=knowledge_base_id,
                workers_stopped=True,
                env_value=OPT_IN_VALUE,
            )
            if (repaired.get("job_id") != job_id or
                    repaired.get("knowledge_base_id") != knowledge_base_id or
                    repaired.get("action") != action or
                    repaired.get("recovery_mutations_performed") is not True):
                raise RuntimeError("Recovery result could not be verified")
            handled.append({"job_id": job_id, "outcome": "repaired", "action": action})
        except Exception:
            # Prior jobs may have committed. This is deliberately NOT an all-or-
            # nothing transaction; stop rather than hide the partial progress.
            return {
                "knowledge_base_id": knowledge_base_id, "mode": "quiesced_test_repair",
                "candidates": len(candidates), "handled": handled,
                "stopped_on_job": job_id, "result": "stopped_on_refusal_or_error",
                "partial_progress_possible": any(x["outcome"] == "repaired" for x in handled),
                "unattended_repair_enabled": False,
            }
    return {
        "knowledge_base_id": knowledge_base_id, "mode": "quiesced_test_repair",
        "candidates": len(candidates), "handled": handled,
        "result": "completed", "partial_progress_possible": False,
        "unattended_repair_enabled": False,
    }
