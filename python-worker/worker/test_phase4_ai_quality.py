"""Phase 4.1 offline AI quality scorer: isolated tests, no DB or service calls."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from .phase4_ai_quality import evaluate, main, validate

EXAMPLE = Path(__file__).resolve().parents[2] / "quality-fixtures" / "phase4_1_synthetic_example.json"


def data():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


class Phase4QualityTests(unittest.TestCase):
    def test_baseline_metrics_and_clear_synthetic_disclaimer(self):
        report = evaluate(data())
        self.assertFalse(report["preliminary_quality_claim_allowed"])
        self.assertEqual(report["cases"], 5)
        self.assertEqual(report["retrieval"][0]["recall_at_k"], 0.5)
        self.assertEqual(report["retrieval"][1]["recall_at_k"], 1.0)
        self.assertEqual(report["retrieval"][0]["mrr_at_k"], 0.75)
        self.assertEqual(report["answer"]["required_abstention_count"], 2)
        self.assertEqual(report["answer"]["correct_abstention_rate"], 1.0)
        self.assertEqual(report["answer"]["generated_answer_count"], 3)
        self.assertEqual(report["answer"]["human_judged_citation_support_precision"], 0.6667)
        self.assertEqual(report["answer"]["full_case_success_rate"], 0.8)
        self.assertEqual(report["answer"]["latency_ms"]["p50"], 680.0)
        self.assertEqual(report["answer"]["response_error_count"], 0)
        for key in ("database_modified", "qdrant_modified", "network_accessed", "model_invoked"):
            self.assertIs(report[key], False)

    def test_protects_claims_against_unlabeled_real_world_results(self):
        benchmark = data()
        benchmark["dataset_kind"] = "real_labeled"
        self.assertFalse(evaluate(benchmark)["preliminary_quality_claim_allowed"])

    def test_invalid_cases_and_score_order(self):
        for mutate in [
            lambda x: x["cases"][0].update({"case_id": x["cases"][1]["case_id"]}),
            lambda x: x["cases"][0]["retrieved"].reverse(),
            lambda x: x["cases"][0]["retrieved"][0].update({"score": float("nan")}),
            lambda x: x["cases"][0]["observed"].update({"factual_correct": None}),
            lambda x: x["cases"][2]["observed"].update({"citations": [{"document_id": "doc-X", "supports_claim": True}]}),
            lambda x: x["cases"][0].update({"gold_document_ids": []}),
            lambda x: x["cases"][3].update({"gold_document_ids": ["doc-a"]}),
            lambda x: x["cases"][0].update({"language": "fake"}),
            lambda x: x["cases"][0]["observed"].update({"latency_ms": -1}),
        ]:
            benchmark = data()
            mutate(benchmark)
            with self.assertRaises(ValueError):
                validate(benchmark)

    def test_duplicate_evidence_does_not_inflate_recall(self):
        benchmark = data()
        benchmark["cases"][3]["retrieved"] = [
            {"document_id": "doc-policy-a", "score": .9},
            {"document_id": "doc-policy-a", "score": .8},
        ]
        scored = evaluate(benchmark)
        # This question requires both policy documents, never full coverage.
        self.assertEqual(scored["retrieval"][1]["all_gold_documents_at_k"], 0.75)

    def test_cli_never_calls_network_and_sanitizes_errors(self):
        with patch("socket.create_connection", side_effect=AssertionError("network accessed")):
            with redirect_stdout(io.StringIO()) as captured:
                result = main(["--input", str(EXAMPLE)])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(captured.getvalue())["cases"], 5)
        with redirect_stdout(io.StringIO()) as captured:
            self.assertEqual(main(["--input", "C:/private/path/not-present.json"]), 1)
        self.assertNotIn("private", captured.getvalue())

    def test_empty_and_unreviewed_answer_rejected(self):
        benchmark = data()
        benchmark["cases"] = []
        with self.assertRaises(ValueError):
            evaluate(benchmark)
        benchmark = data()
        benchmark["cases"][0]["observed"]["citations"][0]["supports_claim"] = "yes"
        with self.assertRaises(ValueError):
            evaluate(benchmark)


if __name__ == "__main__":
    unittest.main()
