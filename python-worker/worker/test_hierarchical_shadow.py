"""Offline distinct v2 shadow-index tests. No real Qdrant, SQL, ML downloads."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from .document_structure import SourceBlock, PdfPageStructure
from .hierarchical_index import HierarchyManifestBuilder, iter_manifested_pdf_passages
from .hierarchical_shadow import (
    SHADOW_COLLECTION, KEYWORD_FIELDS, SHADOW_SCHEMA,
    inspect_shadow_collection, initialize_shadow_collection,
    iter_shadow_embeddings, stage_shadow_index,
)
from .qdrant_indexing import IndexIdentity


class MockModels:
    class Distance:
        COSINE = "Cosine"
    class PayloadSchemaType:
        KEYWORD = "keyword"
    class VectorParams:
        def __init__(self,**kw): self.__dict__.update(kw)
    class PointStruct:
        def __init__(self,**kw): self.__dict__.update(kw)
    class Filter:
        def __init__(self,**kw): self.__dict__.update(kw)
    class MatchValue:
        def __init__(self,**kw): self.__dict__.update(kw)
    class FieldCondition:
        def __init__(self,**kw): self.__dict__.update(kw)


class Tokenizer:
    def encode(self,text):
        return SimpleNamespace(ids=list(range(len(text)+2)))


class FakeModel:
    def embed(self, texts, *, batch_size):
        assert all(t.startswith("passage: ") for t in texts)
        assert batch_size == len(texts)
        return [(1.0,) + (0.0,)*383 for t in texts]


class FakeQdrant:
    def __init__(self):
        self.exists=False
        self.schema={}
        self.points={}
        self.created=[]
        self.indexes=[]
        self.count_calls=0
        self.upserts=0

    def collection_exists(self,name):
        assert name==SHADOW_COLLECTION
        return self.exists

    def get_collection(self,name):
        assert name==SHADOW_COLLECTION
        return SimpleNamespace(
            config=SimpleNamespace(params=SimpleNamespace(
                vectors=SimpleNamespace(size=384,distance="Cosine"))),
            payload_schema=self.schema,
        )

    def create_collection(self,collection_name,vectors_config):
        assert not self.exists
        self.created.append(collection_name)
        self.exists=True

    def create_payload_index(self,collection_name,field_name,field_schema,wait):
        assert collection_name==SHADOW_COLLECTION
        assert field_schema=="keyword" and wait
        self.schema[field_name]=SimpleNamespace(data_type="keyword")
        self.indexes.append(field_name)

    def count(self,collection_name,count_filter,exact):
        assert collection_name==SHADOW_COLLECTION and exact
        self.count_calls+=1
        clauses={x.key:x.match.value for x in count_filter.must}
        values=sum(1 for row in self.points.values() if all(
            row.payload.get(k)==v for k,v in clauses.items()))
        return SimpleNamespace(count=values)

    def upsert(self,collection_name,points,wait):
        assert collection_name==SHADOW_COLLECTION and wait
        self.upserts+=1
        for row in points: self.points[row.id]=row


class ShadowTests(unittest.TestCase):
    def setUp(self):
        self.fake=FakeQdrant()
        from . import hierarchical_shadow as shadow
        self.patch=patch.object(shadow,"_models",return_value=MockModels)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.identity=IndexIdentity("org","kb","doc","ver","job","book.pdf")

    def make(self):
        pages=(PdfPageStructure(i,(
                    SourceBlock(f"block-{i}","ver",0,f"Fact {i}: value {i+1000}",
                                "pdf_text",i,None,(),f"pdf:p{i:05d}:b00000"),),
                False,False) for i in range(1,6))
        manifest_builder=HierarchyManifestBuilder("ver","a"*64,expected_pages=5)
        passages=list(iter_manifested_pdf_passages(
            pages,manifest_builder,Tokenizer()))
        return passages,manifest_builder.finalize()

    def test_preview_does_not_create_index(self):
        self.assertFalse(inspect_shadow_collection(self.fake))
        self.assertFalse(initialize_shadow_collection(self.fake))
        self.assertFalse(self.fake.created)

    def test_explicit_creates_isolated_shadow_only(self):
        self.assertTrue(initialize_shadow_collection(self.fake,explicit_create=True))
        self.assertEqual(self.fake.created,[SHADOW_COLLECTION])
        self.assertNotIn("docmind_documents_e5_384_v1",self.fake.created)
        self.assertEqual(set(self.fake.indexes),set(KEYWORD_FIELDS))
        self.assertTrue(inspect_shadow_collection(self.fake))

    def test_staging_preserves_scope_hierarchy_and_nonvisibility(self):
        passages,manifest=self.make()
        initialize_shadow_collection(self.fake,explicit_create=True)
        embeddings=list(iter_shadow_embeddings(passages,FakeModel(),batch_size=2))
        result=stage_shadow_index(self.fake,self.identity,manifest,embeddings,
                                  "token",lease_guard=lambda:True,batch_size=2)
        self.assertEqual(result.staged_points,5)
        self.assertFalse(result.query_enabled)
        self.assertFalse(result.version_ready)
        self.assertEqual(self.fake.upserts,3)
        self.assertTrue(all(x.payload["index_state"]=="staging"
                            for x in self.fake.points.values()))
        self.assertTrue(all(x.payload["document_version_id"]=="ver"
                            for x in self.fake.points.values()))
        self.assertTrue(all(x.payload["index_generation"]==manifest.digest
                            for x in self.fake.points.values()))
        self.assertTrue(all(len(x.vector)==384 for x in self.fake.points.values()))
        self.assertIn("parent_id",next(iter(self.fake.points.values())).payload)
        self.assertIn("source_locator",next(iter(self.fake.points.values())).payload)
        self.assertEqual(next(iter(self.fake.points.values())).payload["index_schema_version"],
                         SHADOW_SCHEMA)

    def test_existing_generation_refused_without_any_write(self):
        ps,m=self.make()
        initialize_shadow_collection(self.fake,explicit_create=True)
        embed=list(iter_shadow_embeddings(ps,FakeModel()))
        stage_shadow_index(self.fake,self.identity,m,embed,"token",
                           lease_guard=lambda:True)
        prior=self.fake.upserts
        with self.assertRaisesRegex(RuntimeError,"existing|Existing"):
            stage_shadow_index(self.fake,self.identity,m,embed,"token2",
                               lease_guard=lambda:True)
        self.assertEqual(self.fake.upserts,prior)

    def test_lost_lease_aborts_before_network_writes(self):
        ps,m=self.make()
        initialize_shadow_collection(self.fake,explicit_create=True)
        with self.assertRaisesRegex(RuntimeError,"lease"):
            stage_shadow_index(self.fake,self.identity,m,[],
                               "token",lease_guard=lambda:False)
        self.assertEqual(self.fake.upserts,0)

    def test_wrong_manifest_order_refused_before_batch_write(self):
        ps,m=self.make()
        initialize_shadow_collection(self.fake,explicit_create=True)
        embeddings=list(iter_shadow_embeddings(ps,FakeModel()))
        with self.assertRaisesRegex(RuntimeError,"manifest"):
            stage_shadow_index(self.fake,self.identity,m,embeddings[::-1],
                               "token",lease_guard=lambda:True)
        self.assertEqual(self.fake.upserts,0)

    def test_missing_embedding_remains_unpublished(self):
        ps,m=self.make()
        initialize_shadow_collection(self.fake,explicit_create=True)
        embeddings=list(iter_shadow_embeddings(ps[:-1],FakeModel()))
        with self.assertRaisesRegex(RuntimeError,"incomplete"):
            stage_shadow_index(self.fake,self.identity,m,embeddings,
                               "token",lease_guard=lambda:True)
        self.assertTrue(all(p.payload["index_state"]=="staging"
                            for p in self.fake.points.values()))

    def test_changed_version_manifest_refused(self):
        ps,m=self.make()
        initialize_shadow_collection(self.fake,explicit_create=True)
        other=IndexIdentity("org","kb","doc","other","job","book.pdf")
        with self.assertRaises(ValueError):
            stage_shadow_index(self.fake,other,m,[],"token",lease_guard=lambda:True)
        self.assertEqual(self.fake.upserts,0)

    def test_shadow_schema_collision_cannot_repair(self):
        self.fake.exists=True
        with self.assertRaisesRegex(RuntimeError,"indexes"):
            initialize_shadow_collection(self.fake,explicit_create=True)
        self.assertEqual(self.fake.indexes,[])
        self.assertEqual(self.fake.created,[])


if __name__=="__main__":
    unittest.main()
