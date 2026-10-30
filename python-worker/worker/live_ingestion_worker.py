"""Phase 2.9E: explicitly gated single-job pilot ingestion.

No automatic dispatcher. The caller must opt in, and a dedicated Celery worker
must import worker.live_ingestion_task explicitly. Only suitable for a NEW test
job whose document version has zero production Qdrant points.

Cross-store atomicity does not exist: user-facing retrieval MUST check MySQL
DocumentVersion READY as well as tenant authorization and Qdrant index_state.
Orphan staging/ready points are NOT deleted automatically; use the audit.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

PILOT_FLAG = "DOCMIND_ENABLE_PILOT_INGESTION"
PILOT_VALUE = "YES_FOR_TESTING"
MAX_PILOT_CHUNKS = 50_000
HEARTBEAT_INTERVAL_SECONDS = 30
LEASE_SECONDS = 900


def _validate_job_id(job_id: str) -> None:
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError("Ingestion job ID is required")


def _renew_owned_lease(job_id: str, token: str) -> bool:
    """Renew on a NEW MySQL connection with a committed transaction."""
    from .db import get_connection
    from .ingestion_lifecycle import renew_processing_lease
    conn = get_connection()
    try:
        conn.begin()
        if not renew_processing_lease(conn, job_id, token, lease_seconds=LEASE_SECONDS):
            conn.rollback()
            return False
        conn.commit()
        return True
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


class LeaseHeartbeat:
    """Keep the processing lease valid during slow downloads, OCR and inference.

    The daemon never uses the main thread's MySQL connection. If any renewal
    fails, subsequent guard checks fail closed. Stop/join before DB completion.
    """
    def __init__(self, job_id: str, token: str,
                 *, interval_seconds: int = HEARTBEAT_INTERVAL_SECONDS):
        if type(interval_seconds) is not int or interval_seconds < 1:
            raise ValueError("Invalid heartbeat interval")
        self.job_id = job_id
        self.token = token
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._failed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Heartbeat already started")
        self._thread = threading.Thread(target=self._loop, name="docmind-ingestion-lease", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                if not _renew_owned_lease(self.job_id, self.token):
                    self._failed.set()
                    return
            except Exception:
                self._failed.set()
                return

    def check(self) -> bool:
        if self._failed.is_set():
            return False
        from .ingestion_lease_verification import check_current_processing_lease
        return check_current_processing_lease(self.job_id, self.token)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            # DB connect/read/write timeouts are bounded by the connection helper.
            self._thread.join(timeout=60)
            if self._thread.is_alive():
                self._failed.set()
                raise RuntimeError("Heartbeat thread did not stop")


def _count_all_version_points(client, identity) -> int:
    """Catch even malformed/unexpected index_state values before UUID reuse.

    Version scoped to org, KB and document; no Qdrant mutations.
    """
    from qdrant_client import models
    scope = {
        "organization_id": identity.organization_id,
        "knowledge_base_id": identity.knowledge_base_id,
        "document_id": identity.document_id,
        "document_version_id": identity.document_version_id,
    }
    condition = models.Filter(must=[
        models.FieldCondition(key=k, match=models.MatchValue(value=v))
        for k,v in scope.items()
    ])
    return int(client.count(collection_name="docmind_documents_e5_384_v1",
                            count_filter=condition, exact=True).count)


def _inspect_prerequisites(job_id: str):
    """No writes. Return identity + source metadata, refuse orphaned vectors."""
    from .ingestion_lease_verification import read_job_state
    from .production_qdrant import local_client, inspect_collection
    from .publication_audit import audit_publication_counts
    from .storage_extraction_probe import read_job_metadata
    from .storage_qdrant_probe import identity_for_job
    state = read_job_state(job_id)
    if state is None or not state.ready_for_claim:
        raise RuntimeError("Job is not eligible for a pilot claim")
    identity = identity_for_job(job_id)
    metadata = read_job_metadata(job_id)
    if metadata["filename"] != identity.filename:
        raise RuntimeError("Document metadata changed")
    client = local_client()
    try:
        schema = inspect_collection(client)
        if not schema.exists or not schema.compatible or schema.missing_indexes:
            raise RuntimeError("Qdrant production collection not initialized")
        counts = audit_publication_counts(client, identity)
        if counts.staged or counts.ready or _count_all_version_points(client, identity):
            raise RuntimeError("Existing production vectors require manual recovery audit")
    finally:
        client.close()
    return identity, metadata


def preview_pilot(job_id: str) -> dict:
    """Read-only eligibility check; does not enqueue or claim anything."""
    _validate_job_id(job_id)
    identity, metadata = _inspect_prerequisites(job_id)
    return {
        "job_id": job_id,
        "filename": identity.filename,
        "job_status": metadata["jobStatus"],
        "document_status": metadata["versionStatus"],
        "production_collection_clean_for_version": True,
        "eligible_for_explicit_pilot": True,
        "mysql_modified": False,
        "qdrant_written": False,
        "task_submitted": False,
        "automatic_dispatcher_enabled": False,
    }


def _mark_failed_if_owned(job_id: str, token: str, failure_class: str) -> bool:
    """Sanitized error only. Orphan points are deliberately left for audit."""
    from .db import get_connection
    from .ingestion_lifecycle import record_processing_failure
    from .ingestion_lease_verification import check_current_processing_lease
    if not check_current_processing_lease(job_id, token):
        return False
    conn = get_connection()
    try:
        conn.begin()
        changed = record_processing_failure(
            conn, job_id, token, f"Pilot ingestion failed: {failure_class}",
        )
        if changed:
            conn.commit()
        else:
            conn.rollback()
        return changed
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _record_ids(embeddings, identity, heartbeat: LeaseHeartbeat, ids: list[str],
                observed: set[str]):
    """Buffer UUIDs only, not text or vectors; preserve strict chunk ordering."""
    from .document_embeddings import VECTOR_SIZE
    from .token_safe_chunking import SAFE_TOKEN_BUDGET, _count_tokens
    from .storage_qdrant_probe import _tokenizer
    tokenizer = _tokenizer()
    for item in embeddings:
        if not heartbeat.check():
            raise RuntimeError("Processing lease lost during embedding")
        chunk = item.chunk
        if chunk.document_version_id != identity.document_version_id or chunk.chunk_index != len(ids):
            raise RuntimeError("Document chunk scope/order changed")
        try:
            canonical = str(UUID(chunk.chunk_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise RuntimeError("Malformed chunk UUID") from exc
        if canonical != chunk.chunk_id or canonical in observed:
            raise RuntimeError("Duplicate/noncanonical chunk UUID")
        if len(item.vector) != VECTOR_SIZE or _count_tokens(tokenizer, chunk.text) > SAFE_TOKEN_BUDGET:
            raise RuntimeError("Embedding dimensions or token budget invalid")
        if len(ids) >= MAX_PILOT_CHUNKS:
            raise RuntimeError("Pilot chunk limit exceeded")
        ids.append(canonical)
        observed.add(canonical)
        yield item


def run_pilot_ingestion(job_id: str, *, language: str = "eng", max_ocr_pages: int = 10) -> dict:
    """Claim ONE eligible job and process through publication and DB completion.

    Actual MySQL and Qdrant mutations occur. Only enable for a NEW disposable
    test document in an isolated local environment. Never invoke for a real
    customer's production data at this stage.
    """
    _validate_job_id(job_id)
    if os.environ.get(PILOT_FLAG) != PILOT_VALUE:
        raise RuntimeError("Pilot ingestion is disabled")
    if type(max_ocr_pages) is not int or not 1 <= max_ocr_pages <= 10:
        raise ValueError("Pilot OCR pages must be between 1 and 10")
    from .ingestion_claim import claim_ingestion_job
    from .ingestion_lease_verification import make_fresh_lease_guard
    from .document_chunking import iter_document_chunks
    from .document_extraction import iter_extracted_segments
    from .document_ocr import iter_pdf_segments_with_ocr
    from .document_embeddings import iter_chunk_embeddings, load_embedding_model
    from .production_qdrant import local_client, inspect_collection
    from .production_index_staging import stage_batches
    from .staged_vector_verification import verify_staged_vector_ids
    from .ready_vector_verification import verify_ready_vector_ids
    from .vector_publication import publish_verified_staging
    from .ingestion_completion import complete_after_publication
    from .storage_extraction_probe import _s3_client_and_bucket, download_into
    from .storage_qdrant_probe import _tokenizer
    from .token_safe_chunking import iter_token_safe_chunks
    from .db import get_connection

    identity, metadata = _inspect_prerequisites(job_id)
    token = claim_ingestion_job(job_id)
    if token is None:
        return {"job_id": job_id, "result": "not_claimed", "automatic_dispatcher_enabled": False}
    heartbeat = LeaseHeartbeat(job_id, token)
    heartbeat.start()
    ids: list[str] = []
    observed: set[str] = set()
    finished = False
    qdrant = None
    s3 = None
    try:
        guard = make_fresh_lease_guard(job_id, token)
        if not heartbeat.check():
            raise RuntimeError("Processing lease unavailable after claim")
        qdrant = local_client()
        schema = inspect_collection(qdrant)
        if not schema.exists or not schema.compatible or schema.missing_indexes:
            raise RuntimeError("Production collection changed")
        from .publication_audit import audit_publication_counts
        existing = audit_publication_counts(qdrant, identity)
        if existing.staged or existing.ready or _count_all_version_points(qdrant, identity):
            raise RuntimeError("Foreign/stale production vectors detected after claim")
        s3, bucket = _s3_client_and_bucket()
        with TemporaryDirectory(prefix="docmind-live-pilot-") as tmp:
            ext = Path(identity.filename).suffix.lower()
            local_file = Path(tmp) / ("source" + ext)
            download_into(s3, bucket, metadata["storageKey"], local_file,
                          int(metadata["sizeBytes"]))
            if not heartbeat.check():
                raise RuntimeError("Processing lease lost after document download")
            if ext == ".pdf":
                segments = iter_pdf_segments_with_ocr(
                    local_file, language=language, max_ocr_pages=max_ocr_pages,
                )
            else:
                segments = iter_extracted_segments(local_file, identity.filename)
            tokenizer = _tokenizer()
            raw_chunks = iter_document_chunks(segments, identity.document_version_id)
            chunks = iter_token_safe_chunks(raw_chunks, tokenizer)
            model = load_embedding_model()
            vectors = iter_chunk_embeddings(chunks, model)
            checked_vectors = _record_ids(vectors, identity, heartbeat, ids, observed)
            staged = stage_batches(qdrant, checked_vectors, identity, token,
                                   lease_guard=guard)
        if not ids or staged != len(ids):
            raise RuntimeError("No vectors or staged count mismatch")
        verified_staged = verify_staged_vector_ids(qdrant, identity, token, ids,
                                                    lease_guard=guard)
        if verified_staged != len(ids):
            raise RuntimeError("Staging verification failed")
        published = publish_verified_staging(qdrant, identity, token, ids,
                                              lease_guard=guard)
        if published != len(ids):
            raise RuntimeError("Publication count mismatch")
        ready_count = verify_ready_vector_ids(qdrant, identity, token, ids,
                                               lease_guard=guard)
        if ready_count != len(ids):
            raise RuntimeError("Ready-vector verification mismatch")
        heartbeat.stop()
        if not guard():
            raise RuntimeError("Lease lost before MySQL completion")
        conn = get_connection()
        try:
            completed = complete_after_publication(
                conn, job_id, token,
                published_vectors_verified=lambda: verify_ready_vector_ids(
                    qdrant, identity, token, ids, lease_guard=guard
                ), expected_count=len(ids),
            )
        finally:
            conn.close()
        finished = True
        return {"job_id": job_id, "result": "completed", "job_status": "COMPLETED",
                "document_status": "READY", "vectors_verified": completed,
                "collection": "docmind_documents_e5_384_v1",
                "automatic_dispatcher_enabled": False}
    except Exception as exc:
        # Do not reveal raw exceptions (they may contain credentials/storage keys).
        if not finished:
            try:
                heartbeat.stop()
            except Exception:
                pass
            try:
                _mark_failed_if_owned(job_id, token, type(exc).__name__)
            except Exception:
                pass  # Lease recovery will handle abandoned ownership.
        raise RuntimeError("Pilot processing failed; inspect job status and publication audit") from None
    finally:
        try:
            heartbeat.stop()
        except Exception:
            pass
        if s3 is not None:
            s3.close()
        if qdrant is not None:
            qdrant.close()
