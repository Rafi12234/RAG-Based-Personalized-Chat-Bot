"""Pure offline tests: no Qdrant deletes, MySQL changes or Redis messages."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock

from . import recovery_batch as rb

KB = "disposable-kb"
J1 = "job-one"
J2 = "job-two"


def report(job, action, kb=KB):
    return {"job_id":job, "knowledge_base_id":kb, "action":action,
            "staging_vectors_found": 1 if "orphans" in action else 0,
            "ready_vectors_found": 0,
            "recovery_mutations_performed":True}


class RecoveryBatchTests(unittest.TestCase):
    def test_01_list_exact_kb_and_rolls_back(self):
        conn=MagicMock(); cursor=conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value=[{"job_id":J1},{"job_id":J2}]
        result=rb.list_recovery_candidates(KB,connection_factory=lambda:conn)
        self.assertEqual(result,[J1,J2])
        sql, params=cursor.execute.call_args.args
        self.assertIn("d.knowledgeBaseId = %s",sql)
        self.assertIn("j.processingLeaseUntil <= UTC_TIMESTAMP(3)",sql)
        self.assertIn("j.attempts >= 1",sql)
        self.assertIn("d.deletedAt IS NULL",sql)
        self.assertTrue(sql.lstrip().upper().startswith("SELECT"))
        self.assertEqual(params,(KB,20));conn.rollback.assert_called_once();conn.close.assert_called_once()
        conn.commit.assert_not_called()

    def test_02_list_on_exception_rollback(self):
        conn=MagicMock(); conn.cursor.return_value.__enter__.return_value.execute.side_effect=OSError()
        with self.assertRaises(OSError):rb.list_recovery_candidates(KB,connection_factory=lambda:conn)
        conn.rollback.assert_called_once();conn.close.assert_called_once()

    def test_03_invalid_ids_and_limits(self):
        for kb in (None,"", "x"*129):
            with self.assertRaises(ValueError):rb.list_recovery_candidates(kb,connection_factory=Mock())
        for limit in (0,101,True):
            with self.assertRaises(ValueError):rb.list_recovery_candidates(KB,limit=limit,connection_factory=Mock())

    def test_04_duplicate_candidates_fail(self):
        conn=MagicMock();conn.cursor.return_value.__enter__.return_value.fetchall.return_value=[{"job_id":J1},{"job_id":J1}]
        with self.assertRaises(RuntimeError):rb.list_recovery_candidates(KB,connection_factory=lambda:conn)

    def test_05_read_only_batch_summarizes(self):
        p=Mock(return_value=[J1,J2])
        reports={J1:report(J1,"expired_recover_with_orphans"),J2:report(J2,"queued_clean_no_action")}
        got=rb.inspect_batch(KB,candidate_provider=p,inspect_job=lambda j,k:reports[j])
        self.assertEqual((got["scanned"],got["recoverable"],got["clean"],got["refused"]),(2,1,1,0))
        self.assertFalse(got["qdrant_modified"])
        self.assertFalse(got["mysql_modified"])
        self.assertFalse(got["redis_task_published"])
        p.assert_called_once_with(KB,limit=20)

    def test_06_read_only_inspect_exception_does_not_leak(self):
        def bad(*_):raise RuntimeError("SECRET-CREDENTIAL-PATH")
        got=rb.inspect_batch(KB,candidate_provider=lambda *_a,**_kw:[J1],inspect_job=bad)
        self.assertEqual(got["jobs"][0]["action"],"inspection_refused")
        self.assertNotIn("SECRET",str(got))

    def test_07_read_only_scope_mismatch_refused(self):
        got=rb.inspect_batch(KB,candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=lambda j,k:report(j,"queued_orphan_cleanup",kb="other"))
        self.assertEqual(got["refused"],1)

    def test_08_read_only_completed_refused_not_repaired(self):
        got=rb.inspect_batch(KB,candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=lambda j,k:report(j,"completed_protected"))
        self.assertEqual(got["refused"],1)

    def test_09_read_only_limit_enforced(self):
        with self.assertRaises(RuntimeError):rb.inspect_batch(KB,limit=1,
            candidate_provider=lambda *_a,**_kw:[J1,J2],inspect_job=Mock())

    def test_10_repair_requires_exact_kb(self):
        kw=self.base_options()
        kw["confirm_knowledge_base_id"]="other"
        with self.assertRaises(ValueError):rb.repair_batch(KB,**kw)

    def test_11_repair_requires_phrase(self):
        kw=self.base_options();kw["confirm_phrase"]="INVALID"
        with self.assertRaises(ValueError):rb.repair_batch(KB,**kw)

    def test_12_repair_requires_stopped_workers(self):
        kw=self.base_options();kw["workers_stopped"]=False
        with self.assertRaises(RuntimeError):rb.repair_batch(KB,**kw)

    def test_13_repair_requires_environment_opt_in(self):
        kw=self.base_options();kw["env_value"]=""
        with self.assertRaises(RuntimeError):rb.repair_batch(KB,**kw)

    def test_14_repair_capped_to_5(self):
        for value in (0,6,True):
            kw=self.base_options();kw["max_jobs"]=value
            with self.assertRaises(ValueError):rb.repair_batch(KB,**kw)

    def test_15_empty_repair_safe(self):
        got=rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[],
            inspect_job=Mock(),repair_job=Mock())
        self.assertEqual(got["result"],"completed");self.assertEqual(got["handled"],[])

    def test_16_single_recoverable_repaired(self):
        action="expired_recover_with_orphans"
        inspector=Mock(return_value=report(J1,action))
        repair=Mock(return_value=report(J1,action))
        got=rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=inspector,repair_job=repair)
        self.assertEqual(got["handled"][0]["outcome"],"repaired")
        repair.assert_called_once_with(J1,KB,confirm_job_id=J1,confirm_knowledge_base_id=KB,
            workers_stopped=True,env_value=rb.OPT_IN_VALUE)

    def test_17_clean_queued_does_not_repair(self):
        repair=Mock()
        got=rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=lambda *_:report(J1,"queued_clean_no_action"),repair_job=repair)
        self.assertEqual(got["handled"][0]["outcome"],"already_clean")
        repair.assert_not_called()

    def test_18_completed_never_repaired(self):
        repair=Mock()
        got=rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=lambda *_:report(J1,"completed_protected"),repair_job=repair)
        self.assertEqual(got["result"],"stopped_on_refusal_or_error")
        repair.assert_not_called()

    def test_19_live_lease_never_repaired(self):
        repair=Mock()
        got=rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=lambda *_:report(J1,"active_or_unknown_lease_refuse"),repair_job=repair)
        self.assertEqual(got["result"],"stopped_on_refusal_or_error")
        repair.assert_not_called()

    def test_20_wrong_inspection_scope_stops(self):
        repair=Mock()
        got=rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=lambda *_:report(J1,"queued_orphan_cleanup",kb="wrong"),repair_job=repair)
        self.assertEqual(got["result"],"stopped_on_refusal_or_error")
        repair.assert_not_called()

    def test_21_raises_during_repair_not_hidden(self):
        def fail(*a,**kw):raise ConnectionError("secret address")
        got=rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=lambda *_:report(J1,"queued_orphan_cleanup"),repair_job=fail)
        self.assertEqual(got["stopped_on_job"],J1)
        self.assertNotIn("secret",str(got))

    def test_22_bad_repair_result_stops(self):
        got=rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[J1],
            inspect_job=lambda *_:report(J1,"queued_orphan_cleanup"),repair_job=lambda *a,**kw:report("foreign","queued_orphan_cleanup"))
        self.assertEqual(got["result"],"stopped_on_refusal_or_error")

    def test_23_partial_batch_stops_after_first_refusal(self):
        repair=Mock(return_value=report(J1,"expired_recover_empty"))
        actions={J1:"expired_recover_empty",J2:"foreign_attempt_refuse"}
        opts=self.base_options();opts["max_jobs"]=2
        got=rb.repair_batch(KB,**opts,candidate_provider=lambda *_a,**_kw:[J1,J2],
            inspect_job=lambda j,k:report(j,actions[j]),repair_job=repair)
        self.assertEqual(got["result"],"stopped_on_refusal_or_error")
        self.assertEqual(len(got["handled"]),1)
        self.assertTrue(got["partial_progress_possible"])
        self.assertEqual(repair.call_count,1)

    def test_24_reject_excess_repair_candidates(self):
        got=None
        with self.assertRaises(RuntimeError):
            rb.repair_batch(KB,**self.base_options(),candidate_provider=lambda *_a,**_kw:[J1,J2],
                            inspect_job=Mock(),repair_job=Mock())

    def test_25_reject_duplicate_repair_candidates(self):
        opts=self.base_options();opts["max_jobs"]=2
        with self.assertRaises(RuntimeError):
            rb.repair_batch(KB,**opts,candidate_provider=lambda *_a,**_kw:[J1,J1],
                            inspect_job=Mock(),repair_job=Mock())

    def test_26_candidate_list_does_not_log_tokens(self):
        conn=MagicMock();cur=conn.cursor.return_value.__enter__.return_value
        cur.fetchall.return_value=[{"job_id":J1}]
        rb.list_recovery_candidates(KB,connection_factory=lambda:conn)
        sql, params=cur.execute.call_args.args
        self.assertNotIn("processingToken AS",sql)
        self.assertEqual(params,(KB,20))

    def test_27_repair_does_not_publish_tasks(self):
        self.assertNotIn("send_task",rb.repair_batch.__code__.co_names)
        self.assertNotIn("apply_async",rb.repair_batch.__code__.co_names)

    def test_28_repair_max_jobs_is_bound(self):
        self.assertEqual(rb.MAX_REPAIR_JOBS,5)

    @staticmethod
    def base_options():
        return dict(confirm_knowledge_base_id=KB,confirm_phrase=rb.REPAIR_CONFIRM,
                    workers_stopped=True,max_jobs=1,env_value=rb.OPT_IN_VALUE)

if __name__ == "__main__": unittest.main()
