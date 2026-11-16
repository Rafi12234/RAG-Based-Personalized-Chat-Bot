"""Offline 5,000-page extraction checkpoint/regression coverage.

Uses only Python stdlib. Does NOT access customer documents or services.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from .large_document_shards import (
    ShardCheckpointStore, PageRecord, ShardIntegrityError,
    iter_checkpointed_pages, shard_ranges, source_sha256,
)


class ShardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "secure"
        self.root.mkdir(mode=0o700)
        self.root.chmod(0o700)
        self.digest = "e" * 64

    def store(self, pages=5, shard=2, version="ver_test"):
        return ShardCheckpointStore(self.root, version, self.digest, pages,
                                   shard_pages=shard)

    def test_5000_pages_in_exact_shards(self):
        ranges = list(shard_ranges(5000, shard_pages=32))
        self.assertEqual(len(ranges), 157)
        self.assertEqual((ranges[0].first, ranges[0].last), (1, 32))
        self.assertEqual((ranges[-1].first, ranges[-1].last), (4993, 5000))
        self.assertEqual(sum(x.last - x.first + 1 for x in ranges), 5000)

    def test_5000_page_checkpoint_stream_preserves_last_page(self):
        store=self.store(pages=5000,shard=32)
        calls=[]
        def extract(first,last):
            calls.append((first,last))
            return (PageRecord(n, f"Page {n}: unique fact", "native")
                    for n in range(first,last+1))
        count=0
        last_page=None
        for page in iter_checkpointed_pages(store,extract):
            count+=1
            last_page=page.page_number
        self.assertEqual(count,5000)
        self.assertEqual(last_page,5000)
        self.assertEqual(len(calls),157)
        calls.clear()
        self.assertEqual(sum(1 for _ in iter_checkpointed_pages(store,extract)),5000)
        self.assertEqual(calls,[])

    def test_resume_without_extraction_or_reembedding(self):
        store = self.store()
        calls = []
        def extract(a, b):
            calls.append((a, b))
            return (PageRecord(n, "Page " + str(n), "native") for n in range(a,b+1))
        out = list(iter_checkpointed_pages(store, extract))
        self.assertEqual(len(out), 5)
        self.assertEqual(calls, [(1,2),(3,4),(5,5)])
        calls.clear()
        again = list(iter_checkpointed_pages(store, extract))
        self.assertEqual(out, again)
        self.assertEqual(calls, [])

    def test_incomplete_shard_not_saved(self):
        store = self.store(pages=3, shard=3)
        with self.assertRaisesRegex(ShardIntegrityError, "Incomplete"):
            list(iter_checkpointed_pages(store, lambda a,b: [PageRecord(1,"x","native")]))
        self.assertIsNone(store.read(store.ranges[0]))

    def test_duplicate_and_out_of_order_pages_refused(self):
        for pages in ([PageRecord(1,"A","native"),PageRecord(1,"B","native")],
                      [PageRecord(2,"A","native"),PageRecord(1,"B","native")]):
            store = self.store(pages=2, shard=2)
            with self.assertRaises(ShardIntegrityError):
                list(iter_checkpointed_pages(store, lambda a,b: pages))

    def test_corruption_is_not_misread_as_completed(self):
        store = self.store(pages=2)
        shard = store.ranges[0]
        store.write(shard, [PageRecord(1,"hello","native"),
                            PageRecord(2,"secret","native")])
        path = store._path(shard)
        raw = path.read_text()
        path.write_text(raw.replace("secret","wrong!"))
        with self.assertRaisesRegex(ShardIntegrityError,"checksum"):
            store.read(shard)

    def test_version_and_source_mismatch_fail_closed(self):
        store = self.store(pages=2)
        shard = store.ranges[0]
        store.write(shard,[PageRecord(1,"a","native"),PageRecord(2,"b","native")])
        other = ShardCheckpointStore(self.root,"ver_test","a"*64,2,shard_pages=2)
        with self.assertRaisesRegex(ShardIntegrityError,"scope"):
            other.read(shard)
        with self.assertRaises(ValueError):
            self.store(version="../../protected")
        with self.assertRaises(ValueError):
            ShardCheckpointStore(self.root,"ok","not-a-digest",2)

    def test_ocr_total_count_applies_to_cached_shards(self):
        store = self.store(pages=3,shard=1)
        seen=[]
        def extract(a,b):
            seen.append(a)
            return [PageRecord(a,"recognized text","ocr")]
        with self.assertRaisesRegex(ShardIntegrityError,"OCR page budget"):
            list(iter_checkpointed_pages(store,extract,max_ocr_pages=2))
        self.assertEqual(seen,[1,2,3])
        seen.clear()
        with self.assertRaisesRegex(ShardIntegrityError,"OCR page budget"):
            list(iter_checkpointed_pages(store,extract,max_ocr_pages=2))
        self.assertEqual(seen,[])

    def test_blank_pages_are_counted_but_invalid_blank_not_allowed(self):
        store=self.store(pages=2)
        value=list(iter_checkpointed_pages(store,lambda a,b:[
            PageRecord(1,"","blank"),PageRecord(2,"supported text","native")]))
        self.assertEqual([x.page_number for x in value],[1,2])
        with self.assertRaises(ShardIntegrityError):
            self.store(pages=1).write(self.store(pages=1).ranges[0],
                                     [PageRecord(1,"should be empty","blank")])

    def test_reject_symlinks_and_world_readable_posix_dirs(self):
        if os.name != "posix":
            self.skipTest("POSIX permissions")
        exposed=Path(self.temp.name)/"exposed"
        exposed.mkdir(mode=0o755)
        exposed.chmod(0o755)
        with self.assertRaisesRegex(ShardIntegrityError,"private"):
            ShardCheckpointStore(exposed,"ver2",self.digest,2)
        alias=Path(self.temp.name)/"alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ShardIntegrityError,"Symlinked"):
            ShardCheckpointStore(alias,"ver2",self.digest,2)

    def test_source_sha256_uses_file_content(self):
        path=self.root/"original.bin"
        path.write_bytes(b"abc")
        self.assertEqual(source_sha256(path),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")

    def test_max_page_and_range_size_validation(self):
        for count in (0,5001,True):
            with self.assertRaises(ValueError):
                list(shard_ranges(count))
        for size in (0,129,1.0):
            with self.assertRaises(ValueError):
                list(shard_ranges(10,shard_pages=size))


if __name__ == "__main__":
    unittest.main()
