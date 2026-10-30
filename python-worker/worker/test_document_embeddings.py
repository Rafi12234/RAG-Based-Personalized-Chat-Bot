"""Offline tests for embedding validation, streaming, prefixes and metadata.

No real model download, external service, or document bytes involved.
"""
import math
import unittest

from .document_chunking import DocumentChunk
from .document_embeddings import (
    VECTOR_SIZE, EmbeddingValidationError, embed_query, iter_chunk_embeddings,
)


class FakeModel:
    def __init__(self, vector=None, count_delta=0):
        self.vector = vector if vector is not None else [1.0] * VECTOR_SIZE
        self.count_delta = count_delta
        self.calls = []

    def embed(self, documents, *, batch_size=1):
        self.calls.append((list(documents), batch_size))
        count = len(documents) + self.count_delta
        return iter([self.vector] * max(0, count))


def chunk(index=0, text="Sample document text", page=1):
    return DocumentChunk(
        chunk_id=f"chunk-{index}", document_version_id="version-1",
        chunk_index=index, text=text, page_number=page,
        source_start_segment=index, source_end_segment=index,
        source_kind="pdf_page", group_char_start=0, group_char_end=len(text),
    )


class EmbeddingTests(unittest.TestCase):
    def test_one_chunk_returns_normalized_vector(self):
        result = list(iter_chunk_embeddings([chunk()], FakeModel()))
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].vector), VECTOR_SIZE)
        self.assertAlmostEqual(math.sqrt(sum(v*v for v in result[0].vector)), 1.0)

    def test_passage_prefix_used(self):
        model = FakeModel()
        list(iter_chunk_embeddings([chunk(text="Blockchain basics")], model))
        self.assertEqual(model.calls[0][0], ["passage: Blockchain basics"])

    def test_query_prefix_used(self):
        model = FakeModel()
        self.assertEqual(len(embed_query(model, "What is blockchain?")), VECTOR_SIZE)
        self.assertEqual(model.calls[0][0], ["query: What is blockchain?"])

    def test_streamed_batches_preserve_order_and_metadata(self):
        model = FakeModel()
        records = [chunk(i, f"text {i}", page=i+1) for i in range(5)]
        result = list(iter_chunk_embeddings(iter(records), model, batch_size=2))
        self.assertEqual([x.chunk.chunk_id for x in result], [x.chunk_id for x in records])
        self.assertEqual([x.chunk.page_number for x in result], [1, 2, 3, 4, 5])
        self.assertEqual([len(c[0]) for c in model.calls], [2, 2, 1])

    def test_empty_stream_does_not_call_model(self):
        model = FakeModel()
        self.assertEqual(list(iter_chunk_embeddings([], model)), [])
        self.assertFalse(model.calls)

    def test_invalid_batch_size_rejected(self):
        for n in (0, 65, True):
            with self.subTest(n=n), self.assertRaises(ValueError):
                list(iter_chunk_embeddings([chunk()], FakeModel(), batch_size=n))

    def test_empty_chunk_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            list(iter_chunk_embeddings([chunk(text="  ")], FakeModel()))

    def test_excessive_characters_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            list(iter_chunk_embeddings([chunk(text="A" * 1001)], FakeModel()))

    def test_wrong_dimension_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            list(iter_chunk_embeddings([chunk()], FakeModel(vector=[1, 2, 3])))

    def test_nan_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            list(iter_chunk_embeddings([chunk()], FakeModel(vector=[float("nan")] * VECTOR_SIZE)))

    def test_zero_vector_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            list(iter_chunk_embeddings([chunk()], FakeModel(vector=[0.0] * VECTOR_SIZE)))

    def test_wrong_count_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            list(iter_chunk_embeddings([chunk()], FakeModel(count_delta=-1)))

    def test_wrong_type_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            list(iter_chunk_embeddings(["not a chunk"], FakeModel()))

    def test_overflow_vector_rejected(self):
        with self.assertRaises(EmbeddingValidationError):
            list(iter_chunk_embeddings([chunk()], FakeModel(vector=[1e308] * VECTOR_SIZE)))


if __name__ == "__main__":
    unittest.main()
