"""Offline Phase 2.13 acceptance tests; no real database, Qdrant, Redis or S3."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from contextlib import redirect_stdout

from . import phase2_acceptance_audit as audit

KB = 'test-kb'
BASE = {
    'job_id': 'job-1', 'job_status': 'COMPLETED', 'document_status': 'READY',
    'attempts': 1, 'token_present': False, 'lease_present': False,
    'lease_live': False, 'version_id': 'v-1', 'filename': 'sample.txt',
    'document_id': 'doc-1', 'knowledge_base_id': KB, 'organization_id': 'org-1',
}


def point(state='ready'):
    return SimpleNamespace(index_state=state)


def rows(*items):
    return tuple(dict(BASE, **item) for item in items)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.db = MagicMock()
        self.cursor = self.db.cursor.return_value.__enter__.return_value

    def fetch(self, **kw):
        return audit.list_job_snapshot(KB, connection_factory=lambda: self.db, **kw)

    def test_empty_valid(self):
        self.cursor.fetchall.return_value = []
        self.assertEqual(self.fetch(), ())
        self.db.rollback.assert_called_once()
        self.db.close.assert_called_once()
        self.db.commit.assert_not_called()

    def test_exact_kb_sql(self):
        self.cursor.fetchall.return_value = [dict(BASE)]
        self.assertEqual(self.fetch()[0]['job_id'], 'job-1')
        sql, params = self.cursor.execute.call_args.args
        self.assertIn('kb.id = %s', sql)
        self.assertIn('d.deletedAt IS NULL', sql)
        self.assertIn('processingToken IS NOT NULL', sql)
        self.assertEqual(params, (KB, 26))

    def test_limit_bounded(self):
        for val in (0, True, 51, -1):
            with self.assertRaises(ValueError):
                self.fetch(max_jobs=val)

    def test_empty_kb_invalid(self):
        for kb in (None, '', ' ', 'x' * 129):
            with self.assertRaises(ValueError):
                audit.list_job_snapshot(kb, connection_factory=lambda: self.db)

    def test_over_limit_refused_not_partial(self):
        self.cursor.fetchall.return_value = [dict(BASE), dict(BASE, job_id='job-2')]
        with self.assertRaisesRegex(RuntimeError, 'Too many jobs'):
            self.fetch(max_jobs=1)
        self.db.rollback.assert_called_once()

    def test_wrong_scope_refused(self):
        self.cursor.fetchall.return_value = [dict(BASE, knowledge_base_id='foreign')]
        with self.assertRaises(RuntimeError):
            self.fetch()

    def test_duplicate_job_id_refused(self):
        self.cursor.fetchall.return_value = [dict(BASE), dict(BASE)]
        with self.assertRaises(RuntimeError):
            self.fetch()

    def test_invalid_job_id_refused(self):
        self.cursor.fetchall.return_value = [dict(BASE, job_id='')]
        with self.assertRaises(RuntimeError):
            self.fetch()

    def test_query_exception_rolls_back(self):
        self.cursor.execute.side_effect = OSError('connection gone')
        with self.assertRaises(OSError):
            self.fetch()
        self.db.rollback.assert_called_once()
        self.db.close.assert_called_once()
        self.db.commit.assert_not_called()


class AssessmentTests(unittest.TestCase):
    def test_ready_pass(self):
        x = audit.assess_document(dict(BASE), [point()])
        self.assertEqual(x['assessment'], 'ready_scoped_consistent')
        self.assertEqual(x['ready_vectors'], 1)
        self.assertEqual(x['staging_vectors'], 0)

    def test_ready_missing_vector(self):
        self.assertEqual(audit.assess_document(BASE, [])['assessment'], 'ready_inconsistent_manual_review')

    def test_ready_staging_leftover(self):
        self.assertEqual(audit.assess_document(BASE, [point(),point('staging')])['assessment'],
                         'ready_inconsistent_manual_review')

    def test_ready_lease_or_token_refused(self):
        for changed in ({'token_present': True},{'lease_present': True},{'attempts': 0}):
            self.assertEqual(audit.assess_document(dict(BASE, **changed), [point()])['assessment'],
                             'ready_inconsistent_manual_review')

    def test_queued_clean(self):
        x = audit.assess_document(dict(BASE, job_status='QUEUED', document_status='QUEUED', attempts=0), [])
        self.assertEqual(x['assessment'], 'queued_clean_not_complete')

    def test_queued_orphan(self):
        x = audit.assess_document(dict(BASE, job_status='QUEUED', document_status='QUEUED'), [point()])
        self.assertEqual(x['assessment'], 'queued_orphans_or_claim_manual_review')

    def test_running_active(self):
        x = audit.assess_document(dict(BASE, job_status='RUNNING', document_status='PROCESSING', lease_live=True), [])
        self.assertEqual(x['assessment'], 'processing_in_progress')

    def test_running_expired(self):
        x = audit.assess_document(dict(BASE, job_status='RUNNING', document_status='PROCESSING'), [])
        self.assertEqual(x['assessment'], 'expired_or_unknown_processing_lease')

    def test_failed_status(self):
        row = dict(BASE, job_status='FAILED', document_status='FAILED')
        self.assertEqual(audit.assess_document(row, [])['assessment'], 'failed_no_vectors')
        self.assertEqual(audit.assess_document(row, [point()])['assessment'], 'failed_with_vectors_manual_review')

    def test_mismatched_status(self):
        row = dict(BASE, job_status='COMPLETED', document_status='PROCESSING')
        self.assertEqual(audit.assess_document(row, [point()])['assessment'],
                         'job_version_status_mismatch_manual_review')


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.inspection = SimpleNamespace(exists=True, compatible=True, missing_indexes=())
        self.schema = patch('worker.production_qdrant.inspect_collection', return_value=self.inspection)
        self.schema.start()
        self.addCleanup(self.schema.stop)
        self.real_rows = rows({})
        self.reader = MagicMock(return_value=self.real_rows)
        self.points = MagicMock(return_value=(point(),))

    def audit(self, **kwargs):
        return audit.audit_knowledge_base(KB, job_reader=self.reader,
                                           client_factory=lambda: self.client,
                                           point_reader=self.points, **kwargs)

    def test_one_ready_pass_and_read_only_flags(self):
        result = self.audit()
        self.assertEqual(result['assessment'], 'test_kb_consistent')
        self.assertEqual(result['jobs_ready_scoped_consistent'], 1)
        self.assertFalse(result['production_readiness_certified'])
        self.assertFalse(result['retrieval_access_control_verified'])
        for field in ('mysql_modified','qdrant_modified','redis_modified','rustfs_modified'):
            self.assertFalse(result[field])
        self.client.close.assert_called_once()
        self.reader.assert_called_with(KB, max_jobs=25)
        self.assertEqual(self.reader.call_count, 2)
        identity = self.points.call_args.args[1]
        self.assertEqual(identity.knowledge_base_id, KB)
        self.assertEqual(identity.organization_id, 'org-1')
        self.assertEqual(identity.document_version_id, 'v-1')

    def test_zero_jobs_not_ready(self):
        self.reader.return_value = ()
        result = self.audit()
        self.assertEqual(result['assessment'], 'no_jobs_to_verify')
        self.points.assert_not_called()

    def test_queued_job_blocks(self):
        self.reader.return_value = rows({'job_status': 'QUEUED', 'document_status': 'QUEUED', 'attempts': 0})
        self.points.return_value = ()
        self.assertEqual(self.audit()['assessment'], 'manual_review_or_unfinished_jobs')

    def test_status_change_blocks(self):
        self.reader.side_effect = [rows({}), rows({'job_status':'QUEUED'})]
        result = self.audit()
        self.assertFalse(result['mysql_snapshot_stable'])
        self.assertEqual(result['assessment'], 'changed_during_audit')

    def test_new_job_added_during_audit_blocks(self):
        self.reader.side_effect = [rows({}), rows({}, {'job_id':'job-2'})]
        self.assertEqual(self.audit()['assessment'], 'changed_during_audit')

    def test_qdrant_error_fails_closed(self):
        self.points.side_effect = OSError('sensitive Qdrant error')
        result = self.audit()
        self.assertEqual(result['assessment'], 'manual_review_or_unfinished_jobs')
        self.assertEqual(result['jobs'][0]['assessment'], 'qdrant_integrity_not_verified')
        self.assertNotIn('sensitive', str(result))
        self.client.close.assert_called_once()

    def test_oversized_point_scope_fails_closed(self):
        self.points.side_effect = RuntimeError('Exceeded recovery point bound')
        self.assertEqual(self.audit()['jobs'][0]['assessment'], 'qdrant_integrity_not_verified')

    def test_schema_missing_blocks(self):
        self.inspection.exists = False
        with self.assertRaises(RuntimeError):
            self.audit()
        self.client.close.assert_called_once()

    def test_schema_index_missing_blocks(self):
        self.inspection.missing_indexes = ('index_state',)
        with self.assertRaises(RuntimeError):
            self.audit()
        self.client.close.assert_called_once()

    def test_bad_audit_limits(self):
        for max_jobs in (0, True, 60):
            with self.assertRaises(ValueError):
                self.audit(max_jobs=max_jobs)
        self.points.assert_not_called()

    def test_no_payload_text_logged(self):
        report = self.audit()
        self.assertNotIn('sample.txt', str(report))
        self.assertNotIn('processing_token', str(report))


class CliTests(unittest.TestCase):
    def test_cli_clean_return_success(self):
        with patch.object(audit, 'audit_knowledge_base', return_value={'assessment':'test_kb_consistent'}) as func:
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(audit.main(['--knowledge-base-id', KB]),0)
        self.assertIn('test_kb_consistent', out.getvalue())
        func.assert_called_once_with(KB,max_jobs=25)

    def test_cli_unfinished_returns_nonzero(self):
        with patch.object(audit, 'audit_knowledge_base', return_value={'assessment':'no_jobs_to_verify'}):
            with redirect_stdout(io.StringIO()):
                self.assertEqual(audit.main(['--knowledge-base-id',KB]), 1)

    def test_cli_sanitizes_private_error(self):
        with patch.object(audit, 'audit_knowledge_base', side_effect=OSError('SECRET PASSWORD')):
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(audit.main(['--knowledge-base-id', KB]), 1)
        self.assertNotIn('SECRET', out.getvalue())

    def test_cli_optional_json_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'result.json'
            with patch.object(audit,'audit_knowledge_base',return_value={'assessment':'test_kb_consistent'}):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(audit.main(['--knowledge-base-id',KB,'--json-out',str(path)]),0)
                    self.assertEqual(audit.main(['--knowledge-base-id',KB,'--json-out',str(path)]),1)
            self.assertEqual(json_load(path)['assessment'], 'test_kb_consistent')


def json_load(path):
    import json
    return json.loads(path.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
