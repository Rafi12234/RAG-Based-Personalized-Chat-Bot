"""Phase 4.3B: immutable, encrypted hierarchy manifests and source locator ledger.

Pure ledger builder + separately opt-in S3/RustFS store. No MySQL/Qdrant
writes, READY transition, alias swap or live application integrations.

One ledger record per child passage has source offsets, page/section/table
ancestry and SHA-256 digests of original and embedding input. The ledger has
NO raw document text. An AES-256-GCM encrypted, create-only object contains
the complete versioned ledger and signed-in-place original index manifest.
Its associated data binds the tenant, KB, document, version, job, original
source digest, model and exact index generation. Storage remains private.

The caller MUST supply a fresh MySQL processing-lease guard and an identity
resolved from the database, not from user input. The S3 backend MUST support
PutObject(IfNoneMatch="*") atomically, else writes fail closed. This module
does not garbage-collect, list or delete any object.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from hashlib import sha256
from io import BytesIO
from math import isfinite
from typing import Callable, Iterable
import json
import os
import re

from .document_embeddings import MODEL_NAME
from .hierarchical_index import (
    HierarchicalPassage, HierarchyManifest, HierarchyIntegrityError,
    MANIFEST_SCHEMA, MAX_PASSAGES, _canonical,
)
from .qdrant_indexing import IndexIdentity

LEDGER_SCHEMA = "docmind-hierarchy-ledger-v1"
MAGIC = b"DOCMIND-HIERARCHY-LEDGER-V1\x00"
NONCE_SIZE = 12
TAG_SIZE = 16
MAX_PLAINTEXT_BYTES = 48 * 1024 * 1024
MAX_OBJECT_BYTES = MAX_PLAINTEXT_BYTES + len(MAGIC) + NONCE_SIZE + TAG_SIZE
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
HEX_SHA = re.compile(r"^[0-9a-f]{64}$")
UUID_CANONICAL = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


@dataclass(frozen=True)
class LedgerRecord:
    passage_id: str
    passage_index: int
    source_block_id: str
    source_locator: str
    source_kind: str
    parent_id: str
    section_id: str | None
    previous_in_parent: str | None
    section_path: tuple[str, ...]
    page_number: int | None
    bbox: tuple[float, float, float, float] | None
    char_start: int
    char_end: int
    token_count: int
    text_sha256: str
    embedding_text_sha256: str


@dataclass(frozen=True)
class HierarchyLedger:
    schema: str
    manifest: HierarchyManifest
    records: tuple[LedgerRecord, ...]
    records_sha256: str

    def public_metadata(self) -> dict:
        """Never emit private text, source locators or per-passage hashes."""
        return {
            "schema": self.schema,
            "index_generation": self.manifest.digest,
            "document_version_id": self.manifest.document_version_id,
            "record_count": len(self.records),
            "records_sha256": self.records_sha256,
            "query_enabled": False,
            "publication_authorized": False,
        }


def _record(p: HierarchicalPassage) -> LedgerRecord:
    if not isinstance(p, HierarchicalPassage):
        raise HierarchyIntegrityError("Expected hierarchy passage")
    return LedgerRecord(
        passage_id=p.passage_id, passage_index=p.passage_index,
        source_block_id=p.source_block_id, source_locator=p.source_locator,
        source_kind=p.source_kind, parent_id=p.parent_id,
        section_id=p.section_id, previous_in_parent=p.previous_in_parent,
        section_path=p.section_path, page_number=p.page_number, bbox=p.bbox,
        char_start=p.char_start, char_end=p.char_end, token_count=p.token_count,
        text_sha256=sha256(p.text.encode("utf-8")).hexdigest(),
        embedding_text_sha256=sha256(p.embedding_text.encode("utf-8")).hexdigest(),
    )


class HierarchyLedgerBuilder:
    """Collect only small provenance records while passages stream to embedder."""
    def __init__(self) -> None:
        self._records: list[LedgerRecord] = []
        self._sealed = False

    def add_passage(self, passage: HierarchicalPassage) -> None:
        if self._sealed:
            raise HierarchyIntegrityError("Ledger already sealed")
        if len(self._records) >= MAX_PASSAGES:
            raise HierarchyIntegrityError("Too many ledger passages")
        if passage.passage_index != len(self._records):
            raise HierarchyIntegrityError("Ledger passage index is not contiguous")
        self._records.append(_record(passage))

    def finalize(self, manifest: HierarchyManifest) -> HierarchyLedger:
        if self._sealed:
            raise HierarchyIntegrityError("Ledger finalized twice")
        ledger = HierarchyLedger(
            schema=LEDGER_SCHEMA,
            manifest=manifest,
            records=tuple(self._records),
            records_sha256=sha256(_canonical([asdict(x) for x in self._records])).hexdigest(),
        )
        validate_ledger(ledger)
        self._sealed = True
        return ledger


def validate_ledger(ledger: HierarchyLedger) -> None:
    """Recalculate original v2 manifest digest from immutable locator records.

    Authentication of the complete serialized ledger is handled separately
    by AES-GCM. This check ties record ordering/parents/offsets to the
    original HierarchyManifest rather than trusting stored claimed counts.
    """
    if not isinstance(ledger, HierarchyLedger) or ledger.schema != LEDGER_SCHEMA:
        raise HierarchyIntegrityError("Incompatible hierarchy ledger")
    m = ledger.manifest
    if (not isinstance(m, HierarchyManifest) or m.schema != MANIFEST_SCHEMA or
            m.embedding_model != MODEL_NAME or
            not isinstance(m.document_version_id, str) or
            not 1 <= len(m.document_version_id) <= 128 or
            not isinstance(m.source_sha256, str) or
            not HEX_SHA.fullmatch(m.source_sha256) or
            not isinstance(m.digest, str) or not HEX_SHA.fullmatch(m.digest) or
            type(m.passage_count) is not int or
            not 1 <= m.passage_count <= MAX_PASSAGES or
            type(m.block_count) is not int or type(m.parent_count) is not int or
            type(m.accounted_pages) is not int):
        raise HierarchyIntegrityError("Invalid sealed hierarchy manifest")
    if m.expected_pages is not None:
        if (type(m.expected_pages) is not int or
                not 1 <= m.expected_pages <= 5000 or
                m.accounted_pages != m.expected_pages):
            raise HierarchyIntegrityError("Incomplete PDF page accounting")
    elif m.accounted_pages != 0:
        raise HierarchyIntegrityError("DOCX manifest cannot claim PDF page accounting")
    if (type(m.point_ids) is not tuple or type(ledger.records) is not tuple or
            len(ledger.records) != m.passage_count or
            len(m.point_ids) != m.passage_count):
        raise HierarchyIntegrityError("Manifest/ledger passage counts mismatch")
    chain = sha256()
    blocks: set[str] = set()
    parents: set[str] = set()
    seen_ids: set[str] = set()
    last_in_parent: dict[str, str] = {}
    for index, record in enumerate(ledger.records):
        if not isinstance(record, LedgerRecord):
            raise HierarchyIntegrityError("Unexpected ledger record")
        if (type(record.passage_index) is not int or record.passage_index != index or
                not isinstance(record.passage_id, str) or
                not UUID_CANONICAL.fullmatch(record.passage_id) or
                record.passage_id != m.point_ids[index] or
                record.passage_id in seen_ids or
                not isinstance(record.source_block_id, str) or not record.source_block_id or
                not isinstance(record.source_locator, str) or not record.source_locator or
                len(record.source_locator) > 256 or
                not isinstance(record.parent_id, str) or
                not UUID_CANONICAL.fullmatch(record.parent_id) or
                (record.section_id is not None and
                    (not isinstance(record.section_id, str) or
                     not UUID_CANONICAL.fullmatch(record.section_id))) or
                record.previous_in_parent != last_in_parent.get(record.parent_id) or
                type(record.section_path) is not tuple or
                len(record.section_path) > 9 or
                any(not isinstance(x, str) or not x or len(x) > 320
                    for x in record.section_path) or
                type(record.char_start) is not int or
                type(record.char_end) is not int or
                record.char_start < 0 or record.char_end <= record.char_start or
                type(record.token_count) is not int or
                not 1 <= record.token_count <= 480 or
                not isinstance(record.text_sha256, str) or
                not HEX_SHA.fullmatch(record.text_sha256) or
                not isinstance(record.embedding_text_sha256, str) or
                not HEX_SHA.fullmatch(record.embedding_text_sha256)):
            raise HierarchyIntegrityError("Invalid or noncontiguous ledger passage")
        if record.page_number is not None:
            if (m.expected_pages is None or type(record.page_number) is not int or
                    not 1 <= record.page_number <= m.expected_pages or
                    not record.source_kind.startswith("pdf_")):
                raise HierarchyIntegrityError("Invalid PDF source locator")
        elif record.source_kind.startswith("pdf_"):
            raise HierarchyIntegrityError("Missing PDF page number")
        if record.bbox is not None and (
                type(record.bbox) is not tuple or len(record.bbox) != 4 or
                any(not isinstance(v, (float, int)) or not isfinite(v) for v in record.bbox)):
            raise HierarchyIntegrityError("Invalid source bounding box")
        blocks.add(record.source_block_id)
        parents.add(record.parent_id)
        seen_ids.add(record.passage_id)
        last_in_parent[record.parent_id] = record.passage_id
        chain.update(_canonical({
            "id": record.passage_id, "parent": record.parent_id,
            "section": record.section_id, "source": record.source_locator,
            "page": record.page_number, "start": record.char_start,
            "end": record.char_end,
        }) + b"\n")
    if len(blocks) != m.block_count or len(parents) != m.parent_count:
        raise HierarchyIntegrityError("Hierarchy block/parent counts mismatch")
    base = {
        "schema": m.schema, "version": m.document_version_id,
        "source_sha256": m.source_sha256, "model": m.embedding_model,
        "expected_pages": m.expected_pages, "accounted_pages": m.accounted_pages,
        "passages": m.passage_count, "blocks": m.block_count,
        "parents": m.parent_count, "passage_chain_sha256": chain.hexdigest(),
    }
    if sha256(_canonical(base)).hexdigest() != m.digest:
        raise HierarchyIntegrityError("Hierarchy manifest digest mismatch")
    record_digest = sha256(_canonical([asdict(x) for x in ledger.records])).hexdigest()
    if ledger.records_sha256 != record_digest:
        raise HierarchyIntegrityError("Ledger record checksum mismatch")


def _dataclass_from_dict(cls, value: dict, *, transform=None):
    if type(value) is not dict or set(value) != {f.name for f in fields(cls)}:
        raise HierarchyIntegrityError("Unexpected ledger/manifest JSON schema")
    data = dict(value)
    if transform is not None:
        transform(data)
    return cls(**data)


def _decode_ledger(raw: bytes) -> HierarchyLedger:
    if len(raw) > MAX_PLAINTEXT_BYTES:
        raise HierarchyIntegrityError("Decrypted ledger exceeds size budget")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, TypeError) as exc:
        raise HierarchyIntegrityError("Invalid hierarchy ledger JSON") from exc
    if type(value) is not dict or set(value) != {"schema", "manifest", "records", "records_sha256"}:
        raise HierarchyIntegrityError("Invalid hierarchy ledger envelope")
    manifest = _dataclass_from_dict(HierarchyManifest, value["manifest"],
                                    transform=lambda d: d.update(
                                        point_ids=tuple(d["point_ids"])))
    if type(value["records"]) is not list or not 1 <= len(value["records"]) <= MAX_PASSAGES:
        raise HierarchyIntegrityError("Invalid ledger record list")
    records = []
    for item in value["records"]:
        records.append(_dataclass_from_dict(
            LedgerRecord, item, transform=lambda d: d.update(
                section_path=tuple(d["section_path"]),
                bbox=tuple(d["bbox"]) if d["bbox"] is not None else None)))
    ledger = HierarchyLedger(value["schema"], manifest, tuple(records),
                             value["records_sha256"])
    validate_ledger(ledger)
    return ledger


class EncryptedHierarchyManifestStore:
    """Lease-fenced RustFS persistence for an exact single shadow generation."""

    def __init__(self, client, bucket: str, identity: IndexIdentity,
                 manifest: HierarchyManifest, key: bytes, *,
                 lease_guard: Callable[[], bool]):
        if (client is None or not callable(getattr(client, "get_object", None))
                or not callable(getattr(client, "put_object", None))):
            raise TypeError("S3 client with get_object/put_object required")
        if not isinstance(bucket, str) or not SAFE_ID.fullmatch(bucket):
            raise ValueError("Invalid private manifest bucket")
        if not isinstance(identity, IndexIdentity):
            raise TypeError("MySQL-derived document identity required")
        for attr in ("organization_id", "knowledge_base_id", "document_id",
                     "document_version_id", "job_id"):
            value = getattr(identity, attr)
            if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
                raise ValueError("Unsafe manifest scope identifier")
        if (not isinstance(manifest, HierarchyManifest) or
                manifest.document_version_id != identity.document_version_id or
                not HEX_SHA.fullmatch(manifest.digest) or
                not HEX_SHA.fullmatch(manifest.source_sha256)):
            raise ValueError("Invalid manifest generation or version")
        if not isinstance(key, bytes) or len(key) != 32:
            raise ValueError("32-byte AES-256-GCM manifest key required")
        if not callable(lease_guard):
            raise TypeError("Fresh MySQL processing lease guard required")
        self.client = client
        self.bucket = bucket
        self.identity = identity
        self.manifest = manifest
        self.key = key
        self.lease_guard = lease_guard
        self.object_key = (
            "docmind-private-hierarchy/v1/"
            f"{identity.organization_id}/{identity.knowledge_base_id}/"
            f"{identity.document_id}/{identity.document_version_id}/"
            f"{identity.job_id}/{manifest.digest}.aesgcm"
        )

    def _aad(self) -> bytes:
        i, m = self.identity, self.manifest
        return _canonical({
            "schema": LEDGER_SCHEMA,
            "org": i.organization_id, "kb": i.knowledge_base_id,
            "document": i.document_id, "version": i.document_version_id,
            "job": i.job_id, "source_sha256": m.source_sha256,
            "generation": m.digest, "model": m.embedding_model,
        })

    def _check_lease(self) -> None:
        if not self.lease_guard():
            raise RuntimeError("Document processing lease is not current")

    def read(self) -> HierarchyLedger | None:
        """No missing-object fallback on authorization/network failures."""
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._check_lease()
        try:
            response = self.client.get_object(
                Bucket=self.bucket, Key=self.object_key)
        except Exception as exc:
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code == "NoSuchKey":
                return None
            raise
        body = response.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            raise HierarchyIntegrityError("Encrypted manifest missing response stream")
        try:
            if (response.get("ContentLength") is not None and
                    int(response["ContentLength"]) > MAX_OBJECT_BYTES):
                raise HierarchyIntegrityError("Encrypted manifest exceeds size limit")
            content = body.read(MAX_OBJECT_BYTES + 1)
        finally:
            body.close()
        if (not isinstance(content, bytes) or len(content) > MAX_OBJECT_BYTES or
                not content.startswith(MAGIC) or
                len(content) < len(MAGIC) + NONCE_SIZE + TAG_SIZE):
            raise HierarchyIntegrityError("Invalid encrypted hierarchy manifest")
        nonce = content[len(MAGIC):len(MAGIC) + NONCE_SIZE]
        ciphertext = content[len(MAGIC) + NONCE_SIZE:]
        try:
            plaintext = AESGCM(self.key).decrypt(nonce, ciphertext, self._aad())
        except (InvalidTag, ValueError) as exc:
            raise HierarchyIntegrityError("Manifest authentication failed") from exc
        ledger = _decode_ledger(plaintext)
        if ledger.manifest != self.manifest:
            raise HierarchyIntegrityError("Stored manifest doesn't match expected generation")
        self._check_lease()
        return ledger

    def write(self, ledger: HierarchyLedger) -> None:
        """Create once; never overwrite existing, including invalid/corrupt."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self._check_lease()
        validate_ledger(ledger)
        if ledger.manifest != self.manifest:
            raise HierarchyIntegrityError("Ledger doesn't match expected generation")
        if self.read() is not None:
            raise HierarchyIntegrityError("Encrypted generation manifest already exists")
        plaintext = _canonical(asdict(ledger))
        if len(plaintext) > MAX_PLAINTEXT_BYTES:
            raise HierarchyIntegrityError("Hierarchy ledger exceeds size limit")
        nonce = os.urandom(NONCE_SIZE)
        value = MAGIC + nonce + AESGCM(self.key).encrypt(
            nonce, plaintext, self._aad())
        self._check_lease()
        self.client.put_object(
            Bucket=self.bucket, Key=self.object_key, Body=value,
            ContentType="application/octet-stream",
            IfNoneMatch="*",
        )
        self._check_lease()
        stored = self.read()
        if stored != ledger:
            raise HierarchyIntegrityError("Persisted manifest read-back mismatch")
