"""Offline tests for conservative test-KB expired-lease recovery."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock, patch

from . import safe_auto_recovery as mod

KB = "kb_test"
JOB = "job_test"
ROW = {
    "job_id": JOB, "knowledge_base_id": KB, "job_status": "RUNNING",
    "document_status": "PROCESSING", "attempts": 1, "lease_present": True,
    "lease_expired": True, "token_present": True, "processing_token": "token1",
}


def candidate(_kb, *, grace_seconds, limit):
    return [JOB]


def good_inspect(_job, _kb):
    return {"job_id": JOB, "knowledge_base_id": KB, "action": "expired_recover_empty",
            "staging_vectors_found": 0, "ready_vectors_found": 0}


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchall.return_value = [{"job_id": JOB}]

    def scan(self):
        return mod.list_expired_jobs(KB, connection_factory=lambda: self.conn)

    def test_strict_kb_and_expiry(self):
        self.assertEqual(self.scan(), [JOB])
        sql, params = self.cur.execute.call_args.args
        for s in ("d.knowledgeBaseId = %s", "j.status = 'RUNNING'", "v.status = 'PROCESSING'",
                  "DATE_SUB(UTC_TIMESTAMP(3)", "processingToken IS NOT NULL", "d.deletedAt IS NULL"):
            self.assertIn(s, sql)
        self.assertEqual(params, (KB, 180, 20))
        self.conn.rollback.assert_called_once()
        self.conn.close.assert_called_once()
        self.conn.commit.assert_not_called()

    def test_empty_scan(self):
        self.cur.fetchall.return_value = []
        self.assertEqual(self.scan(), [])

    def test_duplicate_rejected(self):
        self.cur.fetchall.return_value = [{"job_id": JOB}, {"job_id": JOB}]
        with self.assertRaises(RuntimeError): self.scan()

    def test_bad_id_rejected(self):
        self.cur.fetchall.return_value = [{"job_id": ""}]
        with self.assertRaises(RuntimeError): self.scan()

    def test_sql_exception_closes(self):
        self.cur.execute.side_effect = RuntimeError("db down")
        with self.assertRaises(RuntimeError): self.scan()
        self.conn.rollback.assert_called_once()
        self.conn.close.assert_called_once()

    def test_invalid_kb(self):
        with self.assertRaises(ValueError): mod.list_expired_jobs("  ")

    def test_bad_grace(self):
        for g in (119, True, 86401):
            with self.assertRaises(ValueError): mod.list_expired_jobs(KB, grace_seconds=g)

    def test_bad_limits(self):
        for n in (0, True, 21):
            with self.assertRaises(ValueError): mod.list_expired_jobs(KB, limit=n)


class PreviewTests(unittest.TestCase):
    def test_preview_clean(self):
        report = mod.preview_expired_jobs(KB, candidate_provider=candidate, inspector=good_inspect)
        self.assertEqual(report["jobs"][0]["assessment"], "eligible_empty_vector_lease")
        self.assertFalse(report["mysql_modified"])
        self.assertFalse(report["qdrant_modified"])

    def test_preview_orphans_refused(self):
        bad = lambda j,k: dict(good_inspect(j,k), action="expired_recover_with_orphans")
        report = mod.preview_expired_jobs(KB, candidate_provider=candidate, inspector=bad)
        self.assertEqual(report["jobs"][0]["assessment"], "manual_orphan_repair_required")

    def test_preview_wrong_kb_fails_closed(self):
        bad = lambda j,k: dict(good_inspect(j,k), knowledge_base_id="foreign")
        r=mod.preview_expired_jobs(KB,candidate_provider=candidate,inspector=bad)
        self.assertEqual(r["jobs"][0]["assessment"],"inspection_failed_closed")

    def test_preview_unknown_action_refused(self):
        bad=lambda j,k: dict(good_inspect(j,k), action="completed_protected")
        r=mod.preview_expired_jobs(KB,candidate_provider=candidate,inspector=bad)
        self.assertEqual(r["jobs"][0]["assessment"],"refused_review_required")

    def test_preview_exception_safe(self):
        def bad(j,k): raise RuntimeError("private password")
        r=mod.preview_expired_jobs(KB,candidate_provider=candidate,inspector=bad)
        self.assertNotIn("password",str(r))

    def test_preview_duplicate_refused(self):
        provider=lambda *_args,**_kw:[JOB,JOB]
        with self.assertRaises(RuntimeError):
            mod.preview_expired_jobs(KB,candidate_provider=provider,inspector=good_inspect)


class MutationTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cur = self.conn.cursor.return_value.__enter__.return_value
        self.cur.fetchone.return_value = {"still_expired": 1}
        self.client = MagicMock()
        self.row = dict(ROW)
        self.reader = Mock(side_effect=lambda *a, **kw: self.row)
        self.points = Mock(return_value=[])
        self.recovery = Mock(return_value=[{"job_id": JOB, "status": "QUEUED"}])
        self.ident = object()

    def run_recovery(self):
        return mod.recover_one_empty(JOB, KB, connection_factory=lambda:self.conn,
            client_factory=lambda:self.client, row_reader=self.reader,
            points_reader=self.points, identity_builder=lambda row:self.ident,
            recovery_fn=self.recovery)

    def test_success_commits_and_does_not_delete(self):
        r=self.run_recovery()
        self.assertEqual(r["new_job_status"], "QUEUED")
        self.assertTrue(r["mysql_modified"])
        self.conn.commit.assert_called_once()
        self.client.delete.assert_not_called()
        self.points.assert_called_with(self.client,self.ident)
        self.assertEqual(self.points.call_count,2)
        self.recovery.assert_called_once_with(self.conn,job_id=JOB,batch_size=1)
        self.reader.assert_called_once_with(self.conn,JOB,KB,lock=True)

    def test_exhausted_attempt_failed(self):
        self.recovery.return_value=[{"job_id":JOB,"status":"FAILED"}]
        self.assertEqual(self.run_recovery()["new_job_status"],"FAILED")

    def test_active_lease_no_mutations(self):
        self.row["lease_expired"]=False
        r=self.run_recovery()
        self.assertEqual(r["outcome"], "not_expired_active_job")
        self.recovery.assert_not_called()
        self.conn.commit.assert_not_called()
        self.client.delete.assert_not_called()

    def test_completed_document_protected(self):
        self.row.update(job_status="COMPLETED", document_status="READY")
        r=self.run_recovery()
        self.assertEqual(r["outcome"],"not_expired_active_job")
        self.recovery.assert_not_called()

    def test_wrong_claim_no_mutations(self):
        self.row["token_present"]=False
        self.assertEqual(self.run_recovery()["outcome"],"not_expired_active_job")

    def test_no_mysql_age_no_mutations(self):
        self.cur.fetchone.return_value=None
        r=self.run_recovery()
        self.assertEqual(r["outcome"],"grace_or_ownership_refused")
        self.recovery.assert_not_called()

    def test_scope_and_token_fenced_sql(self):
        self.run_recovery()
        sql,params=self.cur.execute.call_args.args
        self.assertIn("d.knowledgeBaseId = %s",sql)
        self.assertIn("j.processingToken = %s",sql)
        self.assertIn("DATE_SUB(UTC_TIMESTAMP(3)",sql)
        self.assertEqual(params,(JOB,KB,"token1",180))

    def test_orphan_staging_prevents_mutations(self):
        self.points.return_value=[object()]
        r=self.run_recovery()
        self.assertEqual(r["outcome"],"orphan_vectors_manual_repair_required")
        self.conn.commit.assert_not_called()
        self.recovery.assert_not_called()
        self.client.delete.assert_not_called()

    def test_orphan_ready_prevents_mutations(self):
        self.points.return_value=[object(),object()]
        self.assertEqual(self.run_recovery()["vector_count"],2)

    def test_inflight_qdrant_change_rolls_back(self):
        self.points.side_effect=[[],[object()]]
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.conn.commit.assert_not_called()
        self.conn.rollback.assert_called()

    def test_db_recovery_wrong_job_rolls_back(self):
        self.recovery.return_value=[{"job_id":"foreign","status":"QUEUED"}]
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.conn.commit.assert_not_called()

    def test_db_recovery_empty_rolls_back(self):
        self.recovery.return_value=[]
        with self.assertRaises(RuntimeError):self.run_recovery()

    def test_qdrant_error_rolls_back(self):
        self.points.side_effect=RuntimeError("schema mismatch")
        with self.assertRaises(RuntimeError):self.run_recovery()
        self.conn.rollback.assert_called()
        self.conn.close.assert_called_once()
        self.client.close.assert_called_once()

    def test_sql_error_closes(self):
        self.cur.execute.side_effect=OSError("sql down")
        with self.assertRaises(OSError):self.run_recovery()
        self.conn.close.assert_called_once()

    def test_grace_cannot_be_shortened(self):
        with self.assertRaises(ValueError):mod.recover_one_empty(JOB,KB,grace_seconds=1)


class RoundTests(unittest.TestCase):
    def test_default_disabled(self):
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(RuntimeError):mod.validate_enablement(KB,confirm_knowledge_base_id=KB)

    def test_confirmation_required(self):
        with self.assertRaises(ValueError):
            mod.validate_enablement(KB,confirm_knowledge_base_id="other",env_value=mod.FLAG_VALUE)

    def test_test_only_flag_accepted(self):
        mod.validate_enablement(KB,confirm_knowledge_base_id=KB,env_value=mod.FLAG_VALUE)

    def test_bounded_batch(self):
        for n in (0,6,True):
            with self.assertRaises(ValueError):
                mod.validate_enablement(KB,confirm_knowledge_base_id=KB,env_value=mod.FLAG_VALUE,max_jobs=n)

    def test_batch_success(self):
        provider=Mock(side_effect=lambda *a,**kw:[JOB])
        recovered=Mock(return_value={"job_id":JOB,"outcome":"recovered_empty_vector_job"})
        r=mod.recover_expired_batch(KB,confirm_knowledge_base_id=KB,env_value=mod.FLAG_VALUE,
                                    candidates=provider,recover=recovered)
        self.assertEqual(r["recovered"],1)
        self.assertEqual(r["requires_manual_review"],0)
        self.assertFalse(r["qdrant_modified"])
        recovered.assert_called_once_with(JOB,KB,grace_seconds=180)

    def test_batch_orphans_reported(self):
        report=mod.recover_expired_batch(KB,confirm_knowledge_base_id=KB,env_value=mod.FLAG_VALUE,
            candidates=candidate,recover=lambda *_a,**_kw:{"job_id":JOB,"outcome":"orphan_vectors_manual_repair_required"})
        self.assertEqual(report["requires_manual_review"],1)

    def test_batch_exception_fails_closed(self):
        def bad(*args,**kw):raise RuntimeError("private token")
        report=mod.recover_expired_batch(KB,confirm_knowledge_base_id=KB,env_value=mod.FLAG_VALUE,
                                         candidates=candidate,recover=bad)
        self.assertEqual(report["handled"][0]["outcome"],"recovery_failed_closed")
        self.assertNotIn("private token",str(report))

    def test_batch_scope_mismatch(self):
        report=mod.recover_expired_batch(KB,confirm_knowledge_base_id=KB,env_value=mod.FLAG_VALUE,
            candidates=candidate,recover=lambda *_a,**_kw:{"job_id":"foreign","outcome":"recovered_empty_vector_job"})
        self.assertEqual(report["requires_manual_review"],1)

    def test_batch_oversized_rejected(self):
        provider=lambda *_a,**kw:["1","2","3","4"]
        with self.assertRaises(RuntimeError):
            mod.recover_expired_batch(KB,confirm_knowledge_base_id=KB,env_value=mod.FLAG_VALUE,
                                      candidates=provider)


if __name__ == "__main__":unittest.main()
