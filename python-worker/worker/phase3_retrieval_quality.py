"""Phase 3.3C read-only document-level retrieval-quality benchmark.

Input is a locally labeled JSON dataset with only case IDs, language labels,
expected document IDs, retrieved document IDs and similarity scores.
No MySQL, Qdrant, RustFS, Redis, HTTP, embedding or filesystem writes.

Run from python-worker:
    python -m worker.phase3_retrieval_quality --input ..\\quality-fixtures\\phase3_3c_synthetic.json

Synthetic fixtures are NEVER evidence of live retrieval quality. Real results
require a manually labeled, multi-document dataset obtained with authorized
read-only retrieval requests.
"""
from __future__ import annotations

import argparse
import json
from math import isfinite, log2
from pathlib import Path

LANGUAGES = ("english", "bangla", "mixed")
TOP_K = (1, 3, 5)
MAX_INPUT_BYTES = 512 * 1024
MAX_CASES = 200
MAX_RESULTS = 10
MAX_EXPECTED = 20


def _string(value: object, *, limit: int = 128) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit
            and value.strip() == value and
            not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _validate(data: object) -> tuple[str, list[dict]]:
    if not isinstance(data, dict) or set(data) != {"dataset_kind", "cases"}:
        raise ValueError("Invalid benchmark envelope")
    kind = data["dataset_kind"]
    if kind not in ("synthetic_smoke", "real_labeled"):
        raise ValueError("Invalid dataset kind")
    cases = data["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("Invalid benchmark case count")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"case_id", "language", "expected_document_ids", "results"}:
            raise ValueError("Invalid benchmark case")
        case_id = case["case_id"]
        if not _string(case_id) or case_id in seen:
            raise ValueError("Invalid or duplicate case ID")
        seen.add(case_id)
        if case["language"] not in LANGUAGES:
            raise ValueError("Invalid language")
        expected = case["expected_document_ids"]
        if (not isinstance(expected, list) or len(expected) > MAX_EXPECTED or
                not all(_string(x) for x in expected) or len(set(expected)) != len(expected)):
            raise ValueError("Invalid expected document IDs")
        hits = case["results"]
        if not isinstance(hits, list) or len(hits) > MAX_RESULTS:
            raise ValueError("Invalid retrieved result count")
        previous = float("inf")
        for hit in hits:
            if not isinstance(hit, dict) or set(hit) != {"document_id", "score"}:
                raise ValueError("Invalid retrieved hit")
            score = hit["score"]
            if (not _string(hit["document_id"]) or type(score) not in (float, int)
                    or not isfinite(score) or score < -1 or score > 1):
                raise ValueError("Invalid document ID or similarity score")
            if score > previous + 1e-12:
                raise ValueError("Results must be sorted by descending score")
            previous = score
    return kind, cases


def _metrics_for(cases: list[dict], k: int) -> dict:
    positive = [case for case in cases if case["expected_document_ids"]]
    negative = [case for case in cases if not case["expected_document_ids"]]
    totals = {"hit": 0., "precision": 0., "recall": 0., "mrr": 0., "ndcg": 0.}
    for case in positive:
        expected = set(case["expected_document_ids"])
        seen = set()
        gains = []
        for result in case["results"][:k]:
            doc_id = result["document_id"]
            relevant = int(doc_id in expected and doc_id not in seen)
            seen.add(doc_id)
            gains.append(relevant)
        relevant_count = sum(gains)
        totals["hit"] += float(relevant_count > 0)
        totals["precision"] += relevant_count / k
        totals["recall"] += relevant_count / len(expected)
        totals["mrr"] += next((1.0 / i for i, g in enumerate(gains, 1) if g), 0.)
        dcg = sum(g / log2(i + 1) for i, g in enumerate(gains, 1))
        idcg = sum(1. / log2(i + 1) for i in range(1, min(k, len(expected)) + 1))
        totals["ndcg"] += dcg / idcg
    n = len(positive)
    neg_count = len(negative)
    return {
        "k": k,
        "labeled_positive_cases": n,
        "labeled_negative_cases": neg_count,
        "hit_at_k": round(totals["hit"] / n, 4) if n else None,
        "precision_at_k": round(totals["precision"] / n, 4) if n else None,
        "recall_at_k": round(totals["recall"] / n, 4) if n else None,
        "mrr_at_k": round(totals["mrr"] / n, 4) if n else None,
        "ndcg_at_k": round(totals["ndcg"] / n, 4) if n else None,
        # Until abstention is implemented, any returned source is a negative-case error.
        "negative_empty_result_rate": round(
            sum(not case["results"] for case in negative) / neg_count, 4
        ) if neg_count else None,
    }


def evaluate(data: object) -> dict:
    """Evaluate a pre-existing local JSON object, reporting only aggregates."""
    kind, cases = _validate(data)
    positive = [c for c in cases if c["expected_document_ids"]]
    unique_gold = set(doc_id for c in positive for doc_id in c["expected_document_ids"])
    per_language = {}
    for language in LANGUAGES:
        group = [case for case in cases if case["language"] == language]
        per_language[language] = {
            "cases": len(group),
            "at_1": _metrics_for(group, 1),
            "at_3": _metrics_for(group, 3),
        }
    return {
        "mode": "read_only_phase3_3c_relevance_evaluation",
        "dataset_kind": kind,
        "quality_claim_allowed": kind == "real_labeled" and len(unique_gold) >= 3 and len(positive) >= 6
            and all(sum(c["language"] == language and bool(c["expected_document_ids"])
                        for c in positive) >= 2 for language in LANGUAGES),
        "quality_claim_note": "Only a sufficiently varied real, manually labeled corpus permits a preliminary quality assessment; no production certification is implied.",
        "cases": len(cases),
        "distinct_expected_documents": len(unique_gold),
        "at_k": [_metrics_for(cases, k) for k in TOP_K],
        "languages": per_language,
        "mysql_modified": False,
        "qdrant_modified": False,
        "redis_modified": False,
        "rustfs_modified": False,
        "model_invoked": False,
        "network_accessed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only, offline retrieval-quality evaluation")
    parser.add_argument("--input", required=True, help="Local labeled JSON file (not uploaded)")
    args = parser.parse_args(argv)
    try:
        path = Path(args.input).expanduser()
        if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("Invalid benchmark file")
        data = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps(evaluate(data), indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        # Never echo private paths, IDs, questions, retrieved contents, or input bytes.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
