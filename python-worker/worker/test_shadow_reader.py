"""Offline reader orchestration and denial tests; no live services."""
from dataclasses import replace
from hashlib import sha256
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from . import shadow_reader as reader
from .hierarchical_shadow import initialize_shadow_collection, iter_shadow_embeddings, stage_shadow_index
from .test_hierarchical_shadow import MockModels, FakeModel, FakeQdrant
from .test_hierarchy_manifest_store import make_ledger
from .qdrant_indexing import IndexIdentity
from .auto_ingestion_service import PROTECTED_QA_KBS

PIN = "a" * 64
TOKEN = "original-attempt"


class Conn:
    def __init__(self, rows, calls):
        self.rows, self.calls = rows, calls

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchall(self):
        return self.rows

    def close(self):
        self.calls.append("closed")


class FakeSearch(FakeQdrant):
    def __init__(self):
        super().__init__()
        self.query_count = 0
        self.query_args = None
        self.override = None

    def query_points(self, **kwargs):
        self.query_count += 1
        self.query_args = kwargs
        if self.override is not None:
            return SimpleNamespace(points=self.override)
        scope = {x.key: x.match.value for x in kwargs["query_filter"].must}
        points = [SimpleNamespace(id=p.id, payload=p.payload.copy(), score=.9)
                  for p in self.points.values()
                  if all(p.payload.get(k) == v for k, v in scope.items())]
        return SimpleNamespace(points=points[:kwargs["limit"]])


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.identity = IndexIdentity("org1", "kb1", "doc1", "ver", "job1", "book.pdf")
        self.session = reader.SessionScope("user1", "session1", "f"*64)
        passages, self.ledger = make_ledger()
        self.row = dict(revision=1, title="Trusted SQL title", verifiedAttempt=1,
                        passageCount=self.ledger.manifest.passage_count,
                        sourceSha256=self.ledger.manifest.source_sha256,
                        recordsSha256=self.ledger.records_sha256,
                        modelPinSha256=PIN, attemptTokenSha256=sha256(TOKEN.encode()).hexdigest())
        self.calls = []
        self.snapshots = [[self.row.copy()] for _ in range(3)]
        self.q = FakeSearch()
        self.model = SimpleNamespace(encode=lambda _: SimpleNamespace(ids=[1, 2]),
                                     embed=lambda texts, batch_size: [(1.,)+(0.,)*383])
        for p in (patch.dict(os.environ, {reader.FLAG:"YES_FOR_TESTING", "NODE_ENV":"test"}),
                  patch.object(reader, "_model", return_value=PIN),
                  patch.object(reader, "_models", return_value=MockModels),
                  patch("worker.hierarchical_shadow._models", return_value=MockModels)):
            p.start()
            self.addCleanup(p.stop)
        initialize_shadow_collection(self.q, explicit_create=True)
        stage_shadow_index(self.q, self.identity, self.ledger.manifest,
                           iter_shadow_embeddings(passages, FakeModel()), TOKEN,
                           lease_guard=lambda: True)

    def factory(self):
        return Conn(self.snapshots.pop(0), self.calls)

    def search(self, **kwargs):
        args = dict(connection_factory=self.factory, client=self.q, session=self.session,
                    identity=self.identity, ledger=self.ledger, processing_token=TOKEN,
                    model=self.model, question="Which page?", top_k=2)
        args.update(kwargs)
        return reader.search_verified_shadow(**args)

    def test_valid_search_and_exact_filter_and_final_fence(self):
        hits = self.search()
        self.assertEqual(len(hits), 2)
        self.assertTrue(all(x.document_title == "Trusted SQL title" for x in hits))
        self.assertTrue(all(x.page_number and x.source_locator.startswith("pdf:") for x in hits))
        self.assertEqual(self.calls.count("closed"), 3)
        args = self.q.query_args
        f = {x.key:x.match.value for x in args["query_filter"].must}
        self.assertEqual(f["organization_id"], "org1")
        self.assertEqual(f["document_version_id"], "ver")
        self.assertEqual(f["index_generation"], self.ledger.manifest.digest)
        self.assertEqual(f["processing_token"], TOKEN)
        self.assertEqual(f["index_state"], "staging")
        self.assertFalse(args["with_vectors"])
        self.assertEqual(self.q.upserts, 1)
        self.assertNotIn(TOKEN, repr(hits))

    def test_no_authorization_or_legacy_candidate_no_search(self):
        for rows in ([], [dict(self.row, modelPinSha256=None)],
                     [dict(self.row, attemptTokenSha256="0"*64)],
                     [dict(self.row, recordsSha256="0"*64)], [self.row, self.row]):
            self.snapshots = [rows]
            with self.assertRaises(reader.ShadowReadError):
                self.search()
        self.assertEqual(self.q.query_count, 0)

    def test_revocation_before_or_after_search_aborts(self):
        for snapshots, searches in (([[self.row], []], 0),
                                   ([[self.row], [self.row], []], 1),
                                   ([[self.row], [self.row], [dict(self.row, revision=2)]], 1)):
            self.snapshots = snapshots
            self.q.query_count = 0
            with self.assertRaises(reader.ShadowReadError):
                self.search()
            self.assertEqual(self.q.query_count, searches)

    def test_foreign_tampered_duplicate_nonfinite_and_oversized_results(self):
        p = next(iter(self.q.points.values()))
        good = SimpleNamespace(id=p.id, payload=p.payload.copy(), score=.9)
        bads = [
            [SimpleNamespace(id=p.id, payload=dict(p.payload, organization_id="other"), score=.9)],
            [SimpleNamespace(id=p.id, payload=dict(p.payload, text="forged"), score=.9)],
            [SimpleNamespace(id=p.id, payload=p.payload, score=float("nan"))],
            [good, good], [good]*41,
        ]
        for hits in bads:
            self.snapshots = [[self.row], [self.row], [self.row]]
            self.q.override = hits
            with self.assertRaises(RuntimeError):
                self.search()
        self.assertEqual(self.q.upserts, 1)

    def test_disabled_production_protected_and_bad_limits(self):
        for env in ("production", ""):
            with patch.dict(os.environ, {"NODE_ENV":env}):
                with self.assertRaises(reader.ShadowReadError):
                    self.search()
        with self.assertRaises(RuntimeError):
            self.search(identity=replace(self.identity, knowledge_base_id=PROTECTED_QA_KBS[0]))
        for k in (0, 11, True):
            with self.assertRaises(reader.ShadowReadError):
                self.search(top_k=k)
        self.assertEqual(self.calls, [])

    def test_question_token_budget(self):
        self.model.encode = lambda _: SimpleNamespace(ids=[1]*481)
        with self.assertRaisesRegex(reader.ShadowReadError, "token"):
            self.search()
        self.assertEqual(self.q.query_count, 0)


if __name__ == "__main__":
    unittest.main()
