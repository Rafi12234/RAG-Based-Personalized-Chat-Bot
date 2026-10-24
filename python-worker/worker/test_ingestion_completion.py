"""Offline tests for guarded MySQL completion; never connect to services."""
import unittest
from unittest.mock import MagicMock
from .ingestion_completion import complete_after_publication

class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.conn = MagicMock()
        self.cursor = self.conn.cursor.return_value.__enter__.return_value
        self.cursor.rowcount = 2

    def finish(self, **kwargs):
        return complete_after_publication(self.conn, "job1", "token1",
                                          published_vectors_verified=lambda: 3,
                                          expected_count=3, **kwargs)

    def test_success_commits_and_fences(self):
        self.assertEqual(self.finish(), 3)
        self.conn.commit.assert_called_once()
        sql, params = self.cursor.execute.call_args.args
        self.assertIn("j.processingToken = %s", sql)
        self.assertIn("j.processingLeaseUntil > UTC_TIMESTAMP(3)", sql)
        self.assertIn("d.deletedAt IS NULL", sql)
        self.assertEqual(params, ("job1", "token1"))

    def test_rejected_claim_rolls_back(self):
        self.cursor.rowcount = 0
        with self.assertRaises(RuntimeError): self.finish()
        self.conn.rollback.assert_called_once()
        self.conn.commit.assert_not_called()

    def test_verification_exception_prevents_sql(self):
        with self.assertRaises(RuntimeError):
            complete_after_publication(self.conn, "job1", "token1",
                published_vectors_verified=lambda: (_ for _ in ()).throw(RuntimeError("bad")), expected_count=3)
        self.conn.begin.assert_not_called()

    def test_mismatched_count_prevents_sql(self):
        with self.assertRaises(RuntimeError):
            complete_after_publication(self.conn,"job1","token1",published_vectors_verified=lambda: 2, expected_count=3)
        self.conn.begin.assert_not_called()

    def test_boolean_is_not_a_verified_count(self):
        with self.assertRaises(RuntimeError):
            complete_after_publication(self.conn,"job1","token1",published_vectors_verified=lambda: True, expected_count=1)

    def test_sql_exception_rolls_back(self):
        self.cursor.execute.side_effect = ValueError("sql")
        with self.assertRaises(ValueError): self.finish()
        self.conn.rollback.assert_called_once()

    def test_invalid_job(self):
        with self.assertRaises(ValueError):
            complete_after_publication(self.conn, "", "token1",published_vectors_verified=lambda: 1,expected_count=1)

    def test_invalid_token(self):
        with self.assertRaises(ValueError):
            complete_after_publication(self.conn, "job1", "",published_vectors_verified=lambda: 1,expected_count=1)

    def test_invalid_count(self):
        with self.assertRaises(ValueError):
            complete_after_publication(self.conn, "job1", "token1",published_vectors_verified=lambda: 1, expected_count=0)

    def test_no_callable(self):
        with self.assertRaises(TypeError):
            complete_after_publication(self.conn, "job1", "token1",published_vectors_verified=None,expected_count=1)

if __name__ == '__main__': unittest.main()
