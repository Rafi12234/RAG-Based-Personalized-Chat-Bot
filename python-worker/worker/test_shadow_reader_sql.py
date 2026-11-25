"""CI-only real MySQL integration for the additive reader/provenance schema.

Never run against local user data. Requires the isolated GitHub Actions
MySQL service after Prisma migrations. Qdrant and audit/model are fakes;
the authorization, registration, selection, and constraints are real SQL.
"""
from dataclasses import replace
from hashlib import sha256
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from . import shadow_reader as reader
from .hierarchy_generation_registry import select_verified_candidate, revoke_selection
from .hierarchy_shadow_audit import ShadowIntegrityReport
from .hierarchy_vector_fidelity import VectorFidelityReport
from .pinned_shadow_verification import PinnedFidelityReceipt
from .hierarchical_shadow import SHADOW_COLLECTION, initialize_shadow_collection, iter_shadow_embeddings, stage_shadow_index
from .test_hierarchy_manifest_store import make_ledger
from .test_hierarchical_shadow import MockModels, FakeModel
from .test_shadow_reader import FakeSearch, PIN, TOKEN
from .qdrant_indexing import IndexIdentity

CI_ONLY = (os.environ.get("GITHUB_ACTIONS") == "true" and
           os.environ.get("DOCMIND_SHADOW_SQL_CI") == "YES" and
           os.environ.get("NODE_ENV") == "test" and
           os.environ.get("DATABASE_HOST") == "127.0.0.1" and
           os.environ.get("DATABASE_PORT") == "3307" and
           os.environ.get("DATABASE_NAME") == "docmind_ai")


@unittest.skipUnless(CI_ONLY, "Only the isolated GitHub Actions MySQL service")
class RealMySQLReaderTests(unittest.TestCase):
    def factory(self):
        import pymysql
        return pymysql.connect(host="127.0.0.1", port=3307,
                               user=os.environ["DATABASE_USER"],
                               password=os.environ["DATABASE_PASSWORD"],
                               database="docmind_ai", cursorclass=pymysql.cursors.DictCursor,
                               connect_timeout=5, read_timeout=10, write_timeout=10,
                               autocommit=False)

    def sql(self, sql, params=()):
        connection = self.factory()
        try:
            with connection.cursor() as cur:
                cur.execute(sql, params)
            connection.commit()
        finally:
            connection.close()

    def setUp(self):
        prefix = "shadow-ci-" + uuid4().hex + "-"
        self.user = prefix+"u"
        self.member = prefix+"m"
        self.auth = prefix+"a"
        self.i = IndexIdentity(prefix+"org", prefix+"kb", prefix+"doc",
                               prefix+"v", prefix+"j", "book.pdf")
        self.new_version = prefix+"v2"
        self.hash = sha256(self.auth.encode()).hexdigest()
        self.session = reader.SessionScope(self.user, self.auth, self.hash)
        self.addCleanup(self.cleanup)
        i = self.i
        self.sql("INSERT INTO User (id,name,email,passwordHash,isActive,createdAt,updatedAt) VALUES (%s,'CI user',%s,'test-only',TRUE,UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))",
                 (self.user, self.user+"@example.invalid"))
        self.sql("INSERT INTO Organization (id,name,createdAt,updatedAt) VALUES (%s,'CI org',UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))", (i.organization_id,))
        self.sql("INSERT INTO OrganizationMember (id,userId,organizationId,role,joinedAt) VALUES (%s,%s,%s,'MEMBER',UTC_TIMESTAMP(3))",
                 (self.member,self.user,i.organization_id))
        self.sql("INSERT INTO AuthSession (id,userId,tokenHash,createdAt,expiresAt) VALUES (%s,%s,%s,UTC_TIMESTAMP(3),DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 1 HOUR))",
                 (self.auth,self.user,self.hash))
        self.sql("INSERT INTO KnowledgeBase (id,organizationId,name,createdAt,updatedAt) VALUES (%s,%s,'CI KB',UTC_TIMESTAMP(3),UTC_TIMESTAMP(3))",
                 (i.knowledge_base_id,i.organization_id))
        self.sql("INSERT INTO Document (id,knowledgeBaseId,uploadedById,title,createdAt) VALUES (%s,%s,%s,'CI book',UTC_TIMESTAMP(3))",
                 (i.document_id,i.knowledge_base_id,self.user))
        self.sql("INSERT INTO DocumentVersion (id,documentId,version,filename,mimeType,sizeBytes,storageKey,status,createdAt) VALUES (%s,%s,1,'book.pdf','application/pdf',10,%s,'PROCESSING',UTC_TIMESTAMP(3))",
                 (i.document_version_id,i.document_id,i.document_version_id))
        self.sql("INSERT INTO IngestionJob (id,documentVersionId,status,attempts,createdAt,processingToken,processingLeaseUntil) VALUES (%s,%s,'RUNNING',1,UTC_TIMESTAMP(3),%s,DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 1 HOUR))",
                 (i.job_id,i.document_version_id,TOKEN))
        passages, self.ledger = make_ledger(version=i.document_version_id)
        self.model = SimpleNamespace(encode=lambda _:SimpleNamespace(ids=[1,2]),
                                     embed=lambda texts,batch_size:[(1.,)+(0.,)*383])
        self.q = FakeSearch()
        for p in (patch.dict(os.environ, {reader.FLAG:"YES_FOR_TESTING",
                                          "DOCMIND_ENABLE_SHADOW_REGISTRY":"YES_FOR_TESTING"}),
                  patch.object(reader,"_model",return_value=PIN),
                  patch.object(reader,"_models",return_value=MockModels),
                  patch("worker.hierarchical_shadow._models",return_value=MockModels)):
            p.start()
            self.addCleanup(p.stop)
        initialize_shadow_collection(self.q, explicit_create=True)
        stage_shadow_index(self.q,i,self.ledger.manifest,
                           iter_shadow_embeddings(passages,FakeModel()),TOKEN,
                           lease_guard=lambda:True)

    def cleanup(self):
        i = self.i
        for table, col, val in (
            ("ShadowIndexSelection","documentVersionId",i.document_version_id),
            ("ShadowIndexCandidate","documentVersionId",i.document_version_id),
            ("IngestionJob","id",i.job_id),
            ("DocumentVersion","documentId",i.document_id),
            ("Document","id",i.document_id),
            ("KnowledgeBase","id",i.knowledge_base_id),
            ("AuthSession","id",self.auth),
            ("OrganizationMember","id",self.member),
            ("Organization","id",i.organization_id),
            ("User","id",self.user),
        ):
            self.sql("DELETE FROM "+table+" WHERE "+col+" = %s",(val,))

    def register(self):
        i, l = self.i, self.ledger
        g = l.manifest.digest
        n = l.manifest.passage_count
        structure = ShadowIntegrityReport(SHADOW_COLLECTION,g,n,n)
        vectors = VectorFidelityReport(SHADOW_COLLECTION,g,n,"intfloat/multilingual-e5-small",1.)
        receipt = PinnedFidelityReceipt(i.organization_id,i.knowledge_base_id,
            i.document_id,i.document_version_id,i.job_id,l.manifest.source_sha256,
            l.records_sha256,PIN,vectors)
        con = self.factory()
        try:
            with patch.object(reader,"audit_shadow_generation",return_value=structure) as a, \
                 patch.object(reader,"verify_pinned_shadow",return_value=receipt) as b:
                result = reader.register_pinned_candidate(con,self.q,i,l,TOKEN,self.model,
                                                         lease_guard=lambda:True)
                a.assert_called_once()
                b.assert_called_once()
                return result
        finally:
            con.close()

    def select(self, revision=0):
        con = self.factory()
        try:
            return select_verified_candidate(con,self.i,self.ledger.manifest.digest,
                                             TOKEN,expected_revision=revision,lease_guard=lambda:True)
        finally:
            con.close()

    def search(self, **kwargs):
        args = dict(connection_factory=self.factory,client=self.q,session=self.session,
                    identity=self.i,ledger=self.ledger,processing_token=TOKEN,
                    model=self.model,question="What is on the page?",top_k=2)
        args.update(kwargs)
        return reader.search_verified_shadow(**args)

    def ready(self):
        self.sql("UPDATE IngestionJob SET status='COMPLETED',completedAt=UTC_TIMESTAMP(3),processingToken=NULL,processingLeaseUntil=NULL WHERE id=%s",(self.i.job_id,))
        self.sql("UPDATE DocumentVersion SET status='READY' WHERE id=%s",(self.i.document_version_id,))

    def test_real_sql_registration_authorization_revocation_and_constraints(self):
        import pymysql
        i = self.i
        digest = self.register()
        self.assertEqual(digest,self.ledger.manifest.digest)
        with self.assertRaises(pymysql.IntegrityError):
            self.register()
        self.assertEqual(self.select(),1)
        with self.assertRaises(reader.ShadowReadError):
            self.search()  # PROCESSING cannot be read.
        self.ready()
        hits = self.search()
        self.assertEqual(len(hits),2)
        self.assertEqual(hits[0].document_title,"CI book")

        with self.assertRaises(pymysql.MySQLError):
            self.sql("UPDATE ShadowIndexSelection SET queryEnabled=TRUE WHERE documentVersionId=%s",(i.document_version_id,))
        with self.assertRaises(pymysql.MySQLError):
            self.sql("UPDATE ShadowIndexCandidate SET verifiedAttempt=NULL WHERE documentVersionId=%s",(i.document_version_id,))

        for bad_session in (replace(self.session,token_hash="0"*64),
                            replace(self.session,session_id=self.auth.upper()),
                            replace(self.session,user_id=self.user+"x")):
            with self.assertRaises(reader.ShadowReadError):
                self.search(session=bad_session)
        with self.assertRaises(reader.ShadowReadError):
            self.search(processing_token="other-attempt")

        for bad, restore, params in (
            ("UPDATE User SET isActive=FALSE WHERE id=%s","UPDATE User SET isActive=TRUE WHERE id=%s",(self.user,)),
            ("UPDATE AuthSession SET expiresAt=UTC_TIMESTAMP(3) WHERE id=%s","UPDATE AuthSession SET expiresAt=DATE_ADD(UTC_TIMESTAMP(3), INTERVAL 1 HOUR) WHERE id=%s",(self.auth,)),
            ("UPDATE Document SET deletedAt=UTC_TIMESTAMP(3) WHERE id=%s","UPDATE Document SET deletedAt=NULL WHERE id=%s",(i.document_id,)),
            ("UPDATE IngestionJob SET attempts=2 WHERE id=%s","UPDATE IngestionJob SET attempts=1 WHERE id=%s",(i.job_id,)),
            ("UPDATE ShadowIndexCandidate SET revokedAt=UTC_TIMESTAMP(3) WHERE documentVersionId=%s","UPDATE ShadowIndexCandidate SET revokedAt=NULL WHERE documentVersionId=%s",(i.document_version_id,)),
        ):
            self.sql(bad,params)
            with self.assertRaises(reader.ShadowReadError):
                self.search()
            self.sql(restore,params)

        self.sql("INSERT INTO DocumentVersion (id,documentId,version,filename,mimeType,sizeBytes,storageKey,status,createdAt) VALUES (%s,%s,2,'new.pdf','application/pdf',10,%s,'QUEUED',UTC_TIMESTAMP(3))",
                 (self.new_version,i.document_id,self.new_version))
        with self.assertRaises(reader.ShadowReadError):
            self.search()
        self.sql("DELETE FROM DocumentVersion WHERE id=%s",(self.new_version,))

        # Revoke membership during the actual vector request: final SQL must
        # reject all excerpts rather than returning the already fetched text.
        real_query = self.q.query_points
        def revoke_member(**kwargs):
            result = real_query(**kwargs)
            self.sql("DELETE FROM OrganizationMember WHERE id=%s",(self.member,))
            return result
        with patch.object(self.q,"query_points",side_effect=revoke_member):
            with self.assertRaises(reader.ShadowReadError):
                self.search()
        self.sql("INSERT INTO OrganizationMember (id,userId,organizationId,role,joinedAt) VALUES (%s,%s,%s,'MEMBER',UTC_TIMESTAMP(3))",
                 (self.member,self.user,i.organization_id))
        self.assertEqual(len(self.search()),2)
        con = self.factory()
        try:
            self.assertEqual(revoke_selection(con,i,digest,expected_revision=1),2)
        finally:
            con.close()
        with self.assertRaises(reader.ShadowReadError):
            self.search()


if __name__ == "__main__":
    unittest.main()
