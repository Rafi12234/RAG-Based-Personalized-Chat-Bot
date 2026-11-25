"""Authenticated local advanced answer service over selected READY versions.

A bounded single-session in-process corpus cache; no sharing across sessions.
All selected READY documents must have eligible verified generations. No
partial KB search, no v1 fallback, no query-enabled production cutover.
"""
from dataclasses import dataclass
from time import monotonic
from threading import Lock
import re

from .advanced_live_config import enabled_for,embedding_model,bundle_key,answer_models,ENGINE
from .advanced_live_bundle import read_bundle
from .advanced_shadow import AdvancedShadowSession,SelectedDocument,MAX_DOCUMENTS
from .advanced_grounding import answer_question
from .hybrid_index import language
from .qdrant_indexing import IndexIdentity
from .shadow_reader import SessionScope,ShadowReadError

CACHE_SECONDS=120
_CACHE_LOCK=Lock()
_cache=None


@dataclass(frozen=True)
class RequestScope:
    session:SessionScope
    organization_id:str
    knowledge_base_id:str


def _discover(scope):
    from .db import get_connection
    con=get_connection()
    try:
        with con.cursor() as cur:
            cur.execute("""
                SELECT 1 AS allowed FROM AuthSession a JOIN User u ON u.id=a.userId
                JOIN OrganizationMember m ON m.userId=u.id
                JOIN KnowledgeBase kb ON kb.organizationId=m.organizationId
                WHERE BINARY a.id=%s AND BINARY a.userId=%s AND BINARY a.tokenHash=%s
                  AND a.expiresAt>UTC_TIMESTAMP(3) AND u.isActive=TRUE
                  AND BINARY kb.id=%s AND BINARY kb.organizationId=%s LIMIT 2
                """,(scope.session.session_id,scope.session.user_id,scope.session.token_hash,
                     scope.knowledge_base_id,scope.organization_id))
            if len(cur.fetchall())!=1:
                raise ShadowReadError("Advanced scope unauthorized")
            cur.execute("""
                SELECT d.id AS document_id,d.title,v.id AS version_id,v.filename,
                       s.generationDigest AS generation,s.revision,s.queryEnabled AS enabled,
                       g.jobId AS job_id,g.sourceSha256 AS source,g.recordsSha256 AS records,
                       g.modelPinSha256 AS pin,g.attemptTokenSha256 AS attempt,g.revokedAt AS revoked,
                       g.verifiedAttempt AS verified_attempt
                FROM Document d JOIN KnowledgeBase kb ON kb.id=d.knowledgeBaseId
                JOIN DocumentVersion v ON v.documentId=d.id
                LEFT JOIN ShadowIndexSelection s ON s.documentVersionId=v.id
                LEFT JOIN ShadowIndexCandidate g ON g.documentVersionId=v.id
                     AND BINARY g.generationDigest=BINARY s.generationDigest
                     AND BINARY g.documentId=BINARY d.id
                     AND BINARY g.organizationId=BINARY kb.organizationId
                     AND BINARY g.knowledgeBaseId=BINARY kb.id
                WHERE BINARY kb.id=%s AND BINARY kb.organizationId=%s
                  AND d.deletedAt IS NULL AND v.status='READY'
                  AND NOT EXISTS (SELECT 1 FROM DocumentVersion newer
                      WHERE newer.documentId=d.id AND newer.version>v.version)
                ORDER BY BINARY d.id LIMIT %s
                """,(scope.knowledge_base_id,scope.organization_id,MAX_DOCUMENTS+1))
            rows=tuple(cur.fetchall())
    finally:
        con.close()
    if len(rows)>MAX_DOCUMENTS:
        raise ShadowReadError("Advanced pilot KB exceeds document limit")
    for row in rows:
        if (row["enabled"]!=0 or type(row["revision"]) is not int or row["revision"]<=0 or
                row["revoked"] is not None or type(row["verified_attempt"]) is not int or
                row["verified_attempt"]<=0 or
                any(not isinstance(row[k],str) or not re.fullmatch(r"[a-f0-9]{64}",row[k])
                    for k in ("generation","source","records","pin","attempt")) or
                not isinstance(row["job_id"],str)):
            raise ShadowReadError("A READY document lacks a pinned advanced generation")
    return rows


def _close_cache():
    global _cache
    old=_cache
    _cache=None
    if old is not None:
        old[3].close()


def _guard(scope,rows):
    if not enabled_for(scope.knowledge_base_id) or _discover(scope)!=rows:
        raise ShadowReadError("Advanced selection or permission changed")
    return True


def _open_scope(scope,rows):
    global _cache
    from .db import get_connection
    from .production_qdrant import local_client
    from .storage_extraction_probe import _s3_client_and_bucket
    if _cache is not None:
        cached_scope,cached_rows,deadline,client,session=_cache
        if cached_scope==scope and cached_rows==rows and monotonic()<deadline:
            session.assert_current()
            return session
        _close_cache()
    model=embedding_model()
    if any(r["pin"]!=model.pin.digest for r in rows):
        raise ShadowReadError("Configured embedding pin differs from selection")
    key=bundle_key()
    s3,bucket=_s3_client_and_bucket()
    try:
        docs=[]
        for r in rows:
            identity=IndexIdentity(scope.organization_id,scope.knowledge_base_id,r["document_id"],
                                   r["version_id"],r["job_id"],r["filename"])
            ledger,token=read_bundle(s3,bucket,identity,r["generation"],r["source"],r["records"],
                          r["pin"],r["attempt"],key,guard=lambda:_guard(scope,rows))
            docs.append(SelectedDocument(identity,ledger,token))
    finally:
        s3.close()
    client=local_client()
    try:
        session=AdvancedShadowSession(connection_factory=get_connection,client=client,session=scope.session,
                                      documents=docs,model=model)
        _guard(scope,rows)
    except BaseException:
        client.close()
        raise
    _cache=(scope,rows,monotonic()+CACHE_SECONDS,client,session)
    return session


def _proofs(rows):
    return [{"documentId":r["document_id"],"documentVersionId":r["version_id"],
             "documentTitle":r["title"],"generation":r["generation"],"revision":str(r["revision"])}
            for r in rows]


def _unknown(question):
    return "নথিতে পর্যাপ্ত যাচাইকৃত প্রমাণ পাওয়া যায়নি।" if language(question)=="bangla" else "The selected documents do not contain sufficient verified evidence."


def _answer_response(answer,question,rows):
    if answer.status!="answered":
        return {"status":"insufficient_evidence","answer":_unknown(question),"citations":[]}
    titles={r["version_id"]:r["title"] for r in rows}
    citations=[]
    lines=[]
    for claim in answer.claims:
        # Markers belong to the server only. Reject model-generated markers,
        # controls and unsafe formatting rather than allowing citation spoofing.
        if re.search(r"\[\s*[sS]\s*\d|\x00|[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]",claim.text):
            return {"status":"insufficient_evidence","answer":_unknown(question),"citations":[]}
        markers=[]
        for c in claim.citations:
            sid=f"S{len(citations)+1}"
            citations.append({"sourceId":sid,"documentId":c.document_id,
                "documentVersionId":c.version_id,"documentTitle":titles[c.version_id],
                "chunkId":c.passage_id,"pageNumber":c.page,"sourceLocator":c.locator,
                "quote":c.quote,"start":c.start,"end":c.end,"score":None})
            markers.append(f"[{sid}]")
        lines.append(claim.text.strip()+" "+" ".join(markers))
    return {"status":"answered","answer":"\n".join(lines),"citations":citations}


def run(scope,question,top_k,operation):
    if not enabled_for(scope.knowledge_base_id):
        raise ShadowReadError("Advanced local pilot unavailable")
    if not _CACHE_LOCK.acquire(blocking=False):
        raise ShadowReadError("Advanced local capacity unavailable")
    try:
        rows=_discover(scope)
        if not rows:
            _close_cache()
            data=({"status":"no_relevant_sources","sources":[]} if operation=="retrieve" else
                  {"status":"insufficient_evidence","answer":_unknown(question),"citations":[]})
        else:
            session=_open_scope(scope,rows)
            reranker,nli,generator=answer_models()
            if operation=="retrieve":
                ranking=session.retrieve(question,reranker=reranker)
                # True reranked order, not a manufactured cosine similarity.
                sources=[]
                for rank,key in enumerate(ranking.reranked[:top_k],1):
                    p=session._index.docs[key]
                    record=session._records[key][1]
                    sources.append({"chunkId":p.id,"documentId":p.document_id,
                        "documentVersionId":p.version_id,"documentTitle":p.title,
                        "chunkIndex":record.passage_index,"pageNumber":p.page,
                        "sourceLocator":p.locator,"excerpt":p.text,"rank":rank,"score":None})
                data={"status":"retrieved" if sources else "no_relevant_sources","sources":sources}
                session.assert_current()
            else:
                answer=answer_question(session,question,generator=generator,nli=nli,
                    count_tokens=generator.count_tokens,reranker=reranker,
                    budget=generator.context,maximum=min(20,max(8,top_k*2)))
                data=_answer_response(answer,question,rows)
        _guard(scope,rows)
        return {**data,"engineVersion":ENGINE,"proofs":_proofs(rows)}
    except BaseException:
        _close_cache()
        raise
    finally:
        _CACHE_LOCK.release()
