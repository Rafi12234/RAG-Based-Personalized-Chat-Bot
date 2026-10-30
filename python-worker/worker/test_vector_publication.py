"""Offline publication-contract tests. Never touch real Qdrant or MySQL."""
from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from .production_qdrant import READY, STAGING, PRODUCTION_COLLECTION
from .qdrant_indexing import IndexIdentity
from .vector_publication import (
    _validate_ids, publish_verified_staging, MAX_PUBLICATION_IDS,
)

ID1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ID2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class FieldCondition:
    def __init__(self, key, match):
        self.key, self.match = key, match


class MatchValue:
    def __init__(self, value):
        self.value = value


class HasIdCondition:
    def __init__(self, has_id):
        self.has_id = has_id


class Filter:
    def __init__(self, must):
        self.must = must


FAKE_MODELS = SimpleNamespace(FieldCondition=FieldCondition, MatchValue=MatchValue,
                              HasIdCondition=HasIdCondition, Filter=Filter)


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.identity = IndexIdentity("org", "kb", "doc", "version", "job", "document.pdf")
        self.states = {ID1: STAGING, ID2: STAGING}
        self.client = MagicMock()
        self.client.retrieve.side_effect = self.retrieve
        self.client.set_payload.side_effect = self.set_payload
        self.guard = lambda: True
        self.models_patch = patch.dict(sys.modules, {
            "qdrant_client": types.SimpleNamespace(models=FAKE_MODELS)
        })
        self.models_patch.start()
        self.addCleanup(self.models_patch.stop)
        self.schema_patch = patch("worker.vector_publication.inspect_collection",
                                  return_value=SimpleNamespace(exists=True, compatible=True,
                                                               missing_indexes=()))
        self.schema_patch.start()
        self.addCleanup(self.schema_patch.stop)

    def payload(self, state):
        return {
            "organization_id": "org", "knowledge_base_id": "kb", "document_id": "doc",
            "document_version_id": "version", "job_id": "job", "processing_token": "token",
            "index_state": state,
        }

    def retrieve(self, *, ids, **kw):
        return [SimpleNamespace(id=i, payload=self.payload(self.states[i]))
                for i in ids if i in self.states]

    def set_payload(self, *, collection_name, points, payload, wait):
        self.assertEqual(collection_name, PRODUCTION_COLLECTION)
        self.assertTrue(wait)
        self.assertEqual(payload, {"index_state": READY})
        clauses = {c.key: c.match.value for c in points.must if isinstance(c, FieldCondition)}
        self.assertEqual(clauses, {**self.payload(STAGING)})
        candidates = next(c.has_id for c in points.must if isinstance(c, HasIdCondition))
        for id_ in candidates:
            if self.states.get(id_) == STAGING:
                self.states[id_] = READY
        return SimpleNamespace(status="completed")

    def publish(self, ids=None, guard=None, **kw):
        return publish_verified_staging(self.client, self.identity, "token",
                                        [ID1, ID2] if ids is None else ids,
                                        lease_guard=self.guard if guard is None else guard, **kw)

    def test_staged_promoted_and_verified(self):
        self.assertEqual(self.publish(), 2)
        self.assertEqual(self.states, {ID1: READY, ID2: READY})
        self.client.set_payload.assert_called_once()

    def test_repeat_is_idempotent(self):
        self.publish()
        self.client.reset_mock()
        self.client.retrieve.side_effect = self.retrieve
        self.client.set_payload.side_effect = self.set_payload
        self.assertEqual(self.publish(), 2)
        self.client.set_payload.assert_not_called()

    def test_resume_one_already_ready(self):
        self.states[ID1] = READY
        self.assertEqual(self.publish(), 2)
        ids = next(c.has_id for c in self.client.set_payload.call_args.kwargs["points"].must
                   if isinstance(c, HasIdCondition))
        self.assertEqual(ids, [ID2])

    def test_foreign_tenant_rejected_before_mutation(self):
        original = self.payload
        self.payload = lambda state: dict(original(state), organization_id="wrong")
        with self.assertRaises(RuntimeError): self.publish()
        self.client.set_payload.assert_not_called()

    def test_other_token_rejected_before_mutation(self):
        original = self.payload
        self.payload = lambda state: dict(original(state), processing_token="old")
        with self.assertRaises(RuntimeError): self.publish()
        self.client.set_payload.assert_not_called()

    def test_extra_id_rejected(self):
        old = self.client.retrieve.side_effect
        self.client.retrieve.side_effect = lambda **kwargs: old(**kwargs) + [SimpleNamespace(id=ID1,payload=self.payload(STAGING))]
        with self.assertRaises(RuntimeError): self.publish()
        self.client.set_payload.assert_not_called()

    def test_missing_point_rejected(self):
        self.states.pop(ID2)
        with self.assertRaises(RuntimeError): self.publish()
        self.client.set_payload.assert_not_called()

    def test_lease_missing_rejected(self):
        with self.assertRaises(RuntimeError): self.publish(guard=lambda: False)
        self.client.retrieve.assert_not_called()
        self.client.set_payload.assert_not_called()

    def test_lease_lost_before_write(self):
        calls = iter([True, True, False])
        with self.assertRaises(RuntimeError): self.publish(guard=lambda: next(calls))
        self.client.set_payload.assert_not_called()

    def test_lease_lost_after_write(self):
        calls = iter([True, True, True, False])
        with self.assertRaises(RuntimeError): self.publish(guard=lambda: next(calls))
        self.client.set_payload.assert_called_once()
        self.assertEqual(self.states[ID1], READY)

    def test_write_did_not_finish_rejected(self):
        self.client.set_payload.side_effect = lambda **kwargs: None
        with self.assertRaises(RuntimeError): self.publish()

    def test_wrong_chunk_list_rejected_before_writes(self):
        for ids in ([], [ID1, ID1], ["bad"], [ID1.upper()], "wrong"):
            with self.subTest(ids=ids), self.assertRaises((ValueError, TypeError)):
                self.publish(ids=ids)
        self.client.set_payload.assert_not_called()

    def test_validate_ids_bound(self):
        def lots():
            from uuid import UUID
            for i in range(MAX_PUBLICATION_IDS + 1):
                yield str(UUID(int=i + 1))
        with self.assertRaises(ValueError): _validate_ids(lots())

    def test_batch_splitting(self):
        self.assertEqual(self.publish(batch_size=1), 2)
        self.assertEqual(self.client.set_payload.call_count, 2)

    def test_wrong_batch_size_fails(self):
        with self.assertRaises(ValueError): self.publish(batch_size=0)
        self.client.set_payload.assert_not_called()

    def test_other_document_version_rejected(self):
        original = self.payload
        self.payload = lambda state: dict(original(state), document_version_id="different")
        with self.assertRaises(RuntimeError): self.publish()
        self.client.set_payload.assert_not_called()

    def test_no_publish_if_schema_missing(self):
        with patch("worker.vector_publication.inspect_collection",
                   return_value=SimpleNamespace(exists=False,compatible=True,missing_indexes=())):
            with self.assertRaises(RuntimeError): self.publish()
        self.client.set_payload.assert_not_called()

    def test_no_mysql_functions_invoked(self):
        self.publish()
        self.assertEqual(self.client.set_payload.call_count, 1)


if __name__ == "__main__":
    unittest.main()
