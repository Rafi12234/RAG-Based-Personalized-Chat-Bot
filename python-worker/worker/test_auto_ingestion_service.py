"""Offline safety tests for local multi-KB auto-ingestion; no live services."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, Mock, patch

from . import auto_ingestion_service as service


class AutoIngestionServiceTests(unittest.TestCase):
    def test_safe_by_default(self):
        with self.assertRaises(RuntimeError):
            service.validate_options(active=True, enabled="", scoped_enabled="")
        service.validate_options(active=False, enabled="", scoped_enabled="")

    def test_requires_both_opt_ins_and_nonproduction(self):
        for flags in [
            (service.ENABLE_VALUE, "", "development"),
            ("", service.SCOPED_DISPATCH_VALUE, "development"),
            (service.ENABLE_VALUE, service.SCOPED_DISPATCH_VALUE, "production"),
        ]:
            with self.assertRaises(RuntimeError):
                service.validate_options(active=True, enabled=flags[0],
                                         scoped_enabled=flags[1], environment=flags[2])
        service.validate_options(active=True, enabled=service.ENABLE_VALUE,
                                 scoped_enabled=service.SCOPED_DISPATCH_VALUE,
                                 environment="development")

    def test_parameterized_scope_query_excludes_protected_ids(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchall.return_value = [{"knowledge_base_id": "test-kb"}]
        got = service.list_pending_knowledge_bases(
            protected_ids=("protected-one", "protected-two"),
            connection_factory=lambda: connection)
        self.assertEqual(got, ["test-kb"])
        sql, params = cursor.execute.call_args.args
        for condition in ("j.status = 'QUEUED'", "v.status = 'QUEUED'",
                          "d.deletedAt IS NULL", "j.attempts < 3",
                          "dispatchLeaseUntil", "nextAttemptAt",
                          "d.knowledgeBaseId NOT IN"):
            self.assertIn(condition, sql)
        self.assertEqual(params, (*service.PROTECTED_QA_KBS, "protected-one", "protected-two", 20))
        connection.commit.assert_not_called()
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_cross_scope_discovery_fails_closed(self):
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value.fetchall.return_value = [
            {"knowledge_base_id": "protected"}
        ]
        with self.assertRaises(RuntimeError):
            service.list_pending_knowledge_bases(
                protected_ids=("protected",), connection_factory=lambda: connection)
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()

    def test_preview_does_not_dispatch(self):
        sender = Mock()
        result = service.run_round(
            protected_ids=("protected",),
            discover=lambda **kwargs: ["test-kb"],
            dispatch=sender)
        self.assertEqual(result["mode"], "preview")
        self.assertEqual(result["knowledge_bases_scanned"], 1)
        sender.assert_not_called()

    def test_publish_uses_exact_kb_confirmation_and_scoped_dispatcher(self):
        sender = Mock(return_value={"published": 2, "failed": 0, "preflight_rejected": 0})
        result = service.run_round(
            publish=True, protected_ids=("protected",),
            enabled=service.ENABLE_VALUE,
            scoped_enabled=service.SCOPED_DISPATCH_VALUE,
            discover=lambda **kwargs: ["kb-a", "protected", "kb-b"],
            dispatch=sender)
        self.assertEqual(result["published"], 4)
        self.assertEqual(result["rejected_or_failed_scopes"], 1)
        self.assertEqual(sender.call_count, 2)
        for call in sender.call_args_list:
            kb = call.args[0]
            self.assertNotEqual(kb, "protected")
            self.assertNotIn(kb, service.PROTECTED_QA_KBS)
            self.assertEqual(call.kwargs["confirm_knowledge_base_id"], kb)
            self.assertEqual(call.kwargs["env_flag"], service.SCOPED_DISPATCH_VALUE)
            self.assertTrue(call.kwargs["publish"])

    def test_one_bad_kb_does_not_block_others(self):
        def dispatcher(kb, **kwargs):
            if kb == "broken":
                raise ValueError("private storage secret")
            return {"published": 1, "failed": 0, "preflight_rejected": 0}
        result = service.run_round(
            publish=True, enabled=service.ENABLE_VALUE,
            scoped_enabled=service.SCOPED_DISPATCH_VALUE,
            discover=lambda **kwargs: ["broken", "good"], dispatch=dispatcher)
        self.assertEqual(result["published"], 1)
        self.assertEqual(result["rejected_or_failed_scopes"], 1)
        self.assertNotIn("private storage secret", str(result))

    def test_invalid_config_is_rejected(self):
        with self.assertRaises(ValueError):
            service.validate_options(active=False, interval=1)
        with self.assertRaises(ValueError):
            service.validate_options(active=False, scope_limit=51)
        with self.assertRaises(ValueError):
            service.validate_options(active=False, batch_size=11)

    def test_watch_runs_rounds_without_manual_per_upload_actions(self):
        observed = []
        def once(**kwargs):
            observed.append(kwargs)
            return {"published": 0, "rejected_or_failed_scopes": 0}
        def stop(_seconds):
            raise KeyboardInterrupt
        with patch.dict("os.environ", {
            service.ENABLE_FLAG: service.ENABLE_VALUE,
            service.SCOPED_DISPATCH_FLAG: service.SCOPED_DISPATCH_VALUE,
            "NODE_ENV": "development",
        }):
            with self.assertRaises(KeyboardInterrupt):
                service.watch(run=once, sleep=stop)
        self.assertEqual(len(observed), 1)
        self.assertTrue(observed[0]["publish"])

    def test_cli_does_not_publish_by_default(self):
        with patch.object(service, "run_round",
                          return_value={"rejected_or_failed_scopes": 0, "mode": "preview"}) as f:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(service.main([]), 0)
        self.assertFalse(f.call_args.kwargs["publish"])


if __name__ == "__main__":
    unittest.main()
