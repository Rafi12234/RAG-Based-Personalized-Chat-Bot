"""Phase 4.3C: fully scoped shadow-vector fidelity verification.

Operator-only, read-only. Separately recomputes the pinned multilingual-E5
embedding for every signed-ledger passage and compares it to Qdrant's stored
384-dimensional vector. Every source-text/section fingerprint, page locator,
tenant, version, generation, processing attempt and indexing state is checked
again during this scan. No live query, READY promotion, writes or deletes.

This is a costly offline verification gate, NOT a seconds-level search path.
A passing report is still non-transactional; SQL activation/cutover remains
disabled pending a separate retrieval implementation and race-safe protocol.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import fsum, isfinite, sqrt
from typing import Callable, Iterator
from uuid import UUID

from .document_embeddings import MODEL_NAME, VECTOR_SIZE, _normalize_vector
from .hierarchical_shadow import SHADOW_COLLECTION, inspect_shadow_collection
from .hierarchy_manifest_store import HierarchyLedger, validate_ledger
from .hierarchy_shadow_audit import (
    _validate_point, _version_filter, PAYLOAD_FIELDS,
)
from .qdrant_indexing import IndexIdentity

MAX_POINTS = 50_000
DEFAULT_BATCH = 16
MIN_COSINE = 0.999


class VectorFidelityError(RuntimeError):
    """Shadow vector or its provenance cannot be independently verified."""


@dataclass(frozen=True)
class VectorFidelityReport:
    collection: str
    generation: str
    points_verified: int
    model: str
    min_cosine: float
    vectors_recomputed: bool = True
    snapshot_is_transactional: bool = False
    query_enabled: bool = False
    authorized_to_publish: bool = False


def _embedding_input(record, text: str) -> str:
    if record.source_kind.startswith("docx_") and record.source_kind != "docx_heading":
        path = " > ".join(record.section_path)
        return "passage: " + (f"Section: {path}\n" if path else "") + text
    return "passage: " + text


def _unit(values) -> tuple[float, ...]:
    try:
        numbers = tuple(float(v) for v in values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise VectorFidelityError("Invalid Qdrant vector numbers") from exc
    if len(numbers) != VECTOR_SIZE or not all(isfinite(x) for x in numbers):
        raise VectorFidelityError("Wrong dimension or nonfinite Qdrant vector")
    norm = sqrt(fsum(x * x for x in numbers))
    if not 0.98 <= norm <= 1.02:
        raise VectorFidelityError("Stored vector is not approximately normalized")
    return tuple(x / norm for x in numbers)


def verify_shadow_vector_fidelity(
    client, identity: IndexIdentity, ledger: HierarchyLedger, processing_token: str,
    model, *, lease_guard: Callable[[], bool], batch_size: int = DEFAULT_BATCH,
    minimum_cosine: float = MIN_COSINE,
) -> VectorFidelityReport:
    if not isinstance(identity, IndexIdentity):
        raise TypeError("MySQL-derived index identity required")
    validate_ledger(ledger)
    if ledger.manifest.document_version_id != identity.document_version_id:
        raise VectorFidelityError("Ledger/document version mismatch")
    if not isinstance(processing_token, str) or not 1 <= len(processing_token) <= 64:
        raise ValueError("Valid processing token required")
    if not callable(lease_guard):
        raise TypeError("Fresh lease callback required")
    if not callable(getattr(model, "embed", None)):
        raise TypeError("Pinned embedding model required")
    if type(batch_size) is not int or not 1 <= batch_size <= 32:
        raise ValueError("Invalid bounded embedding batch size")
    if (not isinstance(minimum_cosine, (float, int)) or
            not isfinite(float(minimum_cosine)) or
            not 0.95 <= float(minimum_cosine) <= 1.0):
        raise ValueError("Invalid similarity acceptance threshold")

    def fence():
        if not lease_guard():
            raise VectorFidelityError("Processing lease revoked")

    fence()
    if not inspect_shadow_collection(client):
        raise VectorFidelityError("Shadow collection is absent")
    scope = _version_filter(identity)
    expected = {r.passage_id: r for r in ledger.records}

    def count():
        fence()
        n = client.count(
            collection_name=SHADOW_COLLECTION, count_filter=scope, exact=True
        ).count
        if type(n) is not int or not 0 <= n <= MAX_POINTS:
            raise VectorFidelityError("Shadow point count outside safety bound")
        return n

    before = count()
    if before != len(expected):
        raise VectorFidelityError("Shadow point count does not match ledger")

    seen: set[str] = set()
    seen_offsets: set[str] = set()
    offset = None
    minimum_seen = 1.0
    while True:
        fence()
        page, next_offset = client.scroll(
            collection_name=SHADOW_COLLECTION,
            scroll_filter=scope, limit=batch_size, offset=offset,
            with_payload=list(PAYLOAD_FIELDS), with_vectors=True,
        )
        if not isinstance(page, (list, tuple)) or len(page) > batch_size:
            raise VectorFidelityError("Invalid bounded vector scroll page")
        if not page and next_offset is not None:
            raise VectorFidelityError("Empty Qdrant vector page with continuation")
        docs: list[str] = []
        stored: list[tuple[float, ...]] = []
        for point in page:
            try:
                uid = str(UUID(str(point.id)))
            except (TypeError, ValueError, AttributeError) as exc:
                raise VectorFidelityError("Invalid Qdrant vector point UUID") from exc
            if str(point.id) != uid or uid in seen or uid not in expected:
                raise VectorFidelityError("Unknown, duplicated or noncanonical vector point")
            record = expected[uid]
            _validate_point(record, point.payload, identity, ledger, processing_token)
            text = point.payload["text"]
            docs.append(_embedding_input(record, text))
            stored.append(_unit(point.vector))
            seen.add(uid)
        if docs:
            fence()
            recomputed = list(model.embed(docs, batch_size=len(docs)))
            if len(recomputed) != len(docs):
                raise VectorFidelityError("Embedding model did not produce all passage vectors")
            for claimed, output in zip(stored, recomputed):
                reference = _normalize_vector(output)
                cosine = fsum(a*b for a,b in zip(claimed, reference))
                if not isfinite(cosine) or cosine < minimum_cosine:
                    raise VectorFidelityError("Stored vector differs from source embedding")
                minimum_seen = min(minimum_seen, cosine)
            fence()
        if next_offset is None:
            break
        marker = str(next_offset)
        if marker == str(offset) or marker in seen_offsets:
            raise VectorFidelityError("Qdrant vector scroll did not progress")
        seen_offsets.add(marker)
        if len(seen_offsets) > MAX_POINTS:
            raise VectorFidelityError("Qdrant vector scroll exceeded bound")
        offset = next_offset
    if len(seen) != len(expected) or count() != before:
        raise VectorFidelityError("Shadow vector coverage changed during inspection")
    fence()
    return VectorFidelityReport(SHADOW_COLLECTION, ledger.manifest.digest,
                                len(seen), MODEL_NAME, minimum_seen)
