"""Offline detailed publication CLI guard tests. No real Qdrant or MySQL."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from .publication_audit_probe import inspect_publication


class LargeAuditProbeTests(unittest.TestCase):
    def make_patches(self):
        fake = MagicMock()
        fake.report = {"observed_points": 20000, "safe_to_auto_replay": False}
        state = SimpleNamespace(
            job_status="QUEUED",document_status="QUEUED",
            attempts=1,token_present=False,lease_live=False)
        return fake, [
            patch("worker.publication_audit_probe.read_job_state",return_value=state),
            patch("worker.publication_audit_probe.identity_for_job",return_value=SimpleNamespace(
                organization_id="org",knowledge_base_id="kb",document_id="doc",
                document_version_id="version",job_id="job")),
            patch("worker.publication_audit_probe.local_client",return_value=fake),
            patch("worker.publication_audit_probe.audit_publication_counts",return_value=SimpleNamespace(staged=1,ready=0)),
            patch("worker.publication_audit_probe.interpret_publication_state",return_value="review_orphan_vectors"),
        ]

    def test_default_does_not_scan_50k(self):
        fake, patches=self.make_patches()
        with patches[0],patches[1],patches[2],patches[3],patches[4]:
            result=inspect_publication("job")
        self.assertNotIn("large_version_metadata_audit",result)
        self.assertFalse(result["qdrant_written"])
        fake.close.assert_called_once()

    def test_opt_in_detailed_reports_no_authorized_resume(self):
        fake, patches=self.make_patches()
        report=SimpleNamespace(as_dict=lambda:{
            "observed_points": 25, "safe_to_auto_replay": False,
            "authoritative_recovery_approved": False,
        })
        with patches[0],patches[1],patches[2],patches[3],patches[4],patch(
            "worker.large_vector_audit.inspect_large_version",return_value=report) as scanner:
            result=inspect_publication("job", detailed=True)
            scanner.assert_called_once()
        self.assertEqual(result["large_version_metadata_audit"]["observed_points"],25)
        self.assertFalse(result["large_version_metadata_audit"]["safe_to_auto_replay"])
        self.assertFalse(result["snapshot_is_transactional"])
        fake.close.assert_called_once()

    def test_detailed_scan_failure_closes_client(self):
        fake, patches=self.make_patches()
        with patches[0],patches[1],patches[2],patches[3],patches[4],patch(
            "worker.large_vector_audit.inspect_large_version",
            side_effect=RuntimeError("scoped audit failure")):
            with self.assertRaisesRegex(RuntimeError,"scoped audit failure"):
                inspect_publication("job",detailed=True)
        fake.close.assert_called_once()


if __name__=="__main__":
    unittest.main()
