"""Offline unit tests; no DB/Qdrant/Redis writes or FastEmbed downloads."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from uuid import UUID

from . import live_ingestion_worker as worker


class PilotSafetyTests(unittest.TestCase):
    def test_pilot_disabled_by_default(self):
        with patch.dict(os.environ, {worker.PILOT_FLAG: ""}):
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                worker.run_pilot_ingestion("job1")

    def test_blank_job_rejected(self):
        with self.assertRaises(ValueError): worker.preview_pilot(" ")

    def test_empty_pilot_document_disallowed(self):
        with patch.dict(os.environ, {worker.PILOT_FLAG: worker.PILOT_VALUE}):
            with self.assertRaises(ValueError): worker.run_pilot_ingestion("x", max_ocr_pages=0)

    def test_reject_excessive_ocr_budget(self):
        with patch.dict(os.environ, {worker.PILOT_FLAG: worker.PILOT_VALUE}):
            with self.assertRaises(ValueError): worker.run_pilot_ingestion("x", max_ocr_pages=11)

    def test_heartbeat_interval_checked(self):
        with self.assertRaises(ValueError): worker.LeaseHeartbeat("job", "token", interval_seconds=0)

    def test_heartbeat_guard_fails_after_renewal_failed(self):
        hb = worker.LeaseHeartbeat("job", "token")
        hb._failed.set()
        self.assertFalse(hb.check())

    def test_heartbeat_double_start_rejected(self):
        hb = worker.LeaseHeartbeat("job", "token")
        hb.start()
        try:
            with self.assertRaises(RuntimeError): hb.start()
        finally:
            hb.stop()

    def test_uuid_tracking_detects_duplicate(self):
        first = SimpleNamespace(chunk=SimpleNamespace(document_version_id='ver', chunk_index=0,
                    chunk_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',text='x'), vector=(0.0,)*384)
        second = SimpleNamespace(chunk=SimpleNamespace(document_version_id='ver', chunk_index=1,
                    chunk_id=first.chunk.chunk_id,text='x'), vector=(0.0,)*384)
        tokenizer_module = SimpleNamespace(_tokenizer=lambda: SimpleNamespace(encode=lambda t: SimpleNamespace(ids=[1])))
        with patch.dict('sys.modules', {'worker.storage_qdrant_probe': tokenizer_module}):
            ids, seen = [], set()
            hb = SimpleNamespace(check=lambda: True)
            identity=SimpleNamespace(document_version_id='ver')
            with self.assertRaisesRegex(RuntimeError, 'Duplicate'):
                list(worker._record_ids([first,second],identity,hb,ids,seen))

    def test_uuid_tracking_rejects_wrong_version(self):
        item = SimpleNamespace(chunk=SimpleNamespace(document_version_id='other', chunk_index=0), vector=())
        tokenizer_module = SimpleNamespace(_tokenizer=lambda: object())
        with patch.dict('sys.modules', {'worker.storage_qdrant_probe': tokenizer_module}):
            with self.assertRaisesRegex(RuntimeError,'scope/order'):
                list(worker._record_ids([item],SimpleNamespace(document_version_id='ver'),
                                      SimpleNamespace(check=lambda: True),[],set()))

    def test_uuid_tracking_rejects_expired_lease(self):
        item=SimpleNamespace(chunk=SimpleNamespace(), vector=())
        tokenizer_module = SimpleNamespace(_tokenizer=lambda: object())
        with patch.dict('sys.modules', {'worker.storage_qdrant_probe': tokenizer_module}):
            with self.assertRaisesRegex(RuntimeError,'lease lost'):
                list(worker._record_ids([item],SimpleNamespace(document_version_id='ver'),
                                      SimpleNamespace(check=lambda: False),[],set()))

    def test_preview_pilot_only_reports_safe_metadata(self):
        identity=SimpleNamespace(filename='document.pdf')
        metadata={'jobStatus':'QUEUED','versionStatus':'QUEUED', 'storageKey':'SECRET'}
        with patch.object(worker,'_inspect_prerequisites',return_value=(identity,metadata)):
            result=worker.preview_pilot('job')
        self.assertNotIn('storageKey',result)
        self.assertEqual(result['eligible_for_explicit_pilot'],True)
        self.assertFalse(result['qdrant_written'])

    def _prereq_context(self, *, queued=True, staged=0, ready=0,
                        compatible=True, missing_indexes=(), unexpected=0):
        identity=SimpleNamespace(filename="source.pdf", organization_id="org",
                                 knowledge_base_id="kb", document_id="doc",
                                 document_version_id="ver")
        metadata={"filename":"source.pdf","jobStatus":"QUEUED","versionStatus":"QUEUED"}
        fake_client=MagicMock()
        fake_client.count.return_value=SimpleNamespace(count=unexpected)
        from types import SimpleNamespace as NS
        fake_models=NS(Filter=lambda **kw: kw, FieldCondition=lambda **kw: kw,
                       MatchValue=lambda **kw: kw)
        modules={
            "qdrant_client":NS(models=fake_models),
            "worker.ingestion_lease_verification":SimpleNamespace(
                read_job_state=lambda job:SimpleNamespace(ready_for_claim=queued)),
            "worker.production_qdrant":SimpleNamespace(
                local_client=lambda:fake_client,
                inspect_collection=lambda c:SimpleNamespace(exists=True,compatible=compatible,
                                                           missing_indexes=missing_indexes)),
            "worker.publication_audit":SimpleNamespace(
                audit_publication_counts=lambda c,i:SimpleNamespace(staged=staged,ready=ready)),
            "worker.storage_extraction_probe":SimpleNamespace(read_job_metadata=lambda job:metadata),
            "worker.storage_qdrant_probe":SimpleNamespace(identity_for_job=lambda job:identity),
        }
        return patch.dict('sys.modules',modules),fake_client

    def test_preflight_accepts_clean_queued_version(self):
        ctx, client = self._prereq_context()
        with ctx:
            identity, metadata = worker._inspect_prerequisites("job")
        self.assertEqual(identity.filename, "source.pdf")
        client.close.assert_called_once()

    def test_preflight_refuses_orphan_staging(self):
        ctx, client = self._prereq_context(staged=2)
        with ctx:
            with self.assertRaisesRegex(RuntimeError, 'Existing production vectors'):
                worker._inspect_prerequisites("job")
        client.close.assert_called_once()

    def test_preflight_refuses_orphan_ready(self):
        ctx, _ = self._prereq_context(ready=3)
        with ctx:
            with self.assertRaisesRegex(RuntimeError, 'Existing production vectors'):
                worker._inspect_prerequisites("job")

    def test_preflight_refuses_unknown_state_vectors(self):
        ctx, client = self._prereq_context(unexpected=1)
        with ctx:
            with self.assertRaisesRegex(RuntimeError, 'Existing production vectors'):
                worker._inspect_prerequisites("job")
        client.close.assert_called_once()

    def test_preflight_refuses_incompatible_collection(self):
        ctx, client = self._prereq_context(compatible=False)
        with ctx:
            with self.assertRaisesRegex(RuntimeError, 'not initialized'):
                worker._inspect_prerequisites("job")
        client.close.assert_called_once()

    def test_preflight_refuses_nonqueued(self):
        ctx, _ = self._prereq_context(queued=False)
        with ctx:
            with self.assertRaisesRegex(RuntimeError, 'not eligible'):
                worker._inspect_prerequisites("job")

    def test_renew_commit_on_success(self):
        conn=MagicMock()
        fake_db=SimpleNamespace(get_connection=lambda:conn)
        fake_lifecycle=SimpleNamespace(renew_processing_lease=lambda *a, **kw:True)
        with patch.dict('sys.modules',{'worker.db':fake_db,'worker.ingestion_lifecycle':fake_lifecycle}):
            self.assertTrue(worker._renew_owned_lease('job','tok'))
        conn.commit.assert_called_once()
        conn.close.assert_called_once()

    def test_renew_rolls_back_on_lost_token(self):
        conn=MagicMock()
        fake_db=SimpleNamespace(get_connection=lambda:conn)
        fake_lifecycle=SimpleNamespace(renew_processing_lease=lambda *a, **kw:False)
        with patch.dict('sys.modules',{'worker.db':fake_db,'worker.ingestion_lifecycle':fake_lifecycle}):
            self.assertFalse(worker._renew_owned_lease('job','tok'))
        conn.rollback.assert_called_once()
        conn.commit.assert_not_called()

    def test_renew_rolls_back_on_error(self):
        conn=MagicMock()
        fake_db=SimpleNamespace(get_connection=lambda:conn)
        def broken(*a,**k):raise RuntimeError('boom')
        with patch.dict('sys.modules',{'worker.db':fake_db,'worker.ingestion_lifecycle':SimpleNamespace(renew_processing_lease=broken)}):
            with self.assertRaises(RuntimeError):worker._renew_owned_lease('job','tok')
        conn.rollback.assert_called_once()


if __name__ == '__main__':unittest.main()
