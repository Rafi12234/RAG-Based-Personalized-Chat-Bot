"""Prepare and select one immutable verified advanced generation under live claim.

Only new documents in the explicit disposable KB; no retry overwrite or
deletion. Existing v1 completion remains the final READY fence.
"""
from pathlib import Path

from .advanced_live_config import enabled_for,embedding_model,bundle_key
from .advanced_live_bundle import write_bundle
from .document_structure import iter_pdf_page_structures, iter_docx_structure, _block, PdfPageStructure
from .document_extraction import iter_extracted_segments
from .document_ocr import iter_pdf_segments_with_ocr
from .hierarchical_index import HierarchyManifestBuilder, iter_manifested_pdf_passages, iter_manifested_docx_passages
from .hierarchy_manifest_store import HierarchyLedgerBuilder
from .hierarchical_shadow import initialize_shadow_collection, stage_shadow_index, iter_shadow_embeddings, SHADOW_COLLECTION
from .hierarchy_shadow_audit import _version_filter
from .shadow_reader import register_pinned_candidate
from .hierarchy_generation_registry import select_verified_candidate
from .large_document_shards import source_sha256


def _pages(file,version,language,max_ocr_pages,guard):
    ocr=0
    for p in iter_pdf_page_structures(file,version):
        if guard() is not True:
            raise RuntimeError("Advanced extraction lease lost")
        if p.needs_ocr:
            ocr+=1
            if ocr>max_ocr_pages:
                raise RuntimeError("Advanced OCR budget exceeded")
            text=list(iter_pdf_segments_with_ocr(file,language=language,start_page=p.page_number,
                                end_page=p.page_number,max_ocr_pages=1))
            if len(text)!=1 or text[0].page_number!=p.page_number or not text[0].text.strip():
                raise RuntimeError("Unresolved advanced OCR page")
            b=_block(version,0,text[0].text.strip(),"pdf_ocr",p.page_number,None,(),
                     f"pdf:p{p.page_number:05d}:ocr")
            yield PdfPageStructure(p.page_number,(b,),False,True)
        else:
            yield p


def _txt(file,version):
    for n,s in enumerate(iter_extracted_segments(file,"source.txt")):
        if s.text.strip():
            yield _block(version,n,s.text,"txt_text",None,(),(),f"txt:block:{n:08d}")


def build_ledger(file,identity,model,*,language,max_ocr_pages,guard):
    file=Path(file)
    version=identity.document_version_id
    ext=file.suffix.lower()
    pages=None
    if ext==".pdf":
        import pymupdf
        with pymupdf.open(str(file)) as pdf:
            pages=pdf.page_count
    original=source_sha256(file)
    mb=HierarchyManifestBuilder(version,original,expected_pages=pages)
    lb=HierarchyLedgerBuilder()
    if ext==".pdf":
        iterator=iter_manifested_pdf_passages(_pages(file,version,language,max_ocr_pages,guard),mb,model)
    elif ext==".docx":
        iterator=iter_manifested_docx_passages(iter_docx_structure(file,version),mb,model)
    elif ext==".txt":
        iterator=iter_manifested_docx_passages(_txt(file,version),mb,model)
    else:
        raise ValueError("Unsupported advanced source")
    passages=[]
    size=0
    for p in iterator:
        if len(passages)%128==0 and guard() is not True:
            raise RuntimeError("Advanced extraction lease lost")
        size+=len(p.text.encode("utf-8"))+len(p.embedding_text.encode("utf-8"))
        if size>64*1024*1024:
            raise RuntimeError("Advanced passage memory budget exceeded")
        lb.add_passage(p)
        passages.append(p)
    ledger=lb.finalize(mb.finalize())
    if source_sha256(file)!=original or guard() is not True:
        raise RuntimeError("Original source or lease changed")
    return passages,ledger


def prepare_advanced_if_selected(client,s3,bucket,file,identity,token,*,guard,language,max_ocr_pages):
    if not enabled_for(identity.knowledge_base_id):
        return None
    if guard() is not True:
        raise RuntimeError("Advanced ingestion lease lost")
    from .db import get_connection
    model=embedding_model()
    key=bundle_key()
    initialize_shadow_collection(client,explicit_create=True)
    # Version-wide check, NOT just same generation. Reject orphan staging
    # instead of risking deterministic passage UUID overwrite on retry.
    scope=_version_filter(identity)
    if client.count(collection_name=SHADOW_COLLECTION,count_filter=scope,exact=True).count!=0:
        raise RuntimeError("Existing advanced generation requires manual reconciliation")
    passages,ledger=build_ledger(file,identity,model,language=language,max_ocr_pages=max_ocr_pages,guard=guard)
    if guard() is not True or client.count(collection_name=SHADOW_COLLECTION,count_filter=scope,exact=True).count!=0:
        raise RuntimeError("Advanced version scope is no longer empty")
    stage_shadow_index(client,identity,ledger.manifest,iter_shadow_embeddings(passages,model),token,
                       lease_guard=guard)
    write_bundle(s3,bucket,identity,ledger,model.pin.digest,token,key,guard=guard)
    connection=get_connection()
    try:
        generation=register_pinned_candidate(connection,client,identity,ledger,token,model,lease_guard=guard)
        select_verified_candidate(connection,identity,generation,token,expected_revision=0,lease_guard=guard)
    finally:
        connection.close()
    if guard() is not True:
        raise RuntimeError("Advanced lease lost before v1 publication")
    return generation
