"""Offline Phase 3.2 query-embedding checks; no network, DB or vector writes."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase

from .document_embeddings import EmbeddingValidationError, VECTOR_SIZE
from .phase3_query_compatibility import (
    TEST_CASES, check_query, main, run_probe,
)


class FakeTokenizer:
    def __init__(self):
        self.calls = []

    def encode(self, text):
        self.calls.append(text)
        return SimpleNamespace(ids=list(text))


class FakeModel:
    def __init__(self, vector=None):
        self.calls = []
        self.vector = [1.0] * VECTOR_SIZE if vector is None else vector

    def embed(self, documents, *, batch_size=1):
        self.calls.append((list(documents), batch_size))
        return iter([self.vector] * len(documents))


class Phase3QueryCompatibilityTests(TestCase):
    def setUp(self):
        self.tokenizer = FakeTokenizer()
        self.model = FakeModel()

    def test_english_bangla_and_mixed_cases_pass(self):
        result = run_probe(tokenizer=self.tokenizer, model=self.model)
        self.assertTrue(result["all_cases_passed"])
        self.assertEqual([c["language"] for c in result["cases"]], ["english", "bangla", "mixed"])
        self.assertTrue(all(c["vector_dimensions"] == VECTOR_SIZE and c["normalized"] for c in result["cases"]))
        self.assertEqual(len(self.model.calls), 3)

    def test_query_prefix_matches_embedding_contract(self):
        check_query("Where are vectors stored?", tokenizer=self.tokenizer, model=self.model)
        self.assertEqual(self.tokenizer.calls, ["query: Where are vectors stored?"])
        self.assertEqual(self.model.calls[0][0], ["query: Where are vectors stored?"])

    def test_all_cases_reuse_one_model_and_do_not_return_text(self):
        result = run_probe(tokenizer=self.tokenizer, model=self.model)
        self.assertEqual(len(self.model.calls), 3)
        self.assertNotIn("question", repr(result))
        for prompt in TEST_CASES.values():
            self.assertNotIn(prompt, repr(result))
        self.assertFalse(result["qdrant_modified"])
        self.assertFalse(result["mysql_modified"])

    def test_blank_question_rejected_before_inference(self):
        for text in ("", " \t\n "):
            with self.subTest(text=text), self.assertRaises(ValueError):
                check_query(text, tokenizer=self.tokenizer, model=self.model)
        self.assertFalse(self.model.calls)

    def test_oversized_question_rejected_before_inference(self):
        with self.assertRaises(ValueError):
            check_query("a" * 1001, tokenizer=self.tokenizer, model=self.model)
        self.assertFalse(self.model.calls)

    def test_invalid_controls_rejected_before_inference(self):
        with self.assertRaises(ValueError):
            check_query("hello\x00there", tokenizer=self.tokenizer, model=self.model)
        self.assertFalse(self.model.calls)

    def test_token_budget_rejected_before_inference(self):
        with self.assertRaises(ValueError):
            check_query("x" * 500, tokenizer=self.tokenizer, model=self.model)
        self.assertFalse(self.model.calls)

    def test_wrong_dimension_and_nan_rejected(self):
        for vector in ([1.0] * 383, [float("nan")] * VECTOR_SIZE):
            with self.subTest(dimensions=len(vector)), self.assertRaises(EmbeddingValidationError):
                check_query("Hi", tokenizer=FakeTokenizer(), model=FakeModel(vector))

    def test_single_case(self):
        result = run_probe(case="bangla", tokenizer=self.tokenizer, model=self.model)
        self.assertEqual(len(result["cases"]), 1)
        self.assertEqual(result["cases"][0]["language"], "bangla")

    def test_unknown_case_rejected(self):
        with self.assertRaises(ValueError):
            run_probe(case="unknown", tokenizer=self.tokenizer, model=self.model)
