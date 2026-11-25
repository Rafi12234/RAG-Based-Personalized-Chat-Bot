"""Offline Phase 4.3B encrypted hierarchy manifest + durable ledger tests."""
from __future__ import annotations

from dataclasses import replace
from io import BytesIO
import unittest

from .document_structure import PdfPageStructure, SourceBlock
from .hierarchical_index import (
    HierarchyManifestBuilder, iter_manifested_pdf_passages,
)
from .hierarchy_manifest_store import (
    HierarchyLedgerBuilder, EncryptedHierarchyManifestStore,
    HierarchyIntegrityError, validate_ledger, MAGIC,
)
from .qdrant_indexing import IndexIdentity


class Tokenizer:
    def encode(self, text):
        return type("Encoding", (), {"ids": tuple(range(len(text) + 2))})()


class FakeS3Error(Exception):
    def __init__(self, code):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class MemoryS3:
    def __init__(self):
        self.objects = {}
        self.write_count = 0
        self.get_count = 0
        self.read_error = None
        self.race = False

    def get_object(self, Bucket, Key):
        self.get_count += 1
        if self.read_error:
            raise FakeS3Error(self.read_error)
        try:
            value = self.objects[(Bucket, Key)]
        except KeyError:
            raise FakeS3Error("NoSuchKey")
        return {"Body": BytesIO(value), "ContentLength": len(value)}

    def put_object(self, Bucket, Key, Body, ContentType, IfNoneMatch):
        assert ContentType == "application/octet-stream"
        assert IfNoneMatch == "*"
        if self.race or (Bucket, Key) in self.objects:
            raise FakeS3Error("PreconditionFailed")
        self.objects[(Bucket, Key)] = Body
        self.write_count += 1


def make_ledger(count=5, version="ver"):
    pages = (PdfPageStructure(n, (
        SourceBlock(
            f"source-{n}", version, 0, f"Original book page {n} fact {n*73}",
            "pdf_text", n, (0.0, 5.0, 90.0, 45.0), (),
            f"pdf:p{n:05d}:b00000",
        ),), False, False) for n in range(1, count + 1))
    mb = HierarchyManifestBuilder(version, "a" * 64, expected_pages=count)
    lb = HierarchyLedgerBuilder()
    passages = []
    for passage in iter_manifested_pdf_passages(pages, mb, Tokenizer()):
        lb.add_passage(passage)
        passages.append(passage)
    manifest = mb.finalize()
    return passages, lb.finalize(manifest)


class DurableLedgerTests(unittest.TestCase):
    def setUp(self):
        self.s3 = MemoryS3()
        self.key = b"k" * 32
        self.identity = IndexIdentity("org1", "kb1", "doc1", "ver", "job1", "book.pdf")
        self.passages, self.ledger = make_ledger()
        self.store = self.new_store()

    def new_store(self, *, identity=None, ledger=None, key=None, guard=None):
        selected_ledger = self.ledger if ledger is None else ledger
        return EncryptedHierarchyManifestStore(
            self.s3, "private-bucket",
            self.identity if identity is None else identity,
            selected_ledger.manifest, self.key if key is None else key,
            lease_guard=(lambda: True) if guard is None else guard,
        )

    def test_roundtrip_encrypted_immutable_cross_worker(self):
        self.assertIsNone(self.store.read())
        self.store.write(self.ledger)
        self.assertEqual(self.s3.write_count, 1)
        stored = list(self.s3.objects.values())[0]
        self.assertTrue(stored.startswith(MAGIC))
        self.assertNotIn(b"Original book page", stored)
        self.assertNotIn(b"pdf:p00001", stored)
        self.assertEqual(self.new_store().read(), self.ledger)
        self.assertFalse(self.ledger.public_metadata()["query_enabled"])
        with self.assertRaisesRegex(HierarchyIntegrityError, "already exists"):
            self.new_store().write(self.ledger)
        self.assertEqual(self.s3.write_count, 1)

    def test_5000_page_book_manifest_survives_worker_restart(self):
        passages, ledger = make_ledger(5000)
        store = self.new_store(ledger=ledger)
        store.write(ledger)
        copy = self.new_store(ledger=ledger).read()
        self.assertEqual(copy.manifest.accounted_pages, 5000)
        self.assertEqual(copy.manifest.passage_count, 5000)
        self.assertEqual(copy.records[-1].page_number, 5000)
        self.assertEqual(copy.records[-1].source_locator, "pdf:p05000:b00000")
        self.assertEqual(len(copy.manifest.point_ids), 5000)
        self.assertEqual(self.s3.write_count, 1)

    def test_authentication_tampering_and_wrong_key_fail_closed(self):
        self.store.write(self.ledger)
        k = (self.store.bucket, self.store.object_key)
        body = bytearray(self.s3.objects[k])
        body[-4] ^= 0x01
        self.s3.objects[k] = bytes(body)
        with self.assertRaisesRegex(HierarchyIntegrityError, "authentication"):
            self.store.read()
        with self.assertRaises(HierarchyIntegrityError):
            self.store.write(self.ledger)
        # Wrong keys fail on an uncompromised encrypted manifest as well.
        self.s3.objects[k] = bytes(body[:-1]) + b"\x00"
        with self.assertRaises(HierarchyIntegrityError):
            self.new_store(key=b"x"*32).read()

    def test_cross_tenant_replay_and_source_hash_fails(self):
        self.store.write(self.ledger)
        foreign = IndexIdentity("other", "kb1", "doc1", "ver", "job1", "book.pdf")
        other_store = self.new_store(identity=foreign)
        self.assertIsNone(other_store.read())
        original = self.s3.objects[(self.store.bucket, self.store.object_key)]
        self.s3.objects[(other_store.bucket, other_store.object_key)] = original
        with self.assertRaisesRegex(HierarchyIntegrityError, "authentication"):
            other_store.read()
        m = replace(self.ledger.manifest, source_sha256="b" * 64)
        wrong_source = self.new_store(ledger=replace(self.ledger, manifest=m))
        self.s3.objects[(wrong_source.bucket, wrong_source.object_key)] = original
        with self.assertRaises(HierarchyIntegrityError):
            wrong_source.read()

    def test_lease_revocation_denies_all_remote_operations(self):
        denied = self.new_store(guard=lambda: False)
        with self.assertRaisesRegex(RuntimeError, "lease"):
            denied.read()
        with self.assertRaisesRegex(RuntimeError, "lease"):
            denied.write(self.ledger)
        self.assertEqual(self.s3.get_count, 0)
        self.assertEqual(self.s3.write_count, 0)

    def test_access_denied_not_treated_as_missing(self):
        self.s3.read_error = "AccessDenied"
        with self.assertRaises(FakeS3Error):
            self.store.read()
        with self.assertRaises(FakeS3Error):
            self.store.write(self.ledger)
        self.assertEqual(self.s3.write_count, 0)

    def test_conditional_create_race_fail_closed(self):
        self.s3.race = True
        with self.assertRaises(FakeS3Error):
            self.store.write(self.ledger)
        self.assertEqual(self.s3.write_count, 0)

    def test_manifest_binding_rejects_changed_source_map(self):
        record = self.ledger.records[0]
        modified = replace(record, source_locator="pdf:p49999:b00000")
        tampered = replace(self.ledger, records=(modified, *self.ledger.records[1:]))
        with self.assertRaises(HierarchyIntegrityError):
            validate_ledger(tampered)
        with self.assertRaises(HierarchyIntegrityError):
            self.store.write(tampered)

    def test_manifest_rejects_changed_content_fingerprint(self):
        record = self.ledger.records[0]
        modified = replace(record, text_sha256="b" * 64)
        tampered = replace(self.ledger, records=(modified, *self.ledger.records[1:]))
        with self.assertRaisesRegex(HierarchyIntegrityError, "checksum"):
            validate_ledger(tampered)

    def test_manifest_requires_contiguous_full_page_coverage(self):
        mb = HierarchyManifestBuilder("ver", "a"*64, expected_pages=3)
        mb.add_pdf_page(PdfPageStructure(1, (), False, False))
        with self.assertRaises(HierarchyIntegrityError):
            mb.finalize()

    def test_read_requires_same_exact_generation(self):
        self.store.write(self.ledger)
        other = replace(self.ledger, manifest=replace(
            self.ledger.manifest, digest="e" * 64))
        different = self.new_store(ledger=other)
        self.assertIsNone(different.read())


if __name__ == "__main__":
    unittest.main()
