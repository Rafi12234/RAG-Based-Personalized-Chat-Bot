"""Mock-based verification tests; no live Qdrant or MySQL connection."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from .qdrant_indexing import IndexIdentity
from .ready_vector_verification import verify_ready_vector_ids

ID='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
SCOPE={"organization_id":"org", "knowledge_base_id":"kb", "document_id":"doc", "document_version_id":"version", "processing_token":"token", "index_state":"ready"}

class ReadyVectorTests(unittest.TestCase):
    def setUp(self):
        self.identity=IndexIdentity('org','kb','doc','version','job','filename.pdf')
        self.client=MagicMock()
        self.client.retrieve.return_value=[SimpleNamespace(id=ID,payload=dict(SCOPE))]
        self.client.count.return_value=SimpleNamespace(count=1)
        self.guard=lambda: True
        self.patcher=patch('worker.ready_vector_verification.inspect_collection',return_value=SimpleNamespace(exists=True,compatible=True,missing_indexes=()))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def verify(self, ids=None, guard=None):
        return verify_ready_vector_ids(self.client,self.identity,'token', [ID] if ids is None else ids,
                                       lease_guard=self.guard if guard is None else guard)

    def test_ready_point_valid(self): self.assertEqual(self.verify(),1)
    def test_missing_rejected(self):
        self.client.retrieve.return_value=[]
        with self.assertRaises(RuntimeError): self.verify()
    def test_staging_rejected(self):
        self.client.retrieve.return_value=[SimpleNamespace(id=ID,payload=dict(SCOPE,index_state='staging'))]
        with self.assertRaises(RuntimeError): self.verify()
    def test_foreign_organization_rejected(self):
        self.client.retrieve.return_value=[SimpleNamespace(id=ID,payload=dict(SCOPE,organization_id='other'))]
        with self.assertRaises(RuntimeError): self.verify()
    def test_foreign_processing_token_rejected(self):
        self.client.retrieve.return_value=[SimpleNamespace(id=ID,payload=dict(SCOPE,processing_token='other'))]
        with self.assertRaises(RuntimeError): self.verify()
    def test_extra_scoped_points_rejected(self):
        self.client.count.return_value=SimpleNamespace(count=2)
        with self.assertRaises(RuntimeError): self.verify()
    def test_duplicate_expected_ids_rejected(self):
        with self.assertRaises(ValueError): self.verify(ids=[ID,ID])
    def test_empty_ids_rejected(self):
        with self.assertRaises(ValueError): self.verify(ids=[])
    def test_stale_lease_rejected(self):
        with self.assertRaises(RuntimeError): self.verify(guard=lambda: False)
        self.client.retrieve.assert_not_called()
    def test_expired_after_read_rejected(self):
        state=iter([True,True,False])
        with self.assertRaises(RuntimeError): self.verify(guard=lambda: next(state))
    def test_invalid_batch_size_rejected(self):
        with self.assertRaises(ValueError):
            verify_ready_vector_ids(self.client,self.identity,'token',[ID], lease_guard=self.guard, batch_size=0)
    def test_malformed_id_rejected(self):
        with self.assertRaises(ValueError): self.verify(ids=['bad-id'])

if __name__ == '__main__': unittest.main()
