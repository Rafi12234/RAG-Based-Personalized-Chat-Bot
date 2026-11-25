"""Phase 4.8 native PDF extraction -> hierarchy -> BM25 scale smoke.

Real generated PDF files, synthetic text/tokenizer, no real ML or live stores.
No latency SLO assertions. Report actual timings, page coverage and rare-ID
retrieval from first/middle/last page. Temporary directory cleaned on exit.
"""
from __future__ import annotations

from hashlib import sha256
import json
import platform
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
import tracemalloc

import pymupdf

from .document_structure import iter_pdf_page_structures
from .hierarchical_index import HierarchyManifestBuilder, iter_manifested_pdf_passages
from .hierarchy_manifest_store import HierarchyLedgerBuilder
from .hybrid_index import BM25, Passage
from .advanced_rag_metrics import evaluate


class SyntheticTokenizer:
    def encode(self,text):
        return type("Encoding",(),{"ids":range(len(text)+2)})()


def run_scale(pages):
    if type(pages) is not int or not 1<=pages<=5000:
        raise ValueError("Invalid scale")
    with TemporaryDirectory() as root:
        file=Path(root)/"book.pdf"
        version="synthetic-"+str(pages)
        with pymupdf.open() as doc:
            for i in range(1,pages+1):
                page=doc.new_page()
                page.insert_text((40,60),f"Chapter reference. Unique REF-{i:06d}. Verified value {i*17}.")
            doc.save(str(file))
        sha=sha256(file.read_bytes()).hexdigest()
        mb=HierarchyManifestBuilder(version,sha,expected_pages=pages)
        lb=HierarchyLedgerBuilder()
        start=perf_counter()
        tracemalloc.start()
        try:
            def passages():
                for p in iter_manifested_pdf_passages(iter_pdf_page_structures(file,version),mb,SyntheticTokenizer()):
                    lb.add_passage(p)
                    yield Passage(p.passage_id,"synthetic",version,"pending","Synthetic PDF",
                                  p.page_number,p.source_locator,p.section_path,p.parent_id,
                                  p.previous_in_parent,p.text)
            index=BM25(passages())
            manifest=mb.finalize()
            ledger=lb.finalize(manifest)
            _,peak=tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        elapsed=perf_counter()-start
        cases=[]
        for n in sorted({1,max(1,pages//2),pages}):
            start=perf_counter()
            ranked=index.rank(f"REF-{n:06d}")
            ms=(perf_counter()-start)*1000
            gold=[p.id for p in index.docs.values() if p.page==n]
            # No dense/reranker model in this smoke: report them as not run,
            # not as failed or measured by copying lexical ranks.
            cases.append({"page":n,"rank":next((i+1 for i,k in enumerate(ranked) if k in gold),None),
                          "latency_ms":ms})
        return {"pages":pages,"accounted_pages":manifest.accounted_pages,
                "passages":len(ledger.records),"source_bytes":file.stat().st_size,
                "text_bytes":index.text_bytes,"postings":index.postings_count,
                "build_seconds":elapsed,"pages_per_second":pages/elapsed,
                "python_tracemalloc_peak_bytes":peak,"queries":cases,
                "dense":"not_run","reranker":"not_run","ocr":"not_run"}


def main():
    results=[run_scale(n) for n in (500,2000,5000)]
    print(json.dumps({"dataset_kind":"synthetic_native_pdf_smoke",
                      "hardware":platform.platform(),"machine":platform.machine(),
                      "python":platform.python_version(),"results":results,
                      "production_acceptance":False},indent=2))
    if any(r["accounted_pages"]!=r["pages"] or r["passages"]!=r["pages"] or
           any(q["rank"]!=1 for q in r["queries"]) for r in results):
        raise SystemExit("Synthetic coverage or rare-term retrieval regression")


if __name__=="__main__":
    main()
