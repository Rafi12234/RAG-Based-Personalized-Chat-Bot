"""Offline Phase 3.3C benchmark regression tests; standard library only."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from .phase3_retrieval_quality import evaluate, main


def fixture():
    return {
        "dataset_kind": "synthetic_smoke",
        "cases": [
            {"case_id": "en", "language": "english", "expected_document_ids": ["doc-A"],
             "results": [{"document_id": "doc-A", "score": 0.91}, {"document_id": "doc-B", "score": 0.4}]},
            {"case_id": "bn", "language": "bangla", "expected_document_ids": ["doc-B"],
             "results": [{"document_id": "doc-A", "score": 0.80}, {"document_id": "doc-B", "score": 0.70}]},
            {"case_id": "mixed", "language": "mixed", "expected_document_ids": ["doc-C"],
             "results": [{"document_id": "doc-B", "score": 0.8}]},
            {"case_id": "none", "language": "mixed", "expected_document_ids": [], "results": []},
        ],
    }


class QualityBenchmarkTests(unittest.TestCase):
    def test_metrics_at_1(self):
        report = evaluate(fixture())
        m = report["at_k"][0]
        self.assertEqual(m["hit_at_k"], 0.3333)
        self.assertEqual(m["precision_at_k"], 0.3333)
        self.assertEqual(m["recall_at_k"], 0.3333)
        self.assertEqual(m["mrr_at_k"], 0.3333)
        self.assertEqual(m["ndcg_at_k"], 0.3333)

    def test_metrics_at_3(self):
        m = evaluate(fixture())["at_k"][1]
        self.assertEqual(m["hit_at_k"], 0.6667)
        self.assertEqual(m["precision_at_k"], 0.2222)
        self.assertEqual(m["recall_at_k"], 0.6667)
        self.assertEqual(m["mrr_at_k"], 0.5)
        self.assertAlmostEqual(m["ndcg_at_k"], 0.5436, places=4)

    def test_languages_present_and_no_secrets_echoed(self):
        data = fixture()
        data["cases"][0]["case_id"] = "private-case-string"
        report = evaluate(data)
        self.assertEqual(report["languages"]["bangla"]["cases"], 1)
        self.assertEqual(report["languages"]["mixed"]["cases"], 2)
        self.assertNotIn("private-case-string", repr(report))
        self.assertNotIn("doc-A", repr(report))

    def test_negative_empty_result_rate(self):
        self.assertEqual(evaluate(fixture())["at_k"][0]["negative_empty_result_rate"], 1.0)
        data = fixture()
        data["cases"][3]["results"] = [{"document_id": "doc-A", "score": 0.2}]
        self.assertEqual(evaluate(data)["at_k"][2]["negative_empty_result_rate"], 0.0)

    def test_duplicates_do_not_inflate_relevance(self):
        data = fixture()
        data["cases"][0]["results"] = [
            {"document_id": "doc-A", "score": 0.9},
            {"document_id": "doc-A", "score": 0.8},
        ]
        m = evaluate(data)["at_k"][1]
        self.assertEqual(m["precision_at_k"], 0.2222)

    def test_synthetic_never_claims_accuracy_even_if_perfect(self):
        data = fixture()
        self.assertFalse(evaluate(data)["quality_claim_allowed"])

    def test_quality_claim_needs_multi_doc_multilingual_real_labels(self):
        data = fixture()
        data["dataset_kind"] = "real_labeled"
        self.assertFalse(evaluate(data)["quality_claim_allowed"])
        data["cases"] = [
            {"case_id": f"{lang}-{i}", "language": lang,
             "expected_document_ids": [f"doc-{i}"],
             "results": [{"document_id": f"doc-{i}", "score": 0.7}]}
            for lang in ("english", "bangla", "mixed") for i in range(3)
        ]
        self.assertTrue(evaluate(data)["quality_claim_allowed"])

    def test_reject_malformed_envelope(self):
        for x in (None, [], {}, {"dataset_kind": "wrong", "cases": fixture()["cases"]}):
            with self.subTest(value=str(x)[:10]), self.assertRaises(ValueError):
                evaluate(x)

    def test_reject_unranked_and_invalid_scores(self):
        for score in (float("nan"), float("inf"), 1.1, -1.01, True, "0.8"):
            data = fixture()
            data["cases"][0]["results"][0]["score"] = score
            with self.subTest(score=score), self.assertRaises(ValueError):
                evaluate(data)
        data = fixture()
        data["cases"][0]["results"].reverse()
        with self.assertRaises(ValueError):
            evaluate(data)

    def test_reject_duplicate_cases_and_gold(self):
        data = fixture()
        data["cases"][1]["case_id"] = data["cases"][0]["case_id"]
        with self.assertRaises(ValueError):
            evaluate(data)
        data = fixture()
        data["cases"][0]["expected_document_ids"] = ["doc-A", "doc-A"]
        with self.assertRaises(ValueError):
            evaluate(data)

    def test_reject_excessive_cases_or_results(self):
        data = fixture()
        data["cases"] *= 51
        with self.assertRaises(ValueError):
            evaluate(data)
        data = fixture()
        data["cases"][0]["results"] *= 6
        with self.assertRaises(ValueError):
            evaluate(data)

    def test_cli_read_only_from_local_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.json"
            path.write_text(json.dumps(fixture()))
            with redirect_stdout(io.StringIO()) as output:
                status = main(["--input", str(path)])
            self.assertEqual(status, 0)
            report = json.loads(output.getvalue())
            self.assertFalse(report["quality_claim_allowed"])
            for name in ("mysql_modified", "qdrant_modified", "redis_modified", "rustfs_modified", "network_accessed", "model_invoked"):
                self.assertFalse(report[name])

    def test_cli_sanitizes_bad_input_errors(self):
        with redirect_stdout(io.StringIO()) as output:
            status = main(["--input", "C:/private/secrets/no-file.json"])
        self.assertEqual(status, 1)
        self.assertNotIn("private", output.getvalue())
        self.assertNotIn("secrets", output.getvalue())

    def test_evaluator_uses_no_network(self):
        with patch("socket.create_connection", side_effect=AssertionError("network called")):
            evaluate(fixture())

    def test_all_negative_cases_do_not_fake_relevance_metrics(self):
        data = fixture()
        data["cases"] = [data["cases"][3]]
        metric = evaluate(data)["at_k"][0]
        self.assertIsNone(metric["hit_at_k"])
        self.assertEqual(metric["negative_empty_result_rate"], 1.)


if __name__ == "__main__":
    unittest.main()
