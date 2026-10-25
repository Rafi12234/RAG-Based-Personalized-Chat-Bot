"""Phase 2.11A: explicitly gated, local TEST-KB orphan reconciliation.

Default is a read-only per-job preview. Repair is a one-off operator procedure
ONLY AFTER the dedicated Celery worker AND auto dispatcher are stopped. Do not
schedule this on production; there is no transaction spanning MySQL and Qdrant.

Repair holds the MySQL job row lock while verifying and removing ONLY scoped
orphan points, then optionally calls the existing expired-lease recovery. If a
Qdrant operation fails, MySQL rolls back; Qdrant deletes already performed may
persist and can be rechecked on a later operator run. Never deletes a READY job.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable
from uuid import UUID

from . import settings  # noqa: F401 - load python-worker/.env
from .qdrant_indexing import IndexIdentity
from .production_qdrant import PRODUCTION_COLLECTION, STAGING, READY, inspect_collection, local_client

RECOVERY_ENV = "DOCMIND_ENABLE_TEST_RECOVERY"
RECOVERY_OPT_IN = "YES_FOR_TESTING"
MAX_POINTS = 2048  # bounded first recovery milestone for disposable documents
PAGE_SIZE = 100
_SCOPE_KEYS = ("organization_id", "knowledge_base_id", "document_id",
               "document_version_id", "job_id")
_FIELDS = (*_SCOPE_KEYS, "processing_token", "index_state")


@dataclass(frozen=True)
class PointRef:
    identifier: str
    processing_token: str
    index_state: str


def _required(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError(f"{field} must be a nonempty identifier (max 128 characters)")


def _models():
    from qdrant_client import models
    return models


def version_filter(identity: IndexIdentity):
    """Strongest available indexed version scope; never use ID/version alone."""
    m = _models()
    values = {"organization_id": identity.organization_id,
              "knowledge_base_id": identity.knowledge_base_id,
              "document_id": identity.document_id,
              "document_version_id": identity.document_version_id}
    return m.Filter(must=[m.FieldCondition(key=k, match=m.MatchValue(value=v))
                          for k, v in values.items()])


def _identity(row: dict) -> IndexIdentity:
    return IndexIdentity(
        organization_id=row["organization_id"],
        knowledge_base_id=row["knowledge_base_id"],
        document_id=row["document_id"],
        document_version_id=row["version_id"],
        job_id=row["job_id"],
        filename=row["filename"],
    )


def read_job(connection, job_id: str, knowledge_base_id: str, *, lock: bool = False) -> dict:
    """Resolve exact test-KB ownership from MySQL. Lock when changing anything."""
    _required(job_id, "job_id")
    _required(knowledge_base_id, "knowledge_base_id")
    sql = """
      SELECT j.id AS job_id, j.status AS job_status, v.status AS document_status,
             j.attempts, j.processingToken AS processing_token,
             (j.processingLeaseUntil <= UTC_TIMESTAMP(3)) AS lease_expired,
             (j.processingLeaseUntil IS NOT NULL) AS lease_present,
             (j.processingToken IS NOT NULL) AS token_present,
             d.deletedAt, d.id AS document_id, d.knowledgeBaseId AS knowledge_base_id,
             v.id AS version_id, v.filename, kb.organizationId AS organization_id
      FROM IngestionJob j
      JOIN DocumentVersion v ON v.id = j.documentVersionId
      JOIN Document d ON d.id = v.documentId
      JOIN KnowledgeBase kb ON kb.id = d.knowledgeBaseId
      WHERE j.id = %s AND kb.id = %s
      LIMIT 1
    """ + (" FOR UPDATE" if lock else "")
    with connection.cursor() as cur:
        cur.execute(sql, (job_id, knowledge_base_id))
        row = cur.fetchone()
    if row is None:
        raise RuntimeError("Job not found in the specified knowledge base")
    if row["deletedAt"] is not None:
        raise RuntimeError("Archived document cannot be repaired")
    return row


def _assert_schema(client) -> None:
    schema = inspect_collection(client)
    if not schema.exists or not schema.compatible or schema.missing_indexes:
        raise RuntimeError("Qdrant production schema not compatible")


def list_scoped_points(client, identity: IndexIdentity, *, max_points: int = MAX_POINTS) -> tuple[PointRef, ...]:
    """Read all version points with pagination, payload validation, no text/vectors.

    Refuse unrecognized payloads, invalid IDs, unknown states, multiple attempt
    tokens, duplicates or too-large data. That requires manual review, never
    guessed cleanup. Does not fetch or print document text.
    """
    if type(max_points) is not int or not 1 <= max_points <= MAX_POINTS:
        raise ValueError("Invalid recovery point bound")
    _assert_schema(client)
    expected = dict(zip(_SCOPE_KEYS, (
        identity.organization_id, identity.knowledge_base_id,
        identity.document_id, identity.document_version_id, identity.job_id,
    )))
    point_filter = version_filter(identity)
    reported = client.count(collection_name=PRODUCTION_COLLECTION,
                            count_filter=point_filter, exact=True).count
    if type(reported) is not int or reported < 0 or reported > max_points:
        raise RuntimeError("Version point count is invalid or exceeds recovery cap")
    refs: list[PointRef] = []
    seen: set[str] = set()
    offset = None
    while True:
        records, next_offset = client.scroll(
            collection_name=PRODUCTION_COLLECTION, scroll_filter=point_filter,
            limit=PAGE_SIZE, offset=offset, with_payload=list(_FIELDS),
            with_vectors=False,
        )
        if not records and next_offset is not None:
            raise RuntimeError("Empty Qdrant page with continuation")
        for item in records:
            try:
                identifier = str(UUID(str(item.id)))
            except (ValueError, TypeError, AttributeError) as exc:
                raise RuntimeError("Qdrant point ID is invalid") from exc
            payload = item.payload or {}
            if (identifier in seen or any(payload.get(k) != v for k, v in expected.items())
                    or payload.get("index_state") not in (READY, STAGING)
                    or not isinstance(payload.get("processing_token"), str)
                    or not 1 <= len(payload["processing_token"]) <= 64):
                raise RuntimeError("Unsafe or inconsistent Qdrant point payload")
            seen.add(identifier)
            refs.append(PointRef(identifier, payload["processing_token"], payload["index_state"]))
            if len(refs) > max_points:
                raise RuntimeError("Exceeded recovery point bound")
        if next_offset is None:
            break
        if next_offset == offset:
            raise RuntimeError("Qdrant pagination did not advance")
        offset = next_offset
    if len(refs) != reported:
        raise RuntimeError("Qdrant count/scroll mismatch; concurrent write possible")
    tokens = {r.processing_token for r in refs}
    if len(tokens) > 1:
        raise RuntimeError("Multiple indexing attempts found; manual review required")
    return tuple(refs)


def _action(row: dict, refs: tuple[PointRef, ...]) -> str:
    job = row["job_status"]
    doc = row["document_status"]
    if job == "COMPLETED" and doc == "READY":
        return "completed_protected"
    if job == "RUNNING" and doc == "PROCESSING":
        if int(row["attempts"]) < 1:
            return "inconsistent_status_refuse"
        if not row["lease_present"] or not row["lease_expired"] or not row["token_present"]:
            return "active_or_unknown_lease_refuse"
        if refs and any(r.processing_token != row["processing_token"] for r in refs):
            return "foreign_attempt_refuse"
        return "expired_recover_with_orphans" if refs else "expired_recover_empty"
    if job == "QUEUED" and doc == "QUEUED":
        if refs and int(row["attempts"]) < 1:
            return "unclaimed_orphans_refuse"
        return "queued_orphan_cleanup" if refs else "queued_clean_no_action"
    if job == "FAILED" and doc == "FAILED":
        if refs and int(row["attempts"]) < 1:
            return "unclaimed_orphans_refuse"
        return "failed_orphan_cleanup" if refs else "failed_clean_no_action"
    return "inconsistent_status_refuse"


def _report(row: dict, refs: tuple[PointRef, ...], *, action: str, changed: bool) -> dict:
    return {
        "job_id": row["job_id"], "knowledge_base_id": row["knowledge_base_id"],
        "filename": row["filename"], "job_status_before": row["job_status"],
        "document_status_before": row["document_status"],
        "attempts": int(row["attempts"]),
        "lease_expired": bool(row["lease_expired"]),
        "staging_vectors_found": sum(r.index_state == STAGING for r in refs),
        "ready_vectors_found": sum(r.index_state == READY for r in refs),
        "action": action, "recovery_mutations_performed": changed,
        "automatic_recovery_enabled": False,
    }


def inspect_one(job_id: str, knowledge_base_id: str, *, connection_factory=None,
                client_factory=None) -> dict:
    """Preview ONLY; no SQL updates, Qdrant deletes or Celery tasks."""
    from .db import get_connection
    factory = connection_factory or get_connection
    conn = factory()
    client = None
    try:
        row = read_job(conn, job_id, knowledge_base_id)
        client = (client_factory or local_client)()
        refs = list_scoped_points(client, _identity(row))
        return _report(row, refs, action=_action(row, refs), changed=False)
    finally:
        conn.rollback()
        conn.close()
        if client is not None:
            client.close()


def _point_delete_filter(identity: IndexIdentity, token: str, state: str, ids: list[str]):
    m = _models()
    values = {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "job_id": identity.job_id,
        "processing_token": token,
        "index_state": state,
    }
    clauses = [m.FieldCondition(key=k, match=m.MatchValue(value=v))
               for k, v in values.items()]
    clauses.append(m.HasIdCondition(has_id=ids))
    return m.Filter(must=clauses)


def repair_one(job_id: str, knowledge_base_id: str, *, confirm_job_id: str | None,
               confirm_knowledge_base_id: str | None, workers_stopped: bool = False,
               env_value: str | None = None, connection_factory=None,
               client_factory=None, recover_fn=None) -> dict:
    """Explicit operator-only repair, single test job, with SQL row lock.

    Caller MUST first stop test Celery workers and dispatcher: a stale process
    with an in-flight Qdrant write can race any cross-store cleanup. This
    single-node local procedure cannot establish distributed quiescence.
    """
    _required(job_id, "job_id")
    _required(knowledge_base_id, "knowledge_base_id")
    if confirm_job_id != job_id or confirm_knowledge_base_id != knowledge_base_id:
        raise ValueError("Exact job and knowledge-base confirmations required")
    if workers_stopped is not True:
        raise ValueError("Stop test Celery and dispatcher; then add --workers-stopped")
    enabled = os.environ.get(RECOVERY_ENV) if env_value is None else env_value
    if enabled != RECOVERY_OPT_IN:
        raise RuntimeError("Test recovery not enabled")
    from .db import get_connection
    from .ingestion_recovery import recover_expired_jobs
    conn = (connection_factory or get_connection)()
    client = None
    try:
        conn.begin()
        row = read_job(conn, job_id, knowledge_base_id, lock=True)
        client = (client_factory or local_client)()
        identity = _identity(row)
        refs = list_scoped_points(client, identity)
        action = _action(row, refs)
        if action not in ("expired_recover_with_orphans", "expired_recover_empty",
                          "queued_orphan_cleanup", "queued_clean_no_action",
                          "failed_orphan_cleanup", "failed_clean_no_action"):
            raise RuntimeError("Refused recovery for active, completed or inconsistent job")
        changed = False
        if refs:
            # Each deletion rechecks every identity component, attempt token,
            # visibility state AND the exact point UUID within Qdrant itself.
            m = _models()
            groups: dict[tuple[str, str], list[str]] = {}
            for ref in refs:
                groups.setdefault((ref.processing_token, ref.index_state), []).append(ref.identifier)
            for (token, state), ids in groups.items():
                for start in range(0, len(ids), 64):
                    batch = ids[start:start + 64]
                    client.delete(
                        collection_name=PRODUCTION_COLLECTION,
                        points_selector=m.FilterSelector(
                            filter=_point_delete_filter(identity, token, state, batch)
                        ),
                        wait=True,
                    )
            # Re-read all points; if any survive, don't release the DB row.
            if list_scoped_points(client, identity):
                raise RuntimeError("Orphan vectors remain after deletion")
            changed = True
        if action.startswith("expired_recover"):
            recovered = (recover_fn or recover_expired_jobs)(conn, job_id=job_id, batch_size=1)
            if len(recovered) != 1 or recovered[0]["job_id"] != job_id:
                raise RuntimeError("Expired lease recovery did not match requested job")
            changed = True
        if changed:
            conn.commit()
        else:
            conn.rollback()
        return _report(row, refs, action=action, changed=changed)
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
        if client is not None:
            client.close()
