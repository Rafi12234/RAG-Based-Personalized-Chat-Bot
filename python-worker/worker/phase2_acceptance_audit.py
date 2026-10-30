"""Phase 2.13: read-only ingestion acceptance audit for one local TEST KB.

Inspects committed MySQL job/document statuses and ALL Qdrant point IDs/payload
scopes for each version (up to the existing guarded-recovery 2048-point cap).
NO MySQL/Qdrant/Redis/RustFS writes, Celery publication, or configuration changes.

This is a narrow test-KB consistency check, NOT proof of full document
completeness, production fault tolerance, authorization, or prompt grounding.
Stop the test ingestion worker/dispatcher for a stable result.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import settings  # noqa: F401 - load existing local .env

MAX_AUDIT_JOBS = 50


def _identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{name} must be a nonempty identifier, at most 128 characters")


def list_job_snapshot(knowledge_base_id: str, *, max_jobs: int = 25,
                      connection_factory=None) -> tuple[dict, ...]:
    """READ-ONLY MySQL snapshot of one KB (never prints tokens/file content).

    Reads max_jobs+1 to refuse a partial audit. Caller should stop worker/dispatcher
    while auditing because the DB/Qdrant cannot share an atomic snapshot.
    """
    _identifier(knowledge_base_id, "knowledge_base_id")
    if type(max_jobs) is not int or not 1 <= max_jobs <= MAX_AUDIT_JOBS:
        raise ValueError("max_jobs must be 1..50")
    if connection_factory is None:
        from .db import get_connection
        connection_factory = get_connection
    conn = connection_factory()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT j.id AS job_id, j.status AS job_status,
                       j.attempts, (j.processingToken IS NOT NULL) AS token_present,
                       (j.processingLeaseUntil IS NOT NULL) AS lease_present,
                       (j.processingLeaseUntil > UTC_TIMESTAMP(3)) AS lease_live,
                       v.status AS document_status, v.id AS version_id,
                       v.filename, d.id AS document_id,
                       d.knowledgeBaseId AS knowledge_base_id,
                       kb.organizationId AS organization_id
                FROM IngestionJob AS j
                JOIN DocumentVersion AS v ON v.id = j.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                JOIN KnowledgeBase AS kb ON kb.id = d.knowledgeBaseId
                WHERE kb.id = %s AND d.deletedAt IS NULL
                ORDER BY j.createdAt, j.id
                LIMIT %s
                """,
                (knowledge_base_id, max_jobs + 1),
            )
            rows = cursor.fetchall()
        if len(rows) > max_jobs:
            raise RuntimeError("Too many jobs for bounded acceptance audit")
        if any(row.get("knowledge_base_id") != knowledge_base_id for row in rows):
            raise RuntimeError("Audit job escaped its knowledge-base scope")
        ids = [row.get("job_id") for row in rows]
        if len(ids) != len(set(ids)) or any(not isinstance(x, str) or not x for x in ids):
            raise RuntimeError("Duplicate or malformed ingestion job identifiers")
        return tuple(rows)
    finally:
        conn.rollback()
        conn.close()


def _snapshot_fingerprint(rows: tuple[dict, ...]) -> tuple:
    """Track status/ownership changes that would invalidate a cross-store read."""
    return tuple((r["job_id"], r["job_status"], r["document_status"],
                  int(r["attempts"]), bool(r["token_present"]),
                  bool(r["lease_present"]), bool(r["lease_live"]),
                  r["document_id"], r["version_id"], r["organization_id"],
                  r["knowledge_base_id"]) for r in rows)


def assess_document(row: dict, refs) -> dict:
    """Assess consistency only. Does not prove extraction/embedding completeness."""
    job = row["job_status"]
    doc = row["document_status"]
    attempts = int(row["attempts"])
    token = bool(row["token_present"])
    lease = bool(row["lease_present"])
    ready = sum(ref.index_state == "ready" for ref in refs)
    staging = sum(ref.index_state == "staging" for ref in refs)
    count = len(refs)
    result = {"job_id": row["job_id"], "job_status": job,
              "document_status": doc, "attempts": attempts,
              "ready_vectors": ready, "staging_vectors": staging,
              "total_scoped_vectors": count}
    if job == "COMPLETED" and doc == "READY":
        if ready > 0 and staging == 0 and ready == count and attempts >= 1 and not token and not lease:
            result["assessment"] = "ready_scoped_consistent"
        else:
            result["assessment"] = "ready_inconsistent_manual_review"
    elif job == "QUEUED" and doc == "QUEUED":
        result["assessment"] = ("queued_clean_not_complete" if count == 0 and not token and not lease
                                else "queued_orphans_or_claim_manual_review")
    elif job == "RUNNING" and doc == "PROCESSING":
        result["assessment"] = ("processing_in_progress" if bool(row["lease_live"])
                                else "expired_or_unknown_processing_lease")
    elif job == "FAILED" and doc == "FAILED":
        result["assessment"] = ("failed_no_vectors" if count == 0 else "failed_with_vectors_manual_review")
    else:
        result["assessment"] = "job_version_status_mismatch_manual_review"
    return result


def audit_knowledge_base(knowledge_base_id: str, *, max_jobs: int = 25,
                         job_reader=None, client_factory=None, point_reader=None) -> dict:
    """Audit only this test KB. A pass requires >=1 completed and ALL jobs consistent.

    A fresh MySQL snapshot after Qdrant inspection detects many common races,
    though no read-only check can guarantee atomic cross-store consistency.
    """
    _identifier(knowledge_base_id, "knowledge_base_id")
    if type(max_jobs) is not int or not 1 <= max_jobs <= MAX_AUDIT_JOBS:
        raise ValueError("max_jobs must be 1..50")
    if job_reader is None:
        job_reader = list_job_snapshot
    if client_factory is None:
        from .production_qdrant import local_client
        client_factory = local_client
    if point_reader is None:
        from .guarded_recovery import list_scoped_points
        point_reader = list_scoped_points
    from .production_qdrant import inspect_collection
    from .qdrant_indexing import IndexIdentity

    before = job_reader(knowledge_base_id, max_jobs=max_jobs)
    client = client_factory()
    results = []
    try:
        schema = inspect_collection(client)
        if not schema.exists or not schema.compatible or schema.missing_indexes:
            raise RuntimeError("Production Qdrant collection is not compatible")
        for row in before:
            identity = IndexIdentity(
                organization_id=row["organization_id"],
                knowledge_base_id=row["knowledge_base_id"],
                document_id=row["document_id"],
                document_version_id=row["version_id"],
                job_id=row["job_id"],
                filename=row["filename"],
            )
            try:
                refs = point_reader(client, identity)
                findings = assess_document(row, refs)
            except Exception:
                # Refuse to declare a pass when Qdrant is inaccessible, malformed,
                # inconsistent or exceeds the existing 2048-point inspection cap.
                findings = {"job_id": row["job_id"], "job_status": row["job_status"],
                            "document_status": row["document_status"],
                            "assessment": "qdrant_integrity_not_verified"}
            results.append(findings)
    finally:
        client.close()

    after = job_reader(knowledge_base_id, max_jobs=max_jobs)
    stable = _snapshot_fingerprint(before) == _snapshot_fingerprint(after)
    ready = sum(r["assessment"] == "ready_scoped_consistent" for r in results)
    all_ready = bool(results) and ready == len(results)
    outcome = ("changed_during_audit" if not stable else
               "test_kb_consistent" if all_ready else
               "no_jobs_to_verify" if not results else "manual_review_or_unfinished_jobs")
    return {
        "mode": "read_only_phase2_test_kb_acceptance",
        "knowledge_base_id": knowledge_base_id,
        "jobs_scanned": len(results),
        "jobs_ready_scoped_consistent": ready,
        "mysql_snapshot_stable": stable,
        "assessment": outcome,
        "jobs": results,
        "full_document_chunk_manifest_verified": False,
        "retrieval_access_control_verified": False,
        "production_readiness_certified": False,
        "mysql_modified": False, "qdrant_modified": False,
        "redis_modified": False, "rustfs_modified": False,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--knowledge-base-id", required=True)
    p.add_argument("--max-jobs", type=int, default=25)
    p.add_argument("--json-out", help="Optional new JSON report path; never overwrites")
    args = p.parse_args(argv)
    try:
        report = audit_knowledge_base(args.knowledge_base_id, max_jobs=args.max_jobs)
        if args.json_out:
            with Path(args.json_out).expanduser().open("x", encoding="utf-8") as fp:
                json.dump(report, fp, indent=2)
        print(json.dumps(report, indent=2))
        return 0 if report["assessment"] == "test_kb_consistent" else 1
    except Exception as exc:
        # Never show private filenames, source text, keys, URLs or token contents.
        print("Phase 2 acceptance audit refused or failed: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
