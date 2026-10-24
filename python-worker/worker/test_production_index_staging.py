"""Offline tests: no MySQL connections, RustFS downloads or Qdrant requests."""
import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock, patch

from .document_chunking import DocumentChunk
from .document_embeddings import ChunkEmbedding, VECTOR_SIZE
from .qdrant_indexing import IndexIdentity
from . import production_index_staging as staging


@dataclass
class DummySchema:
    exists: bool = True
    compatible: bool = True
    missing_indexes: tuple = ()


def sample():
    identity = IndexIdentity('org1', 'kb1', 'doc1', 'v1', 'job1', 'example.pdf')
    chunk = DocumentChunk('01234567-89ab-4cde-8f01-234567890abc', 'v1', 0,
                          'safe content', 1, 0, 0, 'pdf_page', 0, 12)
    return identity, ChunkEmbedding(chunk, tuple([0.0] * (VECTOR_SIZE-1) + [1.0]))


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.identity, self.item = sample()
        self.client = Mock()
        self.client.count.return_value = SimpleNamespace(count=1)
        self.schema_patch = patch.object(staging, 'inspect_collection', return_value=DummySchema())
        self.schema_patch.start()
        self.addCleanup(self.schema_patch.stop)
        self.models = SimpleNamespace(
            PointStruct=lambda **kwargs: SimpleNamespace(**kwargs),
            FieldCondition=lambda **kwargs: SimpleNamespace(**kwargs),
            MatchValue=lambda **kwargs: SimpleNamespace(**kwargs),
            Filter=lambda **kwargs: SimpleNamespace(**kwargs),
        )
        self.models_patch = patch.object(staging, '_models', return_value=self.models)
        self.models_patch.start()
        self.addCleanup(self.models_patch.stop)
        self.payload_patch = patch.object(staging, 'staging_payload', side_effect=lambda item, identity, token: {
            'index_state': 'staging', 'processing_token': token,
            'organization_id': identity.organization_id, 'document_version_id': identity.document_version_id,
        })
        self.payload_patch.start()
        self.addCleanup(self.payload_patch.stop)

    def test_staging_point_not_ready(self):
        point = staging.make_staging_point(self.item, self.identity, 'token')
        self.assertEqual(point.payload['index_state'], 'staging')
        self.assertEqual(point.id, self.item.chunk.chunk_id)
        self.assertEqual(len(point.vector), VECTOR_SIZE)

    def test_staging_filter_has_tenant_version_and_token(self):
        where = staging.staging_filter(self.identity, 'tok')
        self.assertEqual({x.key: x.match.value for x in where.must}, {
            'organization_id': 'org1', 'knowledge_base_id': 'kb1',
            'document_version_id': 'v1', 'processing_token': 'tok', 'index_state': 'staging'})

    def test_staged_count_uses_exact_filter(self):
        self.assertEqual(staging.count_staged_points(self.client, self.identity, 'tok'), 1)
        self.client.count.assert_called_once()
        self.assertTrue(self.client.count.call_args.kwargs['exact'])

    def test_stage_calls_lease_guard_before_after(self):
        guard = Mock(return_value=True)
        result = staging.stage_batches(self.client, [self.item], self.identity, 'token', lease_guard=guard)
        self.assertEqual(result, 1)
        self.assertEqual(guard.call_count, 2)
        self.client.upsert.assert_called_once()
        self.assertTrue(self.client.upsert.call_args.kwargs['wait'])

    def test_refuses_without_current_lease(self):
        with self.assertRaisesRegex(RuntimeError, 'lease'):
            staging.stage_batches(self.client, [self.item], self.identity, 'token', lease_guard=lambda: False)
        self.client.upsert.assert_not_called()

    def test_refuses_without_lease_guard(self):
        with self.assertRaises(TypeError):
            staging.stage_batches(self.client, [self.item], self.identity, 'token', lease_guard=None)

    def test_refuses_empty_token(self):
        with self.assertRaises(ValueError):
            staging.stage_batches(self.client, [self.item], self.identity, '', lease_guard=lambda: True)

    def test_rejects_bad_schema(self):
        with patch.object(staging, 'inspect_collection', return_value=DummySchema(False)):
            with self.assertRaises(RuntimeError):
                staging.stage_batches(self.client, [self.item], self.identity, 'tok', lease_guard=lambda: True)
        self.client.upsert.assert_not_called()

    def test_rejects_missing_index(self):
        with patch.object(staging, 'inspect_collection', return_value=DummySchema(True, True, ('index_state',))):
            with self.assertRaises(RuntimeError):
                staging.stage_batches(self.client, [self.item], self.identity, 'tok', lease_guard=lambda: True)

    def test_guard_failure_after_upsert_does_not_mark_ready(self):
        guard = Mock(side_effect=[True, False])
        with self.assertRaisesRegex(RuntimeError, 'expired'):
            staging.stage_batches(self.client, [self.item], self.identity, 'tok', lease_guard=guard)
        self.client.upsert.assert_called_once()
        point = self.client.upsert.call_args.kwargs['points'][0]
        self.assertEqual(point.payload['index_state'], 'staging')

    def test_batches_independently_guarded(self):
        guard = Mock(return_value=True)
        total = staging.stage_batches(self.client, [self.item]*3, self.identity, 'tok',
                                      lease_guard=guard, batch_size=2)
        self.assertEqual(total, 3)
        self.assertEqual(guard.call_count, 4)
        self.assertEqual(self.client.upsert.call_count, 2)

    def test_bad_batch_size_rejected(self):
        for size in (0, 129, True):
            with self.assertRaises(ValueError):
                staging.stage_batches(self.client, [self.item], self.identity, 'tok',
                                      lease_guard=lambda: True, batch_size=size)

    def test_broken_visibility_contract_rejected(self):
        with patch.object(staging, 'staging_payload', return_value={'index_state': 'ready', 'processing_token': 'tok'}):
            with self.assertRaises(ValueError):
                staging.stage_batches(self.client, [self.item], self.identity, 'tok', lease_guard=lambda: True)
        self.client.upsert.assert_not_called()


if __name__ == '__main__':
    unittest.main()
