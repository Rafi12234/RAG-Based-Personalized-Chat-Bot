"""Phase 4.1: read-only, offline retrieval + grounded-answer quality scorer.

Inputs are pre-existing, human-labeled evaluation records. This tool does not
call an LLM, access network services, modify SQL, upload files, or index vectors.
Human reviewers must independently decide whether answers and each citation
are supported by source material. These booleans cannot prove entailment.

Run from python-worker:
  python -m worker.phase4_ai_quality --input ../quality-fixtures/phase4_1_synthetic_example.json
"""
from __future__ import annotations

import argparse
import json
from math import isfinite, log2
from pathlib import Path

LANGUAGES = ("english", "bangla", "mixed")
CATEGORIES = (
    "single_document", "multi_document", "conflict", "unanswerable",
    "table", "ocr", "numeric_temporal", "adversarial", "other",
)
K_VALUES = (1, 3, 5, 10)
MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_CASES = 500
MAX_RETRIEVED = 20
MAX_GOLD = 20
MAX_CITATIONS = 20


def _valid_id(value: object) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= 160
            and value.strip() == value and
            not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _field_keys(value: object, keys: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == keys


def _valid_latency(value: object) -> bool:
    return value is None or (type(value) in (int, float)
                             and isfinite(value) and 0 <= value <= 3_600_000)


def validate(data: object) -> tuple[str, list[dict]]:
    if not _field_keys(data, {"dataset_kind", "cases"}):
        raise ValueError("Invalid benchmark envelope")
    kind = data["dataset_kind"]
    if kind not in ("synthetic_smoke", "real_labeled"):
        raise ValueError("Invalid dataset kind")
    cases = data["cases"]
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise ValueError("Invalid benchmark size")
    seen_cases: set[str] = set()
    case_keys = {"case_id", "question", "language", "category", "split",
                 "gold_document_ids", "expected_status", "retrieved", "observed"}
    for case in cases:
        if not _field_keys(case, case_keys):
            raise ValueError("Invalid benchmark case")
        case_id = case["case_id"]
        if not _valid_id(case_id) or case_id in seen_cases:
            raise ValueError("Invalid or duplicate case ID")
        seen_cases.add(case_id)
        question = case["question"]
        if not isinstance(question, str) or not 2 <= len(question.strip()) <= 1000:
            raise ValueError("Invalid question")
        if any(ord(char) < 32 or ord(char) == 127 for char in question):
            raise ValueError("Question contains control characters")
        if case["language"] not in LANGUAGES or case["category"] not in CATEGORIES:
            raise ValueError("Unsupported language or category")
        if case["split"] not in ("development", "holdout"):
            raise ValueError("Unsupported dataset split")
        gold = case["gold_document_ids"]
        if (not isinstance(gold, list) or len(gold) > MAX_GOLD or
                not all(_valid_id(x) for x in gold) or len(set(gold)) != len(gold)):
            raise ValueError("Invalid gold evidence IDs")
        expected = case["expected_status"]
        if expected not in ("answered", "insufficient_evidence"):
            raise ValueError("Invalid expected answer status")
        if expected == "answered" and not gold:
            raise ValueError("Answered cases require labeled evidence")
        if case["category"] == "conflict" and (expected != "insufficient_evidence" or len(gold) < 2):
            raise ValueError("Conflict cases need multiple gold documents and abstention")
        if case["category"] == "unanswerable" and (expected != "insufficient_evidence" or gold):
            raise ValueError("Unanswerable cases have no gold documents")

        retrieved = case["retrieved"]
        if not isinstance(retrieved, list) or len(retrieved) > MAX_RETRIEVED:
            raise ValueError("Invalid retrieved results")
        previous_score = float("inf")
        for hit in retrieved:
            if not _field_keys(hit, {"document_id", "score"}):
                raise ValueError("Invalid retrieved record")
            score = hit["score"]
            if (not _valid_id(hit["document_id"]) or type(score) not in (int, float) or
                    not isfinite(score) or not -1 <= score <= 1 or score > previous_score):
                raise ValueError("Invalid or unsorted retrieval score")
            previous_score = score

        observed = case["observed"]
        if not _field_keys(observed, {"status", "factual_correct", "citations", "latency_ms"}):
            raise ValueError("Invalid observed answer")
        if observed["status"] not in ("answered", "insufficient_evidence", "error"):
            raise ValueError("Invalid observed status")
        if not _valid_latency(observed["latency_ms"]):
            raise ValueError("Invalid latency")
        citations = observed["citations"]
        if not isinstance(citations, list) or len(citations) > MAX_CITATIONS:
            raise ValueError("Invalid citations")
        if observed["status"] == "answered":
            # These are independent HUMAN annotations, not inferred from URLs/IDs.
            if type(observed["factual_correct"]) is not bool:
                raise ValueError("Answered cases require manually reviewed factual correctness")
        elif observed["factual_correct"] is not None or citations:
            raise ValueError("Abstention/error must not have answer or citations")
        for citation in citations:
            if (not _field_keys(citation, {"document_id", "supports_claim"}) or
                    not _valid_id(citation["document_id"]) or
                    type(citation["supports_claim"]) is not bool):
                raise ValueError("Citations require manually reviewed support flags")
    return kind, cases


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def _ranking(cases: list[dict], k: int) -> dict:
    positives = [x for x in cases if x["gold_document_ids"]]
    negatives = [x for x in cases if not x["gold_document_ids"]]
    recalls: list[float] = []
    reciprocal: list[float] = []
    ndcgs: list[float] = []
    full_coverage: list[float] = []
    for case in positives:
        gold = set(case["gold_document_ids"])
        seen: set[str] = set()
        gains: list[int] = []
        for hit in case["retrieved"][:k]:
            doc_id = hit["document_id"]
            relevant = int(doc_id in gold and doc_id not in seen)
            gains.append(relevant)
            seen.add(doc_id)
        relevant_count = sum(gains)
        recalls.append(relevant_count / len(gold))
        full_coverage.append(float(relevant_count == len(gold)))
        reciprocal.append(next((1 / i for i, rel in enumerate(gains, 1) if rel), 0.0))
        dcg = sum(rel / log2(i + 1) for i, rel in enumerate(gains, 1))
        idcg = sum(1 / log2(i + 1) for i in range(1, min(k, len(gold)) + 1))
        ndcgs.append(dcg / idcg)
    return {
        "k": k,
        "relevant_cases": len(positives),
        "recall_at_k": _mean(recalls),
        "mrr_at_k": _mean(reciprocal),
        "ndcg_at_k": _mean(ndcgs),
        "all_gold_documents_at_k": _mean(full_coverage),
        # A negative query may legitimately retrieve irrelevant passages; the
        # downstream model must still abstain. This is only a diagnostic.
        "unanswerable_empty_retrieval_rate": _mean([
            float(not x["retrieved"]) for x in negatives
        ]),
    }


def _answer(cases: list[dict]) -> dict:
    answered = [x for x in cases if x["expected_status"] == "answered"]
    abstain = [x for x in cases if x["expected_status"] == "insufficient_evidence"]
    attempts = [x for x in cases if x["observed"]["status"] == "answered"]
    factual = [x["observed"]["factual_correct"] for x in attempts]
    citation_flags = [citation["supports_claim"] for x in attempts
                      for citation in x["observed"]["citations"]]
    correct_flags = []
    for case in cases:
        observed = case["observed"]
        if case["expected_status"] == "insufficient_evidence":
            correct_flags.append(float(observed["status"] == "insufficient_evidence"))
        else:
            correct_flags.append(float(
                observed["status"] == "answered"
                and observed["factual_correct"] is True
                and bool(observed["citations"])
                and all(c["supports_claim"] for c in observed["citations"])
                and all(c["document_id"] in case["gold_document_ids"]
                        for c in observed["citations"])
            ))
    return {
        "cases": len(cases),
        "full_case_success_rate": _mean(correct_flags),
        "required_answer_count": len(answered),
        "supported_answer_success_rate": _mean([
            correct_flags[i] for i, x in enumerate(cases) if x["expected_status"] == "answered"
        ]),
        "required_abstention_count": len(abstain),
        "correct_abstention_rate": _mean([
            correct_flags[i] for i, x in enumerate(cases)
            if x["expected_status"] == "insufficient_evidence"
        ]),
        "generated_answer_count": len(attempts),
        "human_judged_factual_correct_rate": _mean([float(flag) for flag in factual]),
        "human_judged_citation_support_precision": _mean([float(flag) for flag in citation_flags]),
        "reviewed_citation_count": len(citation_flags),
        "response_error_count": sum(x["observed"]["status"] == "error" for x in cases),
        "latency_ms": {
            "observations": sum(x["observed"]["latency_ms"] is not None for x in cases),
            "p50": _percentile([x["observed"]["latency_ms"] for x in cases
                                if x["observed"]["latency_ms"] is not None], 0.50),
            "p95": _percentile([x["observed"]["latency_ms"] for x in cases
                                if x["observed"]["latency_ms"] is not None], 0.95),
        },
    }


def evaluate(data: object) -> dict:
    kind, cases = validate(data)
    positive_cases = [x for x in cases if x["gold_document_ids"]]
    negative_cases = [x for x in cases if x["expected_status"] == "insufficient_evidence"]
    languages = {lang: sum(x["language"] == lang for x in cases) for lang in LANGUAGES}
    distinct_docs = {doc for x in positive_cases for doc in x["gold_document_ids"]}
    holdout = sum(x["split"] == "holdout" for x in cases)
    eligible = (kind == "real_labeled" and len(cases) >= 100 and len(positive_cases) >= 50
                and len(negative_cases) >= 20 and len(distinct_docs) >= 10
                and holdout >= 20 and all(n >= 10 for n in languages.values()))
    return {
        "mode": "offline_phase4_ai_quality",
        "dataset_kind": kind,
        "human_evidence_review_required": True,
        "preliminary_quality_claim_allowed": eligible,
        "quality_claim_note": (
            "A dataset's human-review labels are trusted inputs, not verified by this code. "
            "Synthetic examples and small samples cannot establish real-world AI quality."
        ),
        "cases": len(cases),
        "relevant_retrieval_cases": len(positive_cases),
        "unique_gold_documents": len(distinct_docs),
        "holdout_cases": holdout,
        "languages": languages,
        "categories": {category: sum(x["category"] == category for x in cases)
                       for category in CATEGORIES},
        "retrieval": [_ranking(cases, k) for k in K_VALUES],
        "answer": _answer(cases),
        "database_modified": False,
        "qdrant_modified": False,
        "network_accessed": False,
        "model_invoked": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only Phase 4.1 RAG+answer quality scorer")
    parser.add_argument("--input", required=True, help="Local evaluation JSON; no database access")
    args = parser.parse_args(argv)
    try:
        path = Path(args.input).expanduser()
        if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("Invalid benchmark file")
        data = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps(evaluate(data), indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, TypeError, UnicodeError):
        # Never echo private file paths, questions, source titles or source text.
        print(json.dumps({"status": "failed", "error": "invalid_benchmark_input"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
