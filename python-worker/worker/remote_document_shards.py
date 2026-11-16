"""Phase 4.2B: encrypted, cross-worker S3/RustFS PDF extraction checkpoints.

This store ONLY persists complete page-range extraction output. It does NOT
write to MySQL/Qdrant, authorize a job, finalize READY, or repair vector
publication. The caller must own a freshly validated lease and authorize the
exact document version before use.

On-disk/object-store data contains customer text; every object is encrypted
client-side using AES-256-GCM, random nonces and scope-bound authenticated
associated data. Storage must be private and credentials/key managed outside
the repository. No general listing or deletion operations are performed.

Requires an S3 implementation supporting atomic conditional PutObject with
IfNoneMatch="*"; fail closed if it does not. Never use the same object key for
another tenant, document version, source hash, or OCR profile.
"""
from __future__ import annotations

import base64
import binascii
import json
import os
import re
from hashlib import sha256
from typing import Callable, Iterable
from dataclasses import dataclass

from .large_document_shards import (
    DEFAULT_SHARD_PAGES, MAX_CHECKPOINT_BYTES, MAX_PAGE_CHARS,
    PageRecord, ShardRange, ShardIntegrityError, _check_record, shard_ranges,
)

MAGIC = b"DMCP-AES256GCM-V1\x00"
NONCE_BYTES = 12
TAG_BYTES = 16
MAX_OBJECT_BYTES = MAX_CHECKPOINT_BYTES + len(MAGIC) + NONCE_BYTES + TAG_BYTES
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
SAFE_BUCKET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,251}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")


def decode_checkpoint_key(encoded: str) -> bytes:
    """Require exactly 32 random bytes; never default to a development key."""
    if not isinstance(encoded, str):
        raise ValueError("Missing encrypted checkpoint key")
    try:
        key = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Checkpoint key must be base64") from exc
    if len(key) != 32:
        raise ValueError("Checkpoint encryption key must be 256 bits")
    return key


def _canonical(data: dict) -> bytes:
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class CheckpointScope:
    organization_id: str
    knowledge_base_id: str
    document_id: str
    document_version_id: str
    source_sha256: str
    extraction_profile: str

    def validate(self) -> None:
        for value in (self.organization_id, self.knowledge_base_id,
                      self.document_id, self.document_version_id):
            if not isinstance(value, str) or not SAFE_ID.fullmatch(value):
                raise ValueError("Invalid checkpoint scope identifier")
        if not isinstance(self.source_sha256, str) or not DIGEST.fullmatch(self.source_sha256):
            raise ValueError("Invalid source checksum")
        if (not isinstance(self.extraction_profile, str) or
                not 3 <= len(self.extraction_profile) <= 160 or
                any(ord(c) < 33 or ord(c) > 126 for c in self.extraction_profile)):
            raise ValueError("Invalid extraction profile")


class EncryptedS3ShardStore:
    """Compatible with iter_checkpointed_pages()'s .ranges/.read/.write API.

    The caller supplies an authenticated S3 client and bucket. No networking
    occurs during construction. Every write is immutable and conditional;
    corrupted existing content raises rather than being overwritten.
    """

    def __init__(self, client, bucket: str, scope: CheckpointScope,
                 encryption_key: bytes, page_count: int, *,
                 lease_guard: Callable[[], bool],
                 shard_pages: int = DEFAULT_SHARD_PAGES):
        if client is None or not all(callable(getattr(client, x, None))
                                      for x in ("get_object", "put_object")):
            raise TypeError("S3 client must support get_object and put_object")
        if not isinstance(bucket, str) or not SAFE_BUCKET.fullmatch(bucket):
            raise ValueError("Invalid checkpoint bucket")
        if not isinstance(scope, CheckpointScope):
            raise TypeError("CheckpointScope required")
        scope.validate()
        if not isinstance(encryption_key, bytes) or len(encryption_key) != 32:
            raise ValueError("A 32-byte AES encryption key is required")
        if not callable(lease_guard):
            raise TypeError("Fresh MySQL processing lease guard required")
        self.lease_guard = lease_guard
        self.client = client
        self.bucket = bucket
        self.scope = scope
        self.key = encryption_key
        self.ranges = tuple(shard_ranges(page_count, shard_pages=shard_pages))
        self.page_count = page_count
        self.shard_pages = shard_pages
        self.profile_hash = sha256(scope.extraction_profile.encode("ascii")).hexdigest()

    def _check_lease(self) -> None:
        # Never cache a successful result: caller must verify MySQL status,
        # current processing token, document ACTIVE and live lease afresh.
        if not self.lease_guard():
            raise RuntimeError("Processing lease is not current")

    def _metadata(self, shard: ShardRange) -> dict:
        if shard not in self.ranges:
            raise ValueError("Unknown page shard")
        s = self.scope
        return {
            "schema": "docmind-page-shard-v2",
            "organization_id": s.organization_id,
            "knowledge_base_id": s.knowledge_base_id,
            "document_id": s.document_id,
            "document_version_id": s.document_version_id,
            "source_sha256": s.source_sha256,
            "extraction_profile": s.extraction_profile,
            "page_count": self.page_count,
            "shard_pages": self.shard_pages,
            "first_page": shard.first,
            "last_page": shard.last,
        }

    def _key(self, shard: ShardRange) -> str:
        self._metadata(shard)  # validate range
        s = self.scope
        return (f"docmind-private-checkpoints/v2/{s.organization_id}/"
                f"{s.knowledge_base_id}/{s.document_id}/{s.document_version_id}/"
                f"{s.source_sha256}/{self.profile_hash}/"
                f"{shard.first:05d}-{shard.last:05d}.aesgcm")

    def _encode(self, shard: ShardRange, pages: Iterable[PageRecord]) -> bytes:
        contents: list[dict] = []
        text_size = 0
        for index, page in enumerate(pages, start=shard.first):
            if index > shard.last:
                raise ShardIntegrityError("Too many pages in shard")
            _check_record(page, index)
            raw = page.text.encode("utf-8")
            text_size += len(raw)
            if text_size > MAX_CHECKPOINT_BYTES // 2:
                raise ShardIntegrityError("Shard text exceeds size budget")
            contents.append({"page": page.page_number, "text": page.text,
                             "method": page.method, "sha256": sha256(raw).hexdigest()})
        if len(contents) != shard.last - shard.first + 1:
            raise ShardIntegrityError("Incomplete page shard")
        payload = {"scope": self._metadata(shard), "pages": contents}
        data = _canonical({**payload, "checksum": sha256(_canonical(payload)).hexdigest()})
        if len(data) > MAX_CHECKPOINT_BYTES:
            raise ShardIntegrityError("Checkpoint exceeds size budget")
        return data

    def _decode(self, shard: ShardRange, raw: bytes) -> tuple[PageRecord, ...]:
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise ShardIntegrityError("Oversized decrypted checkpoint")
        try:
            obj = json.loads(raw)
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ShardIntegrityError("Invalid checkpoint JSON") from exc
        if (type(obj) is not dict or set(obj) != {"scope", "pages", "checksum"} or
                obj["scope"] != self._metadata(shard) or
                type(obj["pages"]) is not list):
            raise ShardIntegrityError("Checkpoint scope or schema mismatch")
        if obj["checksum"] != sha256(_canonical({"scope": obj["scope"],
                                                  "pages": obj["pages"]})).hexdigest():
            raise ShardIntegrityError("Checkpoint checksum mismatch")
        if len(obj["pages"]) != shard.last - shard.first + 1:
            raise ShardIntegrityError("Missing checkpoint pages")
        result = []
        for expected, p in enumerate(obj["pages"], start=shard.first):
            if type(p) is not dict or set(p) != {"page", "text", "method", "sha256"}:
                raise ShardIntegrityError("Invalid checkpoint page record")
            item = PageRecord(p["page"], p["text"], p["method"])
            _check_record(item, expected)
            if sha256(item.text.encode("utf-8")).hexdigest() != p["sha256"]:
                raise ShardIntegrityError("Checkpoint text hash mismatch")
            result.append(item)
        return tuple(result)

    def read(self, shard: ShardRange) -> tuple[PageRecord, ...] | None:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        object_key = self._key(shard)
        self._check_lease()
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=object_key)
        except Exception as exc:
            # Only a real S3 NotFound is treated as an uncached shard.
            # AccessDenied, networking and other errors ALWAYS fail closed.
            code = str(getattr(exc, "response", {}).get("Error", {}).get("Code", ""))
            if code in ("NoSuchKey", "404", "NotFound"):
                return None
            raise
        body = response.get("Body")
        if body is None or not callable(getattr(body, "read", None)):
            raise ShardIntegrityError("Checkpoint object has no response stream")
        try:
            if (response.get("ContentLength") is not None and
                    int(response["ContentLength"]) > MAX_OBJECT_BYTES):
                raise ShardIntegrityError("Encrypted checkpoint exceeds size budget")
            data = body.read(MAX_OBJECT_BYTES + 1)
        finally:
            body.close()
        if not isinstance(data, bytes) or len(data) > MAX_OBJECT_BYTES:
            raise ShardIntegrityError("Encrypted checkpoint response is too large")
        if not data.startswith(MAGIC) or len(data) < len(MAGIC) + NONCE_BYTES + TAG_BYTES:
            raise ShardIntegrityError("Unsupported encrypted checkpoint format")
        nonce = data[len(MAGIC):len(MAGIC) + NONCE_BYTES]
        ciphertext = data[len(MAGIC) + NONCE_BYTES:]
        try:
            plaintext = AESGCM(self.key).decrypt(
                nonce, ciphertext, _canonical(self._metadata(shard)))
        except (InvalidTag, ValueError) as exc:
            raise ShardIntegrityError("Checkpoint authentication failed") from exc
        decoded = self._decode(shard, plaintext)
        self._check_lease()
        return decoded

    def write(self, shard: ShardRange, pages: Iterable[PageRecord]) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._check_lease()
        raw = self._encode(shard, pages)
        # Refuse existing data, including mismatched/corrupt data. S3 conditional
        # creation makes this safe even when other test workers race.
        if self.read(shard) is not None:
            raise ShardIntegrityError("Checkpoint already exists")
        nonce = os.urandom(NONCE_BYTES)
        encrypted = MAGIC + nonce + AESGCM(self.key).encrypt(
            nonce, raw, _canonical(self._metadata(shard)))
        if len(encrypted) > MAX_OBJECT_BYTES:
            raise ShardIntegrityError("Encrypted checkpoint exceeds size budget")
        self._check_lease()
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(shard), Body=encrypted,
            ContentType="application/octet-stream",
            IfNoneMatch="*",  # no blind overwrite on unsupported providers
        )
        self._check_lease()
