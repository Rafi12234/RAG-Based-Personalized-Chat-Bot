"""Read-only 50k Qdrant reconciliation tests; no services, no user data."""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid5, NAMESPACE_URL

from .large_vector_audit import inspect_large_version, VectorAuditError
from .qdrant_indexing import IndexIdentity


@dataclass
class Point:
    id: str
    payload: dict


class InMemoryQdrant:
    def __init__(self, rows):
        self.rows = list(rows)
        self.write_calls = []
        self.count_calls = 0
        self.scroll_calls = 0

    def count(self, *, collection_name, count_filter, exact):
        self.count_calls += 1
        assert exact is True
        return SimpleNamespace(count=len(self.rows))

    def scroll(self, *, collection_name, scroll_filter, limit,
               offset, with_payload, with_vectors):
        self.scroll_calls += 1
        assert not with_vectors
        assert "text" not in with_payload
        assert "processing_token" in with_payload
        start = offset or 0
        page = self.rows[start:start + limit]
        nxt = start + len(page)
        return page, (nxt if nxt < len(self.rows) else None)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.identity = IndexIdentity("org1","kb1","doc1","ver1","job1","original.pdf")
        self.patch_schema = patch(
            "worker.large_vector_audit.inspect_collection",
            return_value=SimpleNamespace(exists=True,compatible=True,missing_indexes=()))
        self.patch_schema.start()
        self.addCleanup(self.patch_schema.stop)

        class MatchValue:
            def __init__(self, value): self.value = value
        class FieldCondition:
            def __init__(self,key,match): self.key,self.match=key,match
        class Filter:
            def __init__(self,must): self.must=must
        self.patch_models=patch.dict(
            "sys.modules", {"qdrant_client":SimpleNamespace(
                models=SimpleNamespace(MatchValue=MatchValue,
                                       FieldCondition=FieldCondition,Filter=Filter))})
        self.patch_models.start()
        self.addCleanup(self.patch_models.stop)

    def generate(self, n, *, token="attempt1", state="staging"):
        return [Point(str(uuid5(NAMESPACE_URL, f"docmind-audit-{i}")), {
            "organization_id":"org1","knowledge_base_id":"kb1",
            "document_id":"doc1","document_version_id":"ver1","job_id":"job1",
            "processing_token":token,"index_state":state,
            "chunk_index":i,"page_number":i//6+1
        }) for i in range(n)]

    def test_5000_page_50000_vector_read_only_scan(self):
        rows=self.generate(50000)
        c=InMemoryQdrant(rows)
        report=inspect_large_version(c,self.identity,page_size=256)
        self.assertEqual(report.observed_points,50000)
        self.assertEqual(report.staged_points,50000)
        self.assertGreater(c.scroll_calls,150)
        self.assertEqual(report.assessment,"metadata_only_no_expected_manifest")
        self.assertFalse(report.as_dict()["safe_to_auto_replay"])
        self.assertEqual(c.count_calls,2)
        self.assertFalse(c.write_calls)

    def test_exact_manifest_check_does_not_grant_repair(self):
        rows=self.generate(20)
        ids=[r.id for r in rows]
        report=inspect_large_version(InMemoryQdrant(rows),self.identity,expected_ids=ids)
        self.assertTrue(report.exact_manifest_match)
        self.assertFalse(report.as_dict()["authoritative_recovery_approved"])

    def test_manifest_mismatch_blocks_inference(self):
        rows=self.generate(8)
        ids=[r.id for r in rows[:-1]]
        report=inspect_large_version(InMemoryQdrant(rows),self.identity,expected_ids=ids)
        self.assertFalse(report.exact_manifest_match)
        self.assertIn("mismatch",report.assessment)

    def test_mixed_attempt_tokens_manual_review(self):
        rows=self.generate(3)
        rows[0].payload["processing_token"]="old-attempt"
        report=inspect_large_version(InMemoryQdrant(rows),self.identity)
        self.assertEqual(report.distinct_attempt_tokens,2)
        self.assertEqual(report.assessment,"mixed_attempt_tokens_manual_review")

    def test_unknown_state_refused(self):
        rows=self.generate(2);rows[1].payload["index_state"]="unknown"
        with self.assertRaisesRegex(VectorAuditError,"metadata"):
            inspect_large_version(InMemoryQdrant(rows),self.identity)

    def test_foreign_job_refused(self):
        rows=self.generate(2);rows[0].payload["job_id"]="another-job"
        with self.assertRaisesRegex(VectorAuditError,"scope"):
            inspect_large_version(InMemoryQdrant(rows),self.identity)

    def test_duplicate_scroll_uuid_refused(self):
        rows=self.generate(2)
        rows[1].id=rows[0].id
        with self.assertRaisesRegex(VectorAuditError,"Repeated"):
            inspect_large_version(InMemoryQdrant(rows),self.identity,page_size=1)

    def test_count_mismatch_refused(self):
        c=InMemoryQdrant(self.generate(2))
        orig=c.count
        def inconsistent(**kw):
            value=orig(**kw)
            if c.count_calls==2:
                value.count=3
            return value
        c.count=inconsistent
        with self.assertRaisesRegex(VectorAuditError,"count"):
            inspect_large_version(c,self.identity)

    def test_exceeds_point_limit_before_scanning(self):
        c=InMemoryQdrant(self.generate(4))
        with self.assertRaisesRegex(VectorAuditError,"bound"):
            inspect_large_version(c,self.identity,max_points=3)
        self.assertEqual(c.scroll_calls,0)

    def test_unknown_expected_manifest_rejected(self):
        rows=self.generate(2)
        with self.assertRaises(ValueError):
            inspect_large_version(InMemoryQdrant(rows),self.identity,
                                  expected_ids=[rows[0].id,rows[0].id])

    def test_oversized_page_rejected(self):
        class Malformed(InMemoryQdrant):
            def scroll(self, **kw):
                rows,_=super().scroll(**kw)
                return rows+[rows[0]],None
        with self.assertRaisesRegex(VectorAuditError,"oversized"):
            inspect_large_version(Malformed(self.generate(2)),
                                  self.identity,page_size=2)

    def test_completed_ready_points_never_auto_replayed(self):
        rows=self.generate(5,state="ready")
        report=inspect_large_version(InMemoryQdrant(rows),self.identity)
        self.assertEqual(report.ready_points,5)
        self.assertFalse(report.as_dict()["safe_to_auto_replay"])


if __name__=="__main__":
    unittest.main()
