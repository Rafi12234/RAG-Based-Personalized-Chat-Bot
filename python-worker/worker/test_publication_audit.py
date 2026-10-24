"""Offline audit tests; never connect to live services."""
from __future__ import annotations

import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from .qdrant_indexing import IndexIdentity
from .publication_audit import (PublicationCounts, audit_publication_counts,
                                interpret_publication_state)


class FieldCondition:
    def __init__(self, key, match): self.key, self.match = key, match
class MatchValue:
    def __init__(self, value): self.value = value
class Filter:
    def __init__(self, must): self.must = must


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.identity = IndexIdentity("org", "kb", "doc", "version", "job", "doc.pdf")
        models = SimpleNamespace(FieldCondition=FieldCondition, MatchValue=MatchValue, Filter=Filter)
        self.patch_module = patch.dict(sys.modules, {"qdrant_client": types.SimpleNamespace(models=models)})
        self.patch_module.start()
        self.addCleanup(self.patch_module.stop)
        self.patch_schema = patch("worker.publication_audit.inspect_collection",
                                  return_value=SimpleNamespace(exists=True, compatible=True,
                                                               missing_indexes=()))
        self.patch_schema.start()
        self.addCleanup(self.patch_schema.stop)
        self.client = MagicMock()
        self.client.count.side_effect = [SimpleNamespace(count=2), SimpleNamespace(count=3)]

    def test_scoped_read_only_counts(self):
        self.assertEqual(audit_publication_counts(self.client, self.identity), PublicationCounts(2, 3))
        self.assertEqual(self.client.count.call_count, 2)
        calls = self.client.count.call_args_list
        for call, state in zip(calls, ["staging", "ready"]):
            args = call.kwargs
            self.assertTrue(args["exact"])
            clauses = {c.key:c.match.value for c in args["count_filter"].must}
            self.assertEqual(clauses, {"organization_id":"org", "knowledge_base_id":"kb", "document_id":"doc",
                                       "document_version_id":"version", "index_state":state})
        self.client.set_payload.assert_not_called()
        self.client.delete.assert_not_called()

    def test_missing_collection_prevents_count(self):
        with patch("worker.publication_audit.inspect_collection",
                   return_value=SimpleNamespace(exists=False, compatible=True, missing_indexes=())):
            with self.assertRaises(RuntimeError): audit_publication_counts(self.client, self.identity)
        self.client.count.assert_not_called()

    def test_incompatible_collection_prevents_count(self):
        with patch("worker.publication_audit.inspect_collection",
                   return_value=SimpleNamespace(exists=True, compatible=False, missing_indexes=())):
            with self.assertRaises(RuntimeError): audit_publication_counts(self.client, self.identity)

    def test_reject_bad_identity(self):
        with self.assertRaises(TypeError): audit_publication_counts(self.client, "org")

    def test_negative_count_invalid(self):
        self.client.count.side_effect = [SimpleNamespace(count=-1)]
        with self.assertRaises(RuntimeError): audit_publication_counts(self.client, self.identity)

    def test_queued_no_vectors(self):
        self.assertEqual(interpret_publication_state("QUEUED","QUEUED",PublicationCounts(0,0)),"not_started")

    def test_queued_with_leftovers(self):
        self.assertEqual(interpret_publication_state("QUEUED","QUEUED",PublicationCounts(2,0)),"review_orphan_vectors")

    def test_running_mixed(self):
        self.assertEqual(interpret_publication_state("RUNNING","PROCESSING",PublicationCounts(2,3)),"partial_publication_possible")

    def test_running_ready(self):
        self.assertEqual(interpret_publication_state("RUNNING","PROCESSING",PublicationCounts(0,3)),"publication_possible_verify_manifest")

    def test_running_staging(self):
        self.assertEqual(interpret_publication_state("RUNNING","PROCESSING",PublicationCounts(3,0)),"staging_possible_verify_manifest")

    def test_ready_without_vectors(self):
        self.assertEqual(interpret_publication_state("COMPLETED","READY",PublicationCounts(0,0)),"warning_ready_without_vectors")

    def test_completed_ready(self):
        self.assertEqual(interpret_publication_state("COMPLETED","READY",PublicationCounts(0,3)),"completed_recheck_manifest")

    def test_failed_with_leftovers(self):
        self.assertEqual(interpret_publication_state("FAILED","FAILED",PublicationCounts(1,0)),"review_inconsistent_status_or_leftovers")

    def test_failed_without_vectors(self):
        self.assertEqual(interpret_publication_state("FAILED","FAILED",PublicationCounts(0,0)),"no_vectors_observed")


if __name__ == "__main__": unittest.main()
