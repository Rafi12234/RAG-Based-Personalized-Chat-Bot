"""Offline E5 query and Qdrant filter tests; no real vector/model calls."""
from __future__ import annotations
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from . import production_qdrant
from .retrieval_service import RetrievalInput, search_scoped_ready_points


class FakeTokenizer:
    def encode(self, text):
        return SimpleNamespace(ids=list(text))


class FakeModel:
    def __init__(self):
        self.calls = []
    def embed(self, documents, *, batch_size=1):
        self.calls.append((documents, batch_size))
        return iter([[1.0] * 384])


class ScopedSearchTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel()
        self.client = Mock()
        self.client.query_points.return_value = SimpleNamespace(points=[SimpleNamespace(
            id="123e4567-e89b-42d3-a456-426614174000", score=0.8,
            payload={"organization_id": "org1234567890", "knowledge_base_id": "kb1234567890"},
        )])
        self.input = RetrievalInput("org1234567890", "kb1234567890", "What is in the document?", 3)

    def test_scoped_query_is_read_only_and_uses_query_prefix(self):
        schema = SimpleNamespace(exists=True, compatible=True, missing_indexes=())
        with patch.object(production_qdrant, "inspect_collection", return_value=schema), \
             patch.object(production_qdrant, "authorized_ready_filter", return_value="authorized_filter") as filt:
            rows = search_scoped_ready_points(self.input, model=self.model, tokenizer=FakeTokenizer(), client=self.client)
        self.assertEqual(len(rows), 1)
        self.assertEqual(self.model.calls[0][0], ["query: What is in the document?"])
        filt.assert_called_once_with("org1234567890", "kb1234567890")
        self.assertEqual(self.client.query_points.call_args.kwargs["query_filter"], "authorized_filter")
        self.assertEqual(self.client.query_points.call_args.kwargs["collection_name"], production_qdrant.PRODUCTION_COLLECTION)
        self.assertFalse(self.client.query_points.call_args.kwargs["with_vectors"])
        self.client.upsert.assert_not_called()
        self.client.delete_collection.assert_not_called()

    def test_bad_or_oversize_question_fails_before_client_call(self):
        for question in ("", "x" * 1001):
            with self.subTest(question=question[:10]), self.assertRaises(ValueError):
                search_scoped_ready_points(
                    RetrievalInput(self.input.organization_id, self.input.knowledge_base_id, question, 3),
                    model=self.model, tokenizer=FakeTokenizer(), client=self.client,
                )
        self.client.query_points.assert_not_called()

    def test_token_budget_rejected(self):
        with self.assertRaises(ValueError):
            search_scoped_ready_points(
                RetrievalInput(self.input.organization_id, self.input.knowledge_base_id, "x" * 500, 3),
                model=self.model, tokenizer=FakeTokenizer(), client=self.client,
            )
        self.client.query_points.assert_not_called()


if __name__ == "__main__":
    unittest.main()
