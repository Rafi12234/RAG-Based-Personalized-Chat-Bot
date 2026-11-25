"""Phase 4.3C: operator-only verified shadow-index candidate + revocable selection.

This is NOT a user-facing index activation API. It persists a version-scoped
verified generation and a compare-and-swap selection pointer in NEW, additive
MySQL tables. Every selection writes queryEnabled=FALSE. v1 retrieval remains
the only user-facing service; no Qdrant state changes or vector deletions.

Registering a candidate requires BOTH distinct full read-only audits:
source/structure verification and recomputed E5 numerical vector fidelity.
Both are non-transactional, so the registry is deliberately a prerequisite
for a future, stronger publication/cutover protocol, not proof of safe
multi-store activation. Caller must own a fresh processing lease, provide a
MySQL-derived identity and use a disposable test scope.

No automatic ingestion, MySQL migration application or job completion occurs.
"""
from __future__ import annotations

import os
import re
from typing import Callable

from .auto_ingestion_service import PROTECTED_QA_KBS
from .hierarchy_manifest_store import HierarchyLedger, validate_ledger
from .hierarchy_shadow_audit import ShadowIntegrityReport
from .hierarchy_vector_fidelity import VectorFidelityReport, MIN_COSINE
from .qdrant_indexing import IndexIdentity

FLAG = "DOCMIND_ENABLE_SHADOW_REGISTRY"
FLAG_VALUE = "YES_FOR_TESTING"
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,191}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
MAX_REVISION = 9223372036854775806


class RegistrySafetyError(RuntimeError):
    """Unverified shadow version or stale selection prevented a write."""


def _enabled() -> None:
    if (os.environ.get(FLAG) != FLAG_VALUE or
            os.environ.get("NODE_ENV", "").lower() == "production"):
        raise RegistrySafetyError("Shadow generation registry is disabled")


def _scope(identity: IndexIdentity, generation: str | None = None) -> None:
    if not isinstance(identity, IndexIdentity):
        raise TypeError("Authenticated MySQL-derived IndexIdentity required")
    for field in ("organization_id", "knowledge_base_id", "document_id",
                  "document_version_id", "job_id"):
        value = getattr(identity, field)
        if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
            raise ValueError("Unsafe document scope identifier")
    if identity.knowledge_base_id in PROTECTED_QA_KBS:
        raise RegistrySafetyError("Protected QA knowledge base cannot be changed")
    if generation is not None and (not isinstance(generation, str) or
                                   not DIGEST.fullmatch(generation)):
        raise ValueError("Invalid generation digest")


def _guard(check: Callable[[], bool]) -> None:
    if not callable(check) or not check():
        raise RegistrySafetyError("Fresh processing lease is required")


def _token(token: str) -> None:
    if not isinstance(token, str) or not 1 <= len(token) <= 64:
        raise ValueError("Current processing token required")


def _revision(value: int) -> None:
    if type(value) is not int or not 0 <= value <= MAX_REVISION:
        raise ValueError("Invalid optimistic revision")


def _same_report(ledger: HierarchyLedger,
                 source: ShadowIntegrityReport,
                 vectors: VectorFidelityReport) -> None:
    from .hierarchical_shadow import SHADOW_COLLECTION
    m = ledger.manifest
    validate_ledger(ledger)
    if (not isinstance(source, ShadowIntegrityReport) or
            source.collection != SHADOW_COLLECTION or
            source.generation != m.digest or
            source.points_verified != m.passage_count or
            source.text_hashes_verified != m.passage_count or
            not source.structure_verified or
            source.query_enabled or source.authorized_to_publish or
            not isinstance(vectors, VectorFidelityReport) or
            vectors.collection != SHADOW_COLLECTION or
            vectors.generation != m.digest or
            vectors.points_verified != m.passage_count or
            not vectors.vectors_recomputed or
            vectors.query_enabled or vectors.authorized_to_publish or
            vectors.min_cosine < MIN_COSINE):
        raise RegistrySafetyError("Two independent shadow audits are required")


def register_verified_candidate(
    connection, identity: IndexIdentity, ledger: HierarchyLedger,
    source_audit: ShadowIntegrityReport, vector_audit: VectorFidelityReport,
    processing_token: str, *, lease_guard: Callable[[], bool],
) -> str:
    """Immutable one-time registration, fenced by MySQL claim in INSERT SELECT.

    A duplicate digest is NEVER overwritten, including when previous
    registration was revoked. No source text or vectors are written to SQL.
    """
    _enabled()
    _scope(identity, ledger.manifest.digest)
    _token(processing_token)
    _same_report(ledger, source_audit, vector_audit)
    if ledger.manifest.document_version_id != identity.document_version_id:
        raise RegistrySafetyError("Ledger version does not match job")
    _guard(lease_guard)
    sql = """
        INSERT INTO ShadowIndexCandidate
        (documentVersionId, generationDigest, organizationId, knowledgeBaseId,
         documentId, jobId, sourceSha256, recordsSha256, passageCount,
         verifiedAt, revokedAt)
        SELECT v.id, %s, kb.organizationId, d.knowledgeBaseId, d.id, j.id,
               %s, %s, %s, UTC_TIMESTAMP(3), NULL
        FROM IngestionJob AS j
        JOIN DocumentVersion AS v ON v.id = j.documentVersionId
        JOIN Document AS d ON d.id = v.documentId
        JOIN KnowledgeBase AS kb ON kb.id = d.knowledgeBaseId
        WHERE j.id = %s AND j.processingToken = %s
          AND j.status = 'RUNNING' AND v.status = 'PROCESSING'
          AND j.processingLeaseUntil > UTC_TIMESTAMP(3)
          AND d.deletedAt IS NULL
          AND v.id = %s AND d.id = %s
          AND kb.id = %s AND kb.organizationId = %s
    """
    try:
        connection.begin()
        _guard(lease_guard)
        with connection.cursor() as cursor:
            cursor.execute(sql, (
                ledger.manifest.digest, ledger.manifest.source_sha256,
                ledger.records_sha256, ledger.manifest.passage_count,
                identity.job_id, processing_token, identity.document_version_id,
                identity.document_id, identity.knowledge_base_id,
                identity.organization_id,
            ))
            if cursor.rowcount != 1:
                raise RegistrySafetyError("Stale claim or mismatched document scope")
        _guard(lease_guard)
        connection.commit()
        return ledger.manifest.digest
    except BaseException:
        connection.rollback()
        raise


def select_verified_candidate(
    connection, identity: IndexIdentity, generation: str,
    processing_token: str, *, expected_revision: int,
    lease_guard: Callable[[], bool],
) -> int:
    """CAS-select a VERIFIED, non-revoked candidate. Never enables retrieval.

    Revision 0 means no selection row currently exists. Subsequent writes
    require the exact previously observed revision and a fresh lease.
    Selection is revocable without mutating the immutable candidate or vectors.
    """
    _enabled()
    _scope(identity, generation)
    _token(processing_token)
    _revision(expected_revision)
    _guard(lease_guard)
    try:
        connection.begin()
        _guard(lease_guard)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 AS eligible FROM ShadowIndexCandidate AS g
                JOIN DocumentVersion AS v ON v.id = g.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                JOIN KnowledgeBase AS kb ON kb.id = d.knowledgeBaseId
                JOIN IngestionJob AS j ON j.documentVersionId = v.id
                WHERE g.documentVersionId = %s AND g.generationDigest = %s
                  AND g.jobId = %s AND g.revokedAt IS NULL
                  AND g.organizationId = %s AND g.knowledgeBaseId = %s
                  AND g.documentId = %s
                  AND kb.id = %s AND kb.organizationId = %s
                  AND d.deletedAt IS NULL AND v.status = 'PROCESSING'
                  AND j.id = %s AND j.status = 'RUNNING'
                  AND j.processingToken = %s
                  AND j.processingLeaseUntil > UTC_TIMESTAMP(3)
                LIMIT 1 FOR UPDATE
                """,
                (
                    identity.document_version_id, generation, identity.job_id,
                    identity.organization_id, identity.knowledge_base_id,
                    identity.document_id, identity.knowledge_base_id,
                    identity.organization_id, identity.job_id, processing_token,
                ),
            )
            if cursor.fetchone() is None:
                raise RegistrySafetyError("Candidate is not eligible for selection")
            cursor.execute(
                """
                SELECT revision, generationDigest, queryEnabled
                FROM ShadowIndexSelection
                WHERE documentVersionId = %s FOR UPDATE
                """,
                (identity.document_version_id,),
            )
            current = cursor.fetchone()
            if current is None:
                if expected_revision != 0:
                    raise RegistrySafetyError("Selection revision changed")
                cursor.execute(
                    """
                    INSERT INTO ShadowIndexSelection
                    (documentVersionId, generationDigest, revision, queryEnabled, updatedAt)
                    VALUES (%s, %s, 1, FALSE, UTC_TIMESTAMP(3))
                    """,
                    (identity.document_version_id, generation),
                )
            else:
                if (int(current["revision"]) != expected_revision or
                        bool(current["queryEnabled"])):
                    raise RegistrySafetyError("Selection revision changed or query enabled")
                cursor.execute(
                    """
                    UPDATE ShadowIndexSelection
                    SET generationDigest = %s, revision = revision + 1,
                        queryEnabled = FALSE, updatedAt = UTC_TIMESTAMP(3)
                    WHERE documentVersionId = %s AND revision = %s
                      AND queryEnabled = FALSE
                    """,
                    (generation, identity.document_version_id, expected_revision),
                )
            if cursor.rowcount != 1:
                raise RegistrySafetyError("Concurrent selection update detected")
        _guard(lease_guard)
        connection.commit()
        return expected_revision + 1
    except BaseException:
        connection.rollback()
        raise


def revoke_selection(
    connection, identity: IndexIdentity, generation: str, *,
    expected_revision: int,
) -> int:
    """Emergency CAS rollback to NO SHADOW SELECTION.

    Revocation never needs a stale/expired worker claim. This is restricted
    to operator-only library usage, behind the explicit test-only flag.
    The version's production-v1 retrieval continues unaffected.
    """
    _enabled()
    _scope(identity, generation)
    _revision(expected_revision)
    if expected_revision == 0:
        raise RegistrySafetyError("Cannot revoke a nonexistent selection")
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE ShadowIndexSelection AS s
                JOIN DocumentVersion AS v ON v.id = s.documentVersionId
                JOIN Document AS d ON d.id = v.documentId
                JOIN KnowledgeBase AS kb ON kb.id = d.knowledgeBaseId
                SET s.generationDigest = NULL, s.queryEnabled = FALSE,
                    s.revision = s.revision + 1,
                    s.updatedAt = UTC_TIMESTAMP(3)
                WHERE s.documentVersionId = %s AND s.generationDigest = %s
                  AND s.revision = %s
                  AND d.id = %s AND kb.id = %s AND kb.organizationId = %s
                """,
                (
                    identity.document_version_id, generation, expected_revision,
                    identity.document_id, identity.knowledge_base_id,
                    identity.organization_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RegistrySafetyError("Selection changed; revocation was not applied")
        connection.commit()
        return expected_revision + 1
    except BaseException:
        connection.rollback()
        raise
