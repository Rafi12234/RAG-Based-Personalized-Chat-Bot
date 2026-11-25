"""Integrated local pilot tests; real tokenizer/runtime and encrypted artifacts,
fake Qdrant/S3/model generator, no writes to local services. Real MySQL scope
discovery and registry tests run separately in the isolated backend CI job.
"""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import hashlib
import hmac
import json
import os
import time
import unittest

from . import advanced_live_config as cfg,advanced_live_ingestion as ingest
from . import advanced_live_service as svc
from .advanced_live_bundle import write_bundle,read_bundle,reserve_version
from .advanced_grounding import Answer,Claim,Citation
from .auto_ingestion_service import PROTECTED_QA_KBS
from .qdrant_indexing import IndexIdentity
from .test_hierarchy_manifest_store import make_ledger, MemoryS3, Tokenizer, FakeS3Error
from .test_hierarchical_shadow import MockModels,FakeModel
from .test_hierarchy_vector_fidelity import NumericQdrant
from .shadow_reader import SessionScope, ShadowReadError
from .test_pinned_e5 import HAS_TEST_RUNTIME,write_fixture,PinnedE5Model
from .hierarchical_shadow import SHADOW_COLLECTION

KB="testkb0000000001"
ORG="testorg000000001"
DOC="testdoc000000001"
VER="testver000000001"
JOB="testjob000000001"
TOKEN="secret-processing-attempt"
KEY=b"k"*32
PIN="a"*64
FLAGS={cfg.FLAG:cfg.VALUE,cfg.KB_ENV:KB,"NODE_ENV":"development",
       "DOCMIND_ENABLE_SHADOW_READER":cfg.VALUE,"DOCMIND_ENABLE_SHADOW_REGISTRY":cfg.VALUE,
       "DOCMIND_ENABLE_PINNED_SHADOW_AUDIT":cfg.VALUE}


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.i=IndexIdentity(ORG,KB,DOC,VER,JOB,"example.txt")
        self.session=SessionScope("testuser0000001","testsession0001","b"*64)
        self.scope=svc.RequestScope(self.session,ORG,KB)
        self.store=MemoryS3()
        _,self.ledger=make_ledger(version=VER)
        p=patch.dict(os.environ,FLAGS)
        p.start()
        self.addCleanup(p.stop)

    def test_flags_default_production_and_protected(self):
        self.assertTrue(cfg.enabled_for(KB))
        self.assertFalse(cfg.enabled_for("otherkb0000000"))
        with patch.dict(os.environ,{cfg.FLAG:""}):
            self.assertIsNone(cfg.selected_kb())
        for values in ({"NODE_ENV":"production"},{cfg.KB_ENV:PROTECTED_QA_KBS[0]},
                       {"DOCMIND_ENABLE_SHADOW_READER":""}):
            with patch.dict(os.environ,values):
                with self.assertRaises(RuntimeError):
                    cfg.selected_kb()

    def test_post_lease_bundle_and_create_only_reservation(self):
        reserve_version(self.store,"private",self.i,TOKEN,PIN,KEY,guard=lambda:True)
        with self.assertRaises(FakeS3Error):
            reserve_version(self.store,"private",replace(self.i,job_id="different-job"),
                            "different-attempt",PIN,KEY,guard=lambda:True)
        write_bundle(self.store,"private",self.i,self.ledger,PIN,TOKEN,KEY,guard=lambda:True)
        m=self.ledger.manifest
        args=(self.store,"private",self.i,m.digest,m.source_sha256,self.ledger.records_sha256,
              PIN,sha256(TOKEN.encode()).hexdigest(),KEY)
        self.assertEqual(read_bundle(*args,guard=lambda:True),(self.ledger,TOKEN))
        self.assertTrue(all(TOKEN.encode() not in raw for raw in self.store.objects.values()))
        with self.assertRaises(RuntimeError):
            read_bundle(*args,guard=lambda:False)
        with self.assertRaises(Exception):
            read_bundle(*args[:-1],b"x"*32,guard=lambda:True)
        with self.assertRaises(FakeS3Error):
            write_bundle(self.store,"private",self.i,self.ledger,PIN,TOKEN,KEY,guard=lambda:True)

    def test_txt_docx_and_native_pdf_ledger(self):
        import pymupdf
        from docx import Document
        with TemporaryDirectory() as folder:
            root=Path(folder)
            txt=root/"sample.txt"
            txt.write_text("বাংলা মেয়াদ ১২ দিন। Original fact 123.",encoding="utf-8")
            ps,l=ingest.build_ledger(txt,self.i,Tokenizer(),language="eng",max_ocr_pages=1,guard=lambda:True)
            self.assertIsNone(ps[0].page_number)
            self.assertTrue(ps[0].source_locator.startswith("txt:"))
            self.assertIsNone(ps[0].bbox)
            self.assertEqual(l.manifest.source_sha256,sha256(txt.read_bytes()).hexdigest())
            pdf=root/"sample.pdf"
            with pymupdf.open() as d:
                for n in range(1,4):
                    page=d.new_page()
                    page.insert_text((50,70),f"Native PDF verified fact on page {n}.")
                d.save(str(pdf))
            ps,l=ingest.build_ledger(pdf,self.i,Tokenizer(),language="eng",max_ocr_pages=1,guard=lambda:True)
            self.assertEqual(l.manifest.accounted_pages,3)
            self.assertEqual([p.page_number for p in ps],[1,2,3])
            word=root/"sample.docx"
            d=Document()
            d.add_heading("Policy",level=1)
            d.add_paragraph("The duration is 12 days.")
            d.save(word)
            ps,l=ingest.build_ledger(word,self.i,Tokenizer(),language="eng",max_ocr_pages=1,guard=lambda:True)
            self.assertTrue(any(p.section_path==("Policy",) for p in ps))
            self.assertTrue(all(p.page_number is None for p in ps))

    @unittest.skipUnless(HAS_TEST_RUNTIME,"Pinned ONNX runtime required")
    def test_stage_and_register_before_live_ready(self):
        with TemporaryDirectory() as folder:
            root=Path(folder)
            pin,mp,tp=write_fixture(root)
            model=PinnedE5Model(pin,mp,tp)
            file=root/"sample.txt"
            file.write_text("book বাংলা fact 12",encoding="utf-8")
            q=NumericQdrant()
            con=SimpleNamespace(close=lambda:None)
            with patch.object(ingest,"embedding_model",return_value=model), \
                 patch.object(ingest,"bundle_key",return_value=KEY), \
                 patch("worker.hierarchical_shadow._models",return_value=MockModels), \
                 patch("worker.hierarchy_shadow_audit._models",return_value=MockModels), \
                 patch("worker.db.get_connection",return_value=con), \
                 patch.object(ingest,"register_pinned_candidate",side_effect=lambda c,q,i,l,t,m,**kw:l.manifest.digest) as register, \
                 patch.object(ingest,"select_verified_candidate",return_value=1) as select:
                generation=ingest.prepare_advanced_if_selected(q,self.store,"private",file,self.i,TOKEN,
                                   guard=lambda:True,language="eng",max_ocr_pages=1)
                self.assertEqual(len(generation),64)
                register.assert_called_once()
                select.assert_called_once()
                with self.assertRaises(RuntimeError):
                    ingest.prepare_advanced_if_selected(q,self.store,"private",file,self.i,TOKEN,
                                      guard=lambda:True,language="eng",max_ocr_pages=1)
                register.assert_called_once()

    def test_response_and_authorization_boundary(self):
        r=dict(version_id=VER,document_id=DOC,title="Trusted title",generation="a"*64,revision=1)
        c=Citation("p",DOC,VER,2,"pdf:p2",0,8,"12 days.")
        answer=Answer("answered",(Claim("Duration is 12 days",(c,)),))
        data=svc._answer_response(answer,"duration",[r])
        self.assertEqual(data["answer"],"Duration is 12 days [S1]")
        self.assertEqual(data["citations"][0]["quote"],"12 days.")
        self.assertIsNone(data["citations"][0]["score"])
        forged=Answer("answered",(Claim("Duration [S99]",(c,)),))
        self.assertEqual(svc._answer_response(forged,"duration",[r])["status"],"insufficient_evidence")
        with patch.object(svc,"_discover",side_effect=[(),(r,)]):
            with self.assertRaises(ShadowReadError):
                svc.run(self.scope,"question",5,"answer")

    def test_hmac_internal_endpoint(self):
        from fastapi.testclient import TestClient
        from .app import app
        payload={"organizationId":ORG,"knowledgeBaseId":KB,"userId":self.session.user_id,
                 "sessionId":self.session.session_id,"tokenHash":self.session.token_hash,
                 "question":"Which fact?","topK":5,"operation":"answer"}
        body=json.dumps(payload).encode()
        secret="secret"*8
        timestamp=str(int(time.time()*1000))
        nonce=os.urandom(16).hex()
        sig=hmac.new(secret.encode(),timestamp.encode()+b"."+nonce.encode()+b"."+body,hashlib.sha256).hexdigest()
        headers={"content-type":"application/json","x-docmind-internal-timestamp":timestamp,
                 "x-docmind-internal-nonce":nonce,"x-docmind-internal-signature":sig}
        with patch.dict(os.environ,{"DOCMIND_INTERNAL_RETRIEVAL_SECRET":secret}), \
             patch("worker.advanced_live_api.run",return_value={"status":"insufficient_evidence"}) as run, \
             TestClient(app) as client:
            self.assertEqual(client.post("/internal/v2/advanced",content=body).status_code,401)
            self.assertEqual(client.post("/internal/v2/advanced",content=body,headers=headers).status_code,200)
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0],self.scope)
            self.assertEqual(client.post("/internal/v2/advanced",content=body,headers=headers).status_code,401)


if __name__=="__main__":
    unittest.main()
