"""Offline tests for the opt-in Phase 2.11C CLI and watcher."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock,patch

from . import safe_auto_recovery_operator as op
from . import safe_auto_recovery as mod

KB="kb_test"


class OperatorTests(unittest.TestCase):
    def test_default_read_only(self):
        with patch.object(op,"preview_expired_jobs",return_value={"scanned":0}) as prev, \
             patch.object(op,"recover_expired_batch") as write, redirect_stdout(io.StringIO()):
            self.assertEqual(op.main(["--knowledge-base-id",KB]),0)
        prev.assert_called_once()
        write.assert_not_called()

    def test_explicit_once_routed(self):
        with patch.object(op,"recover_expired_batch",return_value={"requires_manual_review":0}) as write, \
             redirect_stdout(io.StringIO()):
            self.assertEqual(op.main(["--knowledge-base-id",KB,"--run-once", "--confirm-knowledge-base-id",KB]),0)
        self.assertEqual(write.call_args.kwargs["confirm_knowledge_base_id"],KB)

    def test_mutually_exclusive(self):
        with redirect_stdout(io.StringIO()),self.assertRaises(SystemExit):
            op.main(["--knowledge-base-id",KB,"--run-once","--watch"])

    def test_cli_error_sanitized(self):
        with patch.object(op,"preview_expired_jobs",side_effect=RuntimeError("my secret token")), \
             redirect_stdout(io.StringIO()) as out:
            self.assertEqual(op.main(["--knowledge-base-id",KB]),1)
        self.assertNotIn("secret",out.getvalue())

    def test_run_once_reports_manual_review_as_failure(self):
        with patch.object(op,"recover_expired_batch",return_value={"requires_manual_review":1}),\
             redirect_stdout(io.StringIO()):
            self.assertEqual(op.main(["--knowledge-base-id",KB,"--run-once"]),1)

    def test_watch_poll_interval(self):
        with self.assertRaises(ValueError):
            op.watch_recovery(KB,confirm_knowledge_base_id=KB,interval_seconds=1,
                              env_value=mod.FLAG_VALUE)

    def test_watch_flag_required(self):
        with self.assertRaises(RuntimeError):
            op.watch_recovery(KB,confirm_knowledge_base_id=KB,env_value="")

    def test_watch_one_round(self):
        def stop(n):raise KeyboardInterrupt()
        worker=Mock(return_value={"recovered":0})
        with patch.object(op.LOGGER,"info"), self.assertRaises(KeyboardInterrupt):
            op.watch_recovery(KB,confirm_knowledge_base_id=KB,env_value=mod.FLAG_VALUE,
                              recovery_round=worker,wait=stop)
        worker.assert_called_once()

    def test_cli_grace_validation(self):
        with patch.object(op,"preview_expired_jobs",side_effect=ValueError("bad")),\
             redirect_stdout(io.StringIO()):
            self.assertEqual(op.main(["--knowledge-base-id",KB,"--grace-seconds","3"]),1)

    def test_keyboard_interrupt_clean(self):
        with patch.object(op,"watch_recovery",side_effect=KeyboardInterrupt()),\
             redirect_stdout(io.StringIO()):
            self.assertEqual(op.main(["--knowledge-base-id",KB,"--watch"]),0)


if __name__ == "__main__":unittest.main()
