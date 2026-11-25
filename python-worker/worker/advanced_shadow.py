"""Phase 4.4–4.7 isolated, multi-document advanced RAG integration.

Only the same permission-fenced development/test selection as Phase 4.3E.
No activation, live route, datastore writes, or global corpus cache.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from time import perf_counter
from types import MappingProxyType

from . import shadow_reader as sr
from .document_embeddings import embed_query, MODEL_NAME
from .hierarchical_shadow import SHADOW_COLLECTION, SHADOW_SCHEMA, STAGING, inspect_shadow_collection
from .hierarchy_manifest_store import validate_ledger, HierarchyLedger
from .hierarchy_shadow_audit import _version_filter, _validate_point, PAYLOAD_FIELDS
from .hybrid_index import BM25, Passage, reciprocal_rank_fusion, language, MAX_PASSAGES
from .advanced_context import rerank, pack
from .local_pair_model import LocalPairModel

MAX_DOCUMENTS = 8
MAX_SUBQUERIES = 4
BRANCH_SIZE = 50


@dataclass(frozen=True)
class SelectedDocument:
    identity: object
    ledger: HierarchyLedger
    processing_token: str = field(repr=False)


@dataclass(frozen=True)
class Retrieval:
    dense: tuple[str, ...]
    lexical: tuple[str, ...]
    hybrid: tuple[str, ...]
    reranked: tuple[str, ...]
    language: str
    timings_ms: tuple[tuple[str,float], ...]


class AdvancedShadowSession:
    """Prepared corpus for a single trusted authenticated server session.

    Materializes source-validated text ONCE, not on every query. Memory only;
    dispose on logout/expiry. Every search rechecks all selected documents.
    This does not establish transactional consistency with Qdrant.
    """
    def __init__(self, *, connection_factory, client, session, documents, model):
        sr._enabled()
        if type(session) is not sr.SessionScope or not isinstance(documents,(list,tuple)):
            raise sr.ShadowReadError("Authenticated selected scope required")
        if not 1 <= len(documents) <= MAX_DOCUMENTS or any(type(d) is not SelectedDocument for d in documents):
            raise sr.ShadowReadError("Invalid selected document set")
        self._connection_factory=connection_factory
        self._client=client
        self._session=session
        self._documents=tuple(documents)
        self._model=model
        self._pin=sr._model(model)
        first=documents[0].identity
        seen=set()
        total=0
        for d in documents:
            sr._scope(d.identity,d.ledger.manifest.digest)
            validate_ledger(d.ledger)
            if (d.identity.organization_id,d.identity.knowledge_base_id)!=(first.organization_id,first.knowledge_base_id):
                raise sr.ShadowReadError("Mixed tenant/KB corpus prohibited")
            if (d.identity.document_id in seen or
                    d.ledger.manifest.document_version_id!=d.identity.document_version_id):
                raise sr.ShadowReadError("Duplicate or mismatched document")
            seen.add(d.identity.document_id)
            total+=d.ledger.manifest.passage_count
            if total>MAX_PASSAGES:
                raise sr.ShadowReadError("Corpus passage budget exceeded")
            sr._digest(d.processing_token)
        self._snapshots=self._read()
        if not inspect_shadow_collection(client):
            raise sr.ShadowReadError("Shadow collection unavailable")
        self._records={}
        for d in documents:
            for r in d.ledger.records:
                if r.passage_id in self._records:
                    raise sr.ShadowReadError("Duplicate passage identity across corpus")
                self._records[r.passage_id]=(d,r)
        self._index=BM25(self._load())
        self._next={}
        for p in self._index.docs.values():
            if p.previous is not None:
                prev=self._index.docs.get(p.previous)
                if (prev is None or (prev.version_id,prev.parent)!=(p.version_id,p.parent)
                        or p.previous in self._next):
                    raise sr.ShadowReadError("Invalid neighbor chain")
                self._next[p.previous]=p.id
        self._next=MappingProxyType(self._next)
        self.assert_current()

    def _read(self):
        return tuple(sr._read_snapshot(self._connection_factory,self._session,d.identity,d.ledger,
                    self._pin,sr._digest(d.processing_token)) for d in self._documents)

    def assert_current(self):
        sr._enabled()
        if sr._model(self._model)!=self._pin or self._read()!=self._snapshots:
            raise sr.ShadowReadError("Advanced search authorization or revision changed")

    def _load(self):
        for d,snapshot in zip(self._documents,self._snapshots):
            scope=_version_filter(d.identity)
            expected=d.ledger.manifest.passage_count
            if self._client.count(collection_name=SHADOW_COLLECTION,count_filter=scope,exact=True).count!=expected:
                raise sr.ShadowReadError("Shadow coverage mismatch")
            seen=set()
            offsets=set()
            offset=None
            while True:
                self.assert_current()
                points,following=self._client.scroll(collection_name=SHADOW_COLLECTION,
                    scroll_filter=scope,limit=128,offset=offset,with_payload=list(PAYLOAD_FIELDS),with_vectors=False)
                if not isinstance(points,(tuple,list)) or len(points)>128 or (not points and following is not None):
                    raise sr.ShadowReadError("Invalid shadow scroll")
                for p in points:
                    key=str(p.id)
                    item=self._records.get(key)
                    if item is None or item[0] is not d or key in seen:
                        raise sr.ShadowReadError("Unexpected scoped passage")
                    r=item[1]
                    _validate_point(r,p.payload,d.identity,d.ledger,d.processing_token)
                    seen.add(key)
                    yield Passage(key,d.identity.document_id,d.identity.document_version_id,d.ledger.manifest.digest,
                                  snapshot[1],r.page_number,r.source_locator,r.section_path,r.parent_id,
                                  r.previous_in_parent,p.payload["text"])
                if following is None:
                    break
                marker=str(following)
                if marker in offsets or len(offsets)>=expected:
                    raise sr.ShadowReadError("Shadow pagination failed")
                offsets.add(marker)
                offset=following
            if len(seen)!=expected or self._client.count(collection_name=SHADOW_COLLECTION,count_filter=scope,exact=True).count!=expected:
                raise sr.ShadowReadError("Incomplete shadow corpus")

    def _dense(self, question):
        if len(self._model.encode("query: "+question.strip()).ids)>480:
            raise ValueError("Question exceeds E5 token budget")
        vector=embed_query(self._model,question)
        m=sr._models()
        scores={}
        for d in self._documents:
            self.assert_current()
            i=d.identity
            fields={"organization_id":i.organization_id,"knowledge_base_id":i.knowledge_base_id,
                    "document_id":i.document_id,"document_version_id":i.document_version_id,
                    "job_id":i.job_id,"index_generation":d.ledger.manifest.digest,
                    "processing_token":d.processing_token,"index_state":STAGING,
                    "index_schema_version":SHADOW_SCHEMA,"embedding_model":MODEL_NAME}
            limit=min(BRANCH_SIZE,d.ledger.manifest.passage_count)
            points=self._client.query_points(collection_name=SHADOW_COLLECTION,query=list(vector),
                    query_filter=m.Filter(must=[m.FieldCondition(key=k,match=m.MatchValue(value=v))
                                               for k,v in fields.items()]),limit=limit,
                    with_payload=list(PAYLOAD_FIELDS),with_vectors=False,timeout=10).points
            if not isinstance(points,(list,tuple)) or len(points)>limit:
                raise sr.ShadowReadError("Invalid dense result bound")
            for p in points:
                key=str(p.id)
                item=self._records.get(key)
                if (item is None or item[0] is not d or key in scores or type(p.score) not in (int,float)
                        or not isfinite(p.score) or not -1.00001<=p.score<=1.00001):
                    raise sr.ShadowReadError("Unexpected dense result")
                _validate_point(item[1],p.payload,i,d.ledger,d.processing_token)
                scores[key]=p.score
        return tuple(sorted(scores,key=lambda key:(-scores[key],key))[:BRANCH_SIZE])

    def retrieve(self,question,*,reranker=None):
        if not isinstance(question,str) or not question.strip() or len(question)>1000:
            raise ValueError("Invalid bounded question")
        self.assert_current()
        start=perf_counter()
        dense=self._dense(question)
        t1=perf_counter()
        lexical=self._index.rank(question)
        routed=self._index.rank(question,sections=True)
        fused=reciprocal_rank_fusion([(dense,1.),(lexical,1.),(routed,.35)],limit=50)
        # Preserve a bounded set of lexical-only candidates for reranking;
        # never let a strong dense/routed overlap erase rare identifier hits.
        hybrid=tuple(dict.fromkeys((*fused,*lexical[:15],*dense[:15])))[:80]
        t2=perf_counter()
        if reranker is not None:
            if type(reranker) is not LocalPairModel or reranker.pin.labels!=("relevance",):
                raise ValueError("Pinned relevance model required")
            ranked=rerank(question,hybrid,self._index.docs,reranker,limit=40)
        else:
            ranked=hybrid[:40]
        t3=perf_counter()
        self.assert_current()
        return Retrieval(dense,lexical,hybrid,ranked,language(question),
                         (("dense",(t1-start)*1000),("hybrid",(t2-t1)*1000),
                          ("rerank",(t3-t2)*1000)))

    def context(self,question,retrievals,count_tokens,*,budget=3000,maximum=12):
        self.assert_current()
        if not 1<=len(retrievals)<=MAX_SUBQUERIES:
            raise ValueError("Invalid subquery count")
        # Merge each explicit subquery's ranked list; no automatic translation
        # or decomposition that might alter identifiers.
        combined=reciprocal_rank_fusion([(r.reranked,1.) for r in retrievals],limit=60)
        result=pack(question,combined,self._index.docs,count_tokens,budget=budget,maximum=maximum,
                    neighbors=self._next)
        self.assert_current()
        return result

    @property
    def metrics(self):
        # Numeric diagnostics only; no identifiers, source text, tokens.
        return {"documents":len(self._documents),"passages":len(self._index.docs),
                "text_bytes":self._index.text_bytes,"postings":self._index.postings_count}
