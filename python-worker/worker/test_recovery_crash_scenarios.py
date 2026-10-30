"""Simulated cross-store crash conditions with Phase 2.11A's actual logic.

Never touches live MySQL or Qdrant; the fake Qdrant responds to filtered deletes.
These tests are NOT an actual process-kill / network-partition integration test.
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from . import guarded_recovery as rec
from .test_guarded_recovery import (
    KB, JOB, ID1, ID2, FakeClient, make_models, point, row, db,
)


class CrashRecoverySimulationTests(unittest.TestCase):
    def setUp(self):
        self.m=patch.object(rec,"_models",side_effect=make_models)
        self.m.start();self.addCleanup(self.m.stop)
        self.s=patch.object(rec,"_assert_schema",return_value=None)
        self.s.start();self.addCleanup(self.s.stop)

    def _preview(self,status,entries):
        conn=db(status);client=FakeClient(entries)
        result=rec.inspect_one(JOB,KB,connection_factory=lambda:conn,client_factory=lambda:client)
        self.assertFalse(result["recovery_mutations_performed"])
        self.assertFalse(client.deleted_calls)
        return result

    def _repair(self,status,entries,*,recover=None):
        conn=db(status);client=FakeClient(entries)
        result=rec.repair_one(JOB,KB,confirm_job_id=JOB,
            confirm_knowledge_base_id=KB,workers_stopped=True,
            env_value=rec.RECOVERY_OPT_IN,connection_factory=lambda:conn,
            client_factory=lambda:client,recover_fn=recover)
        return result,conn,client

    def test_01_worker_crash_after_staging_with_expired_lease(self):
        status=row("RUNNING","PROCESSING",attempts=1,expired=True,token="oldtoken")
        report=self._preview(status,{ID1:point()})
        self.assertEqual(report["action"],"expired_recover_with_orphans")
        recover=MagicMock(return_value=[{"job_id":JOB,"status":"QUEUED"}])
        result,conn,client=self._repair(status,{ID1:point()},recover=recover)
        self.assertEqual(result["action"],"expired_recover_with_orphans")
        self.assertFalse(client.entries)
        conn.commit.assert_called_once()

    def test_02_crash_after_ready_before_mysql_complete(self):
        status=row("RUNNING","PROCESSING",attempts=1,expired=True,token="oldtoken")
        report=self._preview(status,{ID1:point(state="ready")})
        self.assertEqual(report["action"],"expired_recover_with_orphans")
        recover=MagicMock(return_value=[{"job_id":JOB,"status":"QUEUED"}])
        result,conn,client=self._repair(status,{ID1:point(state="ready")},recover=recover)
        self.assertFalse(client.entries)
        conn.commit.assert_called_once()

    def test_03_crash_during_partial_publication(self):
        status=row("RUNNING","PROCESSING",attempts=1,expired=True,token="oldtoken")
        entries={ID1:point(state="staging"),ID2:point(state="ready")}
        report=self._preview(status,entries)
        self.assertEqual((report["staging_vectors_found"],report["ready_vectors_found"]),(1,1))
        recover=MagicMock(return_value=[{"job_id":JOB,"status":"QUEUED"}])
        _,conn,client=self._repair(status,entries,recover=recover)
        self.assertFalse(client.entries)
        conn.commit.assert_called_once()

    def test_04_worker_still_has_lease_cannot_clean(self):
        status=row("RUNNING","PROCESSING",attempts=1,expired=False,token="oldtoken")
        report=self._preview(status,{ID1:point()})
        self.assertEqual(report["action"],"active_or_unknown_lease_refuse")
        with self.assertRaises(RuntimeError):self._repair(status,{ID1:point()})

    def test_05_reclaimed_token_mismatch_refused(self):
        status=row("RUNNING","PROCESSING",attempts=2,expired=True,token="newtoken")
        report=self._preview(status,{ID1:point(token="oldtoken")})
        self.assertEqual(report["action"],"foreign_attempt_refuse")
        with self.assertRaises(RuntimeError):self._repair(status,{ID1:point(token="oldtoken")})

    def test_06_completed_ready_never_cleaned(self):
        status=row("COMPLETED","READY",attempts=1)
        report=self._preview(status,{ID1:point(state="ready")})
        self.assertEqual(report["action"],"completed_protected")
        with self.assertRaises(RuntimeError):self._repair(status,{ID1:point(state="ready")})

    def test_07_error_after_qdrant_delete_rolls_back_mysql(self):
        status=row("RUNNING","PROCESSING",attempts=1,expired=True,token="oldtoken")
        conn=db(status);client=FakeClient({ID1:point()})
        failed=MagicMock(return_value=[])
        with self.assertRaises(RuntimeError):
            rec.repair_one(JOB,KB,confirm_job_id=JOB,
                confirm_knowledge_base_id=KB,workers_stopped=True,
                env_value=rec.RECOVERY_OPT_IN,connection_factory=lambda:conn,
                client_factory=lambda:client,recover_fn=failed)
        self.assertFalse(client.entries)  # Qdrant delete can persist!
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_08_after_partial_failure_read_only_audit_can_retry(self):
        status=row("RUNNING","PROCESSING",attempts=1,expired=True,token="oldtoken")
        report=self._preview(status,{})
        self.assertEqual(report["action"],"expired_recover_empty")

    def test_09_multiple_attempt_tokens_need_manual_investigation(self):
        status=row("QUEUED","QUEUED",attempts=2)
        entries={ID1:point(token="oldtoken"),ID2:point(token="newtoken")}
        with self.assertRaises(RuntimeError):self._preview(status,entries)
        with self.assertRaises(RuntimeError):self._repair(status,entries)

    def test_10_wrong_kb_has_no_visibility(self):
        conn=db();conn.cursor.return_value.__enter__.return_value.fetchone.return_value=None
        with self.assertRaises(RuntimeError):
            rec.inspect_one(JOB,"foreign-kb",connection_factory=lambda:conn,
                client_factory=lambda:FakeClient({ID1:point()}))

if __name__=='__main__':unittest.main()
