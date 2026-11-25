"""Offline E5/Qdrant v2 numeric-fidelity gates; no service or model downloads."""
from __future__ import annotations

import copy
import math
import unittest
from unittest.mock import patch

from .hierarchy_vector_fidelity import verify_shadow_vector_fidelity, VectorFidelityError
from .hierarchical_shadow import initialize_shadow_collection, iter_shadow_embeddings, stage_shadow_index
from .qdrant_indexing import IndexIdentity
from .test_hierarchical_shadow import MockModels, FakeModel
from .test_hierarchy_manifest_store import make_ledger
from .test_hierarchy_shadow_audit import AuditQdrant


class NumericQdrant(AuditQdrant):
    def scroll(self, *, collection_name, scroll_filter, limit, offset,
               with_payload, with_vectors):
        assert with_vectors is True
        scope = {x.key: x.match.value for x in scroll_filter.must}
        matching = [p for p in self.points.values() if all(
            p.payload.get(k) == v for k,v in scope.items())]
        start = offset or 0
        page = matching[start:start + limit]
        after = start + len(page)
        self.scroll_reads += 1
        return page, (after if after < len(matching) else None)


class CountingModel(FakeModel):
    def __init__(self):
        self.calls = []
    def embed(self, texts, *, batch_size):
        self.calls.append((len(texts), batch_size))
        return super().embed(texts, batch_size=batch_size)


class FidelityTests(unittest.TestCase):
    def setUp(self):
        from . import hierarchical_shadow, hierarchy_vector_fidelity
        self.models1 = patch.object(hierarchical_shadow, "_models", return_value=MockModels)
        self.models2 = patch("worker.hierarchy_shadow_audit._models", return_value=MockModels)
        self.models1.start()
        self.models2.start()
        self.addCleanup(self.models1.stop)
        self.addCleanup(self.models2.stop)
        self.client = NumericQdrant()
        self.identity = IndexIdentity("org1","kb1","doc1","ver","job1","book.pdf")
        self.passages, self.ledger = make_ledger()
        initialize_shadow_collection(self.client, explicit_create=True)
        stage_shadow_index(
            self.client, self.identity, self.ledger.manifest,
            iter_shadow_embeddings(self.passages, FakeModel()), "token",
            lease_guard=lambda: True,
        )

    def run_proof(self, model=None, **kw):
        return verify_shadow_vector_fidelity(
            self.client, self.identity, self.ledger, "token",
            model or CountingModel(), lease_guard=lambda: True, **kw,
        )

    def test_correct_vectors_independently_recomputed(self):
        model = CountingModel()
        prior = self.client.upserts
        report = self.run_proof(model=model, batch_size=2)
        self.assertEqual(report.points_verified, len(self.passages))
        self.assertTrue(report.vectors_recomputed)
        self.assertGreaterEqual(report.min_cosine, .999)
        self.assertFalse(report.query_enabled)
        self.assertFalse(report.authorized_to_publish)
        self.assertFalse(report.snapshot_is_transactional)
        self.assertEqual(self.client.upserts, prior)
        self.assertEqual(sum(c[0] for c in model.calls),len(self.passages))
        self.assertTrue(all(c[0] <= 2 for c in model.calls))

    def test_wrong_stored_embedding_refused(self):
        point=next(iter(self.client.points.values()))
        point.vector=[0.0,1.0]+[0.0]*382
        with self.assertRaisesRegex(VectorFidelityError, "differs"):
            self.run_proof()

    def test_bad_vector_dimension_and_nan_refused(self):
        p=next(iter(self.client.points.values()))
        p.vector=[1.0,0.0]
        with self.assertRaisesRegex(VectorFidelityError, "dimension"):
            self.run_proof()
        p.vector=[math.nan]+[0.0]*383
        with self.assertRaisesRegex(VectorFidelityError, "nonfinite"):
            self.run_proof()

    def test_vector_must_be_unit_normalized(self):
        p=next(iter(self.client.points.values()))
        p.vector=[2.0]+[0.0]*383
        with self.assertRaisesRegex(VectorFidelityError, "normalized"):
            self.run_proof()

    def test_missing_or_extra_point_rejected(self):
        target=next(iter(self.client.points))
        removed=self.client.points.pop(target)
        with self.assertRaisesRegex(VectorFidelityError, "count"):
            self.run_proof()
        self.client.points[target]=removed
        extra=copy.deepcopy(removed)
        extra.id="00000000-0000-4000-8000-000000000099"
        self.client.points[extra.id]=extra
        with self.assertRaisesRegex(VectorFidelityError,"count"):
            self.run_proof()

    def test_foreign_generation_and_attempt_rejected(self):
        p=next(iter(self.client.points.values()))
        p.payload["index_generation"]="b"*64
        with self.assertRaisesRegex(VectorFidelityError,"(count|generation)"):
            self.run_proof()
        p.payload["index_generation"]=self.ledger.manifest.digest
        p.payload["processing_token"]="foreign"
        with self.assertRaisesRegex(RuntimeError,"processing_token"):
            self.run_proof()

    def test_source_text_corruption_prevents_embedding(self):
        p=next(iter(self.client.points.values()))
        p.payload["text"]="Malicious replacement book passage"
        m=CountingModel()
        with self.assertRaisesRegex(RuntimeError,"text hash"):
            self.run_proof(model=m)
        self.assertEqual(m.calls,[])

    def test_revoked_lease_stops_without_model_call(self):
        m=CountingModel()
        with self.assertRaisesRegex(VectorFidelityError,"lease"):
            verify_shadow_vector_fidelity(
                self.client,self.identity,self.ledger,"token",m,
                lease_guard=lambda:False)
        self.assertEqual(m.calls,[])

    def test_5000_page_proof_uses_bounded_batches(self):
        from .test_hierarchy_manifest_store import make_ledger
        p, ledger=make_ledger(5000)
        identity=self.identity
        client=NumericQdrant()
        initialize_shadow_collection(client,explicit_create=True)
        stage_shadow_index(client,identity,ledger.manifest,
                           iter_shadow_embeddings(p,FakeModel(),batch_size=8),
                           "token",lease_guard=lambda:True,batch_size=24)
        m=CountingModel()
        report=verify_shadow_vector_fidelity(
            client,identity,ledger,"token",m,
            lease_guard=lambda:True,batch_size=32)
        self.assertEqual(report.points_verified,5000)
        self.assertEqual(sum(a for a,b in m.calls),5000)
        self.assertTrue(all(a<=32 and b<=32 for a,b in m.calls))
        self.assertFalse(report.authorized_to_publish)


if __name__=="__main__":
    unittest.main()
