"""Phase 4.3E: pinned candidate registration and isolated READY shadow reader.

No public endpoint, activation, Qdrant write, or change to the live v1 path.
Only new disposable test scopes, explicit opt-in, and trusted server callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
import os
import re
from typing import Callable

from .document_embeddings import MODEL_NAME, embed_query
from .hierarchical_shadow import SHADOW_COLLECTION, SHADOW_SCHEMA, STAGING, inspect_shadow_collection
from .hierarchy_generation_registry import _scope, _token, _same_report
from .hierarchy_manifest_store import HierarchyLedger, validate_ledger
from .hierarchy_shadow_audit import PAYLOAD_FIELDS, _validate_point, audit_shadow_generation
from .pinned_e5 import PinnedE5Model
from .pinned_shadow_verification import verify_pinned_shadow
from .qdrant_indexing import IndexIdentity

FLAG = "DOCMIND_ENABLE_SHADOW_READER"
HEX = re.compile(r"[0-9a-f]{64}")
ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class ShadowReadError(RuntimeError):
    """Generic fail-closed shadow access or provenance error."""


def _enabled():
    if (os.environ.get(FLAG) != "YES_FOR_TESTING" or
            os.environ.get("NODE_ENV", "").strip().lower() not in ("development", "test")):
        raise ShadowReadError("Isolated shadow reader is disabled")


def _digest(token):
    _token(token)
    return sha256(token.encode("utf-8")).hexdigest()


def _model(model):
    if type(model) is not PinnedE5Model:
        raise ShadowReadError("Pinned E5 model required")
    return model.pin.digest


def register_pinned_candidate(connection, client, identity: IndexIdentity,
                              ledger: HierarchyLedger, processing_token: str,
                              model: PinnedE5Model, *,
                              lease_guard: Callable[[], bool]) -> str:
    """Run both independent audits, then atomically insert new pinned metadata.

    Caller owns a dedicated write connection and fresh independent lease guard.
    No upgrade/overwrite of legacy candidates. No selection/READY transition.
    The audits remain nontransactional observations of Qdrant.
    """
    _enabled()
    _scope(identity, ledger.manifest.digest)
    validate_ledger(ledger)
    if ledger.manifest.document_version_id != identity.document_version_id:
        raise ShadowReadError("Ledger version mismatch")
    token_hash = _digest(processing_token)
    pin = _model(model)

    def fence():
        if not callable(lease_guard) or lease_guard() is not True:
            raise ShadowReadError("Fresh processing lease required")
        return True

    fence()
    structure = audit_shadow_generation(client, identity, ledger, processing_token,
                                        lease_guard=fence)
    receipt = verify_pinned_shadow(client, identity, ledger, processing_token,
                                   model, lease_guard=fence)
    _same_report(ledger, structure, receipt.report)
    if (receipt.artifact_pin_sha256 != pin or _model(model) != pin or
            receipt.source_sha256 != ledger.manifest.source_sha256 or
            receipt.records_sha256 != ledger.records_sha256 or
            receipt.organization_id != identity.organization_id or
            receipt.knowledge_base_id != identity.knowledge_base_id or
            receipt.document_id != identity.document_id or
            receipt.document_version_id != identity.document_version_id or
            receipt.job_id != identity.job_id or receipt.query_enabled or
            receipt.authorized_to_publish or receipt.report.model != MODEL_NAME):
        raise ShadowReadError("Pinned verification scope mismatch")
    fence()
    try:
        connection.begin()
        with connection.cursor() as cur:
            cur.execute("""
                INSERT INTO ShadowIndexCandidate
                  (documentVersionId, generationDigest, organizationId, knowledgeBaseId,
                   documentId, jobId, sourceSha256, recordsSha256, passageCount,
                   modelPinSha256, attemptTokenSha256, verifiedAttempt, verifiedAt, revokedAt)
                SELECT v.id, %s, kb.organizationId, kb.id, d.id, j.id,
                       %s, %s, %s, %s, %s, j.attempts, UTC_TIMESTAMP(3), NULL
                FROM IngestionJob j
                JOIN DocumentVersion v ON v.id = j.documentVersionId
                JOIN Document d ON d.id = v.documentId
                JOIN KnowledgeBase kb ON kb.id = d.knowledgeBaseId
                WHERE BINARY j.id = %s AND BINARY j.processingToken = %s
                  AND j.status = 'RUNNING' AND v.status = 'PROCESSING'
                  AND j.attempts > 0 AND j.processingLeaseUntil > UTC_TIMESTAMP(3)
                  AND d.deletedAt IS NULL AND BINARY v.id = %s AND BINARY d.id = %s
                  AND BINARY kb.id = %s AND BINARY kb.organizationId = %s
                  AND NOT EXISTS (SELECT 1 FROM DocumentVersion newer
                      WHERE newer.documentId = d.id AND newer.version > v.version)
                  AND NOT EXISTS (SELECT 1 FROM IngestionJob other_job
                      WHERE other_job.documentVersionId = v.id AND other_job.id <> j.id)
                """, (ledger.manifest.digest, ledger.manifest.source_sha256,
                      ledger.records_sha256, ledger.manifest.passage_count, pin, token_hash,
                      identity.job_id, processing_token, identity.document_version_id,
                      identity.document_id, identity.knowledge_base_id, identity.organization_id))
            if cur.rowcount != 1:
                raise ShadowReadError("Pinned registration claim not eligible")
        fence()
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    return ledger.manifest.digest


@dataclass(frozen=True)
class SessionScope:
    """From trusted authenticated server context, never browser JSON."""
    user_id: str
    session_id: str
    token_hash: str

    def __post_init__(self):
        if (not isinstance(self.user_id, str) or not ID.fullmatch(self.user_id) or
                not isinstance(self.session_id, str) or not ID.fullmatch(self.session_id) or
                not isinstance(self.token_hash, str) or not HEX.fullmatch(self.token_hash)):
            raise ShadowReadError("Invalid authenticated session scope")


@dataclass(frozen=True)
class ShadowEvidence:
    passage_id: str
    document_id: str
    document_version_id: str
    document_title: str
    generation: str
    page_number: int | None
    source_locator: str
    section_path: tuple[str, ...]
    excerpt: str
    score: float


# This SQL intentionally requires the disabled pointer. This is a benchmark
# reader, NOT a bypass or implementation of a production queryEnabled switch.
# Match an exact selected generation, a real unexpired session and membership,
# latest READY version, completed original attempt and non-revoked candidate.
_READ_SQL = """
    SELECT s.revision, d.title, g.passageCount, g.sourceSha256,
           g.recordsSha256, g.modelPinSha256, g.attemptTokenSha256, g.verifiedAttempt
    FROM ShadowIndexSelection s
    JOIN ShadowIndexCandidate g ON g.documentVersionId = s.documentVersionId
                                AND g.generationDigest = s.generationDigest
    JOIN DocumentVersion v ON v.id = s.documentVersionId
    JOIN Document d ON d.id = v.documentId
    JOIN KnowledgeBase kb ON kb.id = d.knowledgeBaseId
    JOIN IngestionJob j ON j.id = g.jobId AND j.documentVersionId = v.id
    JOIN OrganizationMember m ON m.organizationId = kb.organizationId
    JOIN User u ON u.id = m.userId
    JOIN AuthSession a ON a.userId = u.id
    WHERE BINARY a.id = %s AND BINARY u.id = %s AND BINARY a.tokenHash = %s
      AND a.expiresAt > UTC_TIMESTAMP(3) AND u.isActive = TRUE
      AND BINARY kb.organizationId = %s AND BINARY kb.id = %s
      AND BINARY d.id = %s AND BINARY v.id = %s AND BINARY j.id = %s
      AND BINARY g.organizationId = %s AND BINARY g.knowledgeBaseId = %s
      AND BINARY g.documentId = %s AND BINARY g.generationDigest = %s
      AND s.queryEnabled = FALSE AND s.revision > 0 AND g.revokedAt IS NULL
      AND g.modelPinSha256 IS NOT NULL AND g.attemptTokenSha256 IS NOT NULL
      AND g.verifiedAttempt = j.attempts AND j.attempts > 0
      AND j.status = 'COMPLETED' AND j.completedAt IS NOT NULL
      AND j.processingToken IS NULL AND j.processingLeaseUntil IS NULL
      AND v.status = 'READY' AND d.deletedAt IS NULL
      AND NOT EXISTS (SELECT 1 FROM DocumentVersion newer
                      WHERE newer.documentId = d.id AND newer.version > v.version)
      AND NOT EXISTS (SELECT 1 FROM IngestionJob other_job
                      WHERE other_job.documentVersionId = v.id AND other_job.id <> j.id)
    LIMIT 2
"""


def _read_snapshot(connection_factory, session, identity, ledger, pin, token_hash):
    # Each boundary uses a separate short-lived connection, not a cached
    # repeatable-read transaction. Factory must yield NEW owned connections.
    con = connection_factory()
    try:
        with con.cursor() as cur:
            cur.execute(_READ_SQL, (
                session.session_id, session.user_id, session.token_hash,
                identity.organization_id, identity.knowledge_base_id, identity.document_id,
                identity.document_version_id, identity.job_id, identity.organization_id,
                identity.knowledge_base_id, identity.document_id, ledger.manifest.digest))
            rows = cur.fetchall()
    finally:
        con.close()
    if len(rows) != 1:
        raise ShadowReadError("Shadow access or selected generation unavailable")
    row = rows[0]
    if (type(row["revision"]) is not int or row["revision"] <= 0 or
            not isinstance(row["title"], str) or not row["title"].strip() or
            len(row["title"]) > 255 or
            type(row["verifiedAttempt"]) is not int or row["verifiedAttempt"] <= 0 or
            row["passageCount"] != ledger.manifest.passage_count or
            row["sourceSha256"] != ledger.manifest.source_sha256 or
            row["recordsSha256"] != ledger.records_sha256 or
            row["modelPinSha256"] != pin or row["attemptTokenSha256"] != token_hash):
        raise ShadowReadError("Shadow provenance or selected generation unavailable")
    return (row["revision"], row["title"], row["verifiedAttempt"])


def _models():
    from qdrant_client import models
    return models


def search_verified_shadow(*, connection_factory, client, session: SessionScope,
                           identity: IndexIdentity, ledger: HierarchyLedger,
                           processing_token: str, model: PinnedE5Model,
                           question: str, top_k: int = 5) -> tuple[ShadowEvidence, ...]:
    """Search one selected disposable document, returning only verified evidence.

    Trusted caller supplies the authenticated ledger, original attempt token,
    and server-derived identity. No automatic discovery, public API or user
    traffic; source excerpts are for an isolated evaluator only.
    """
    _enabled()
    if type(session) is not SessionScope:
        raise ShadowReadError("Authenticated session required")
    _scope(identity, ledger.manifest.digest)
    validate_ledger(ledger)
    if ledger.manifest.document_version_id != identity.document_version_id:
        raise ShadowReadError("Ledger version mismatch")
    if (not isinstance(question, str) or not question.strip() or len(question) > 1000 or
            type(top_k) is not int or not 1 <= top_k <= 10):
        raise ShadowReadError("Invalid bounded search")
    pin = _model(model)
    token_hash = _digest(processing_token)

    def snapshot():
        return _read_snapshot(connection_factory, session, identity, ledger, pin, token_hash)

    before = snapshot()
    if len(model.encode("query: " + question.strip()).ids) > 480:
        raise ShadowReadError("Question exceeds token budget")
    vector = embed_query(model, question)
    if not inspect_shadow_collection(client):
        raise ShadowReadError("Shadow collection unavailable")
    # Membership/session and revision must still be valid immediately before search.
    if snapshot() != before or _model(model) != pin:
        raise ShadowReadError("Shadow authorization changed")
    m = _models()
    filters = {
        "organization_id": identity.organization_id, "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id, "document_version_id": identity.document_version_id,
        "job_id": identity.job_id, "index_generation": ledger.manifest.digest,
        "processing_token": processing_token, "index_state": STAGING,
        "index_schema_version": SHADOW_SCHEMA, "embedding_model": MODEL_NAME,
    }
    limit = min(top_k * 4, 40, ledger.manifest.passage_count)
    result = client.query_points(
        collection_name=SHADOW_COLLECTION, query=list(vector),
        query_filter=m.Filter(must=[m.FieldCondition(key=k, match=m.MatchValue(value=v))
                                   for k, v in filters.items()]),
        limit=limit, with_payload=list(PAYLOAD_FIELDS), with_vectors=False, timeout=10)
    hits = result.points
    if not isinstance(hits, (list, tuple)) or len(hits) > limit:
        raise ShadowReadError("Invalid bounded shadow response")
    records = {r.passage_id: r for r in ledger.records}
    seen = set()
    verified = []
    for hit in hits:
        point_id = str(hit.id)
        score = hit.score
        if (point_id not in records or point_id in seen or type(score) not in (int, float) or
                not isfinite(score) or not -1.00001 <= score <= 1.00001):
            raise ShadowReadError("Unexpected shadow point or score")
        record = records[point_id]
        _validate_point(record, hit.payload, identity, ledger, processing_token)
        seen.add(point_id)
        verified.append(ShadowEvidence(
            point_id, identity.document_id, identity.document_version_id, before[1],
            ledger.manifest.digest, record.page_number, record.source_locator,
            record.section_path, hit.payload["text"], max(-1.0, min(1.0, float(score)))))
    verified.sort(key=lambda item: (-item.score, item.passage_id))
    # Abort the WHOLE response on any authorization, title or revision change.
    if snapshot() != before or _model(model) != pin:
        raise ShadowReadError("Shadow authorization changed")
    return tuple(verified[:top_k])
