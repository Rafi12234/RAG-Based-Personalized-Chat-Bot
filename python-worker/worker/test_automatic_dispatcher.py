"""Offline tests of Phase 2.10 test-KB scoped dispatch. No MySQL/Redis/Qdrant writes."""
from __future__ import annotations

import io
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from contextlib import redirect_stdout

from . import automatic_dispatcher as auto

KB = "kb_test_only"
JOB = "job_one"
LEASE = datetime(2026, 10, 25, 1, 1, 1)


def eligible(_job_id):
    return {"eligible_for_explicit_pilot": True, "production_collection_clean_for_version": True}


class AutoDispatcherTests(unittest.TestCase):
    def test_defaults_safe(self):
        self.assertEqual(auto.TASK_NAME, "docmind.ingestion.live_pilot_v1")
        self.assertEqual(auto.QUEUE_NAME, "docmind_phase2_live")
        self.assertEqual(auto.DEFAULT_BATCH_SIZE, 3)

    def test_blank_kb_rejected(self):
        with self.assertRaises(ValueError):
            auto.validate_options(" ")

    def test_preview_requires_no_flag(self):
        auto.validate_options(KB, env_flag="")

    def test_publish_requires_exact_confirmation(self):
        with self.assertRaises(ValueError):
            auto.validate_options(KB, publish=True, env_flag=auto.AUTODISPATCH_VALUE)
        with self.assertRaises(ValueError):
            auto.validate_options(KB, publish=True, confirm_knowledge_base_id="other", env_flag=auto.AUTODISPATCH_VALUE)

    def test_publish_requires_separate_env_flag(self):
        with self.assertRaises(RuntimeError):
            auto.validate_options(KB, publish=True, confirm_knowledge_base_id=KB, env_flag="NO")

    def test_invalid_batch_and_scan_limits(self):
        for x in (0, 11, True):
            with self.assertRaises(ValueError):
                auto.validate_options(KB, batch_size=x)
        with self.assertRaises(ValueError):
            auto.validate_options(KB, batch_size=3, scan_limit=2)
        with self.assertRaises(ValueError):
            auto.validate_options(KB, scan_limit=201)

    def test_candidate_query_exact_kb_and_eligibility(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"job_id": "j1"}, {"job_id": "j2"}]
        result = auto.list_candidate_jobs(KB, limit=10, connection_factory=lambda: conn)
        self.assertEqual(result, ["j1", "j2"])
        sql, params = cursor.execute.call_args.args
        self.assertIn("kb.id = %s", sql)
        self.assertIn("d.deletedAt IS NULL", sql)
        self.assertIn("dispatchLeaseUntil", sql)
        self.assertIn("nextAttemptAt", sql)
        self.assertEqual(params, (KB, 10))
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()
        conn.commit.assert_not_called()

    def test_candidate_query_rolls_back_on_exception(self):
        conn = MagicMock()
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = OSError("disconnected")
        with self.assertRaises(OSError):
            auto.list_candidate_jobs(KB, connection_factory=lambda: conn)
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_reservation_commits_only_matching_scope(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"knowledge_base_id": KB, "deletedAt": None,
                                     "job_status": "QUEUED", "version_status": "QUEUED"}
        with patch("worker.ingestion_dispatch_lease.reserve_dispatch_batch", return_value=[{"job_id": JOB, "lease_until": LEASE}]) as reserve:
            result = auto.reserve_exact_scoped_job(JOB, KB, connection_factory=lambda: conn)
        self.assertEqual(result["job_id"], JOB)
        reserve.assert_called_once_with(conn, batch_size=1, lease_seconds=120, job_id=JOB)
        sql, params = cur.execute.call_args.args
        self.assertIn("d.knowledgeBaseId", sql)
        self.assertEqual(params, (JOB,))
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_cross_kb_attempt_rolls_back(self):
        conn = MagicMock()
        cur = conn.cursor.return_value.__enter__.return_value
        cur.fetchone.return_value = {"knowledge_base_id": "different", "deletedAt": None,
                                     "job_status": "QUEUED", "version_status": "QUEUED"}
        with patch("worker.ingestion_dispatch_lease.reserve_dispatch_batch", return_value=[{"job_id": JOB, "lease_until": LEASE}]):
            with self.assertRaises(RuntimeError):
                auto.reserve_exact_scoped_job(JOB, KB, connection_factory=lambda: conn)
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_stale_or_archived_lease_rolls_back(self):
        for change in ({"deletedAt": LEASE}, {"job_status": "RUNNING"}, {"version_status": "READY"}):
            conn = MagicMock()
            cur = conn.cursor.return_value.__enter__.return_value
            row = {"knowledge_base_id": KB, "deletedAt": None,
                   "job_status": "QUEUED", "version_status": "QUEUED"}
            row.update(change)
            cur.fetchone.return_value = row
            with patch("worker.ingestion_dispatch_lease.reserve_dispatch_batch", return_value=[{"job_id": JOB, "lease_until": LEASE}]):
                with self.assertRaises(RuntimeError):
                    auto.reserve_exact_scoped_job(JOB, KB, connection_factory=lambda: conn)
            conn.commit.assert_not_called()

    def test_raced_reservation_returns_none(self):
        conn = MagicMock()
        with patch("worker.ingestion_dispatch_lease.reserve_dispatch_batch", return_value=[]):
            self.assertIsNone(auto.reserve_exact_scoped_job(JOB, KB, connection_factory=lambda: conn))
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_release_uses_precise_timestamp(self):
        conn = MagicMock()
        with patch("worker.ingestion_dispatch_lease.release_dispatch_lease", return_value=True) as release:
            self.assertTrue(auto.release_matching_reservation(JOB, LEASE, connection_factory=lambda: conn))
        release.assert_called_once_with(conn, JOB, LEASE)
        conn.commit.assert_called_once()

    def test_preview_no_mysql_mutations_or_celery_messages(self):
        with patch.object(auto, "list_candidate_jobs", return_value=[JOB]), \
             patch.object(auto, "reserve_exact_scoped_job") as reserve, \
             patch.object(auto, "release_matching_reservation") as release:
            sender = Mock()
            result = auto.dispatch_scope_once(KB, preflight=eligible, sender=sender, env_flag="")
        self.assertEqual(result["mode"], "read_only_preview")
        self.assertEqual(result["eligible"], 1)
        self.assertFalse(result["mysql_dispatch_reservations_modified"])
        sender.assert_not_called()
        reserve.assert_not_called()
        release.assert_not_called()

    def test_preview_skips_bad_preflight(self):
        def check(job):
            if job == "unsafe":
                raise RuntimeError("some secret path")
            return eligible(job)
        with patch.object(auto, "list_candidate_jobs", return_value=["unsafe", JOB]):
            result = auto.dispatch_scope_once(KB, preflight=check, batch_size=1)
        self.assertEqual(result["preflight_rejected"], 1)
        self.assertEqual(result["eligible"], 1)
        self.assertNotIn("secret", str(result))

    def test_explicit_publish_to_correct_queue(self):
        with patch.object(auto, "list_candidate_jobs", return_value=[JOB]), \
             patch.object(auto, "reserve_exact_scoped_job", return_value={"job_id": JOB, "lease_until": LEASE}):
            sender = Mock(return_value=SimpleNamespace(id="celery-task"))
            result = auto.dispatch_scope_once(KB, publish=True, confirm_knowledge_base_id=KB,
                env_flag=auto.AUTODISPATCH_VALUE, preflight=eligible, sender=sender)
        self.assertEqual(result["reserved"], 1)
        self.assertEqual(result["published"], 1)
        self.assertTrue(result["mysql_dispatch_reservations_modified"])
        sender.assert_called_once_with(auto.TASK_NAME, args=[JOB], queue=auto.QUEUE_NAME, retry=False)

    def test_broker_failure_releases_only_matching_lease(self):
        with patch.object(auto, "list_candidate_jobs", return_value=[JOB]), \
             patch.object(auto, "reserve_exact_scoped_job", return_value={"job_id": JOB, "lease_until": LEASE}), \
             patch.object(auto, "release_matching_reservation", return_value=True) as release:
            sender = Mock(side_effect=ConnectionError("broker"))
            result = auto.dispatch_scope_once(KB, publish=True, confirm_knowledge_base_id=KB,
                env_flag=auto.AUTODISPATCH_VALUE, preflight=eligible, sender=sender)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["published"], 0)
        release.assert_called_once_with(JOB, LEASE, connection_factory=None)

    def test_unavailable_job_does_not_send(self):
        with patch.object(auto, "list_candidate_jobs", return_value=[JOB]), \
             patch.object(auto, "reserve_exact_scoped_job", return_value=None):
            sender = Mock()
            result = auto.dispatch_scope_once(KB, publish=True, confirm_knowledge_base_id=KB,
                env_flag=auto.AUTODISPATCH_VALUE, preflight=eligible, sender=sender)
        self.assertEqual(result["reserved"], 0)
        sender.assert_not_called()

    def test_watch_requires_safe_interval(self):
        with self.assertRaises(ValueError):
            auto.watch_knowledge_base(KB, confirm_knowledge_base_id=KB, interval_seconds=1,
                                      env_flag=auto.AUTODISPATCH_VALUE)

    def test_watch_polls_and_stops_with_keyboard_interrupt(self):
        sent = Mock(return_value={"published": 1})
        def stop(_interval):
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            auto.watch_knowledge_base(KB, confirm_knowledge_base_id=KB,
                env_flag=auto.AUTODISPATCH_VALUE, dispatch_once=sent, wait=stop)
        sent.assert_called_once()

    def test_cli_preview_uses_no_publish(self):
        with patch.object(auto, "dispatch_scope_once", return_value={"failed": 0, "mode": "read_only_preview"}) as call:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(auto.main(["--knowledge-base-id", KB]), 0)
        self.assertEqual(call.call_args.kwargs["publish"], False)

    def test_cli_explicit_publish_flag_required(self):
        with patch.object(auto, "dispatch_scope_once", return_value={"failed": 0}) as call:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(auto.main(["--knowledge-base-id", KB, "--publish-once"]), 0)
        self.assertEqual(call.call_args.kwargs["publish"], True)

    def test_mutually_exclusive_modes(self):
        with redirect_stdout(io.StringIO()), self.assertRaises(SystemExit):
            auto.main(["--knowledge-base-id", KB, "--publish-once", "--watch"])

    def test_no_raw_errors_to_console(self):
        with patch.object(auto, "dispatch_scope_once", side_effect=RuntimeError("private secret")), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(auto.main(["--knowledge-base-id", KB]), 1)
        self.assertNotIn("private secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
