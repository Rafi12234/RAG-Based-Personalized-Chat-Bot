"""Offline read-only operator preflight tests."""
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from .ingestion_preflight_probe import preview_ingestion_readiness


class PreflightTests(unittest.TestCase):
    @patch('worker.ingestion_preflight_probe.local_client')
    @patch('worker.ingestion_preflight_probe.inspect_collection')
    @patch('worker.ingestion_preflight_probe.read_job_state')
    def test_preview_no_mutation(self, state, inspect, local):
        state.return_value = SimpleNamespace(
            job_status='QUEUED', document_status='QUEUED', attempts=0,
            document_active=True, token_present=False, lease_live=False,
            ready_for_claim=True,
        )
        inspect.return_value = SimpleNamespace(exists=True, compatible=True, missing_indexes=())
        client = MagicMock()
        local.return_value = client
        result = preview_ingestion_readiness('job1')
        self.assertTrue(result['eligible_for_future_claim'])
        self.assertFalse(result['qdrant_written'])
        self.assertFalse(result['mysql_modified'])
        self.assertFalse(result['automatic_ingestion_enabled'])
        client.upsert.assert_not_called()
        client.set_payload.assert_not_called()
        client.create_collection.assert_not_called()
        client.close.assert_called_once()

    @patch('worker.ingestion_preflight_probe.read_job_state', return_value=None)
    def test_missing_job_rejected_without_qdrant(self, _):
        with self.assertRaisesRegex(ValueError, 'not found'):
            preview_ingestion_readiness('missing')


if __name__ == '__main__':
    unittest.main()
