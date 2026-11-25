"""Phase 4.3B: read-only v2 shadow generation source-integrity audit.

This audit is intentionally NOT a publication mechanism. It scans the scoped
shadow collection (including other generations/attempts), verifies the exact
expected point ID set and payload/provenance/source-text fingerprints, and
fails closed on any extra/missing/modified point. Uses MySQL-derived scope and
a FRESH per-operation processing lease guard.

A successful result is a non-transactional inspection. It does not prove
the dense vector floats match their inputs, validate a relational READY
transaction, or approve a cutover or automatic orphan replay. No writes,
deletes, alias operations, or production-v1 collection access are made.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable
from uuid import UUID

from .document_embeddings import MODEL_NAME
from .hierarchical_shadow import (
    SHADOW_COLLECTION, SHADOW_SCHEMA, STAGING, inspect_shadow_collection,
)
from .hierarchy_manifest_store import (
    HierarchyLedger, LedgerRecord, validate_ledger,
)
from .qdrant_indexing import IndexIdentity

MAX_SCAN_POINTS = 50_000
SCROLL_BATCH_SIZE = 128
PAYLOAD_FIELDS = (
    "organization_id", "knowledge_base_id", "document_id",
    "document_version_id", "job_id", "index_generation", "index_state",
    "processing_token", "index_schema_version", "embedding_model",
    "source_sha256", "chunk_index", "parent_id", "section_id",
    "previous_in_parent", "section_path", "source_block_id",
    "source_locator", "source_kind", "page_number", "bbox",
    "source_char_start", "source_char_end", "token_count", "text",
)


class ShadowAuditError(RuntimeError):
    """Incomplete, mixed, unauthenticated or otherwise unsafe shadow index."""


@dataclass(frozen=True)
class ShadowIntegrityReport:
    collection: str
    generation: str
    points_verified: int
    text_hashes_verified: int
    structure_verified: bool = True
    vector_values_verified: bool = False
    snapshot_is_transactional: bool = False
    query_enabled: bool = False
    authorized_to_publish: bool = False
    safe_to_replay_orphans: bool = False


def _models():
    from qdrant_client import models
    return models


def _version_filter(identity: IndexIdentity):
    m = _models()
    return m.Filter(must=[
        m.FieldCondition(key=key, match=m.MatchValue(value=value))
        for key, value in (
            ("organization_id", identity.organization_id),
            ("knowledge_base_id", identity.knowledge_base_id),
            ("document_id", identity.document_id),
            ("document_version_id", identity.document_version_id),
        )
    ])


def _expected_text_hash(record: LedgerRecord, text: str) -> tuple[str, str]:
    if record.source_kind.startswith("docx_") and record.source_kind != "docx_heading":
        path = " > ".join(record.section_path)
        prefix = f"Section: {path}\n" if path else ""
    else:
        prefix = ""
    return (sha256(text.encode("utf-8")).hexdigest(),
            sha256((prefix + text).encode("utf-8")).hexdigest())


def _validate_point(record: LedgerRecord, payload: dict,
                    identity: IndexIdentity, ledger: HierarchyLedger,
                    processing_token: str) -> None:
    if type(payload) is not dict:
        raise ShadowAuditError("Shadow payload is missing")
    expected = {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
        "job_id": identity.job_id,
        "index_generation": ledger.manifest.digest,
        "index_state": STAGING,
        "processing_token": processing_token,
        "index_schema_version": SHADOW_SCHEMA,
        "embedding_model": MODEL_NAME,
        "source_sha256": ledger.manifest.source_sha256,
        "chunk_index": record.passage_index,
        "parent_id": record.parent_id,
        "section_id": record.section_id or "",
        "previous_in_parent": record.previous_in_parent,
        "section_path": list(record.section_path),
        "source_block_id": record.source_block_id,
        "source_locator": record.source_locator,
        "source_kind": record.source_kind,
        "page_number": record.page_number,
        "bbox": list(record.bbox) if record.bbox is not None else None,
        "source_char_start": record.char_start,
        "source_char_end": record.char_end,
        "token_count": record.token_count,
    }
    for name, expected_value in expected.items():
        if payload.get(name) != expected_value:
            raise ShadowAuditError(f"Shadow point metadata mismatch: {name}")
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip() or len(text) > 200_000:
        raise ShadowAuditError("Shadow point text unavailable or oversized")
    text_hash, embed_hash = _expected_text_hash(record, text)
    if (text_hash != record.text_sha256 or
            embed_hash != record.embedding_text_sha256):
        raise ShadowAuditError("Shadow passage text hash mismatch")


def audit_shadow_generation(
    client, identity: IndexIdentity, ledger: HierarchyLedger,
    processing_token: str, *, lease_guard: Callable[[], bool],
    batch_size: int = SCROLL_BATCH_SIZE,
) -> ShadowIntegrityReport:
    if not isinstance(identity, IndexIdentity):
        raise TypeError("MySQL-derived identity required")
    validate_ledger(ledger)
    if ledger.manifest.document_version_id != identity.document_version_id:
        raise ShadowAuditError("Document version does not match ledger")
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("Processing token required")
    if not callable(lease_guard):
        raise TypeError("Fresh MySQL lease guard required")
    if type(batch_size) is not int or not 1 <= batch_size <= 512:
        raise ValueError("Invalid shadow scroll page size")

    def fence() -> None:
        if not lease_guard():
            raise ShadowAuditError("Processing lease is no longer valid")

    fence()
    if not inspect_shadow_collection(client):
        raise ShadowAuditError("Shadow schema is absent or incomplete")
    scoped_filter = _version_filter(identity)
    records = {r.passage_id: r for r in ledger.records}

    def exact_count() -> int:
        fence()
        result = client.count(
            collection_name=SHADOW_COLLECTION,
            count_filter=scoped_filter, exact=True)
        count = result.count
        if type(count) is not int or not 0 <= count <= MAX_SCAN_POINTS:
            raise ShadowAuditError("Shadow version count is outside safety bound")
        return count

    count = exact_count()
    if count != len(records):
        raise ShadowAuditError("Shadow generation count mismatch or foreign points")
    seen: set[str] = set()
    seen_offsets: set[str] = set()
    offset = None
    while True:
        fence()
        page, next_offset = client.scroll(
            collection_name=SHADOW_COLLECTION,
            scroll_filter=scoped_filter,
            limit=batch_size, offset=offset,
            with_payload=list(PAYLOAD_FIELDS), with_vectors=False,
        )
        if not isinstance(page, (list, tuple)) or len(page) > batch_size:
            raise ShadowAuditError("Qdrant returned an invalid scroll page")
        if not page and next_offset is not None:
            raise ShadowAuditError("Empty Qdrant page has continuation")
        for point in page:
            try:
                uid = str(UUID(str(point.id)))
            except (ValueError, AttributeError, TypeError) as exc:
                raise ShadowAuditError("Malformed Qdrant point ID") from exc
            if str(point.id) != uid or uid in seen or uid not in records:
                raise ShadowAuditError("Unknown, noncanonical or duplicate shadow point")
            _validate_point(records[uid], point.payload, identity, ledger, processing_token)
            seen.add(uid)
        fence()
        if next_offset is None:
            break
        marker = str(next_offset)
        if marker in seen_offsets or marker == str(offset):
            raise ShadowAuditError("Shadow pagination failed to advance")
        seen_offsets.add(marker)
        if len(seen_offsets) > MAX_SCAN_POINTS:
            raise ShadowAuditError("Shadow pagination exceeds safety bound")
        offset = next_offset
    if len(seen) != len(records) or exact_count() != count:
        raise ShadowAuditError("Shadow passage coverage changed or is incomplete")
    fence()
    return ShadowIntegrityReport(
        collection=SHADOW_COLLECTION, generation=ledger.manifest.digest,
        points_verified=count, text_hashes_verified=count)
