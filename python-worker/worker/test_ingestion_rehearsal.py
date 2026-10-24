"""Offline unit tests: no real MySQL, S3, Qdrant, Celery, OCR or model download."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
from uuid import NAMESPACE_URL, uuid5

from .document_chunking import DocumentChunk
from .document_embeddings import ChunkEmbedding, VECTOR_SIZE
from .ingestion_lease_verification import JobState
from .ingestion_rehearsal import inspect_embedded_stream, rehearse_ingestion


class _Encoding:
    def __init__(self, text):
        self.ids = list(range(len(text) + 2))


class _Tokenizer:
    def encode(self, text):
        return _Encoding(text)


def example_chunk(index=0, *, text="hello world", version="version-1", page=1, chunk_id=None):
    return DocumentChunk(
        chunk_id=chunk_id or str(uuid5(NAMESPACE_URL, f"docmind-test:{version}:{index}")),
        document_version_id=version, chunk_index=index,
        text=text, page_number=page,
        source_start_segment=0, source_end_segment=0, source_kind="pdf_page",
        group_char_start=0, group_char_end=len(text),
    )


def example_embedding(index=0, **kwargs):
    return ChunkEmbedding(example_chunk(index, **kwargs), (1.0,) + (0.0,) * (VECTOR_SIZE - 1))


class ReadOnlyRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.tokenizer = _Tokenizer()

    def test_one_chunk(self):
        r = inspect_embedded_stream([example_embedding()], self.tokenizer, "version-1")
        self.assertEqual(r.chunks, 1)
        self.assertEqual(r.embedded_vectors, 1)
        self.assertEqual(r.pages, (1,))

    def test_multiple_pages_sorted(self):
        r = inspect_embedded_stream([
            example_embedding(0, page=3), example_embedding(1, page=1),
        ], self.tokenizer, "version-1")
        self.assertEqual(r.pages, (1, 3))
        self.assertEqual(len(r.sample_ids), 2)

    def test_empty_document_rejected(self):
        with self.assertRaises(RuntimeError):
            inspect_embedded_stream([], self.tokenizer, "version-1")

    def test_wrong_version_rejected(self):
        with self.assertRaises(RuntimeError):
            inspect_embedded_stream([example_embedding(version="other")], self.tokenizer, "version-1")

    def test_chunk_order_must_be_contiguous(self):
        with self.assertRaises(RuntimeError):
            inspect_embedded_stream([example_embedding(1)], self.tokenizer, "version-1")

    def test_duplicate_point_ids_rejected(self):
        shared = example_chunk().chunk_id
        with self.assertRaises(RuntimeError):
            inspect_embedded_stream([example_embedding(), example_embedding(1, chunk_id=shared)], self.tokenizer, "version-1")

    def test_invalid_uuid_rejected(self):
        with self.assertRaises(RuntimeError):
            inspect_embedded_stream([example_embedding(chunk_id="not-a-uuid")], self.tokenizer, "version-1")

    def test_vector_size_checked(self):
        item = ChunkEmbedding(example_chunk(), (1.0, 2.0))
        with self.assertRaises(RuntimeError):
            inspect_embedded_stream([item], self.tokenizer, "version-1")

    def test_token_budget_checked(self):
        with self.assertRaises(RuntimeError):
            inspect_embedded_stream([example_embedding(text="x" * 500)], self.tokenizer, "version-1")

    def test_max_chunks_enforced(self):
        with self.assertRaises(RuntimeError):
            inspect_embedded_stream([example_embedding(0), example_embedding(1)], self.tokenizer, "version-1", max_chunks=1)

    def test_invalid_max_chunks_rejected(self):
        with self.assertRaises(ValueError):
            inspect_embedded_stream([], self.tokenizer, "version-1", max_chunks=0)

    def test_invalid_version_rejected(self):
        with self.assertRaises(ValueError):
            inspect_embedded_stream([], self.tokenizer, " ")

    def test_rehearsal_rejects_unsafe_job_before_network(self):
        inactive = JobState("RUNNING", "PROCESSING", 1, True, True, True, False)
        with patch("worker.ingestion_rehearsal.read_job_state", return_value=inactive), \
             patch("worker.ingestion_rehearsal.read_job_metadata") as meta, \
             patch("worker.ingestion_rehearsal.local_client") as qdrant:
            with self.assertRaises(RuntimeError):
                rehearse_ingestion("job1")
            meta.assert_not_called()
            qdrant.assert_not_called()

    def test_rehearsal_rejects_invalid_ocr_budget_before_network(self):
        with patch("worker.ingestion_rehearsal.read_job_state") as state:
            with self.assertRaises(ValueError):
                rehearse_ingestion("job1", max_ocr_pages=0)
            state.assert_not_called()


if __name__ == "__main__":
    unittest.main()
