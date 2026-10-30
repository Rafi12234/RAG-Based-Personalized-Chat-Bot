"""Phase 2.12: READ-ONLY document stress benchmark for DocMind-AI.

Operates on an existing LOCAL PDF/DOCX/TXT file. Does NOT access MySQL,
Qdrant, Redis, RustFS, Celery, or change ingestion-job state.

Examples (from python-worker):
  python -m worker.phase2_qa_benchmark --file ..\\qa-fixtures\\qa_native.pdf --mode extract --expected-pages 20
  python -m worker.phase2_qa_benchmark --file ..\\qa-fixtures\\qa_native.pdf --mode chunks --expected-pages 20
  python -m worker.phase2_qa_benchmark --file ..\\qa-fixtures\\qa_scanned.pdf --mode chunks --ocr --ocr-language eng --max-ocr-pages 2 --expected-pages 2
  python -m worker.phase2_qa_benchmark --file ..\\qa-fixtures\\qa_bengali.txt --mode embed --max-chunks 500

`--mode embed` performs REAL local model inference and can be slow. Only
Python allocation peak is tracked by tracemalloc: native/ONNX memory is NOT
accounted for, so this is not a full-process RAM benchmark.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import time
import tracemalloc
from uuid import UUID

from .document_extraction import iter_extracted_segments, ExtractedSegment
from .document_ocr import iter_pdf_segments_with_ocr
from .document_chunking import iter_document_chunks
from .token_safe_chunking import iter_token_safe_chunks, _count_tokens, SAFE_TOKEN_BUDGET
from .document_embeddings import VECTOR_SIZE, MODEL_NAME, iter_chunk_embeddings

MAX_INPUT_BYTES = 250 * 1024 * 1024
MAX_QA_CHUNKS = 50_000
MODES = ("extract", "chunks", "embed")


def _check_source(path: Path) -> None:
    if not path.is_file() or path.suffix.lower() not in (".pdf", ".docx", ".txt"):
        raise ValueError("Provide an existing local PDF, DOCX, or TXT file")
    size = path.stat().st_size
    if size < 1 or size > MAX_INPUT_BYTES:
        raise ValueError("Input file size is outside accepted upload limits")


def benchmark_local_file(path: str | Path, *, mode: str = "extract", ocr: bool = False,
                         ocr_language: str = "eng", max_ocr_pages: int = 10,
                         max_chunks: int = MAX_QA_CHUNKS, expected_pages: int | None = None,
                         tokenizer=None, model=None) -> dict:
    """Validate actual streaming pipeline; never publish or persist vectors.

    The caller can inject a synthetic tokenizer/model for *unit tests*.
    CLI defaults to the real E5 tokenizer and model only for chunk/embed modes.
    """
    source = Path(path).expanduser().resolve()
    _check_source(source)
    if mode not in MODES:
        raise ValueError("mode must be extract, chunks, or embed")
    if type(ocr) is not bool:
        raise TypeError("ocr must be a bool")
    if ocr and source.suffix.lower() != ".pdf":
        raise ValueError("OCR is only applicable to PDF input")
    if type(max_ocr_pages) is not int or not 1 <= max_ocr_pages <= 5000:
        raise ValueError("max_ocr_pages must be 1..5000")
    if type(max_chunks) is not int or not 1 <= max_chunks <= MAX_QA_CHUNKS:
        raise ValueError("max_chunks must be 1..50000")
    if expected_pages is not None and (type(expected_pages) is not int or expected_pages < 1):
        raise ValueError("expected_pages must be positive")
    if expected_pages is not None and source.suffix.lower() != ".pdf":
        raise ValueError("--expected-pages applies only to PDFs")

    start = time.perf_counter()
    peak_before = tracemalloc.is_tracing()
    if not peak_before:
        tracemalloc.start()
    tracemalloc.reset_peak()
    segments_seen = 0
    characters_seen = 0
    ocr_candidates = 0
    blank_pages = []
    ocr_pages = []
    pdf_pages = 0
    chunk_count = 0
    vector_count = 0
    max_tokens_seen = 0
    ids_seen: set[str] = set()
    last_chunk_pdf_page = 0
    pdf_pages_with_text: set[int] = set()
    pdf_pages_indexed: set[int] = set()
    version_id = "docmind-qa-local-benchmark-only"

    try:
        if ocr:
            source_segments = iter_pdf_segments_with_ocr(
                source, language=ocr_language, max_ocr_pages=max_ocr_pages,
                on_ocr=ocr_pages.append, on_blank=blank_pages.append,
            )
        else:
            source_segments = iter_extracted_segments(source, source.name)

        def observed_segments():
            nonlocal segments_seen, characters_seen, ocr_candidates, pdf_pages
            for segment in source_segments:
                if not isinstance(segment, ExtractedSegment):
                    raise RuntimeError("Extraction did not produce ExtractedSegment")
                segments_seen += 1
                characters_seen += len(segment.text)
                if segment.needs_ocr:
                    ocr_candidates += 1
                    # Avoid silently indexing incomplete native PDF extraction.
                    if mode != "extract":
                        raise RuntimeError("Potential OCR page: rerun with --ocr; no partial benchmark")
                if segment.page_number is not None:
                    if segment.page_number != pdf_pages + 1:
                        raise RuntimeError("PDF page order/numbering is inconsistent")
                    pdf_pages = segment.page_number
                    if segment.text.strip():
                        pdf_pages_with_text.add(segment.page_number)
                yield segment

        if mode == "extract":
            for _ in observed_segments():
                pass
        else:
            if tokenizer is None:
                from .storage_qdrant_probe import _tokenizer
                tokenizer = _tokenizer()
            base_chunks = iter_document_chunks(observed_segments(), version_id)
            safe_chunks = iter_token_safe_chunks(base_chunks, tokenizer)

            def checked_chunks():
                nonlocal chunk_count, max_tokens_seen, last_chunk_pdf_page
                for chunk in safe_chunks:
                    if chunk_count >= max_chunks:
                        raise RuntimeError("QA chunk budget exceeded; use a smaller input")
                    if chunk.chunk_index != chunk_count or chunk.document_version_id != version_id:
                        raise RuntimeError("Chunk order or version mismatch")
                    canonical = str(UUID(chunk.chunk_id))
                    if canonical != chunk.chunk_id or canonical in ids_seen:
                        raise RuntimeError("Duplicate/noncanonical chunk UUID")
                    ids_seen.add(canonical)
                    size = _count_tokens(tokenizer, chunk.text)
                    if size > SAFE_TOKEN_BUDGET:
                        raise RuntimeError("Token budget exceeded")
                    if chunk.page_number is not None:
                        if chunk.page_number < last_chunk_pdf_page:
                            raise RuntimeError("Chunks do not follow PDF page order")
                        last_chunk_pdf_page = chunk.page_number
                        pdf_pages_indexed.add(chunk.page_number)
                    max_tokens_seen = max(max_tokens_seen, size)
                    chunk_count += 1
                    yield chunk

            if mode == "chunks":
                for _ in checked_chunks():
                    pass
            else:
                if model is None:
                    from .document_embeddings import load_embedding_model
                    model = load_embedding_model()
                for item in iter_chunk_embeddings(checked_chunks(), model):
                    if len(item.vector) != VECTOR_SIZE or not all(math.isfinite(x) for x in item.vector):
                        raise RuntimeError("Malformed embedding")
                    vector_count += 1
                if vector_count != chunk_count:
                    raise RuntimeError("Missing embeddings")

        if segments_seen == 0 or (mode != "extract" and chunk_count == 0):
            raise RuntimeError("No indexable content in file")
        if expected_pages is not None and pdf_pages != expected_pages:
            raise RuntimeError("PDF page count does not match expectation")
        if mode != "extract" and source.suffix.lower() == ".pdf":
            if pdf_pages_indexed != pdf_pages_with_text:
                raise RuntimeError("Nonblank PDF pages missing from chunk output")

        _, python_peak = tracemalloc.get_traced_memory()
        return {
            "filename": source.name,
            "mode": mode,
            "source_bytes": source.stat().st_size,
            "segments": segments_seen,
            "characters": characters_seen,
            "pdf_pages": pdf_pages if source.suffix.lower() == ".pdf" else None,
            "native_ocr_candidate_pages": ocr_candidates,
            "pdf_pages_with_text": len(pdf_pages_with_text) if source.suffix.lower() == ".pdf" else None,
            "pdf_pages_indexed": len(pdf_pages_indexed) if mode != "extract" and source.suffix.lower() == ".pdf" else None,
            "ocr_pages_processed": len(ocr_pages),
            "blank_pages_observed": len(blank_pages),
            "chunks": chunk_count if mode != "extract" else None,
            "vectors": vector_count if mode == "embed" else None,
            "max_e5_tokens": max_tokens_seen if mode != "extract" else None,
            "embedding_model": MODEL_NAME if mode == "embed" else None,
            "elapsed_seconds": round(time.perf_counter() - start, 3),
            "peak_python_allocations_mb": round(python_peak / 1048576, 2),
            "peak_process_rss_mb": None,
            "qa_passed": True,
            "mysql_modified": False,
            "qdrant_modified": False,
            "redis_modified": False,
            "storage_modified": False,
        }
    finally:
        if not peak_before:
            tracemalloc.stop()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True)
    parser.add_argument("--mode", choices=MODES, default="extract")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--ocr-language", default="eng")
    parser.add_argument("--max-ocr-pages", type=int, default=10)
    parser.add_argument("--max-chunks", type=int, default=MAX_QA_CHUNKS)
    parser.add_argument("--expected-pages", type=int)
    parser.add_argument("--json-out", help="Optional NEW local JSON report path; never overwrites")
    a = parser.parse_args(argv)
    try:
        result = benchmark_local_file(a.file, mode=a.mode, ocr=a.ocr,
                    ocr_language=a.ocr_language, max_ocr_pages=a.max_ocr_pages,
                    max_chunks=a.max_chunks, expected_pages=a.expected_pages)
        if a.json_out:
            with Path(a.json_out).expanduser().open("x", encoding="utf-8") as out:
                json.dump(result, out, indent=2, ensure_ascii=False)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        # No exception text, filename paths or original document content.
        print("Read-only QA benchmark failed: " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
