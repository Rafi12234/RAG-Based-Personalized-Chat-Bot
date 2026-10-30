"""Offline CLI tests; no live databases or queues used."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch
from . import recovery_batch_operator as cli

KB="test_kb"
REPORT={"knowledge_base_id": KB, "mode": "read_only_audit", "scanned":0, "jobs":[]}


class OperatorTests(unittest.TestCase):
    def test_01_default_is_read_only(self):
        with patch.object(cli,"inspect_batch",return_value=REPORT) as scan, \
             patch.object(cli,"repair_batch") as mutate, redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--knowledge-base-id",KB]),0)
        scan.assert_called_once_with(KB,limit=20);mutate.assert_not_called()

    def test_02_watch_is_read_only(self):
        scan=Mock(return_value=REPORT)
        def quit_at_wait(_seconds):raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            cli.watch_read_only(KB,interval_seconds=30,inspect=scan,wait=quit_at_wait)
        scan.assert_called_once_with(KB,limit=20)

    def test_03_watch_interval_bounded(self):
        for value in (0,29,3601,True):
            with self.assertRaises(ValueError):cli.watch_read_only(KB,interval_seconds=value)

    def test_04_cli_watch_flag_does_not_enable_repair(self):
        with patch.object(cli,"watch_read_only",side_effect=KeyboardInterrupt) as watch, \
             patch.object(cli,"repair_batch") as mutate,redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--knowledge-base-id",KB,"--watch"]),0)
        watch.assert_called_once();mutate.assert_not_called()

    def test_05_repair_rejection_is_nonzero(self):
        with patch.object(cli,"repair_batch",return_value={"result":"stopped_on_refusal_or_error"}), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--knowledge-base-id",KB,"--repair-once"]),1)

    def test_06_repair_cli_passes_confirmations(self):
        with patch.object(cli,"repair_batch",return_value={"result":"completed"}) as repair, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--knowledge-base-id",KB,"--repair-once",
                "--confirm-knowledge-base-id",KB,
                "--confirm-phrase","REPAIR_DISPOSABLE_TEST_KB",
                "--workers-stopped","--max-jobs","2"]),0)
        self.assertEqual(repair.call_args.kwargs["max_jobs"],2)
        self.assertTrue(repair.call_args.kwargs["workers_stopped"])

    def test_07_watch_and_repair_mutually_exclusive(self):
        with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):
            cli.main(["--knowledge-base-id",KB,"--watch","--repair-once"])

    def test_08_secret_exception_not_printed(self):
        with patch.object(cli,"inspect_batch",side_effect=RuntimeError("SECRET-DB-KEY")), \
             redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["--knowledge-base-id",KB]),1)
        self.assertNotIn("SECRET",output.getvalue())

    def test_09_empty_kb_passed_to_validator(self):
        with patch.object(cli,"inspect_batch",side_effect=ValueError("bad")), \
             redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["--knowledge-base-id",""]),1)

    def test_10_repair_does_not_automatically_publish_tasks(self):
        self.assertNotIn("send_task", cli.main.__code__.co_names)

if __name__=='__main__':unittest.main()
