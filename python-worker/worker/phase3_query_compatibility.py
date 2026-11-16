"""Phase 3.2 read-only query embedding compatibility smoke test.

Reuses the exact Phase 2 E5 model loader and Phase 3.1 query embedding helper.
Does not access MySQL, Qdrant, RustFS or Redis; does not print questions or vectors.

Run from python-worker with the existing .venv:
    python -m worker.phase3_query_compatibility --case all
"""
from __future__ import annotations

import argparse
import json
from math import fsum, isfinite, sqrt

from .document_embeddings import MODEL_NAME, VECTOR_SIZE, embed_query
from .retrieval_service import MAX_QUERY_TOKENS

MAX_QUESTION_CHARS = 1000
QUERY_PREFIX = "query: "

# Deliberately generic public prompts; no private document text or secrets.
TEST_CASES = {
    "english": "Which database stores the document vectors?",
    "bangla": "নথির ভেক্টর কোন ডাটাবেসে সংরক্ষিত হয়?",
    "mixed": "DocMind-AI document vectors কোথায় রাখা হয়?",
}


def check_query(question: str, *, tokenizer, model) -> dict:
    """Validate and embed one question, returning safe aggregate properties only."""
    if not isinstance(question, str):
        raise ValueError("Invalid query")
    cleaned = question.strip()
    if not cleaned or len(cleaned) > MAX_QUESTION_CHARS:
        raise ValueError("Query length outside permitted bounds")
    if any(ord(ch) in set(range(0, 9)) | {11, 12} | set(range(14, 32)) | {127} for ch in cleaned):
        raise ValueError("Query contains invalid control characters")
    encoded = tokenizer.encode(QUERY_PREFIX + cleaned)
    if not hasattr(encoded, "ids"):
        raise ValueError("Tokenizer did not return token IDs")
    token_count = len(encoded.ids)
    if not 1 <= token_count <= MAX_QUERY_TOKENS:
        raise ValueError("Query exceeds E5 token budget")

    vector = embed_query(model, cleaned)
    # embed_query already rejects wrong dimensions/non-finite/zero vectors,
    # and normalizes the output. Check norm independently for the smoke report.
    norm = sqrt(fsum(float(v) * float(v) for v in vector))
    if len(vector) != VECTOR_SIZE or not isfinite(norm) or abs(norm - 1.0) > 1e-5:
        raise RuntimeError("Unexpected query vector properties")
    return {
        "query_tokens": token_count,
        "vector_dimensions": len(vector),
        "normalized": True,
    }


def run_probe(*, case: str = "all", tokenizer=None, model=None) -> dict:
    """Share a single tokenizer/model across all requested cases.

    Injected model/tokenizer support deterministic, offline unit tests. The
    normal CLI uses Phase 2's cached load_embedding_model() function.
    """
    if case not in (*TEST_CASES, "all"):
        raise ValueError("Unknown query test case")
    if tokenizer is None:
        from .storage_qdrant_probe import _tokenizer
        tokenizer = _tokenizer()
    if model is None:
        from .document_embeddings import load_embedding_model
        model = load_embedding_model()

    selected = list(TEST_CASES) if case == "all" else [case]
    reports = []
    for language in selected:
        details = check_query(TEST_CASES[language], tokenizer=tokenizer, model=model)
        reports.append({"language": language, **details})
    return {
        "mode": "read_only_phase3_query_compatibility",
        "embedding_model": MODEL_NAME,
        "query_prefix": QUERY_PREFIX,
        "expected_dimensions": VECTOR_SIZE,
        "model_revision_pinned_and_verified": False,
        "cases": reports,
        "all_cases_passed": len(reports) == len(selected),
        "mysql_modified": False,
        "qdrant_modified": False,
        "redis_modified": False,
        "rustfs_modified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=[*TEST_CASES, "all"], default="all")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run_probe(case=args.case), indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        # Do not print exception bodies: third-party errors can contain private
        # environment information or a question's text.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
