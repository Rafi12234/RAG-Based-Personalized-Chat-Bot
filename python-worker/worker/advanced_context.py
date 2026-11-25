"""Phase 4.5: bounded reranking, adjacent evidence and exact context budget."""
from __future__ import annotations

from hashlib import sha256
from math import isfinite
import json

from .hybrid_index import normalize, Passage

MAX_RERANK = 80
MAX_CONTEXT = 20


def rerank(question, ids, docs, scorer, *, limit=40):
    if (type(limit) is not int or not 1 <= limit <= MAX_RERANK or
            len(ids) > MAX_RERANK or len(set(ids)) != len(ids)):
        raise ValueError("Invalid reranking budget")
    scores = []
    for start in range(0,len(ids),16):
        batch = ids[start:start+16]
        out = scorer.score([(question,docs[key].text) for key in batch])
        if len(out)!=len(batch):
            raise RuntimeError("Reranker omitted candidates")
        for key,score in zip(batch,out):
            if type(score) not in (float,int) or not isfinite(score):
                raise RuntimeError("Invalid reranker score")
            scores.append((key,score))
    # Stable tie order preserves the fused ranking.
    return tuple(key for key,_ in sorted(scores,key=lambda x:-x[1])[:limit])


def evidence_json(passages):
    return json.dumps([{"id":p.id,"document":p.title,"page":p.page,
                        "locator":p.locator,"section":p.section,"text":p.text}
                       for p in passages],ensure_ascii=False,separators=(",",":"))


def pack(question, ranked, docs, count_tokens, *, budget=3000, maximum=12, neighbors=None):
    """Deduplicate, interleave pages/documents, expand immediate same-parent neighbors.

    count_tokens must be the exact generation tokenizer for the FULL serialized
    prompt (including instructions and reserved completion tokens), supplied by
    the trusted operator. No approximate character/token ratio or clipped text.
    """
    if (type(budget) is not int or not 256 <= budget <= 32000 or
            type(maximum) is not int or not 1 <= maximum <= MAX_CONTEXT or
            len(ranked)>MAX_RERANK or len(set(ranked))!=len(ranked)):
        raise ValueError("Invalid context budget")
    # Build only a compact adjacency map; callers may precompute outside query.
    # Each passage has at most one previous/next neighbor within a parent.
    next_id=neighbors
    if next_id is None:
        next_id={}
        for p in docs.values():
            if p.previous in docs:
                previous=docs[p.previous]
                if (previous.version_id,previous.parent)==(p.version_id,p.parent):
                    if p.previous in next_id:
                        raise RuntimeError("Ambiguous verified neighbor")
                    next_id[p.previous]=p.id
    primary=[]
    deferred=[]
    seen_pages=set()
    for key in ranked:
        p=docs[key]
        bucket=(p.version_id,p.page if p.page is not None else p.parent)
        if bucket in seen_pages:
            deferred.append(key)
        else:
            primary.append(key)
            seen_pages.add(bucket)
    selected=[]
    seen_ids=set()
    seen_text=set()

    def add(key):
        if key is None or key in seen_ids or len(selected)>=maximum:
            return
        p=docs[key]
        digest=sha256(normalize(p.text).encode()).digest()
        # Keep identical statements from DIFFERENT documents for provenance.
        fingerprint=(p.version_id,digest)
        if fingerprint in seen_text:
            return
        proposed=selected+[p]
        n=count_tokens(question,evidence_json(proposed))
        if type(n) is not int or n<0:
            raise RuntimeError("Invalid generation tokenizer count")
        if n<=budget:
            selected.append(p)
            seen_ids.add(key)
            seen_text.add(fingerprint)

    # First allocate diverse primary hits; then local neighbors and leftovers.
    for key in primary[:max(1,maximum//2)]:
        add(key)
    for p in tuple(selected):
        if p.previous in docs:
            prev=docs[p.previous]
            if (prev.version_id,prev.parent)==(p.version_id,p.parent):
                add(prev.id)
        add(next_id.get(p.id))
    for key in primary+deferred:
        add(key)
    return tuple(selected)
