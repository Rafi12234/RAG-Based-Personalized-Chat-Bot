"""Offline unit tests for diagnostic dispatcher transaction and publish paths.

No MySQL connection is made and NO Celery messages are sent.
Use `python -m unittest worker.test_dispatcher -v` from python-worker.
"""

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import dispatcher


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.db = Mock()
        self.db_factory = patch.object(dispatcher, "get_connection", return_value=self.db)
        self.db_factory.start()
        self.addCleanup(self.db_factory.stop)
        self.reserve = patch.object(dispatcher, "reserve_dispatch_batch")
        self.reserve_mock = self.reserve.start()
        self.addCleanup(self.reserve.stop)
        self.send = patch.object(dispatcher.celery_app, "send_task")
        self.send_mock = self.send.start()
        self.addCleanup(self.send.stop)

    def test_preview_rolls_back_and_does_not_publish(self):
        self.reserve_mock.return_value = [{"job_id": "job1", "lease_until": datetime(2026, 10, 23)}]
        result = dispatcher.dispatch_once(job_id="job1")
        self.assertEqual(result, {"reserved": 1, "published": 0, "failed": 0})
        self.db.begin.assert_called_once()
        self.db.rollback.assert_called_once()
        self.db.commit.assert_not_called()
        self.db.close.assert_called_once()
        self.send_mock.assert_not_called()
        self.reserve_mock.assert_called_once_with(
            self.db, batch_size=25, lease_seconds=120, job_id="job1"
        )

    def test_publish_commits_before_sending(self):
        self.reserve_mock.return_value = [{"job_id": "job1", "lease_until": datetime(2026, 10, 23)}]
        self.send_mock.return_value = SimpleNamespace(id="celery1")
        def check_commit(*args, **kwargs):
            self.db.commit.assert_called_once()
            self.db.close.assert_called_once()
            return SimpleNamespace(id="celery1")
        self.send_mock.side_effect = check_commit
        result = dispatcher.dispatch_once(publish=True, job_id="job1")
        self.assertEqual(result, {"reserved": 1, "published": 1, "failed": 0})
        self.db.rollback.assert_not_called()
        self.send_mock.assert_called_once_with(
            "docmind.ingestion.inspect", args=["job1"],
            queue="docmind_phase2", retry=False,
        )

    def test_publish_failure_releases_matching_lease(self):
        lease = datetime(2026, 10, 23)
        self.reserve_mock.return_value = [{"job_id": "job1", "lease_until": lease}]
        self.send_mock.side_effect = RuntimeError("simulated broker error")
        with patch.object(dispatcher, "release_failed_publication", return_value=True) as release, \
             patch.object(dispatcher.LOGGER, "exception") as logged_error:
            result = dispatcher.dispatch_once(publish=True)
        logged_error.assert_called_once()
        self.assertEqual(result, {"reserved": 1, "published": 0, "failed": 1})
        release.assert_called_once_with("job1", lease)
        self.db.commit.assert_called_once()

    def test_no_eligible_job_does_not_publish(self):
        self.reserve_mock.return_value = []
        result = dispatcher.dispatch_once(publish=True)
        self.assertEqual(result, {"reserved": 0, "published": 0, "failed": 0})
        self.send_mock.assert_not_called()

    def test_reservation_failure_rolls_back(self):
        self.reserve_mock.side_effect = RuntimeError("simulated database error")
        with self.assertRaisesRegex(RuntimeError, "simulated database error"):
            dispatcher.dispatch_once()
        self.db.rollback.assert_called_once()
        self.db.close.assert_called_once()
        self.send_mock.assert_not_called()

    def test_release_commits_and_preserves_matching_reservation(self):
        lease = datetime(2026, 10, 23)
        with patch.object(dispatcher, "release_dispatch_lease", return_value=True) as release:
            self.assertTrue(dispatcher.release_failed_publication("job1", lease))
        release.assert_called_once_with(self.db, "job1", lease)
        self.db.commit.assert_called_once()
        self.db.close.assert_called_once()

    def test_publish_mode_must_be_explicit(self):
        self.reserve_mock.return_value = []
        code = dispatcher.main(["--job-id", "job1"])
        self.assertEqual(code, 0)
        self.db.rollback.assert_called_once()
        self.db.commit.assert_not_called()
        self.send_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
