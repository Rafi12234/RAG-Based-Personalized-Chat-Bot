"""Phase 4.3C offline generation-registry transaction and CAS safety tests.

Only in-memory SQL fakes. No MySQL, RustFS, Qdrant, vectors or user data.
GitHub's backend CI additionally migrates an empty isolated MySQL service.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import os
import unittest
from unittest.mock import patch

from .auto_ingestion_service import PROTECTED_QA_KBS
from .hierarchical_shadow import SHADOW_COLLECTION
from .hierarchy_shadow_audit import ShadowIntegrityReport
from .hierarchy_vector_fidelity import VectorFidelityReport
from .hierarchy_generation_registry import (
    RegistrySafetyError, register_verified_candidate,
    select_verified_candidate, revoke_selection,
)
from .qdrant_indexing import IndexIdentity
from .test_hierarchy_manifest_store import make_ledger


class FakeCursor:
    def __init__(self, connection):
        self.db=connection
        self.rowcount=0
        self.last=None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.db.queries.append((sql, params))
        self.last=None
        if "INSERT INTO ShadowIndexCandidate" in sql:
            if not self.db.claim_valid:
                self.rowcount=0
                return
            generation=params[0]
            if generation in self.db.candidates:
                raise RuntimeError("Duplicate immutable candidate")
            self.db.candidates[generation]={
                "source":params[1],"records":params[2],"count":params[3]
            }
            self.rowcount=1
            return
        if "SELECT 1 AS eligible FROM ShadowIndexCandidate" in sql:
            generation=params[1]
            if self.db.claim_valid and generation in self.db.candidates:
                self.last={"eligible":1}
            self.rowcount=0
            return
        if "SELECT revision, generationDigest, queryEnabled" in sql:
            self.last=deepcopy(self.db.pointer)
            self.rowcount=0
            return
        if "INSERT INTO ShadowIndexSelection" in sql:
            if self.db.pointer is not None:
                raise RuntimeError("Concurrent pointer creation")
            self.db.pointer={"revision":1,"generationDigest":params[1],
                             "queryEnabled":False}
            self.rowcount=1
            return
        if "UPDATE ShadowIndexSelection AS s" in sql:
            version,generation,rev,doc,kb,org=params
            if (self.db.pointer and self.db.pointer["revision"]==rev and
                    self.db.pointer["generationDigest"]==generation and
                    (doc,kb,org)==("doc1","kb1","org1")):
                self.db.pointer.update(
                    generationDigest=None,queryEnabled=False,revision=rev+1)
                self.rowcount=1
            else:
                self.rowcount=0
            return
        if "UPDATE ShadowIndexSelection" in sql:
            generation,version,rev=params
            if (self.db.pointer is not None and
                    self.db.pointer["revision"]==rev and
                    not self.db.pointer["queryEnabled"]):
                self.db.pointer.update(
                    generationDigest=generation,queryEnabled=False,revision=rev+1)
                self.rowcount=1
            else:
                self.rowcount=0
            return
        raise AssertionError("Unexpected SQL path")

    def fetchone(self):
        return self.last


class FakeConnection:
    def __init__(self):
        self.pointer=None
        self.candidates={}
        self.queries=[]
        self.claim_valid=True
        self.begins=0
        self.commits=0
        self.rollbacks=0
        self._snapshot=None
        self._cursor=FakeCursor(self)

    def begin(self):
        self._snapshot=(deepcopy(self.pointer), deepcopy(self.candidates))
        self.begins+=1

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits+=1
        self._snapshot=None

    def rollback(self):
        self.rollbacks+=1
        if self._snapshot is not None:
            self.pointer,self.candidates=self._snapshot
        self._snapshot=None


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.patch_env=patch.dict(os.environ,{
            "DOCMIND_ENABLE_SHADOW_REGISTRY":"YES_FOR_TESTING",
            "NODE_ENV":"test",
        })
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)
        self.db=FakeConnection()
        self.identity=IndexIdentity("org1","kb1","doc1","ver","job1","book.pdf")
        self.passages,self.ledger=make_ledger()
        m=self.ledger.manifest
        self.provenance=ShadowIntegrityReport(
            SHADOW_COLLECTION,m.digest,m.passage_count,m.passage_count)
        self.vectors=VectorFidelityReport(
            SHADOW_COLLECTION,m.digest,m.passage_count,
            "intfloat/multilingual-e5-small",1.0)

    def register(self,**kw):
        return register_verified_candidate(
            self.db,self.identity,self.ledger,
            kw.get("source",self.provenance),
            kw.get("vectors",self.vectors),"token",
            lease_guard=kw.get("guard",lambda:True))

    def select(self,rev=0,guard=lambda:True):
        return select_verified_candidate(
            self.db,self.identity,self.ledger.manifest.digest,"token",
            expected_revision=rev,lease_guard=guard)

    def revoke(self,rev,gen=None):
        return revoke_selection(
            self.db,self.identity,gen or self.ledger.manifest.digest,
            expected_revision=rev)

    def test_registration_uses_fenced_sql_and_no_source_text(self):
        self.assertEqual(self.register(),self.ledger.manifest.digest)
        self.assertEqual(self.db.commits,1)
        sql,args=self.db.queries[0]
        self.assertIn("j.processingToken = %s",sql)
        self.assertIn("processingLeaseUntil > UTC_TIMESTAMP(3)",sql)
        self.assertIn("d.deletedAt IS NULL",sql)
        self.assertIn("kb.organizationId = %s",sql)
        self.assertIn("v.status = 'PROCESSING'",sql)
        self.assertFalse(any(isinstance(v,str) and "Original book" in v
                             for v in args))
        self.assertIsNone(self.db.pointer)

    def test_missing_valid_source_and_vector_audits_rejected_before_db(self):
        cases=[
            {"source":replace(self.provenance,points_verified=0)},
            {"source":replace(self.provenance,generation="b"*64)},
            {"source":replace(self.provenance,authorized_to_publish=True)},
            {"vectors":replace(self.vectors,points_verified=0)},
            {"vectors":replace(self.vectors,min_cosine=.5)},
            {"vectors":replace(self.vectors,min_cosine=float("nan"))},
            {"vectors":replace(self.vectors,min_cosine=float("inf"))},
            {"vectors":replace(self.vectors,vectors_recomputed=False)},
        ]
        for kw in cases:
            with self.subTest(kw=kw):
                with self.assertRaises(RegistrySafetyError):
                    self.register(**kw)
        self.assertEqual(self.db.begins,0)

    def test_stale_claim_and_mid_lease_loss_rollback(self):
        self.db.claim_valid=False
        with self.assertRaisesRegex(RegistrySafetyError,"Stale claim"):
            self.register()
        self.assertEqual(self.db.commits,0)
        self.assertEqual(self.db.rollbacks,1)
        calls=iter([True,True,False])
        self.db.claim_valid=True
        with self.assertRaisesRegex(RegistrySafetyError,"lease"):
            self.register(guard=lambda:next(calls))
        self.assertEqual(self.db.candidates,{})
        self.assertEqual(self.db.commits,0)

    def test_candidate_is_immutable_no_duplicate_overwrite(self):
        self.register()
        with self.assertRaisesRegex(RuntimeError,"Duplicate"):
            self.register()
        self.assertEqual(len(self.db.candidates),1)
        self.assertEqual(self.db.commits,1)

    def test_select_then_cas_replace_and_rollback_revocation(self):
        self.register()
        m=self.ledger.manifest.digest
        self.assertEqual(self.select(rev=0),1)
        self.assertEqual(self.db.pointer["generationDigest"],m)
        self.assertFalse(self.db.pointer["queryEnabled"])
        self.assertEqual(self.select(rev=1),2)
        self.assertEqual(self.revoke(2),3)
        self.assertIsNone(self.db.pointer["generationDigest"])
        self.assertFalse(self.db.pointer["queryEnabled"])
        self.assertEqual(self.db.pointer["revision"],3)
        self.assertFalse(any("ready" in q[0].lower() and "UPDATE" in q[0]
                             for q in self.db.queries))

    def test_stale_compare_and_swap_refuses_writes(self):
        self.register()
        self.select(rev=0)
        before=deepcopy(self.db.pointer)
        with self.assertRaisesRegex(RegistrySafetyError,"revision"):
            self.select(rev=0)
        self.assertEqual(self.db.pointer,before)
        with self.assertRaisesRegex(RegistrySafetyError,"revocation"):
            self.revoke(rev=8)
        self.assertEqual(self.db.pointer,before)

    def test_new_selection_requires_registered_candidate(self):
        with self.assertRaisesRegex(RegistrySafetyError,"not eligible"):
            self.select(rev=0)
        self.assertIsNone(self.db.pointer)

    def test_stale_claim_rejects_selection_even_with_candidate(self):
        self.register()
        self.db.claim_valid=False
        with self.assertRaisesRegex(RegistrySafetyError,"not eligible"):
            self.select()
        self.assertIsNone(self.db.pointer)

    def test_revocation_does_not_require_expired_worker_lease(self):
        self.register()
        self.select()
        self.db.claim_valid=False
        self.assertEqual(self.revoke(1),2)
        self.assertFalse(self.db.pointer["queryEnabled"])

    def test_selection_with_query_flag_true_fails_closed(self):
        self.register()
        self.select()
        self.db.pointer["queryEnabled"]=True
        with self.assertRaisesRegex(RegistrySafetyError,"query enabled"):
            self.select(rev=1)

    def test_protected_kb_rejected_for_all_write_paths(self):
        self.identity=IndexIdentity(
            "org1",PROTECTED_QA_KBS[0],"doc1","ver","job1","book.pdf")
        with self.assertRaisesRegex(RegistrySafetyError,"Protected"):
            self.register()
        with self.assertRaisesRegex(RegistrySafetyError,"Protected"):
            self.select()
        with self.assertRaisesRegex(RegistrySafetyError,"Protected"):
            self.revoke(1)
        self.assertEqual(self.db.begins,0)

    def test_disabled_by_default_and_reject_production(self):
        with patch.dict(os.environ,{"DOCMIND_ENABLE_SHADOW_REGISTRY":""}):
            with self.assertRaisesRegex(RegistrySafetyError,"disabled"):
                self.register()
        with patch.dict(os.environ,{"NODE_ENV":"production"}):
            with self.assertRaisesRegex(RegistrySafetyError,"disabled"):
                self.register()
        self.assertEqual(self.db.begins,0)

    def test_revision_limit_and_noncanonical_digest_rejected(self):
        self.register()
        with self.assertRaises(ValueError):
            self.select(rev=-1)
        with self.assertRaises(ValueError):
            select_verified_candidate(
                self.db,self.identity,"../invalid","token",
                expected_revision=0,lease_guard=lambda:True)

    def test_failed_revocation_never_clears_different_generation(self):
        self.register()
        self.select()
        with self.assertRaisesRegex(RegistrySafetyError,"revocation"):
            self.revoke(1,gen="f"*64)
        self.assertEqual(self.db.pointer["generationDigest"],self.ledger.manifest.digest)


if __name__=="__main__":
    unittest.main()
