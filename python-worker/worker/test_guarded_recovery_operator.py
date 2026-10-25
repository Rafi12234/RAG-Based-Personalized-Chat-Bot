"""Offline tests: default CLI preview; mutations only on explicit --repair."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from . import guarded_recovery_operator as cli


class RecoveryOperatorTests(unittest.TestCase):
    def test_default_readonly(self):
        with patch.object(cli, "inspect_one", return_value={"action":"queued_clean_no_action"}) as inspect, \
             patch.object(cli, "repair_one") as repair, redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(cli.main(["job1","--knowledge-base-id","kb1"]),0)
        inspect.assert_called_once_with("job1","kb1")
        repair.assert_not_called()
        self.assertIn("queued_clean_no_action",stream.getvalue())

    def test_explicit_repair_passes_all_confirmations(self):
        with patch.object(cli, "repair_one", return_value={"action":"queued_orphan_cleanup"}) as repair, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["job1","--knowledge-base-id","kb1", "--repair",
                                       "--confirm-job-id","job1","--confirm-knowledge-base-id","kb1",
                                       "--workers-stopped"]),0)
        self.assertEqual(repair.call_args.kwargs["confirm_job_id"],"job1")
        self.assertTrue(repair.call_args.kwargs["workers_stopped"])

    def test_sensitive_error_message_never_printed(self):
        with patch.object(cli, "inspect_one", side_effect=RuntimeError("private storage key")), \
             redirect_stdout(io.StringIO()) as stream:
            self.assertEqual(cli.main(["job1","--knowledge-base-id","kb1"]),1)
        self.assertNotIn("private storage key",stream.getvalue())


if __name__ == '__main__': unittest.main()
