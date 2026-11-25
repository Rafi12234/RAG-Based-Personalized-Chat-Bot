"""Read-only hierarchical shadow integrity audit tests against local fakes."""
from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from .hierarchy_shadow_audit import audit_shadow_generation, ShadowAuditError
from .hierarchical_shadow import (
    initialize_shadow_collection, iter_shadow_embeddings, stage_shadow_index,
)
from .qdrant_indexing import IndexIdentity
from .test_hierarchical_shadow import FakeQdrant, MockModels, FakeModel
from .test_hierarchy_manifest_store import make_ledger


class AuditQdrant(FakeQdrant):
    def __init__(self):
        super().__init__()
        self.scroll_reads = 0
        self.count_override = None
        self.text_requested = 0

    def count(self, **kw):
        result = super().count(**kw)
        if self.count_override is not None:
            result.count = self.count_override
        return result

    def scroll(self, *, collection_name, scroll_filter,
               limit, offset, with_payload, with_vectors):
        assert with_vectors is False
        assert collection_name == "docmind_documents_hierarchy_e5_384_shadow_v2"
        assert "text" in with_payload
        self.text_requested += 1
        scope = {x.key: x.match.value for x in scroll_filter.must}
        matching = [p for p in self.points.values() if all(
            p.payload.get(key) == value for key, value in scope.items())]
        start = offset or 0
        self.scroll_reads += 1
        batch = matching[start:start+limit]
        after = start + len(batch)
        return batch, after if after < len(matching) else None


class ShadowAuditTests(unittest.TestCase):
    def setUp(self):
        self.identity = IndexIdentity("org1", "kb1", "doc1", "ver", "job1", "book.pdf")
        self.passages, self.ledger = make_ledger()
        self.client = AuditQdrant()
        from . import hierarchical_shadow, hierarchy_shadow_audit
        self.patch1 = patch.object(hierarchical_shadow, "_models", return_value=MockModels)
        self.patch2 = patch.object(hierarchy_shadow_audit, "_models", return_value=MockModels)
        self.patch1.start()
        self.patch2.start()
        self.addCleanup(self.patch1.stop)
        self.addCleanup(self.patch2.stop)
        initialize_shadow_collection(self.client, explicit_create=True)
        embeddings = iter_shadow_embeddings(self.passages, FakeModel())
        stage_shadow_index(self.client, self.identity, self.ledger.manifest,
                           embeddings, "token1", lease_guard=lambda:True)

    def audit(self, **kw):
        return audit_shadow_generation(
            self.client, self.identity, self.ledger, "token1",
            lease_guard=lambda: True, batch_size=2, **kw)

    def test_verified_complete_shadow_but_no_publication(self):
        before = self.client.upserts
        report = self.audit()
        self.assertEqual(report.points_verified, 5)
        self.assertEqual(report.text_hashes_verified, 5)
        self.assertFalse(report.vector_values_verified)
        self.assertFalse(report.query_enabled)
        self.assertFalse(report.authorized_to_publish)
        self.assertFalse(report.safe_to_replay_orphans)
        self.assertFalse(report.snapshot_is_transactional)
        self.assertEqual(self.client.upserts, before)
        self.assertGreater(self.client.scroll_reads, 2)

    def test_text_mutation_refused(self):
        point = next(iter(self.client.points.values()))
        point.payload["text"] = "Corrupted source text"
        with self.assertRaisesRegex(ShadowAuditError, "text hash"):
            self.audit()

    def test_foreign_attempt_in_same_version_refused(self):
        point = next(iter(self.client.points.values()))
        point.payload["processing_token"] = "foreign-token"
        with self.assertRaisesRegex(ShadowAuditError, "processing_token"):
            self.audit()

    def test_wrong_generation_refused(self):
        point = next(iter(self.client.points.values()))
        point.payload["index_generation"] = "f" * 64
        with self.assertRaisesRegex(ShadowAuditError, "index_generation"):
            self.audit()

    def test_ready_point_is_not_reused(self):
        point = next(iter(self.client.points.values()))
        point.payload["index_state"] = "ready"
        with self.assertRaisesRegex(ShadowAuditError, "index_state"):
            self.audit()

    def test_foreign_doc_job_refused(self):
        point = next(iter(self.client.points.values()))
        point.payload["job_id"] = "other"
        with self.assertRaisesRegex(ShadowAuditError, "job_id"):
            self.audit()

    def test_missing_point_refused_preflight(self):
        del self.client.points[next(iter(self.client.points))]
        with self.assertRaisesRegex(ShadowAuditError, "count mismatch"):
            self.audit()

    def test_duplicate_or_new_point_refused_preflight(self):
        other = copy.deepcopy(next(iter(self.client.points.values())))
        other.id = "00000000-0000-4000-8000-000000000007"
        self.client.points[other.id] = other
        with self.assertRaisesRegex(ShadowAuditError, "count mismatch"):
            self.audit()

    def test_wrong_section_or_locator_refused(self):
        point = next(iter(self.client.points.values()))
        point.payload["parent_id"] = "different"
        with self.assertRaisesRegex(ShadowAuditError, "parent_id"):
            self.audit()

    def test_lease_loss_blocks_scanning(self):
        before = self.client.count_calls
        with self.assertRaisesRegex(ShadowAuditError, "lease"):
            audit_shadow_generation(
                self.client, self.identity, self.ledger,
                "token1", lease_guard=lambda: False)
        self.assertEqual(self.client.count_calls, before)

    def test_count_change_during_scroll_refused(self):
        self.client.count_override = 6
        with self.assertRaisesRegex(ShadowAuditError, "count mismatch"):
            self.audit()

    def test_other_organization_identity_refused(self):
        other = IndexIdentity("foreign", "kb1", "doc1", "ver", "job1","book.pdf")
        with self.assertRaisesRegex(ShadowAuditError, "count mismatch"):
            audit_shadow_generation(
                self.client, other, self.ledger, "token1",
                lease_guard=lambda: True)

    def test_embedding_input_hash_is_independent_check(self):
        # For PDF the embedding input equals original passage text, so a
        # source hash mismatch is sufficient. DOCX context is independently
        # derived from section_path by the verifier.
        record = self.ledger.records[0]
        from dataclasses import replace
        bad = replace(record, embedding_text_sha256="d" * 64)
        from .hierarchy_manifest_store import HierarchyLedger
        from hashlib import sha256
        from dataclasses import asdict
        from .hierarchical_index import _canonical
        revised = replace(self.ledger, records=(bad, *self.ledger.records[1:]))
        revised = replace(revised, records_sha256=sha256(
            _canonical([asdict(r) for r in revised.records])).hexdigest())
        # Valid signed records can still expose an incorrect embedding prefix.
        with self.assertRaisesRegex(ShadowAuditError, "text hash"):
            audit_shadow_generation(self.client,self.identity,revised,"token1",
                                    lease_guard=lambda: True)


if __name__ == "__main__":
    unittest.main()
