"""Offline encrypted RustFS checkpoint tests: no S3, SQL, vectors or OCR."""
from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace
import unittest

from .large_document_shards import PageRecord, ShardIntegrityError, iter_checkpointed_pages
from .remote_document_shards import (
    CheckpointScope, EncryptedS3ShardStore, MAGIC, decode_checkpoint_key,
)


class FakeS3Error(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.writes = 0
        self.reads = 0
        self.error = None

    def get_object(self, Bucket, Key):
        self.reads += 1
        if self.error:
            raise FakeS3Error(self.error)
        if (Bucket, Key) not in self.objects:
            raise FakeS3Error("NoSuchKey")
        value = self.objects[(Bucket, Key)]
        return {"Body": BytesIO(value), "ContentLength": len(value)}

    def put_object(self, Bucket, Key, Body, ContentType, IfNoneMatch):
        if IfNoneMatch != "*":
            raise AssertionError("Missing atomic create-only condition")
        if (Bucket, Key) in self.objects:
            raise FakeS3Error("PreconditionFailed")
        if ContentType != "application/octet-stream":
            raise AssertionError("Incorrect content type")
        self.objects[(Bucket, Key)] = Body
        self.writes += 1


class EncryptedCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3()
        self.key = b"\xab" * 32
        self.scope = CheckpointScope("org_1", "kb_1", "doc_1", "ver_1",
                                     "d" * 64, "pymupdf-ocr-v1:eng:180")
        self.store = self.make_store()

    def make_store(self, *, scope=None, key=None, pages=5, shard=2):
        return EncryptedS3ShardStore(
            self.s3, "test-bucket", scope or self.scope, key or self.key,
            pages, lease_guard=lambda: True, shard_pages=shard)

    def extract(self, first, last):
        return (PageRecord(n, f"private page {n}", "native")
                for n in range(first, last + 1))

    def test_roundtrip_encrypted_and_resume_no_ocr_rerun(self):
        pages = list(iter_checkpointed_pages(self.store, self.extract))
        self.assertEqual([p.page_number for p in pages], [1,2,3,4,5])
        self.assertEqual(self.s3.writes, 3)
        for stored in self.s3.objects.values():
            self.assertTrue(stored.startswith(MAGIC))
            self.assertNotIn(b"private page", stored)
        rerun = lambda *_: self.fail("Resume must use encrypted checkpoint")
        self.assertEqual(list(iter_checkpointed_pages(
            self.make_store(), rerun)), pages)
        self.assertEqual(self.s3.writes, 3)

    def test_cross_worker_5000_pages_last_page_and_no_rework(self):
        store = self.make_store(pages=5000, shard=64)
        count = sum(1 for _ in iter_checkpointed_pages(store, self.extract))
        self.assertEqual(count, 5000)
        self.assertEqual(self.s3.writes, 79)
        assert_only_cache = lambda *_: self.fail("worker re-extracted cached pages")
        other_worker = self.make_store(pages=5000, shard=64)
        last = None
        for p in iter_checkpointed_pages(other_worker, assert_only_cache):
            last = p.page_number
        self.assertEqual(last, 5000)

    def test_revoked_processing_lease_blocks_remote_operations(self):
        store = EncryptedS3ShardStore(
            self.s3, "test-bucket", self.scope, self.key, 2,
            lease_guard=lambda: False)
        with self.assertRaisesRegex(RuntimeError, "lease"):
            store.read(store.ranges[0])
        with self.assertRaisesRegex(RuntimeError, "lease"):
            store.write(store.ranges[0], self.extract(1, 2))
        self.assertEqual(self.s3.writes, 0)
        self.assertEqual(self.s3.reads, 0)

    def test_tamper_rejected_without_fallback_or_overwrite(self):
        s = self.store.ranges[0]
        self.store.write(s, list(self.extract(s.first, s.last)))
        key = (self.store.bucket, self.store._key(s))
        body = bytearray(self.s3.objects[key])
        body[-5] ^= 0x80
        self.s3.objects[key] = bytes(body)
        with self.assertRaisesRegex(ShardIntegrityError, "authentication"):
            self.store.read(s)
        with self.assertRaises(ShardIntegrityError):
            self.store.write(s, list(self.extract(s.first, s.last)))
        self.assertEqual(self.s3.writes, 1)

    def test_source_language_version_and_tenant_are_bound(self):
        s = self.store.ranges[0]
        self.store.write(s, self.extract(s.first, s.last))
        for changed in (
            CheckpointScope("org_2", "kb_1", "doc_1", "ver_1", "d"*64, self.scope.extraction_profile),
            CheckpointScope("org_1", "kb_1", "doc_1", "ver_2", "d"*64, self.scope.extraction_profile),
            CheckpointScope("org_1", "kb_1", "doc_1", "ver_1", "e"*64, self.scope.extraction_profile),
            CheckpointScope("org_1", "kb_1", "doc_1", "ver_1", "d"*64, "pymupdf-ocr-v1:eng+ben:180"),
        ):
            remote = self.make_store(scope=changed)
            self.assertIsNone(remote.read(remote.ranges[0]))
        # Even if a malicious actor copies the bytes to the wrong scope key,
        # the AES-GCM AAD prevents cross-scope replay.
        different = self.make_store(scope=CheckpointScope(
            "org_2", "kb_1", "doc_1", "ver_1", "d"*64, self.scope.extraction_profile))
        alt = (different.bucket, different._key(different.ranges[0]))
        self.s3.objects[alt] = self.s3.objects[(self.store.bucket, self.store._key(s))]
        with self.assertRaisesRegex(ShardIntegrityError, "authentication"):
            different.read(different.ranges[0])

    def test_secret_rotation_and_invalid_key_fail_closed(self):
        shard = self.store.ranges[0]
        self.store.write(shard, self.extract(shard.first, shard.last))
        with self.assertRaisesRegex(ShardIntegrityError, "authentication"):
            self.make_store(key=b"\x99"*32).read(shard)
        self.assertEqual(decode_checkpoint_key(base64.b64encode(self.key).decode()), self.key)
        for value in ("", "bad!", base64.b64encode(b"a"*16).decode()):
            with self.assertRaises(ValueError):
                decode_checkpoint_key(value)

    def test_only_missing_is_treated_as_cache_miss(self):
        self.s3.error = "AccessDenied"
        with self.assertRaises(FakeS3Error):
            self.store.read(self.store.ranges[0])
        self.s3.error = "SlowDown"
        with self.assertRaises(FakeS3Error):
            self.store.read(self.store.ranges[0])

    def test_create_only_conditional_write_failure_not_silenced(self):
        class Racing(FakeS3):
            def put_object(self, **kw):
                raise FakeS3Error("PreconditionFailed")
        self.s3 = Racing()
        store = self.make_store()
        with self.assertRaises(FakeS3Error):
            store.write(store.ranges[0], self.extract(1,2))

    def test_ocr_limit_counts_cached_pages(self):
        store = self.make_store(pages=3,shard=1)
        def scanned(a,b):
            return [PageRecord(a, "recognized scanned page", "ocr")]
        with self.assertRaisesRegex(ShardIntegrityError, "OCR page budget"):
            list(iter_checkpointed_pages(store, scanned, max_ocr_pages=2))
        self.assertEqual(self.s3.writes, 3)
        with self.assertRaisesRegex(ShardIntegrityError, "OCR page budget"):
            list(iter_checkpointed_pages(store, self.extract, max_ocr_pages=2))
        self.assertEqual(self.s3.writes, 3)

    def test_reject_wrong_input_scope(self):
        for bad in ("../abc", "", "a/b", "x"*129):
            scope = CheckpointScope(bad, "kb", "doc", "ver", "d"*64, "pymupdf-ocr-v1:eng:180")
            with self.assertRaises(ValueError):
                self.make_store(scope=scope)

    def test_incomplete_pages_rejected_without_any_put(self):
        shard = self.store.ranges[0]
        with self.assertRaises(ShardIntegrityError):
            self.store.write(shard,[PageRecord(1,"only one","native")])
        self.assertEqual(self.s3.writes,0)


if __name__ == "__main__":
    unittest.main()
