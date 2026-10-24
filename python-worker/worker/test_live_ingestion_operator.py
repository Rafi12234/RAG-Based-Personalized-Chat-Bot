"""Read-only and send-task mocks. No external services accessed."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch,MagicMock
from . import live_ingestion_operator as op

class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.preview=patch.object(op,'preview_pilot',return_value={'job_id':'job1','eligible_for_explicit_pilot':True})
        self.preview.start();self.addCleanup(self.preview.stop)

    def test_preview_never_sends(self):
        self.assertEqual(op.operator_action('job1')['job_id'],'job1')

    def test_requires_confirm_matching_id(self):
        with self.assertRaises(ValueError):op.operator_action('job1',submit=True)
        with self.assertRaises(ValueError):op.operator_action('job1',submit=True,confirm_job_id='job2')

    def test_explicit_submission_dedicated_queue(self):
        fake=SimpleNamespace(celery_app=SimpleNamespace(send_task=MagicMock(return_value=SimpleNamespace(id='celery-id'))))
        with patch.dict('sys.modules', {'worker.celery_app':fake}):
            result=op.operator_action('job1',submit=True,confirm_job_id='job1')
        self.assertEqual(result['queue'],'docmind_phase2_live')
        self.assertFalse(result['job_completed'])
        fake.celery_app.send_task.assert_called_once_with(
            'docmind.ingestion.live_pilot_v1',args=['job1'],queue='docmind_phase2_live',retry=False)

if __name__=='__main__':unittest.main()
