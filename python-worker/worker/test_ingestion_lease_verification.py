"""Offline tests: read-only DB lease checks; no service access."""
import unittest
from unittest.mock import MagicMock

from .ingestion_lease_verification import (
    check_current_processing_lease, make_fresh_lease_guard, read_job_state,
)


def fake_connection(row):
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    cur.fetchone.return_value = row
    return conn, cur


class LeaseVerificationTests(unittest.TestCase):
    def test_live_token_owned(self):
        conn, cur = fake_connection({"valid": 1})
        self.assertTrue(check_current_processing_lease("job1", "token1", connection_factory=lambda: conn))
        params = cur.execute.call_args.args[1]
        self.assertEqual(params, ("job1", "token1"))
        conn.rollback.assert_called_once()
        conn.close.assert_called_once()

    def test_missing_or_stale_token_fails_closed(self):
        conn, _ = fake_connection(None)
        self.assertFalse(check_current_processing_lease("job1", "oldtoken", connection_factory=lambda: conn))

    def test_fresh_connection_every_guard_call(self):
        built = []
        def new_connection():
            conn, _ = fake_connection({"valid": 1} if not built else None)
            built.append(conn)
            return conn
        guard = make_fresh_lease_guard("job1", "token1", connection_factory=new_connection)
        self.assertTrue(guard())
        self.assertFalse(guard())
        self.assertEqual(len(built), 2)
        self.assertTrue(all(c.close.call_count == 1 for c in built))

    def test_invalid_tokens_and_jobs_rejected(self):
        for job, token in (("", "x"), ("job", ""), ("job", "x" * 65)):
            with self.assertRaises(ValueError):
                make_fresh_lease_guard(job, token)

    def test_queued_job_eligible(self):
        conn, _ = fake_connection({
            "job_status": "QUEUED", "document_status": "QUEUED", "attempts": 0,
            "document_active": 1, "token_present": 0, "lease_live": 0, "retry_due": 1,
        })
        state = read_job_state("job1", connection_factory=lambda: conn)
        self.assertTrue(state.ready_for_claim)
        self.assertFalse(state.token_present)
        self.assertEqual(state.job_status, "QUEUED")

    def test_archived_or_exhausted_or_delayed_not_eligible(self):
        for change in ({"document_active": 0}, {"attempts": 3}, {"retry_due": 0},
                       {"document_status": "PROCESSING"}):
            row = {"job_status": "QUEUED", "document_status": "QUEUED", "attempts": 0,
                   "document_active": 1, "token_present": 0, "lease_live": 0, "retry_due": 1}
            row.update(change)
            conn, _ = fake_connection(row)
            self.assertFalse(read_job_state("job1", connection_factory=lambda: conn).ready_for_claim)

    def test_missing_job_returns_none_and_closes(self):
        conn, _ = fake_connection(None)
        self.assertIsNone(read_job_state("missing", connection_factory=lambda: conn))
        conn.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
