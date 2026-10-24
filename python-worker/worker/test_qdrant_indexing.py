"""Offline, mocked tests; no Qdrant, Redis, MySQL or S3 connections."""
import unittest
from math import nan
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock, patch

from .document_chunking import DocumentChunk
from .document_embeddings import ChunkEmbedding, VECTOR_SIZE
from . import qdrant_indexing as q


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.identity = q.IndexIdentity("org1", "kb1", "doc1", "ver1", "job1", "example.pdf")
        self.chunk = DocumentChunk(str(uuid4()), "ver1", 0, "Important private text", 2,
                                   0, 0, "pdf_page", 0, 22)
        self.item = ChunkEmbedding(self.chunk, tuple([0.1] * VECTOR_SIZE))

    def test_payload_has_tenant_and_page(self):
        result = q.payload_for(self.item, self.identity)
        self.assertEqual(result["organization_id"], "org1")
        self.assertEqual(result["knowledge_base_id"], "kb1")
        self.assertEqual(result["page_number"], 2)
        self.assertEqual(result["text"], self.chunk.text)

    def test_mismatch_version_rejected(self):
        other = q.IndexIdentity("org1", "kb1", "doc1", "ver2", "job1", "example.pdf")
        with self.assertRaises(ValueError):
            q.payload_for(self.item, other)

    def test_bad_vector_rejected(self):
        with self.assertRaises(ValueError):
            q.payload_for(ChunkEmbedding(self.chunk, (1.0,)), self.identity)
        with self.assertRaises(ValueError):
            q.payload_for(ChunkEmbedding(self.chunk, tuple([nan] * VECTOR_SIZE)), self.identity)

    def test_stable_uuid_point_id(self):
        m = SimpleNamespace(PointStruct=lambda **kwargs: SimpleNamespace(**kwargs))
        with patch.object(q, "_qmodels", return_value=m):
            first = q.point_for(self.item, self.identity)
            second = q.point_for(self.item, self.identity)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(first.vector), VECTOR_SIZE)

    def test_batches(self):
        m = SimpleNamespace(PointStruct=lambda **kwargs: SimpleNamespace(**kwargs))
        client = Mock()
        with patch.object(q, "_qmodels", return_value=m):
            result = q.upsert_diagnostic(client, [self.item] * 5, self.identity, batch_size=2)
        self.assertEqual(result, 5)
        self.assertEqual(client.upsert.call_count, 3)
        self.assertTrue(all(c.kwargs["wait"] is True for c in client.upsert.call_args_list))

    def test_bad_batch_size(self):
        with self.assertRaises(ValueError):
            q.upsert_diagnostic(Mock(), [], self.identity, batch_size=0)

    def test_collection_created_with_384_cosine(self):
        m = SimpleNamespace(Distance=SimpleNamespace(COSINE="Cosine"),
                            VectorParams=lambda **k: SimpleNamespace(**k),
                            PayloadSchemaType=SimpleNamespace(KEYWORD="keyword"))
        client = Mock()
        client.collection_exists.return_value = False
        with patch.object(q, "_qmodels", return_value=m):
            self.assertTrue(q.ensure_diagnostic_collection(client))
        self.assertEqual(client.create_collection.call_args.kwargs["vectors_config"].size, 384)
        self.assertEqual(client.create_payload_index.call_count, 3)

    def test_collection_reuse_verified(self):
        m = SimpleNamespace(Distance=SimpleNamespace(COSINE="Cosine"))
        client = Mock()
        client.collection_exists.return_value = True
        client.get_collection.return_value.config.params.vectors = SimpleNamespace(size=384, distance="Cosine")
        with patch.object(q, "_qmodels", return_value=m):
            self.assertFalse(q.ensure_diagnostic_collection(client))
        client.create_collection.assert_not_called()

    def test_incompatible_collection_refused(self):
        m = SimpleNamespace(Distance=SimpleNamespace(COSINE="Cosine"))
        client = Mock()
        client.collection_exists.return_value = True
        client.get_collection.return_value.config.params.vectors = SimpleNamespace(size=768, distance="Cosine")
        with patch.object(q, "_qmodels", return_value=m):
            with self.assertRaises(ValueError):
                q.ensure_diagnostic_collection(client)

    def test_tenant_and_version_filter(self):
        m = SimpleNamespace(FieldCondition=lambda **k: SimpleNamespace(**k),
                            MatchValue=lambda **k: SimpleNamespace(**k),
                            Filter=lambda **k: SimpleNamespace(**k))
        client = Mock()
        client.count.return_value.count = 4
        with patch.object(q, "_qmodels", return_value=m):
            self.assertEqual(q.count_version_points(client, self.identity), 4)
        cond = client.count.call_args.kwargs["count_filter"].must
        self.assertEqual({x.key for x in cond}, {"organization_id", "document_version_id"})

    def test_local_url_guard(self):
        with patch.dict("os.environ", {"QDRANT_URL": "https://remote.example"}):
            with self.assertRaises(ValueError):
                q.diagnostic_client()


if __name__ == "__main__":
    unittest.main()
