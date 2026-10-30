"""Offline Qdrant point verification tests; no production operations."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid5, NAMESPACE_URL

from .qdrant_indexing import IndexIdentity
from .staged_vector_verification import verify_staged_vector_ids


def ids(count):
    return [str(uuid5(NAMESPACE_URL, f"test:{i}")) for i in range(count)]


def identity():
    return IndexIdentity("org1", "kb1", "doc1", "v1", "job1", "demo.pdf")


def payload():
    return dict(organization_id="org1", knowledge_base_id="kb1", document_id="doc1",
                document_version_id="v1", processing_token="tok", index_state="staging")


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.patch_schema = patch(
            "worker.staged_vector_verification.inspect_collection",
            return_value=SimpleNamespace(exists=True, compatible=True, missing_indexes=()),
        )
        self.patch_count = patch("worker.staged_vector_verification.count_staged_points")
        self.patch_schema.start()
        self.count = self.patch_count.start()
        self.addCleanup(self.patch_schema.stop)
        self.addCleanup(self.patch_count.stop)
        self.client.retrieve.side_effect = lambda **kw: [
            SimpleNamespace(id=p, payload=payload()) for p in kw["ids"]
        ]

    def check(self, items=None, guard=lambda: True, batch_size=64):
        items = ids(3) if items is None else items
        self.count.return_value = len(items)
        return verify_staged_vector_ids(self.client, identity(), "tok", items,
                                        lease_guard=guard, batch_size=batch_size)

    def test_verifies_matching_points(self):
        self.assertEqual(self.check(), 3)
        self.client.retrieve.assert_called_once()
        self.client.upsert.assert_not_called()
        self.client.set_payload.assert_not_called()
        self.client.delete.assert_not_called()

    def test_batches_are_bounded(self):
        self.assertEqual(self.check(ids(5), batch_size=2), 5)
        self.assertEqual(self.client.retrieve.call_count, 3)

    def test_guard_denies_at_start(self):
        with self.assertRaisesRegex(RuntimeError, "not owned"):
            self.check(guard=lambda: False)
        self.client.retrieve.assert_not_called()

    def test_lost_lease_during_verification(self):
        calls = [True, True, False]
        with self.assertRaisesRegex(RuntimeError, "lease lost"):
            self.check(guard=lambda: calls.pop(0))

    def test_scope_mismatch_rejected(self):
        wrong = payload()
        wrong["organization_id"] = "other"
        self.client.retrieve.side_effect = lambda **kw: [
            SimpleNamespace(id=i, payload=wrong) for i in kw["ids"]
        ]
        with self.assertRaisesRegex(RuntimeError, "foreign"):
            self.check()

    def test_ready_point_rejected(self):
        wrong = payload()
        wrong["index_state"] = "ready"
        self.client.retrieve.side_effect = lambda **kw: [
            SimpleNamespace(id=i, payload=wrong) for i in kw["ids"]
        ]
        with self.assertRaisesRegex(RuntimeError, "non-staging"):
            self.check()

    def test_missing_points_rejected(self):
        self.client.retrieve.side_effect = lambda **kw: []
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            self.check()

    def test_unexpected_scoped_points_rejected(self):
        self.count.return_value = 100
        with self.assertRaisesRegex(RuntimeError, "extra or missing"):
            verify_staged_vector_ids(self.client, identity(), "tok", ids(3), lease_guard=lambda: True)

    def test_duplicate_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.check([ids(1)[0], ids(1)[0]])

    def test_empty_expected_ids_rejected(self):
        with self.assertRaisesRegex(ValueError, "At least"):
            self.check([])

    def test_invalid_input_rejected(self):
        with self.assertRaises(ValueError):
            self.check(ids(1), batch_size=0)
        with self.assertRaises(ValueError):
            self.check(ids(1), batch_size=129)
        with self.assertRaises(ValueError):
            self.check(["bad-id"])


if __name__ == "__main__":
    unittest.main()
