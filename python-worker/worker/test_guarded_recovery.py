"""Offline, service-free Phase 2.11A tests. Never delete real Qdrant points."""
from __future__ import annotations

import unittest
from types import SimpleNamespace as NS
from unittest.mock import MagicMock, patch

from . import guarded_recovery as rec

KB = "kb_test"; JOB = "job1"; ID1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ID2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"; ID3 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
BASE = {"organization_id": "org", "knowledge_base_id": KB, "document_id": "doc",
        "document_version_id": "ver", "job_id": JOB}


def make_models():
    def match(value): return NS(value=value)
    def field(key, match): return ("field", key, match.value)
    def has_id(has_id): return ("ids", tuple(has_id))
    return NS(MatchValue=match, FieldCondition=field, HasIdCondition=has_id,
              Filter=lambda must: NS(must=must),
              FilterSelector=lambda filter: NS(filter=filter))


class FakeClient:
    def __init__(self, entries=None):
        self.entries = dict(entries or {})
        self.deleted_calls = []
        self.closed = False
        self.fail_delete = False
        self.no_delete = False
        self.bad_count = False
    @staticmethod
    def matching(payload, filt, point_id):
        return all(payload.get(k) == v if tag == 'field' else point_id in v
                   for tag,k,v in (c if c[0] == 'field' else ("ids", None, c[1])
                                    for c in filt.must))
    def selected(self, filt):
        return [(k,v) for k,v in sorted(self.entries.items()) if self.matching(v, filt, k)]
    def count(self, *, collection_name, count_filter, exact):
        assert collection_name == rec.PRODUCTION_COLLECTION and exact
        n = len(self.selected(count_filter))
        return NS(count=n + (1 if self.bad_count else 0))
    def scroll(self, *, collection_name, scroll_filter, limit, offset,
               with_payload, with_vectors):
        assert with_vectors is False and len(with_payload) == len(rec._FIELDS)
        chosen = self.selected(scroll_filter)
        i = int(offset or 0)
        data = chosen[i:i+limit]
        end = i+len(data)
        return [NS(id=k, payload=dict(v)) for k,v in data], end if end < len(chosen) else None
    def delete(self, *, collection_name, points_selector, wait):
        assert wait and collection_name == rec.PRODUCTION_COLLECTION
        self.deleted_calls.append(points_selector)
        if self.fail_delete:
            raise RuntimeError("Qdrant failure")
        if not self.no_delete:
            for k,v in list(self.entries.items()):
                if self.matching(v, points_selector.filter, k):
                    del self.entries[k]
    def close(self): self.closed=True


def point(token="oldtoken",state="staging",job_id=JOB, **kwargs):
    return {**BASE, "job_id":job_id, "processing_token":token, "index_state":state, **kwargs}


def row(job="QUEUED", doc="QUEUED", attempts=1, expired=False,
        token=None, deleted=None):
    return {"job_id":JOB, "knowledge_base_id":KB,"filename":"crash_test.txt",
            "organization_id":"org", "document_id":"doc", "version_id":"ver",
            "job_status":job,"document_status":doc,"attempts":attempts,
            "processing_token":token, "token_present":token is not None,
            "lease_present":job=="RUNNING", "lease_expired":expired,"deletedAt":deleted}


def db(data=None):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = data or row()
    return conn


class GuardedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.mods = patch.object(rec, "_models", side_effect=make_models)
        self.mods.start(); self.addCleanup(self.mods.stop)
        self.schema = patch.object(rec, "_assert_schema", return_value=None)
        self.schema.start(); self.addCleanup(self.schema.stop)

    def _identity(self): return rec._identity(row())

    def preview(self, data=None, entries=None):
        conn=db(data)
        client=FakeClient(entries)
        report=rec.inspect_one(JOB,KB,connection_factory=lambda:conn,client_factory=lambda:client)
        return report,conn,client

    def repair(self, data=None, entries=None, *, recovery=None, **overrides):
        conn=db(data)
        client=FakeClient(entries)
        opts={"confirm_job_id":JOB,"confirm_knowledge_base_id":KB,
              "workers_stopped":True,"env_value":rec.RECOVERY_OPT_IN}
        opts.update(overrides)
        result=rec.repair_one(JOB,KB,connection_factory=lambda:conn,
                              client_factory=lambda:client,recover_fn=recovery, **opts)
        return result,conn,client

    def test_01_preview_queued_clean_no_mutations(self):
        result, conn, client = self.preview()
        self.assertEqual(result["action"],"queued_clean_no_action")
        self.assertFalse(result["recovery_mutations_performed"])
        conn.commit.assert_not_called(); conn.rollback.assert_called_once()
        self.assertEqual(client.deleted_calls, [])
        self.assertTrue(client.closed)

    def test_02_preview_detects_orphan_staging(self):
        result,_,_=self.preview(entries={ID1:point()})
        self.assertEqual(result["staging_vectors_found"],1)
        self.assertEqual(result["action"],"queued_orphan_cleanup")

    def test_03_preview_detects_partial_ready(self):
        result,_,_=self.preview(entries={ID1:point(state="ready")})
        self.assertEqual(result["ready_vectors_found"],1)

    def test_04_completed_protected_even_with_points(self):
        result,_,_=self.preview(row("COMPLETED","READY"),{ID1:point(state="ready")})
        self.assertEqual(result["action"],"completed_protected")

    def test_05_expired_processing_recoverable(self):
        state=row("RUNNING","PROCESSING",expired=True,token="oldtoken")
        result,_,_=self.preview(state,{ID1:point()})
        self.assertEqual(result["action"],"expired_recover_with_orphans")

    def test_06_active_processing_refused(self):
        state=row("RUNNING","PROCESSING",expired=False,token="oldtoken")
        result,_,_=self.preview(state)
        self.assertEqual(result["action"],"active_or_unknown_lease_refuse")

    def test_07_wrong_token_refused(self):
        state=row("RUNNING","PROCESSING",expired=True,token="oldtoken")
        result,_,_=self.preview(state,{ID1:point(token="other")})
        self.assertEqual(result["action"],"foreign_attempt_refuse")

    def test_08_failed_job_remains_failed(self):
        result,_,_=self.preview(row("FAILED","FAILED",attempts=3),{ID1:point()})
        self.assertEqual(result["action"],"failed_orphan_cleanup")

    def test_09_sql_scopes_kb_and_lock(self):
        conn=db(); rec.read_job(conn,JOB,KB,lock=True)
        sql,parameters=conn.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertIn('FOR UPDATE',sql); self.assertIn('kb.id = %s',sql)
        self.assertEqual(parameters,(JOB,KB))

    def test_10_wrong_kb_not_found(self):
        conn=db();conn.cursor.return_value.__enter__.return_value.fetchone.return_value=None
        with self.assertRaises(RuntimeError):rec.read_job(conn,JOB,'other',lock=True)

    def test_11_archived_refused(self):
        conn=db(row(deleted='2026-10-25'))
        with self.assertRaises(RuntimeError):rec.read_job(conn,JOB,KB)

    def test_12_invalid_job_or_kb_refused(self):
        for job,kb in (('',KB),(JOB,''),(JOB, 'x'*129)):
            with self.assertRaises(ValueError): rec.read_job(db(),job,kb)

    def test_13_foreign_payload_refused(self):
        client=FakeClient({ID1:point(job_id='other')})
        with self.assertRaises(RuntimeError):rec.list_scoped_points(client,self._identity())

    def test_14_unknown_index_state_refused(self):
        client=FakeClient({ID1:point(state='mystery')})
        with self.assertRaises(RuntimeError):rec.list_scoped_points(client,self._identity())

    def test_15_missing_token_refused(self):
        client=FakeClient({ID1:point(token='')})
        with self.assertRaises(RuntimeError):rec.list_scoped_points(client,self._identity())

    def test_16_multiple_tokens_refused(self):
        client=FakeClient({ID1:point(token='old'),ID2:point(token='new')})
        with self.assertRaises(RuntimeError):rec.list_scoped_points(client,self._identity())

    def test_17_count_mismatch_refused(self):
        client=FakeClient({ID1:point()});client.bad_count=True
        with self.assertRaises(RuntimeError):rec.list_scoped_points(client,self._identity())

    def test_18_pagination_150_points(self):
        # 150 sequential UUIDs require 2 scroll calls, no data/text is emitted.
        entries={f'{i:08x}-aaaa-4aaa-8aaa-aaaaaaaaaaaa':point() for i in range(150)}
        self.assertEqual(len(rec.list_scoped_points(FakeClient(entries),self._identity())),150)

    def test_19_repair_requires_confirmations(self):
        cases=[{"confirm_job_id":'wrong'}, {"confirm_knowledge_base_id":'wrong'},
               {"workers_stopped":False}, {"env_value":'OFF'}]
        for override in cases:
            with self.subTest(override=override):
                with self.assertRaises((ValueError,RuntimeError)):
                    self.repair(entries={ID1:point()},**override)

    def test_20_completed_cannot_be_deleted(self):
        with self.assertRaises(RuntimeError):
            self.repair(row("COMPLETED","READY"),{ID1:point(state='ready')})

    def test_21_queued_orphan_cleanup_exact_scope(self):
        result,conn,client=self.repair(entries={ID1:point(),ID2:point(state='ready')})
        self.assertEqual(result["action"],"queued_orphan_cleanup")
        self.assertEqual(len(client.entries),0)
        self.assertEqual(len(client.deleted_calls),2)
        conn.commit.assert_called_once()
        sql,params=conn.cursor.return_value.__enter__.return_value.execute.call_args.args
        self.assertIn('FOR UPDATE',sql)

    def test_22_no_orphans_no_writes(self):
        result,conn,client=self.repair()
        self.assertFalse(result["recovery_mutations_performed"])
        conn.commit.assert_not_called();conn.rollback.assert_called_once()
        self.assertFalse(client.deleted_calls)

    def test_23_expired_calls_existing_recovery(self):
        recover=MagicMock(return_value=[{"job_id":JOB,"status":"QUEUED"}])
        result,conn,client=self.repair(row("RUNNING","PROCESSING",expired=True,token="oldtoken"),
                                      {ID1:point()},recovery=recover)
        recover.assert_called_once_with(conn,job_id=JOB,batch_size=1)
        conn.commit.assert_called_once()
        self.assertEqual(result["action"],"expired_recover_with_orphans")

    def test_24_expired_empty_calls_existing_recovery(self):
        recover=MagicMock(return_value=[{"job_id":JOB,"status":"FAILED"}])
        result,conn,_=self.repair(row("RUNNING","PROCESSING",expired=True,token="oldtoken"),
                                   recovery=recover)
        self.assertTrue(result["recovery_mutations_performed"])
        recover.assert_called_once()

    def test_25_failed_job_cleanup_does_not_restart(self):
        recover=MagicMock()
        result,conn,client=self.repair(row("FAILED","FAILED",attempts=3),
                                       {ID1:point()},recovery=recover)
        recover.assert_not_called()
        self.assertEqual(result["action"],"failed_orphan_cleanup")
        conn.commit.assert_called_once()

    def test_26_recovery_failure_rolls_back(self):
        recover=MagicMock(return_value=[])
        conn=db(row("RUNNING","PROCESSING",expired=True,token="oldtoken"))
        client=FakeClient({ID1:point()})
        with self.assertRaises(RuntimeError):
            rec.repair_one(JOB,KB, confirm_job_id=JOB,confirm_knowledge_base_id=KB,
                workers_stopped=True,env_value=rec.RECOVERY_OPT_IN,
                connection_factory=lambda:conn,client_factory=lambda:client,recover_fn=recover)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_27_qdrant_failure_rolls_back(self):
        conn=db()
        client=FakeClient({ID1:point()}); client.fail_delete=True
        with self.assertRaises(RuntimeError):
            rec.repair_one(JOB,KB, confirm_job_id=JOB,confirm_knowledge_base_id=KB,
                workers_stopped=True,env_value=rec.RECOVERY_OPT_IN,
                connection_factory=lambda:conn,client_factory=lambda:client)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_28_no_delete_detects_survivors(self):
        conn=db()
        client=FakeClient({ID1:point()});client.no_delete=True
        with self.assertRaises(RuntimeError):
            rec.repair_one(JOB,KB, confirm_job_id=JOB,confirm_knowledge_base_id=KB,
                workers_stopped=True,env_value=rec.RECOVERY_OPT_IN,
                connection_factory=lambda:conn,client_factory=lambda:client)
        conn.commit.assert_not_called()

    def test_29_no_qdrant_collection_reset_or_upsert(self):
        _,_,client=self.repair(entries={ID1:point()})
        self.assertFalse(hasattr(client,"delete_collection"))
        self.assertFalse(hasattr(client,"upsert"))

    def test_30_absurd_point_bound_refused(self):
        client=FakeClient({ID1:point()})
        with self.assertRaises(ValueError):
            rec.list_scoped_points(client,self._identity(),max_points=rec.MAX_POINTS+1)

    def test_31_unclaimed_queued_orphans_never_deleted(self):
        state=row(attempts=0)
        result,_,_=self.preview(state,{ID1:point()})
        self.assertEqual(result["action"],"unclaimed_orphans_refuse")
        with self.assertRaises(RuntimeError): self.repair(state,{ID1:point()})

    def test_32_unclaimed_failed_orphans_never_deleted(self):
        state=row("FAILED","FAILED",attempts=0)
        result,_,_=self.preview(state,{ID1:point()})
        self.assertEqual(result["action"],"unclaimed_orphans_refuse")
        with self.assertRaises(RuntimeError): self.repair(state,{ID1:point()})


if __name__ == '__main__': unittest.main()
